from datetime import datetime, timedelta, timezone

from app.downloads.router import _as_utc


def test_as_utc_normalises_naive_sqlite_datetime():
    value = datetime(2026, 9, 5, 10, 44, 0)
    result = _as_utc(value)

    assert result.tzinfo is not None
    assert result.utcoffset() == timedelta(0)
    assert result == datetime(2026, 9, 5, 10, 44, 0, tzinfo=timezone.utc)


def test_as_utc_preserves_aware_utc_datetime():
    value = datetime(2026, 9, 5, 10, 44, 0, tzinfo=timezone.utc)
    result = _as_utc(value)

    assert result == value
    assert result.tzinfo == timezone.utc


def test_normalised_expiry_can_compare_with_utc_now():
    expires = datetime(2026, 9, 5, 10, 44, 0)
    now = datetime(2026, 8, 31, 11, 55, 0, tzinfo=timezone.utc)

    assert _as_utc(expires) > now
