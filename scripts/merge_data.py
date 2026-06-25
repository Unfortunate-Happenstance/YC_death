#!/usr/bin/env python3
"""Merge per-batch scrape results into dashboard.json + manifest."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.config import MVP_BATCHES
from scripts.overrides import active_companies
from scripts.utils import BATCHES_DIR, DATA_DIR, batch_slug, load_json, save_json

METRICS = {
    "deathScore": "Death Score",
    "crudScore": "It's Just CRUD",
    "aiWrapperScore": "Secret AI Wrapper",
    "moatDepth": "Moat Depth",
    "markdownReplaceable": "Markdown Replaceable",
    "pricingAudacity": "Pricing Audacity",
}


def batch_sort_key(batch: str) -> tuple:
    season_order = {"Winter": 0, "Spring": 1, "Summer": 2, "Fall": 3}
    parts = batch.split()
    year = int(parts[1])
    season = season_order.get(parts[0], 9)
    return (year, season)


def extract_metrics(death: dict) -> dict:
    m = death.get("metrics") or {}
    return {
        "deathScore": death.get("deathScore"),
        "crudScore": m.get("crudScore"),
        "aiWrapperScore": m.get("aiWrapperScore"),
        "moatDepth": m.get("moatDepth"),
        "markdownReplaceable": m.get("markdownReplaceable"),
        "pricingAudacity": m.get("pricingAudacity"),
    }


def merge_all() -> dict:
    all_companies = load_json(DATA_DIR / "companies.json", [])
    active = active_companies(all_companies)
    manifest_batches = {}
    merged: list[dict] = []

    for batch in MVP_BATCHES:
        slug = batch_slug(batch)
        batch_file = BATCHES_DIR / f"{slug}.json"
        batch_data = load_json(batch_file, {})
        batch_companies = batch_data.get("companies", [])
        expected = sum(1 for c in active if c.get("batch") == batch)
        scraped_ok = sum(1 for c in batch_companies if c.get("scrapeStatus") == "ok")
        complete = batch_data.get("complete", False) and scraped_ok >= expected - _skipped(batch_companies)

        manifest_batches[slug] = {
            "batch": batch,
            "expected": expected,
            "scraped": scraped_ok,
            "complete": complete,
            "updatedAt": batch_data.get("updatedAt"),
        }
        merged.extend(batch_companies)

    merged.sort(key=lambda c: (batch_sort_key(c.get("batch", "")), c.get("name", "")))

    chart = build_chart_data(merged)

    dashboard = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "batches": MVP_BATCHES,
        "metrics": METRICS,
        "manifest": {"batches": manifest_batches},
        "summary": {
            "totalCompanies": len(active),
            "scrapedCompanies": sum(1 for c in merged if c.get("scrapeStatus") == "ok"),
            "excludedCompanies": len(all_companies) - len(active),
            "batchesComplete": sum(1 for b in manifest_batches.values() if b["complete"]),
            "batchesTotal": len(MVP_BATCHES),
        },
        "chart": chart,
        "companies": merged,
    }

    save_json(DATA_DIR / "manifest.json", {"batches": manifest_batches, "generatedAt": dashboard["generatedAt"]})
    save_json(DATA_DIR / "dashboard.json", dashboard)
    return dashboard


def _skipped(batch_companies: list[dict]) -> int:
    return sum(1 for c in batch_companies if c.get("scrapeStatus") == "skipped")


def build_chart_data(companies: list[dict]) -> dict:
    by_batch: dict[str, list[dict]] = {}
    for c in companies:
        if c.get("scrapeStatus") != "ok":
            continue
        by_batch.setdefault(c["batch"], []).append(c)

    batches_sorted = sorted(by_batch.keys(), key=batch_sort_key)
    series = {key: [] for key in METRICS}

    for batch in batches_sorted:
        batch_cos = by_batch[batch]
        for metric_key in METRICS:
            values = []
            for c in batch_cos:
                death = c.get("death") or {}
                if metric_key == "deathScore":
                    v = death.get("deathScore")
                else:
                    v = (death.get("metrics") or {}).get(metric_key)
                if v is not None:
                    values.append(v)
            avg = round(sum(values) / len(values), 1) if values else None
            series[metric_key].append(avg)

    return {
        "labels": batches_sorted,
        "series": series,
    }


if __name__ == "__main__":
    d = merge_all()
    print(f"Merged {d['summary']['scrapedCompanies']} companies, "
          f"{d['summary']['batchesComplete']}/{d['summary']['batchesTotal']} batches complete")
