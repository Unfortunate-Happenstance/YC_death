#!/usr/bin/env python3
"""Flag likely mislabeled SaaS / non-SaaS tags for manual review."""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import DATA_DIR, ISSUES_DIR, save_json

EXPLICIT_SAAS = {
    "SaaS",
    "B2B SaaS",
    "Developer Tools",
    "Infrastructure",
    "Security",
    "Analytics",
    "Productivity",
    "Marketing",
    "Sales",
    "Recruiting",
    "Legal",
}

HARD_NON_SAAS = {
    "Healthcare",
    "Healthcare IT",
    "Healthcare Services",
    "Biotech",
    "Medical Devices",
    "Drug Discovery and Delivery",
    "Therapeutics",
    "Diagnostics",
    "Hardware",
    "Industrials",
    "Manufacturing and Robotics",
    "Robotics",
    "Consumer",
    "Consumer Health and Wellness",
    "Consumer Electronics",
    "Energy",
    "Climate",
    "Agriculture",
    "Drones",
    "Aviation and Space",
    "Automotive",
    "Construction",
    "Industrial Bio",
    "Food and Beverage",
    "Apparel and Cosmetics",
    "Home and Personal",
}

B2B_PHYSICAL_SECONDARY = {
    "Manufacturing and Robotics",
    "Aviation and Space",
    "Automotive",
    "Construction",
    "Defense",
    "Supply Chain and Logistics",
    "Industrial Bio",
    "Medical Devices",
    "Drug Discovery and Delivery",
    "Therapeutics",
    "Diagnostics",
    "Housing and Real Estate",
    "Retail",
}

NON_SAAS_KEYWORDS = re.compile(
    r"\b("
    r"hardware|robot|robotics|drone|uav|autonomous vehicle|self-driving|"
    r"semiconductor|chip|fab|battery|solar panel|wind turbine|"
    r"therapeutic|clinical trial|drug discovery|biotech|pharma|genomic|"
    r"medical device|implant|diagnostic|hospital|patient care|"
    r"construction|building material|manufacturing|factory|"
    r"restaurant chain|food delivery kitchen|grocery store|"
    r"real estate broker|property management company|"
    r"game studio|video game|roblox game|"
    r"defense contractor|military hardware|satellite launch|rocket|spacecraft"
    r")\b",
    re.I,
)

SAAS_KEYWORDS = re.compile(
    r"\b("
    r"\bsaas\b|software platform|cloud platform|api platform|developer platform|"
    r"workflow automation|no-code|low-code|crm|erp|billing platform|"
    r"analytics platform|data platform|observability|monitoring platform|"
    r"copilot for|ai agent for|automation for|platform for teams|"
    r"dashboard for|management software|scheduling software|"
    r"infrastructure for developers|devtools|sdk|"
    r"subscription|per seat|b2b software|enterprise software"
    r")\b",
    re.I,
)


