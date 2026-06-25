#!/usr/bin/env python3
"""
Classify YC companies as SaaS vs non-SaaS using Gemini API.

Phases:
  1. --sanity          Test API + JSON parsing (3 calls, no progress saved)
  2. --flagged-only    Heuristic-flagged companies, 1 per request (~608)
  3. --unflagged       Rest of corpus, --batch-size 4 (~542 requests)

Note: Gemini does NOT dedupe quota for identical prompts. This script keeps a local
prompt cache (data/issues/gemini_saas_audit.cache.json) so repeated prompts skip the API.

Usage:
  uv run python scripts/gemini_classify_saas.py --sanity
  uv run python scripts/gemini_classify_saas.py --flagged-only
  uv run python scripts/gemini_classify_saas.py --unflagged --batch-size 4
"""

from __future__ import annotations

import argparse
import hashlib
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

from scripts.utils import DATA_DIR, ISSUES_DIR, save_json

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
PROGRESS_PATH = ISSUES_DIR / "gemini_saas_audit.progress.json"
CACHE_PATH = ISSUES_DIR / "gemini_saas_audit.cache.json"
FLAGGED_JSON_PATH = ISSUES_DIR / "mislabeled_saas.json"
GEMINI_JSON_PATH = ISSUES_DIR / "mislabeled_saas_gemini.json"
GEMINI_MD_PATH = ISSUES_DIR / "mislabeled_saas.md"

MODEL = "gemini-2.5-flash-lite"
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
# Free tier flash-lite: 15 RPM, 1,000 RPD (ai.google.dev/gemini-api/docs/rate-limits)
MAX_RPM = 13  # stay under 15 RPM with headroom
MAX_DAILY_REQUESTS = 950  # stay under 1,000 RPD with headroom
MAX_503_RETRIES = 2
MAX_429_RETRIES = 4

SYSTEM_PROMPT = """You classify Y Combinator startups for a dashboard that measures \
"death by Claude" vulnerability — whether a company's core product is software/SaaS \
that could plausibly be replaced by an AI assistant or a markdown skill file.

Classify the PRIMARY business, not side tools or internal ops software.

saasLikely — The company's main product IS software delivered digitally:
  - B2B SaaS, devtools, APIs, AI platforms, fintech software, vertical SaaS \
(selling software TO logistics/retail/healthcare counts as SaaS)
  - Consumer apps/subscriptions that are primarily software
  - AI wrappers, agents, copilots, automation platforms

nonSaas — The company's main product is NOT replaceable-by-a-markdown-file software:
  - Hardware, robotics, drones, semiconductors, manufacturing
  - Biotech, therapeutics, medical devices, clinical services
  - Physical goods, restaurants, game studios (making games, not game dev tools)
  - Pure marketplace/ops without software as the product (rare at YC)

Respond ONLY with valid JSON."""

SINGLE_SCHEMA = '{"classification": "saasLikely" | "nonSaas", "reasoning": "one sentence"}'
BATCH_SCHEMA = (
    '{"results": [{"slug": "company-slug", "classification": "saasLikely" | "nonSaas", '
    '"reasoning": "one sentence"}, ...]}'
)

# Sanity cases: slug → expected classification (spot-check only)
SANITY_SLUGS = {
    "trycardinal-ai": "saasLikely",   # B2B AI outbound platform
    "roforco": "nonSaas",             # Roblox game accelerator
    "stripe": None,                   # not YC — skip if missing
    "bby": "nonSaas",                 # Physical breast milk product
}


def load_companies() -> list[dict]:
    return [
        c
        for c in json.loads((DATA_DIR / "companies.json").read_text(encoding="utf-8"))
        if not c.get("excluded")
    ]


def load_flagged_slugs() -> set[str]:
    if not FLAGGED_JSON_PATH.exists():
        raise SystemExit(f"Missing {FLAGGED_JSON_PATH}. Run scripts/audit_saas_labels.py first.")
    data = json.loads(FLAGGED_JSON_PATH.read_text(encoding="utf-8"))
    slugs: set[str] = set()
    for item in data.get("flags", []):
        slugs.add(item["slug"])
    return slugs


def build_user_prompt(company: dict) -> str:
    inds = ", ".join(company.get("industries") or []) or "unknown"
    return (
        f"Company: {company.get('name')}\n"
        f"Slug: {company.get('slug')}\n"
        f"YC batch: {company.get('batch')}\n"
        f"YC industries: {inds}\n"
        f"One-liner: {company.get('oneLiner') or 'missing'}\n"
        f"Current automated tag: {company.get('saasTag')}"
    )


