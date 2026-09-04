"""Publication-date normalization and 24-hour freshness validation.

Handles: ISO-8601, RFC-822/RSS pubDate, epoch seconds, JSON-LD `datePublished`,
OpenGraph `article:published_time`, relative strings ("2 hours ago",
"yesterday"), and bare dates with no time component (assumed midnight UTC of
that day, which biases toward *rejecting* borderline items rather than
accepting stale ones as fresh).

If nothing above matches, `parse_datetime` returns None — callers must treat
that as "unknown", never as "fresh". The one documented heuristic exception
is `heuristic_is_new` for sources with no timestamp at all, and it can only
ever be additive evidence from checkpoint state (item id not seen in a prior
crawl), never a silent freshness override.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from dateutil import parser as dateutil_parser

_RELATIVE_RE = re.compile(
    r"(?P<num>\d+)\s*(?P<unit>second|minute|min|hour|hr|day|week|month|year)s?\s*ago",
    re.IGNORECASE,
)
_UNIT_TO_SECONDS = {
    "second": 1,
    "minute": 60,
    "min": 60,
    "hour": 3600,
    "hr": 3600,
    "day": 86400,
    "week": 604800,
    "month": 2592000,
    "year": 31536000,
}


def parse_datetime(value: Optional[str], now: Optional[datetime] = None) -> Optional[datetime]:
    """Best-effort parse of any of the supported date formats to an
    aware UTC datetime. Returns None if `value` is empty or unparseable —
    callers must not assume a None means "now".
    """
    if not value or not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None

    now = now or datetime.now(timezone.utc)

    lowered = value.lower()
    if lowered in ("just now", "moments ago", "now"):
        return now
    if lowered == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if lowered == "yesterday":
        return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    rel = _RELATIVE_RE.search(value)
    if rel:
        n = int(rel.group("num"))
        unit = rel.group("unit").lower()
        seconds = _UNIT_TO_SECONDS.get(unit)
        if seconds:
            return now - timedelta(seconds=n * seconds)

    if value.isdigit():
        as_int = int(value)
        try:
            if as_int > 10_000_000_000:
                return datetime.fromtimestamp(as_int / 1000, tz=timezone.utc)
            return datetime.fromtimestamp(as_int, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            pass

    try:
        parsed = dateutil_parser.parse(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def is_fresh(dt: Optional[datetime], window_hours: int = 24, now: Optional[datetime] = None) -> bool:
    """Strict freshness check. A None or unparseable date is NEVER fresh —
    "we couldn't determine the date" must never be conflated with "it's new".
    """
    if dt is None:
        return False
    now = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    age = now - dt
    return timedelta(0) <= age <= timedelta(hours=window_hours) or (
        age < timedelta(0) and age > -timedelta(minutes=5)  # tolerate small clock skew, not future-dated spam
    )


def heuristic_is_new(item_id: str, seen_ids: set[str], now: Optional[datetime] = None) -> Optional[datetime]:
    """Documented fallback for sources that expose neither a timestamp nor a
    reliable "sort by newest" guarantee: if this exact item id was NOT present
    in the checkpoint state from the previous crawl of this source, treat it
    as new-as-of-now. This never marks something fresh on a first-ever crawl
    of a source (empty `seen_ids` means "unknown", not "everything is new") —
    that would let a full backlog flood in as false-fresh on day one.
    """
    if not seen_ids:
        return None
    if item_id in seen_ids:
        return None
    return now or datetime.now(timezone.utc)
