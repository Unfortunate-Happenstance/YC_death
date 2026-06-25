"""Shared helpers for SaaS classification audits (Gemini, OpenRouter, etc.)."""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from scripts.utils import DATA_DIR, ISSUES_DIR, save_json

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

FLAGGED_JSON_PATH = ISSUES_DIR / "mislabeled_saas.json"
MISLABEL_MD_PATH = ISSUES_DIR / "mislabeled_saas.md"

SANITY_CASES = [
    ("trycardinal-ai", "saasLikely"),
    ("roforco", "nonSaas"),
    ("bby", "nonSaas"),
]


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
    return {item["slug"] for item in data.get("flags", [])}


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


def prompt_cache_key(model: str, user_text: str, json_schema_hint: str) -> str:
    material = f"{model}\n{SYSTEM_PROMPT}\n{json_schema_hint}\n{user_text}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def load_prompt_cache(cache_path: Path, model: str) -> dict:
    if not cache_path.exists():
        return {"model": model, "entries": {}}
    data = json.loads(cache_path.read_text(encoding="utf-8"))
    if data.get("model") != model:
        return {"model": model, "entries": {}}
    data.setdefault("entries", {})
    return data


def save_prompt_cache(cache_path: Path, cache: dict) -> None:
    save_json(cache_path, cache)


def extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```\s*$", "", text)
    return json.loads(text)


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
                print(
                    f"    rpm throttle: waiting {sleep_for:.0f}s "
                    f"({len(self.timestamps)}/{self.max_rpm} in last 60s)",
                    flush=True,
                )
                time.sleep(sleep_for)
                now = time.time()
                self.timestamps = [t for t in self.timestamps if now - t < 60]
        self.timestamps.append(time.time())


def load_progress(progress_path: Path) -> dict:
    if progress_path.exists():
        return json.loads(progress_path.read_text(encoding="utf-8"))
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


def save_progress(progress_path: Path, progress: dict) -> None:
    save_json(progress_path, progress)


def bump_api_attempt(progress: dict | None) -> None:
    if progress is None:
        return
    progress["requestsToday"] = progress.get("requestsToday", 0) + 1
    progress["stats"]["apiAttempts"] = progress["stats"].get("apiAttempts", 0) + 1


def bump_request(progress: dict) -> None:
    progress["stats"]["requests"] += 1


def check_quota(progress: dict, max_daily: int) -> bool:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if progress.get("day") != today:
        progress["requestsToday"] = 0
        progress["day"] = today
    attempts = progress.get("requestsToday", 0)
    if attempts >= max_daily:
        print(f"Daily quota buffer reached ({attempts}/{max_daily} API attempts). Resume tomorrow.")
        return False
    return True


def ensure_md_header(section_title: str, blurb: str) -> None:
    if not MISLABEL_MD_PATH.exists():
        return
    if section_title in MISLABEL_MD_PATH.read_text(encoding="utf-8"):
        return
    with MISLABEL_MD_PATH.open("a", encoding="utf-8") as f:
        f.write(f"\n---\n\n## {section_title}\n\n{blurb}\n\n")


def append_mislabel_md(entry: dict, tag_field: str = "auditTag") -> None:
    line = (
        f"- **{entry['name']}** ({entry['batch']}) — "
        f"`{entry['currentTag']}` → `{entry[tag_field]}` — "
        f"{entry['reasoning']} "
        f"[YC]({entry['ycUrl']})\n"
    )
    with MISLABEL_MD_PATH.open("a", encoding="utf-8") as f:
        f.write(line)


def record_mislabel(
    progress: dict,
    company: dict,
    audit_tag: str,
    reasoning: str,
    provider: str,
    tag_field: str = "auditTag",
) -> None:
    entry = {
        "ycId": company.get("ycId"),
        "name": company.get("name"),
        "slug": company["slug"],
        "batch": company.get("batch"),
        "industries": company.get("industries"),
        "oneLiner": company.get("oneLiner"),
        "currentTag": company.get("saasTag"),
        tag_field: audit_tag,
        "provider": provider,
        "reasoning": reasoning,
        "ycUrl": f"https://www.ycombinator.com/companies/{company['slug']}",
        "auditedAt": datetime.now(timezone.utc).isoformat(),
    }
    progress["mislabeled"].append(entry)
    progress["stats"]["mislabeled"] += 1
    append_mislabel_md(entry, tag_field=tag_field)


def process_result(
    progress: dict,
    company: dict,
    audit_tag: str,
    reasoning: str,
    provider: str,
    tag_field: str = "auditTag",
) -> None:
    if audit_tag not in ("saasLikely", "nonSaas"):
        raise ValueError(f"Invalid classification: {audit_tag!r}")
    current = company.get("saasTag")
    if audit_tag != current:
        record_mislabel(progress, company, audit_tag, reasoning, provider, tag_field)
        print(f"  ✗ MISLABELED → {audit_tag}: {reasoning}", flush=True)
    else:
        progress["stats"]["agreed"] += 1
        print(f"  ✓ agrees ({audit_tag})", flush=True)


def export_results(progress: dict, model: str, provider: str) -> dict:
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "provider": provider,
        "model": model,
        "summary": progress["stats"],
        "mislabeled": progress["mislabeled"],
    }