def build_batch_prompt(companies: list[dict]) -> str:
    blocks = []
    for i, c in enumerate(companies, 1):
        inds = ", ".join(c.get("industries") or []) or "unknown"
        blocks.append(
            f"--- Company {i} ---\n"
            f"Slug: {c.get('slug')}\n"
            f"Name: {c.get('name')}\n"
            f"Batch: {c.get('batch')}\n"
            f"Industries: {inds}\n"
            f"One-liner: {c.get('oneLiner') or 'missing'}\n"
            f"Current tag: {c.get('saasTag')}"
        )
    return (
        "Classify each company independently.\n\n"
        + "\n\n".join(blocks)
        + f"\n\nRespond with JSON matching: {BATCH_SCHEMA}"
    )


def prompt_cache_key(user_text: str, json_schema_hint: str) -> str:
    material = f"{MODEL}\n{SYSTEM_PROMPT}\n{json_schema_hint}\n{user_text}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def load_prompt_cache() -> dict:
    if not CACHE_PATH.exists():
        return {"model": MODEL, "entries": {}}
    data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    if data.get("model") != MODEL:
        return {"model": MODEL, "entries": {}}
    data.setdefault("entries", {})
    return data


def save_prompt_cache(cache: dict) -> None:
    save_json(CACHE_PATH, cache)


class RateLimiter:
    """Sliding 60s window — every HTTP attempt counts, including failed retries."""

    def __init__(self, max_rpm: int) -> None:
        self.max_rpm = max_rpm
        self.timestamps: list[float] = []

    def wait(self) -> None:
        now = time.time()
        self.timestamps = [t for t in self.timestamps if now - t < 60]
        if len(self.timestamps) >= self.max_rpm:
            sleep_for = 60 - (now - self.timestamps[0]) + 1.0
            if sleep_for > 0:
                print(f"    rpm throttle: waiting {sleep_for:.0f}s ({len(self.timestamps)}/{self.max_rpm} in last 60s)", flush=True)
                time.sleep(sleep_for)
                now = time.time()
                self.timestamps = [t for t in self.timestamps if now - t < 60]
        self.timestamps.append(time.time())


def parse_retry_seconds(resp: requests.Response) -> float:
    try:
        data = resp.json()
        for detail in data.get("error", {}).get("details", []):
            if "RetryInfo" in detail.get("@type", ""):
                delay = str(detail.get("retryDelay", "60s")).rstrip("s")
                return max(float(delay), 10.0)
    except Exception:
        pass
    return 60.0


def parse_retry_seconds_from_error(msg: str) -> float:
    match = re.search(r"Retry in (\d+(?:\.\d+)?)s", msg)
    if match:
        return max(float(match.group(1)), 10.0)
    return 60.0


def bump_api_attempt(progress: dict | None) -> None:
    if progress is None:
        return
    progress["requestsToday"] = progress.get("requestsToday", 0) + 1
    progress["stats"]["apiAttempts"] = progress["stats"].get("apiAttempts", 0) + 1


def call_gemini_raw(
    api_key: str,
    user_text: str,
    json_schema_hint: str,
    cache: dict,
    rate_limiter: RateLimiter,
    progress: dict | None = None,
) -> tuple[dict, bool]:
    key = prompt_cache_key(user_text, json_schema_hint)
    hit = cache["entries"].get(key)
    if hit is not None:
        if progress is not None:
            progress["stats"]["cacheHits"] = progress["stats"].get("cacheHits", 0) + 1
        return hit["response"], True

    payload = {
        "systemInstruction": {
            "parts": [{"text": SYSTEM_PROMPT + f"\n\nOutput schema: {json_schema_hint}"}]
        },
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
        },
    }
    last_err: Exception | None = None
    retries_429 = 0
    retries_503 = 0
    while True:
        rate_limiter.wait()
        bump_api_attempt(progress)
        resp = requests.post(
            API_URL,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=90,
        )
        if resp.status_code == 429:
            retries_429 += 1
            wait = parse_retry_seconds(resp)
            last_err = RuntimeError(f"Rate limited (429). Retry in {wait:.0f}s.")
            print(f"    429 retry {retries_429}/{MAX_429_RETRIES} — waiting {wait:.0f}s", flush=True)
            if retries_429 >= MAX_429_RETRIES:
                raise last_err
            time.sleep(wait)
            continue
        if resp.status_code in (503, 502, 500):
            retries_503 += 1
            wait = min(90, 20 * retries_503)
            last_err = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            print(f"    {resp.status_code} retry {retries_503}/{MAX_503_RETRIES} — waiting {wait}s", flush=True)
            if retries_503 >= MAX_503_RETRIES:
                raise last_err
            time.sleep(wait)
            continue
        if not resp.ok:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        result = json.loads(text)
        cache["entries"][key] = {
            "response": result,
            "cachedAt": datetime.now(timezone.utc).isoformat(),
        }
        save_prompt_cache(cache)
        return result, False


