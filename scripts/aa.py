"""Shared helpers: paths, config, Artificial Analysis API access, record parsing.

Standard library only. Field paths below are unverified until the step-0 spike;
correct them here and nowhere else.
"""
from __future__ import annotations

import hashlib
import json
import math
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
SITE_DATA_DIR = ROOT / "site" / "data"

FREE_URL = "https://artificialanalysis.ai/api/v2/language/models/free"
PAGE_SIZE = 200
RETRY_DELAYS = (2, 8, 32)

# Dotted paths into one API record. Verify in step 0.
FIELDS = {
    "intelligence": "evaluations.artificial_analysis_intelligence_index",
    "cost": "artificial_analysis_intelligence_index_cost.cost_per_task.total_cost",
    "time": "performance_v3.mean_time_to_first_answer_token_seconds",
    "creator": "model_creator.name",
    "release_date": "release_date",
    "price_input": "pricing.price_1m_input_tokens",
    "price_output": "pricing.price_1m_output_tokens",
}
INTELLIGENCE_KEY = FIELDS["intelligence"].split(".")[-1]
# Top-level keys kept in snapshots; everything else is dropped.
KEEP_KEYS = (
    "slug",
    "name",
    "release_date",
    "model_creator",
    "evaluations",
    "artificial_analysis_intelligence_index_cost",
    "performance_v3",
    "pricing",
)
MEASURES = ("intelligence", "cost", "time")


# ---------------------------------------------------------------- files

def read_json(path: Path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, sort_keys=False, ensure_ascii=False)
        f.write("\n")
    tmp.replace(path)


def load_config(config_dir: Path = CONFIG_DIR):
    return (
        read_json(config_dir / "slate.json"),
        read_json(config_dir / "axes.json"),
        read_json(config_dir / "settings.json"),
    )


def snapshot_paths(snapshots_dir: Path) -> list[Path]:
    return sorted(p for p in snapshots_dir.glob("*.json") if p.stem[:4].isdigit())


# ---------------------------------------------------------------- records

def get_path(record, path: str):
    cur = record
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def creator_of(record) -> str | None:
    c = record.get("model_creator")
    if isinstance(c, str):
        return c
    return get_path(record, FIELDS["creator"])


