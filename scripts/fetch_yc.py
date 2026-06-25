#!/usr/bin/env python3
"""Fetch YC companies from Algolia and export issues for manual review."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.config import ALGOLIA_API_KEY, ALGOLIA_APP_ID, ALGOLIA_INDEX, MVP_BATCHES
from scripts.overrides import apply_overrides, export_excluded, export_resolved_overrides, active_companies
from scripts.utils import (
    DATA_DIR,
    ISSUES_DIR,
    batch_slug,
    classify_saas,
    normalize_domain,
    save_json,
)


def fetch_batch(batch: str) -> list[dict]:
    url = f"https://{ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/{ALGOLIA_INDEX}/query"
    headers = {
        "X-Algolia-Application-Id": ALGOLIA_APP_ID,
        "X-Algolia-API-Key": ALGOLIA_API_KEY,
        "Content-Type": "application/json",
    }
    facet = f'[["batch:{batch}"]]'
    params = f"query=&hitsPerPage=1000&facetFilters={facet}"
    all_hits: list[dict] = []
    page = 0
    while True:
        page_params = f"{params}&page={page}"
        resp = requests.post(url, headers=headers, json={"params": page_params}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        hits = data.get("hits", [])
        all_hits.extend(hits)
        if page >= data.get("nbPages", 1) - 1:
            break
        page += 1
        time.sleep(0.2)
    return all_hits


def build_company_record(hit: dict) -> dict:
    industries = hit.get("industries") or []
    if hit.get("industry") and hit["industry"] not in industries:
        industries = [hit["industry"], *industries]
    domain = normalize_domain(hit.get("website"))
    return {
        "ycId": hit.get("id"),
        "name": hit.get("name"),
        "slug": hit.get("slug"),
        "batch": hit.get("batch"),
        "website": hit.get("website"),
        "domain": domain,
        "oneLiner": hit.get("one_liner"),
        "industries": industries,
        "saasTag": classify_saas(industries),
        "status": hit.get("status"),
    }


def export_issues(companies: list[dict]) -> dict:
    issues = {
        "no_website": [],
        "invalid_domain": [],
    }
    for c in companies:
        if c.get("excluded"):
            continue
        base = {
            "ycId": c["ycId"],
            "name": c["name"],
            "batch": c["batch"],
            "website": c.get("website"),
            "slug": c.get("slug"),
        }
        if not c.get("website"):
            issues["no_website"].append(base)
        elif not c.get("domain"):
            issues["invalid_domain"].append(base)
    return issues


def issues_markdown(issues: dict) -> str:
    lines = ["# YC Death Watch — Issues for Manual Review\n"]
    for category, items in issues.items():
        if category == "summary":
            continue
        title = category.replace("_", " ").title()
        lines.append(f"## {title} ({len(items)})\n")
        if not items:
            lines.append("_None_\n")
            continue
        for item in items:
            lines.append(
                f"- **{item['name']}** ({item['batch']}) — "
                f"website: `{item.get('website') or 'missing'}` — "
                f"[YC](https://www.ycombinator.com/companies/{item['slug']})\n"
            )
        lines.append("")
    summary = issues.get("summary", {})
    if summary:
        lines.append("## Summary\n")
        for k, v in summary.items():
            lines.append(f"- **{k}**: {v}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch YC companies")
    parser.add_argument("--batches", nargs="*", default=MVP_BATCHES)
    args = parser.parse_args()

    all_companies: list[dict] = []
    batch_counts: dict[str, int] = {}

    for batch in args.batches:
        print(f"Fetching {batch}...", flush=True)
        hits = fetch_batch(batch)
        records = [build_company_record(h) for h in hits]
        all_companies.extend(records)
        batch_counts[batch] = len(records)
        print(f"  → {len(records)} companies", flush=True)

    all_companies = apply_overrides(all_companies)
    export_excluded(all_companies)
    export_resolved_overrides(all_companies)

    active = active_companies(all_companies)
    issues = export_issues(active)
    issues["summary"] = {
        "totalCompanies": len(all_companies),
        "activeCompanies": len(active),
        "excluded": len(all_companies) - len(active),
        "noWebsite": len(issues["no_website"]),
        "invalidDomain": len(issues["invalid_domain"]),
        "scrapeable": len(active) - len(issues["no_website"]) - len(issues["invalid_domain"]),
    }

    save_json(DATA_DIR / "companies.json", all_companies)
    save_json(DATA_DIR / "batch_counts.json", batch_counts)
    save_json(ISSUES_DIR / "issues.json", issues)
    (ISSUES_DIR / "issues.md").write_text(issues_markdown(issues), encoding="utf-8")

    print(f"\nSaved {len(all_companies)} companies ({len(active)} active) to data/companies.json")
    print(f"Issues: {issues['summary']}")


if __name__ == "__main__":
    main()