def call_gemini_single(
    api_key: str,
    company: dict,
    cache: dict,
    rate_limiter: RateLimiter,
    progress: dict | None = None,
) -> tuple[dict, bool]:
    result, from_cache = call_gemini_raw(
        api_key, build_user_prompt(company), SINGLE_SCHEMA, cache, rate_limiter, progress
    )
    if "classification" not in result:
        raise ValueError(f"Missing classification: {result}")
    return result, from_cache


def call_gemini_batch(
    api_key: str,
    companies: list[dict],
    cache: dict,
    rate_limiter: RateLimiter,
    progress: dict | None = None,
) -> tuple[list[dict], bool]:
    result, from_cache = call_gemini_raw(
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


def ensure_md_header() -> None:
    if not GEMINI_MD_PATH.exists():
        return
    if "## Gemini-confirmed mislabels" in GEMINI_MD_PATH.read_text(encoding="utf-8"):
        return
    with GEMINI_MD_PATH.open("a", encoding="utf-8") as f:
        f.write(
            "\n---\n\n## Gemini-confirmed mislabels\n\n"
            "_Auto-generated by `scripts/gemini_classify_saas.py`. "
            "Only companies where Gemini disagrees with the current tag._\n\n"
        )


def append_mislabel_md(entry: dict) -> None:
    line = (
        f"- **{entry['name']}** ({entry['batch']}) — "
        f"`{entry['currentTag']}` → `{entry['geminiTag']}` — "
        f"{entry['reasoning']} "
        f"[YC]({entry['ycUrl']})\n"
    )
    with GEMINI_MD_PATH.open("a", encoding="utf-8") as f:
        f.write(line)


def load_progress() -> dict:
    if PROGRESS_PATH.exists():
        return json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    return {
        "sanityPassed": False,
        "processedSlugs": [],
        "requestsToday": 0,
        "day": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "stats": {
            "agreed": 0,
            "mislabeled": 0,
            "errors": 0,
            "requests": 0,
            "apiAttempts": 0,
            "cacheHits": 0,
        },
        "mislabeled": [],
    }


def save_progress(progress: dict) -> None:
    save_json(PROGRESS_PATH, progress)


def record_mislabel(progress: dict, company: dict, gemini_tag: str, reasoning: str) -> None:
    entry = {
        "ycId": company.get("ycId"),
        "name": company.get("name"),
        "slug": company["slug"],
        "batch": company.get("batch"),
        "industries": company.get("industries"),
        "oneLiner": company.get("oneLiner"),
        "currentTag": company.get("saasTag"),
        "geminiTag": gemini_tag,
        "reasoning": reasoning,
        "ycUrl": f"https://www.ycombinator.com/companies/{company['slug']}",
        "auditedAt": datetime.now(timezone.utc).isoformat(),
    }
    progress["mislabeled"].append(entry)
    progress["stats"]["mislabeled"] += 1
    append_mislabel_md(entry)


def process_result(progress: dict, company: dict, gemini_tag: str, reasoning: str) -> None:
    if gemini_tag not in ("saasLikely", "nonSaas"):
        raise ValueError(f"Invalid classification: {gemini_tag!r}")
    current = company.get("saasTag")
    if gemini_tag != current:
        record_mislabel(progress, company, gemini_tag, reasoning)
        print(f"  ✗ MISLABELED → {gemini_tag}: {reasoning}", flush=True)
    else:
        progress["stats"]["agreed"] += 1
        print(f"  ✓ agrees ({gemini_tag})", flush=True)


def run_sanity(api_key: str, cache: dict) -> bool:
    rate_limiter = RateLimiter(MAX_RPM)
    by_slug = {c["slug"]: c for c in load_companies()}
    cases = [
        ("trycardinal-ai", "saasLikely"),
        ("roforco", "nonSaas"),
        ("bby", "nonSaas"),
    ]
    print(f"Sanity check — model {MODEL} (repeated prompts use local cache)\n")
    ok = True
    for slug, expected in cases:
        company = by_slug.get(slug)
        if not company:
            print(f"  SKIP {slug} — not in companies.json")
            continue
        print(f"  Testing {company['name']} (expect ~{expected})...", flush=True)
        from_cache = False
        try:
            result, from_cache = call_gemini_single(api_key, company, cache, rate_limiter)
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


def check_quota(progress: dict) -> bool:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if progress.get("day") != today:
        progress["requestsToday"] = 0
        progress["day"] = today
    attempts = progress.get("requestsToday", 0)
    if attempts >= MAX_DAILY_REQUESTS:
        print(f"Daily quota buffer reached ({attempts}/{MAX_DAILY_REQUESTS} API attempts). Resume tomorrow.")
        return False
    return True


def bump_request(progress: dict) -> None:
    """Count a successful classification (distinct from raw API attempts)."""
    progress["stats"]["requests"] += 1


def run_flagged(api_key: str, progress: dict, cache: dict, limit: int | None) -> None:
    rate_limiter = RateLimiter(MAX_RPM)
    flagged_slugs = load_flagged_slugs()
    by_slug = {c["slug"]: c for c in load_companies()}
    done = set(progress.get("processedSlugs", []))
    pending = [by_slug[s] for s in flagged_slugs if s in by_slug and s not in done]
    if limit:
        pending = pending[:limit]

    print(f"Flagged phase: {len(pending)} pending ({len(flagged_slugs)} total flagged)")
    ensure_md_header()

    for i, company in enumerate(pending, 1):
        if not check_quota(progress):
            break
        print(f"[{i}/{len(pending)}] {company['name']} ({company.get('saasTag')})...", flush=True)
        try:
            result, from_cache = call_gemini_single(
                api_key, company, cache, rate_limiter, progress
            )
            if from_cache:
                print("  (cached)", flush=True)
            else:
                bump_request(progress)
            process_result(progress, company, result["classification"], result.get("reasoning", ""))
            progress["processedSlugs"].append(company["slug"])
        except Exception as exc:
            progress["stats"]["errors"] += 1
            print(f"  ! error: {exc}", flush=True)
            save_progress(progress)
            if "429" in str(exc):
                wait = max(parse_retry_seconds_from_error(str(exc)), 60.0)
                print(f"  pausing {wait:.0f}s before continuing...", flush=True)
                time.sleep(wait)
            else:
                time.sleep(30)
            continue
        save_progress(progress)
        save_json(GEMINI_JSON_PATH, _gemini_export(progress))


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
    ensure_md_header()

    i = 0
    while i < len(pending):
        if not check_quota(progress):
            break
        batch = pending[i : i + batch_size]
        names = ", ".join(c["name"] for c in batch)
        print(f"[{i+1}-{i+len(batch)}/{len(pending)}] {names}...", flush=True)
        try:
            results, from_cache = call_gemini_batch(
                api_key, batch, cache, rate_limiter, progress
            )
            if from_cache:
                print("  (cached)", flush=True)
            else:
                bump_request(progress)
            for company, result in zip(batch, results):
                process_result(
                    progress, company, result["classification"], result.get("reasoning", "")
                )
                progress["processedSlugs"].append(company["slug"])
            i += len(batch)
        except Exception as exc:
            progress["stats"]["errors"] += 1
            print(f"  ! batch error: {exc}", flush=True)
            save_progress(progress)
            if "429" in str(exc):
                wait = max(parse_retry_seconds_from_error(str(exc)), 60.0)
                print(f"  pausing {wait:.0f}s before continuing...", flush=True)
                time.sleep(wait)
            else:
                time.sleep(30)
            continue
        save_progress(progress)
        save_json(GEMINI_JSON_PATH, _gemini_export(progress))


def _gemini_export(progress: dict) -> dict:
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "model": MODEL,
        "summary": progress["stats"],
        "mislabeled": progress["mislabeled"],
    }


def main() -> None:
    load_dotenv(ENV_PATH)
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key or api_key == "your_api_key_here":
        print(f"Missing GEMINI_API_KEY in {ENV_PATH}", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Gemini SaaS classification audit")
    parser.add_argument("--sanity", action="store_true", help="Run 3-case API test (no progress)")
    parser.add_argument("--flagged-only", action="store_true", help="Audit heuristic-flagged companies")
    parser.add_argument("--unflagged", action="store_true", help="Audit remaining companies in batches")
    parser.add_argument("--batch-size", type=int, default=4, help="Batch size for --unflagged")
    parser.add_argument("--limit", type=int, help="Max companies (or batches for unflagged)")
    args = parser.parse_args()

    cache = load_prompt_cache()
    cached = len(cache.get("entries", {}))
    if cached:
        print(f"Prompt cache: {cached} entries ({CACHE_PATH.name})")

    if args.sanity:
        ok = run_sanity(api_key, cache)
        if ok:
            progress = load_progress()
            progress["sanityPassed"] = True
            save_progress(progress)
        sys.exit(0 if ok else 1)

    progress = load_progress()
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
    print(f"Mislabels: {GEMINI_MD_PATH}")


if __name__ == "__main__":
    main()