def flag_company(c: dict) -> dict | None:
    inds = set(c.get("industries") or [])
    tag = c.get("saasTag")
    one = c.get("oneLiner") or ""
    reasons: list[str] = []
    suggested: str | None = None
    confidence = "medium"
    priority = "review"

    has_b2b = "B2B" in inds
    has_explicit_saas = bool(inds & EXPLICIT_SAAS)
    has_hard_non = bool(inds & HARD_NON_SAAS)
    secondary = inds - {
        "B2B",
        "Fintech",
        "Finance",
        "Operations",
        "Engineering, Product and Design",
    }
    physical_secondary = bool(secondary & B2B_PHYSICAL_SECONDARY)
    non_kw = bool(NON_SAAS_KEYWORDS.search(one))
    saas_kw = bool(SAAS_KEYWORDS.search(one))

    if tag == "saasLikely":
        if has_hard_non and has_b2b:
            reasons.append(
                f"Has hard non-SaaS industry but B2B wins: {sorted(inds & HARD_NON_SAAS)}"
            )
            suggested = "nonSaas"
            confidence = "high"
            priority = "mislabeled"

        if has_b2b and physical_secondary and not has_explicit_saas:
            reasons.append(
                f"B2B + operational/physical secondary: {sorted(secondary & B2B_PHYSICAL_SECONDARY)}"
            )
            suggested = "nonSaas"
            confidence = "medium"
            priority = "mislabeled"

        if non_kw and not saas_kw and (physical_secondary or has_hard_non or not has_explicit_saas):
            reasons.append(f"One-liner suggests non-software: \"{one[:140]}\"")
            suggested = "nonSaas"
            confidence = "high" if has_hard_non else "medium"
            priority = "mislabeled"

        if has_b2b and not has_explicit_saas and len(inds) == 1:
            reasons.append("B2B-only tag — ambiguous, may or may not be SaaS")
            suggested = "review"
            confidence = "low"
            priority = "ambiguous"

    elif tag == "nonSaas":
        if bool(inds & EXPLICIT_SAAS):
            reasons.append(f"Explicit software industry present: {sorted(inds & EXPLICIT_SAAS)}")
            suggested = "saasLikely"
            confidence = "high"
            priority = "mislabeled"

        if saas_kw and not non_kw:
            reasons.append(f"One-liner reads as software: \"{one[:140]}\"")
            suggested = "saasLikely"
            confidence = "medium"
            priority = "mislabeled"

        if "Gaming" in inds and re.search(
            r"platform|sdk|engine|tools for (game|developer)", one, re.I
        ):
            reasons.append("Gaming + platform/tools language in one-liner")
            suggested = "saasLikely"
            confidence = "medium"
            priority = "mislabeled"

    if not reasons:
        return None

    return {
        "ycId": c.get("ycId"),
        "name": c.get("name"),
        "slug": c.get("slug"),
        "batch": c.get("batch"),
        "industries": c.get("industries"),
        "oneLiner": one,
        "currentTag": tag,
        "suggestedTag": suggested or "review",
        "confidence": confidence,
        "priority": priority,
        "reasons": reasons,
        "ycUrl": f"https://www.ycombinator.com/companies/{c.get('slug')}",
    }


def to_markdown(data: dict) -> str:
    lines = [
        "# SaaS label audit — for manual review\n",
        f"Generated: {data['generatedAt']}\n",
        "## Summary\n",
        f"- Active companies: **{data['summary']['totalActiveCompanies']}**",
        f"- Flagged total: **{data['summary']['flagged']}**",
        f"- Likely mislabeled (`priority: mislabeled`): **{data['summary']['likelyMislabeled']}**",
        f"- Ambiguous B2B-only (`priority: ambiguous`): **{data['summary']['ambiguous']}**\n",
    ]
    for section, title in [
        ("likelyMislabeled", "Likely mislabeled"),
        ("ambiguous", "Ambiguous (B2B-only)"),
    ]:
        items = data[section]
        lines.append(f"## {title} ({len(items)})\n")
        if not items:
            lines.append("_None_\n")
            continue
        for item in items:
            lines.append(
                f"- **{item['name']}** ({item['batch']}) — "
                f"`{item['currentTag']}` → `{item['suggestedTag']}` "
                f"[{item['confidence']}] — {item['reasons'][0]} "
                f"[YC]({item['ycUrl']})\n"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    companies = [
        c
        for c in json.load((DATA_DIR / "companies.json").open(encoding="utf-8"))
        if not c.get("excluded")
    ]
    flags = [f for c in companies if (f := flag_company(c))]

    conf_order = {"high": 0, "medium": 1, "low": 2}
    flags.sort(key=lambda x: (x["priority"] == "ambiguous", conf_order.get(x["confidence"], 9), x["batch"], x["name"]))

    mislabeled = [f for f in flags if f["priority"] == "mislabeled"]
    ambiguous = [f for f in flags if f["priority"] == "ambiguous"]

    out = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "totalActiveCompanies": len(companies),
            "flagged": len(flags),
            "likelyMislabeled": len(mislabeled),
            "ambiguous": len(ambiguous),
            "bySuggestedTag": dict(Counter(f["suggestedTag"] for f in mislabeled)),
            "byConfidence": dict(Counter(f["confidence"] for f in mislabeled)),
        },
        "likelyMislabeled": mislabeled,
        "ambiguous": ambiguous,
        "flags": flags,
    }

    save_json(ISSUES_DIR / "mislabeled_saas.json", out)
    (ISSUES_DIR / "mislabeled_saas.md").write_text(to_markdown(out), encoding="utf-8")
    print(json.dumps(out["summary"], indent=2))


if __name__ == "__main__":
    main()
