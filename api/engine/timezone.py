"""IANA 시간대 문자열을 UTC 로 변환한다.

고정 UTC 오프셋을 쓰지 않는다. 한국은 1954~1961 UTC+8:30, 1987~1988 여름
서머타임처럼 시대에 따라 규칙이 바뀌므로, zoneinfo 로 해당 시점의 실제
규칙을 적용해야 한다.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo


def to_utc(
    year: int, month: int, day: int, hour: int, minute: int, second: int, tz_name: str
) -> dt.datetime:
    local = dt.datetime(year, month, day, hour, minute, second, tzinfo=ZoneInfo(tz_name))
    return local.astimezone(dt.timezone.utc)
