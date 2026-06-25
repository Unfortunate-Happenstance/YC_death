"""Apply manual domain overrides and exclusions from data/manual_overrides.json."""

from __future__ import annotations

from scripts.utils import DATA_DIR, ISSUES_DIR, load_json, normalize_domain, save_json


def load_overrides() -> dict:
    path = DATA_DIR / "manual_overrides.json"
    return load_json(path, {"domainOverrides": {}, "excluded": {}})


def apply_overrides(companies: list[dict]) -> list[dict]:
    overrides = load_overrides()
    domain_overrides = overrides.get("domainOverrides", {})
    excluded = overrides.get("excluded", {})

    for company in companies:
        slug = company.get("slug", "")
        if slug in excluded:
            company["excluded"] = True
            company["excludeReason"] = excluded[slug].get("reason", "excluded")
            company["excludeNote"] = excluded[slug].get("note", "")
            continue

        company["excluded"] = False
        if slug in domain_overrides:
            entry = domain_overrides[slug]
            company["website"] = entry["website"]
            company["domain"] = normalize_domain(entry["website"])
            company["websiteSource"] = "manual_override"

    return companies


def active_companies(companies: list[dict]) -> list[dict]:
    return [c for c in companies if not c.get("excluded")]


def export_excluded(companies: list[dict]) -> None:
    excluded = [
        {
            "name": c["name"],
            "slug": c["slug"],
            "batch": c["batch"],
            "reason": c.get("excludeReason"),
            "note": c.get("excludeNote"),
        }
        for c in companies
        if c.get("excluded")
    ]
    save_json(ISSUES_DIR / "excluded.json", excluded)


def export_resolved_overrides(companies: list[dict]) -> None:
    resolved = [
        {
            "name": c["name"],
            "slug": c["slug"],
            "batch": c["batch"],
            "website": c.get("website"),
            "domain": c.get("domain"),
        }
        for c in companies
        if c.get("websiteSource") == "manual_override"
    ]
    save_json(ISSUES_DIR / "resolved_overrides.json", resolved)
