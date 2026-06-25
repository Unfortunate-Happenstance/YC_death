#!/usr/bin/env python3
"""Scrape deathbyclawd.com per YC batch. Resumable."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.config import (
    DEATHBYCLAWD_URL,
    MVP_BATCHES,
    OLDER_BATCHES,
    PRIORITY_BATCHES,
    SCRAPE_DELAY_SECONDS,
    SCRAPE_MAX_RETRIES,
    SCRAPE_TIMEOUT_SECONDS,
)
from scripts.merge_data import merge_all
from scripts.overrides import active_companies
from scripts.utils import BATCHES_DIR, DATA_DIR, ISSUES_DIR, batch_slug, load_json, save_json


def is_garbage_result(result: dict) -> bool:
    rating = result.get("deathRating", "")
    score = result.get("deathScore", 0)
    return rating == "ALREADY DEAD" and score >= 99


def fetch_death_score(domain: str) -> dict:
    last_error = None
    for attempt in range(1, SCRAPE_MAX_RETRIES + 1):
        try:
            resp = requests.post(
                DEATHBYCLAWD_URL,
                json={"url": domain},
                timeout=SCRAPE_TIMEOUT_SECONDS,
            )
            if resp.status_code == 400:
                body = resp.json()
                raise ValueError(body.get("error", "Bad request"))
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            last_error = exc
            if attempt < SCRAPE_MAX_RETRIES:
                wait = attempt * 5
                print(f"    retry {attempt}/{SCRAPE_MAX_RETRIES} in {wait}s: {exc}", flush=True)
                time.sleep(wait)
    raise RuntimeError(str(last_error))


def scrape_batch(batch: str, force: bool = False) -> None:
    companies = load_json(DATA_DIR / "companies.json", [])
    if not companies:
        raise SystemExit("Run fetch_yc.py first.")

    slug = batch_slug(batch)
    out_path = BATCHES_DIR / f"{slug}.json"
    progress_path = BATCHES_DIR / f"{slug}.progress.json"

    batch_companies = [c for c in companies if c.get("batch") == batch and not c.get("excluded")]
    if not batch_companies:
        print(f"No companies for batch {batch}")
        return

    existing = load_json(out_path, {"companies": []})
    done_domains = {c["domain"] for c in existing.get("companies", []) if c.get("domain")}
    progress = load_json(progress_path, {"completed": [], "failed": [], "skipped": []})

    print(f"\n=== {batch} ({len(batch_companies)} companies, {len(done_domains)} done) ===", flush=True)

    results = list(existing.get("companies", []))
    api_failed = load_json(ISSUES_DIR / "api_failed.json", [])
    api_garbage = load_json(ISSUES_DIR / "api_garbage.json", [])

    for i, company in enumerate(batch_companies, 1):
        domain = company.get("domain")
        name = company.get("name")

        if company.get("excluded"):
            continue

        if not domain:
            entry = {**company, "scrapeStatus": "skipped", "scrapeReason": "no_domain"}
            if entry not in progress["skipped"]:
                progress["skipped"].append({"name": name, "reason": "no_domain"})
            continue

        if domain in done_domains and not force:
            print(f"  [{i}/{len(batch_companies)}] skip (done) {name} ({domain})", flush=True)
            continue

        print(f"  [{i}/{len(batch_companies)}] {name} ({domain})...", flush=True)
        try:
            death = fetch_death_score(domain)
            entry = {
                **company,
                "scrapeStatus": "ok",
                "scrapedAt": datetime.now(timezone.utc).isoformat(),
                "death": death,
            }
            if is_garbage_result(death):
                api_garbage.append(
                    {
                        "name": name,
                        "batch": batch,
                        "domain": domain,
                        "deathScore": death.get("deathScore"),
                        "deathRating": death.get("deathRating"),
                        "oneLiner": death.get("oneLiner"),
                    }
                )
            results.append(entry)
            done_domains.add(domain)
            progress["completed"].append(domain)
            print(f"    → {death.get('deathScore')} {death.get('deathRating')}", flush=True)
        except Exception as exc:
            print(f"    ✗ failed: {exc}", flush=True)
            progress["failed"].append({"domain": domain, "name": name, "error": str(exc)})
            api_failed.append(
                {"name": name, "batch": batch, "domain": domain, "error": str(exc)}
            )

        save_json(out_path, {
            "batch": batch,
            "slug": slug,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "companies": results,
        })
        save_json(progress_path, progress)
        save_json(ISSUES_DIR / "api_failed.json", api_failed)
        save_json(ISSUES_DIR / "api_garbage.json", api_garbage)
        merge_all()
        time.sleep(SCRAPE_DELAY_SECONDS)

    save_json(out_path, {
        "batch": batch,
        "slug": slug,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "complete": True,
        "companies": results,
    })
    merge_all()
    print(f"Batch {batch} complete. Merged dashboard.json.", flush=True)


def scrape_batches(batches: list[str], force: bool = False) -> None:
    for batch in batches:
        scrape_batch(batch, force=force)


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape deathbyclawd per batch")
    parser.add_argument("--batch", help="Single batch name, e.g. 'Fall 2026'")
    parser.add_argument("--all", action="store_true", help="Scrape all MVP batches")
    parser.add_argument("--priority", action="store_true", help="Scrape latest → 2025 batches")
    parser.add_argument("--older", action="store_true", help="Scrape 2022–2024 batches")
    parser.add_argument("--remaining", action="store_true", help="Scrape batches not yet complete")
    parser.add_argument("--force", action="store_true", help="Re-scrape even if done")
    args = parser.parse_args()

    if args.batch:
        scrape_batch(args.batch, force=args.force)
    elif args.priority:
        scrape_batches(PRIORITY_BATCHES, force=args.force)
    elif args.older:
        scrape_batches(OLDER_BATCHES, force=args.force)
    elif args.all:
        scrape_batches(MVP_BATCHES, force=args.force)
    elif args.remaining:
        manifest = load_json(DATA_DIR / "manifest.json", {"batches": {}})
        for batch in MVP_BATCHES:
            slug = batch_slug(batch)
            info = manifest.get("batches", {}).get(slug, {})
            if not info.get("complete"):
                scrape_batch(batch, force=args.force)
    else:
        parser.print_help()
        raise SystemExit(1)


if __name__ == "__main__":
    main()
