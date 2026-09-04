from datetime import datetime, timedelta, timezone

from src.utils.freshness import heuristic_is_new, is_fresh, parse_datetime

NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)


def test_parse_iso8601():
    assert parse_datetime("2026-09-04T10:00:00Z") == datetime(2026, 9, 4, 10, 0, 0, tzinfo=timezone.utc)


def test_parse_rfc822_rss_date():
    dt = parse_datetime("Fri, 04 Sep 2026 10:00:00 +0000")
    assert dt == datetime(2026, 9, 4, 10, 0, 0, tzinfo=timezone.utc)


def test_parse_relative_hours_ago():
    dt = parse_datetime("2 hours ago", now=NOW)
    assert dt == NOW - timedelta(hours=2)


def test_parse_relative_minutes_ago():
    dt = parse_datetime("45 minutes ago", now=NOW)
    assert dt == NOW - timedelta(minutes=45)


def test_parse_yesterday():
    dt = parse_datetime("yesterday", now=NOW)
    assert dt.date() == (NOW - timedelta(days=1)).date()


def test_parse_epoch_seconds():
    epoch = int(NOW.timestamp())
    dt = parse_datetime(str(epoch))
    assert abs((dt - NOW).total_seconds()) < 1


def test_parse_epoch_millis():
    epoch_ms = int(NOW.timestamp() * 1000)
    dt = parse_datetime(str(epoch_ms))
    assert abs((dt - NOW).total_seconds()) < 1


def test_parse_missing_returns_none():
    assert parse_datetime(None) is None
    assert parse_datetime("") is None
    assert parse_datetime("not a date at all !!") is None


def test_parse_naive_datetime_assumed_utc():
    dt = parse_datetime("2026-09-04 10:00:00")
    assert dt.tzinfo is not None


def test_is_fresh_within_window():
    dt = NOW - timedelta(hours=5)
    assert is_fresh(dt, window_hours=24, now=NOW) is True


def test_is_fresh_outside_window():
    dt = NOW - timedelta(hours=25)
    assert is_fresh(dt, window_hours=24, now=NOW) is False


def test_is_fresh_exactly_at_boundary():
    dt = NOW - timedelta(hours=24)
    assert is_fresh(dt, window_hours=24, now=NOW) is True


def test_is_fresh_none_is_never_fresh():
    assert is_fresh(None, window_hours=24, now=NOW) is False


def test_is_fresh_far_future_rejected_as_spam():
    dt = NOW + timedelta(hours=5)
    assert is_fresh(dt, window_hours=24, now=NOW) is False


def test_is_fresh_tolerates_small_clock_skew():
    dt = NOW + timedelta(minutes=2)
    assert is_fresh(dt, window_hours=24, now=NOW) is True


def test_heuristic_is_new_first_crawl_never_floods():
    assert heuristic_is_new("item-1", seen_ids=set(), now=NOW) is None


def test_heuristic_is_new_unseen_item_is_new():
    result = heuristic_is_new("item-2", seen_ids={"item-1"}, now=NOW)
    assert result == NOW


def test_heuristic_is_new_seen_item_is_not_new():
    assert heuristic_is_new("item-1", seen_ids={"item-1"}, now=NOW) is None