def number(value):
    """Return a float for numeric values, None for anything else (incl. bools, NaN)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return float(value)


def measures_of(record) -> dict:
    return {m: number(get_path(record, FIELDS[m])) for m in MEASURES}


def matches(name: str | None, base: str) -> bool:
    """A row belongs to a model when its name is the base name, or the base name plus ' (…)'."""
    name = name or ""
    return name == base or name.startswith(base + " (")


def matches_any(name: str | None, bases) -> bool:
    return any(matches(name, b) for b in bases)


def parse_name(name: str, settings: dict | None = None):
    """'Claude Opus 5.5 (Max, Default Fallback)' -> ('Claude Opus 5.5', 'max', 'Default Fallback').

    A name with no bracket (AA lists some single-setting models that way) gets effort 'default'.
    """
    name = (name or "").strip()
    i = name.find(" (")
    if i < 0 or not name.endswith(")"):
        return name, "default", None
    base, inner = name[:i].strip(), name[i + 2 : -1]
    parts = [p.strip() for p in inner.split(",")]
    effort = normalise_effort(parts[0], settings) if parts[0] else None
    note = ", ".join(p for p in parts[1:] if p) or None
    return base, effort, note


def normalise_effort(label: str, settings: dict | None = None) -> str:
    e = label.strip().lower()
    aliases = (settings or {}).get("effort_aliases", {})
    return aliases.get(e, e)


def effort_rank(effort: str | None, settings: dict) -> tuple:
    order = settings["effort_order"]
    if effort in order:
        return (0, order.index(effort), "")
    return (1, 0, effort or "")


def sig(x, n: int = 2):
    """Round to n significant figures (display precision)."""
    if x is None:
        return None
    if x == 0:
        return 0.0
    return round(x, -int(math.floor(math.log10(abs(x)))) + (n - 1))


def display_values(record) -> tuple:
    m = measures_of(record)
    intel = None if m["intelligence"] is None else round(m["intelligence"])
    return (intel, sig(m["cost"]), sig(m["time"]))


def fingerprint(records: list, index_version) -> str:
    rows = sorted(
        (r.get("slug") or "", r.get("name") or "", *display_values(r)) for r in records
    )
    blob = json.dumps({"v": index_version, "rows": rows}, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()


def trim(records: list, slate: dict, settings: dict, today: date) -> list:
    """Keep slate matches and recent records from tracked creators; drop unused fields."""
    bases = [m["aa_name"] for m in slate["models"]]
    pinned = {s for m in slate["models"] for s in m.get("slugs", [])}
    creators = set(settings.get("tracked_creators", []))
    cutoff = (today - timedelta(days=settings.get("candidate_window_days", 120))).isoformat()
    kept = []
    for r in records:
        name = r.get("name") or ""
        keep = matches_any(name, bases) or r.get("slug") in pinned
        if not keep and creator_of(r) in creators:
            rd = r.get("release_date")
            keep = isinstance(rd, str) and rd >= cutoff
        if keep:
            kept.append({k: r[k] for k in KEEP_KEYS if k in r})
    kept.sort(key=lambda r: r.get("slug") or "")
    return kept


# ---------------------------------------------------------------- HTTP

class ApiError(Exception):
    def __init__(self, status, message, retry_after=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def _ssl_context() -> ssl.SSLContext:
    """Verified TLS. python.org macOS builds ship without CA certs; fall back to the system bundle."""
    ctx = ssl.create_default_context()
    if not ctx.cert_store_stats().get("x509_ca"):
        for bundle in ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"):
            if Path(bundle).exists():
                ctx.load_verify_locations(cafile=bundle)
                break
    return ctx


def _get(url: str, key: str, timeout: int = 30, sleep=time.sleep):
    req = urllib.request.Request(
        url,
        headers={"x-api-key": key, "Accept": "application/json", "User-Agent": "aa-dashboard/1"},
    )
    ctx = _ssl_context()
    err = None
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                return json.load(resp), dict(resp.headers.items())
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise ApiError(401, "401 unauthorised: AA_API_KEY is missing or invalid") from None
            if e.code == 403:
                raise ApiError(403, "403 forbidden: this key's tier cannot use this endpoint") from None
            if e.code == 429:
                raise ApiError(429, "429: daily quota used", e.headers.get("Retry-After")) from None
            if e.code < 500:
                raise ApiError(e.code, f"HTTP {e.code} from API") from None
            err = ApiError(e.code, f"HTTP {e.code} from API (after retries)")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            err = ApiError(None, f"request failed (after retries): {type(e).__name__}: {getattr(e, 'reason', e)}")
        if attempt < len(RETRY_DELAYS):
            sleep(RETRY_DELAYS[attempt])
    raise err


def has_next(pagination: dict, page: int, n_items: int) -> bool:
    """Interpret the pagination block defensively; shape verified in step 0."""
    p = pagination or {}
    for k in ("has_more", "has_next", "has_next_page"):
        if k in p:
            return bool(p[k])
    for k in ("next", "next_page", "next_cursor"):
        if k in p:
            return bool(p[k])
    if "total_pages" in p:
        return page < int(p["total_pages"])
    total = p.get("total") or p.get("total_count") or p.get("total_items")
    per = p.get("per_page") or p.get("page_size") or p.get("limit") or PAGE_SIZE
    if total is not None:
        return page * int(per) < int(total)
    return n_items >= PAGE_SIZE


def fetch_all(key: str, url: str = FREE_URL, max_pages: int = 20, getter=_get):
    """Download every page. Raises ApiError on any failure, so partial data is never returned."""
    records, meta, headers, page = [], {}, {}, 1
    while True:
        sep = "&" if "?" in url else "?"
        body, headers = getter(f"{url}{sep}{urllib.parse.urlencode({'page': page})}", key)
        if page == 1:
            meta = {k: body.get(k) for k in ("tier", "intelligence_index_version")}
        data = body.get("data")
        if not isinstance(data, list):
            raise ApiError(None, "response has no 'data' list")
        records.extend(data)
        if not data or not has_next(body.get("pagination"), page, len(data)):
            break
        page += 1
        if page > max_pages:
            raise ApiError(None, f"more than {max_pages} pages; refusing to continue")
    rate = {k: v for k, v in headers.items() if "ratelimit" in k.lower() or k.lower() == "retry-after"}
    return records, meta, rate
