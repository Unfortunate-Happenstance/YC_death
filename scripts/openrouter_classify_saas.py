#!/usr/bin/env python3
"""
Classify YC companies as SaaS vs non-SaaS using OpenRouter API.

Default model: openrouter/owl-alpha

Phases:
  1. --sanity          Test API + JSON parsing (3 calls)
  2. --flagged-only    Heuristic-flagged companies, 1 per request (~608)
  3. --unflagged       Rest of corpus, --batch-size 4 (~542 requests)

OpenRouter free models: 20 RPM; 50 RPD without credits, 1,000 RPD with $10+ credits.
Failed attempts still count.

Usage:
  uv run python scripts/openrouter_classify_saas.py --sanity
  uv run python scripts/openrouter_classify_saas.py --flagged-only
  uv run python scripts/openrouter_classify_saas.py --unflagged --batch-size 4
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.saas_classify_shared import (
    BATCH_SCHEMA,
    SANITY_CASES,
    SINGLE_SCHEMA,
    SYSTEM_PROMPT,
    RateLimiter,
    build_batch_prompt,
    build_user_prompt,
    bump_api_attempt,
    bump_request,
    check_quota,
    ensure_md_header,
    export_results,
    extract_json,
    load_companies,
    load_flagged_slugs,
    load_progress,
    load_prompt_cache,
    process_result,
    prompt_cache_key,
    save_progress,
    save_prompt_cache,
)
from scripts.utils import ISSUES_DIR, save_json

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
load_dotenv(ENV_PATH)

MODEL = os.environ.get("OPENROUTER_MODEL", "openrouter/owl-alpha")
API_URL = "https://openrouter.ai/api/v1/chat/completions"
PROVIDER = "openrouter"
TAG_FIELD = "openrouterTag"

PROGRESS_PATH = ISSUES_DIR / "openrouter_saas_audit.progress.json"
CACHE_PATH = ISSUES_DIR / "openrouter_saas_audit.cache.json"
OUTPUT_JSON_PATH = ISSUES_DIR / "mislabeled_saas_openrouter.json"
MD_SECTION = "OpenRouter-confirmed mislabels"

# OpenRouter free tier: 20 RPM; 50 RPD without credits, 1000 with $10+ credits
MAX_RPM = 18
MAX_DAILY_REQUESTS = int(os.environ.get("OPENROUTER_MAX_DAILY", "45"))
MAX_429_RETRIES = 4
MAX_503_RETRIES = 2


def parse_retry_seconds(resp: requests.Response) -> float:
    retry_after = resp.headers.get("Retry-After")
    if retry_after:
        try:
            return max(float(retry_after), 10.0)
        except ValueError:
            pass
    try:
        data = resp.json()
        msg = data.get("error", {}).get("message", "")
        match = re.search(r"retry(?: shortly)?", msg, re.I)
        if match:
            return 60.0
    except Exception:
        pass
    return 60.0


def call_openrouter_raw(
    api_key: str,
    user_text: str,
    json_schema_hint: str,
    cache: dict,
    rate_limiter: RateLimiter,
    progress: dict | None = None,
) -> tuple[dict, bool]:
    key = prompt_cache_key(MODEL, user_text, json_schema_hint)
    hit = cache["entries"].get(key)
    if hit is not None:
        if progress is not None:
            progress["stats"]["cacheHits"] = progress["stats"].get("cacheHits", 0) + 1
        return hit["response"], True

    system = SYSTEM_PROMPT + f"\n\nOutput schema: {json_schema_hint}"
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_text},
        ],
        "temperature": 0.1,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/Unfortunate-Happenstance/YC_death",
        "X-OpenRouter-Title": "YC Death Watch",
    }

    retries_429 = 0
    retries_503 = 0
    while True:
        rate_limiter.wait()
        bump_api_attempt(progress)
        resp = requests.post(API_URL, headers=headers, json=payload, timeout=120)
        if resp.status_code == 429:
            retries_429 += 1
            wait = parse_retry_seconds(resp)
            print(f"    429 retry {retries_429}/{MAX_429_RETRIES} — waiting {wait:.0f}s", flush=True)
            if retries_429 >= MAX_429_RETRIES:
                raise RuntimeError(f"Rate limited (429). Retry in {wait:.0f}s.")
            time.sleep(wait)
            continue
        if resp.status_code in (503, 502, 500):
            retries_503 += 1
            wait = min(90, 20 * retries_503)
            print(f"    {resp.status_code} retry {retries_503}/{MAX_503_RETRIES} — waiting {wait}s", flush=True)
            if retries_503 >= MAX_503_RETRIES:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            time.sleep(wait)
            continue
        if not resp.ok:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        result = extract_json(text)
        cache["entries"][key] = {
            "response": result,
            "cachedAt": datetime.now(timezone.utc).isoformat(),
        }
        save_prompt_cache(CACHE_PATH, cache)
        return result, False


def call_single(
    api_key: str,
    company: dict,
    cache: dict,
    rate_limiter: RateLimiter,
    progress: dict | None = None,
) -> tuple[dict, bool]:
    result, from_cache = call_openrouter_raw(
        api_key, build_user_prompt(company), SINGLE_SCHEMA, cache, rate_limiter, progress
    )
    if "classification" not in result:
        raise ValueError(f"Missing classification: {result}")
    return result, from_cache


def call_batch(
    api_key: str,
    companies: list[dict],
    cache: dict,
    rate_limiter: RateLimiter,
    progress: dict | None = None,
) -> tuple[list[dict], bool]:
    result, from_cache = call_openrouter_raw(
        api_key, build_batch_prompt(companies), BATCH_SCHEMA, cache, rate_limiter, progress
    )
    items = result.get("results")
    if not isinstance(items, list):
        raise ValueError(f"Expected results array: {result}")
    by_slug = {item.get("slug"): item for item in items if item.get("slug")}
    out = []
    for c in companies:
        slug = c["slug"]
        if slug not in by_slug:
            raise ValueError(f"Missing slug in batch response: {slug}")
        out.append(by_slug[slug])
    return out, from_cache


def run_sanity(api_key: str, cache: dict) -> bool:
    rate_limiter = RateLimiter(MAX_RPM)
    by_slug = {c["slug"]: c for c in load_companies()}
    print(f"Sanity check — {MODEL} via OpenRouter (repeated prompts use local cache)\n")
    ok = True
    for slug, expected in SANITY_CASES:
        company = by_slug.get(slug)
        if not company:
            print(f"  SKIP {slug} — not in companies.json")
            continue
        print(f"  Testing {company['name']} (expect ~{expected})...", flush=True)
        try:
            result, from_cache = call_single(api_key, company, cache, rate_limiter)
            tag = result["classification"]
            reason = result.get("reasoning", "")
            cache_note = " [cached]" if from_cache else ""
            match = "✓" if tag == expected else "~"
            print(f"    {match} got {tag}{cache_note}: {reason}")
            if tag not in ("saasLikely", "nonSaas"):
                ok = False
        except Exception as exc:
            print(f"    ✗ FAILED: {exc}")
            ok = False
    if ok:
        print("\nSanity PASSED — safe to run --flagged-only")
    else:
        print("\nSanity FAILED — fix errors before bulk run")
    return ok


def run_flagged(api_key: str, progress: dict, cache: dict, limit: int | None) -> None:
    rate_limiter = RateLimiter(MAX_RPM)
    flagged_slugs = load_flagged_slugs()
    by_slug = {c["slug"]: c for c in load_companies()}
    done = set(progress.get("processedSlugs", []))
    pending = [by_slug[s] for s in flagged_slugs if s in by_slug and s not in done]
    if limit:
        pending = pending[:limit]

    print(f"Flagged phase: {len(pending)} pending ({len(flagged_slugs)} total flagged)")
    ensure_md_header(
        MD_SECTION,
        "_Auto-generated by `scripts/openrouter_classify_saas.py`. "
        "Only companies where OpenRouter disagrees with the current tag._",
    )

    for i, company in enumerate(pending, 1):
        if not check_quota(progress, MAX_DAILY_REQUESTS):
            break
        print(f"[{i}/{len(pending)}] {company['name']} ({company.get('saasTag')})...", flush=True)
        try:
            result, from_cache = call_single(api_key, company, cache, rate_limiter, progress)
            if from_cache:
                print("  (cached)", flush=True)
            else:
                bump_request(progress)
            process_result(
                progress, company, result["classification"], result.get("reasoning", ""),
                PROVIDER, TAG_FIELD,
            )
            progress["processedSlugs"].append(company["slug"])
        except Exception as exc:
            progress["stats"]["errors"] += 1
            print(f"  ! error: {exc}", flush=True)
            save_progress(PROGRESS_PATH, progress)
            if "429" in str(exc):
                print("  pausing 60s before continuing...", flush=True)
                time.sleep(60)
            else:
                time.sleep(30)
            continue
        save_progress(PROGRESS_PATH, progress)
        save_json(OUTPUT_JSON_PATH, export_results(progress, MODEL, PROVIDER))


def run_unflagged(
    api_key: str, progress: dict, cache: dict, batch_size: int, limit: int | None
) -> None:
    rate_limiter = RateLimiter(MAX_RPM)
    flagged_slugs = load_flagged_slugs()
    done = set(progress.get("processedSlugs", []))
    pending = [
        c for c in load_companies()
        if c["slug"] not in flagged_slugs and c["slug"] not in done
    ]
    if limit:
        pending = pending[: limit * batch_size]

    print(f"Unflagged phase: {len(pending)} companies, batch size {batch_size}")
    ensure_md_header(
        MD_SECTION,
        "_Auto-generated by `scripts/openrouter_classify_saas.py`. "
        "Only companies where OpenRouter disagrees with the current tag._",
    )

    i = 0
    while i < len(pending):
        if not check_quota(progress, MAX_DAILY_REQUESTS):
            break
        batch = pending[i : i + batch_size]
        names = ", ".join(c["name"] for c in batch)
        print(f"[{i+1}-{i+len(batch)}/{len(pending)}] {names}...", flush=True)
        try:
            results, from_cache = call_batch(api_key, batch, cache, rate_limiter, progress)
            if from_cache:
                print("  (cached)", flush=True)
            else:
                bump_request(progress)
            for company, result in zip(batch, results):
                process_result(
                    progress, company, result["classification"], result.get("reasoning", ""),
                    PROVIDER, TAG_FIELD,
                )
                progress["processedSlugs"].append(company["slug"])
            i += len(batch)
        except Exception as exc:
            progress["stats"]["errors"] += 1
            print(f"  ! batch error: {exc}", flush=True)
            save_progress(PROGRESS_PATH, progress)
            if "429" in str(exc):
                print("  pausing 60s before continuing...", flush=True)
                time.sleep(60)
            else:
                time.sleep(30)
            continue
        save_progress(PROGRESS_PATH, progress)
        save_json(OUTPUT_JSON_PATH, export_results(progress, MODEL, PROVIDER))


def main() -> None:
    load_dotenv(ENV_PATH)
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key or api_key == "your_api_key_here":
        print(f"Missing OPENROUTER_API_KEY in {ENV_PATH}", file=sys.stderr)
        print("Get a key at https://openrouter.ai/keys", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(description="OpenRouter SaaS classification audit")
    parser.add_argument("--sanity", action="store_true", help="Run 3-case API test")
    parser.add_argument("--flagged-only", action="store_true", help="Audit heuristic-flagged companies")
    parser.add_argument("--unflagged", action="store_true", help="Audit remaining companies in batches")
    parser.add_argument("--batch-size", type=int, default=4, help="Batch size for --unflagged")
    parser.add_argument("--limit", type=int, help="Max companies (or batches for unflagged)")
    args = parser.parse_args()

    cache = load_prompt_cache(CACHE_PATH, MODEL)
    cached = len(cache.get("entries", {}))
    if cached:
        print(f"Prompt cache: {cached} entries ({CACHE_PATH.name})")

    if args.sanity:
        ok = run_sanity(api_key, cache)
        if ok:
            progress = load_progress(PROGRESS_PATH)
            progress["sanityPassed"] = True
            progress["model"] = MODEL
            save_progress(PROGRESS_PATH, progress)
        sys.exit(0 if ok else 1)

    progress = load_progress(PROGRESS_PATH)
    if not progress.get("sanityPassed"):
        print("Run --sanity first to validate API setup.", file=sys.stderr)
        sys.exit(1)

    print(
        f"Model: {MODEL} | RPM limit: {MAX_RPM} | "
        f"API attempts today: {progress.get('requestsToday', 0)}/{MAX_DAILY_REQUESTS}"
    )

    if args.flagged_only:
        run_flagged(api_key, progress, cache, args.limit)
    elif args.unflagged:
        run_unflagged(api_key, progress, cache, args.batch_size, args.limit)
    else:
        parser.print_help()
        sys.exit(1)

    print(f"\nDone. Stats: {progress['stats']}")
    print(f"Mislabels: {OUTPUT_JSON_PATH}")


if __name__ == "__main__":
    main()
