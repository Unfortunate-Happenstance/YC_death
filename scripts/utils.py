"""Shared utilities."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

from scripts.config import NON_SAAS_INDUSTRIES, SAAS_LIKELY_INDUSTRIES

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
BATCHES_DIR = DATA_DIR / "batches"
ISSUES_DIR = DATA_DIR / "issues"


def batch_slug(batch: str) -> str:
    return batch.lower().replace(" ", "-")


def batch_from_slug(slug: str) -> str:
    parts = slug.split("-")
    return f"{parts[0].capitalize()} {parts[1]}"


def normalize_domain(website: str | None) -> str | None:
    if not website or not str(website).strip():
        return None
    raw = str(website).strip()
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    try:
        parsed = urlparse(raw)
    except Exception:
        return None
    host = (parsed.netloc or parsed.path or "").lower()
    host = host.removeprefix("www.")
    host = host.split(":")[0].rstrip("/")
    if not host or "." not in host:
        return None
    if not re.match(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", host):
        return None
    return host


def classify_saas(industries: list[str]) -> str:
    normalized = {i.strip() for i in industries if i}
    if normalized & SAAS_LIKELY_INDUSTRIES:
        return "saasLikely"
    if normalized & NON_SAAS_INDUSTRIES:
        return "nonSaas"
    return "unknown"


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
