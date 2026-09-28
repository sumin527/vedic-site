"""상용 소프트웨어 화면 표기(도분초) → 사이데리얼 경도(float, 0~360) 파서.

지원 형식 세 가지:
  "Ta 12°34'56\""       — 사인 약어 + 도(°)분(')초(") (0 이상 30 미만)
  "42°34'56\""          — 사인 없이 전체 황경 도(°)분(')초(") (0 이상 360 미만)
  "15 Sg 46' 21.70\""   — JHora 실제 출력 형식. 도(기호 없음) 사인 분(')초(")

사인 약어는 표준 2글자 표기: Ar Ta Ge Cn Le Vi Li Sc Sg Cp Aq Pi (Ar=1번 사인).
분·초 기호는 ASCII(' ")·유니코드 프라임(′ ″) 둘 다 인식한다.
"""
from __future__ import annotations

import re

SIGN_ABBREVIATIONS: dict[str, int] = {
    "Ar": 1, "Ta": 2, "Ge": 3, "Cn": 4, "Le": 5, "Vi": 6,
    "Li": 7, "Sc": 8, "Sg": 9, "Cp": 10, "Aq": 11, "Pi": 12,
}

_DEG = r"(?P<deg>\d+(?:\.\d+)?)"
_MIN = r"(?P<min>\d+(?:\.\d+)?)['′]"
_SEC = r"(?P<sec>\d+(?:\.\d+)?)[\"″]"
_SIGN = r"(?P<sign>[A-Za-z]{2})"

# "Ta 12°34'56\"" — 사인 먼저, 도에 ° 기호
_SIGN_FIRST_PATTERN = re.compile(rf"^\s*{_SIGN}\s+{_DEG}[°]\s*{_MIN}\s*{_SEC}\s*$")
# "42°34'56\"" — 사인 없음, 도에 ° 기호
_NO_SIGN_PATTERN = re.compile(rf"^\s*{_DEG}[°]\s*{_MIN}\s*{_SEC}\s*$")
# "15 Sg 46' 21.70\"" — JHora. 도가 먼저 오고 ° 기호 없이 사인이 뒤따름
_DEG_FIRST_PATTERN = re.compile(rf"^\s*{_DEG}\s*{_SIGN}\s*{_MIN}\s*{_SEC}\s*$")

_ERROR_HINT = "지원 형식: \"Ta 12°34'56\\\"\", \"42°34'56\\\"\", \"15 Sg 46' 21.70\\\"\""


def _within_sign_to_absolute(sign_token: str, within_sign: float) -> float:
    sign_num = SIGN_ABBREVIATIONS.get(sign_token.capitalize())
    if sign_num is None:
        raise ValueError(f"알 수 없는 사인 약어: {sign_token!r} — 지원: {sorted(SIGN_ABBREVIATIONS)}")
    if not (0.0 <= within_sign < 30.0):
        raise ValueError(f"사인 내 도수는 0~30 범위여야 함: {within_sign}")
    return (sign_num - 1) * 30.0 + within_sign


def parse_dms(text: str) -> float:
    for pattern in (_SIGN_FIRST_PATTERN, _DEG_FIRST_PATTERN):
        match = pattern.match(text)
        if match:
            deg, minute, sec = float(match["deg"]), float(match["min"]), float(match["sec"])
            within_sign = deg + minute / 60.0 + sec / 3600.0
            return _within_sign_to_absolute(match["sign"], within_sign)

    match = _NO_SIGN_PATTERN.match(text)
    if match:
        deg, minute, sec = float(match["deg"]), float(match["min"]), float(match["sec"])
        return (deg + minute / 60.0 + sec / 3600.0) % 360.0

    raise ValueError(f"DMS 형식을 인식할 수 없음: {text!r} — {_ERROR_HINT}")
