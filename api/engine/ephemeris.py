"""사이데리얼(라히리) 경도 산출. 1단계.

constants.yaml.arithmetic.ephemeris 의 설정을 코드 레벨 계약으로 옮긴 것:
backend=swiss_se1, ayanamsa_constant=SIDM_LAHIRI, node_type=mean(ISSUE-009 확정).
node_type="true" 는 여전히 미구현이라 NotImplementedError 로 막는다.

position_mode=apparent (constants.yaml) — SEFLG_TRUEPOS 를 어떤 호출에도 넣지
않는다. describe_swisseph_flags() 로 실제 호출 플래그를 확인할 수 있다.

라그나는 ISSUE-012에서 확정한 ``tropical_houses_minus_ayanamsa`` 방식이다.
``swe.houses_ex`` 를 트로피컬(flags=0)로 호출한 뒤 ``swe.get_ayanamsa_ut`` 값을
수동 차감한다. 폐기된 내장 사이데리얼 하우스 플래그를 다시 사용하지 않는다.
"""
from __future__ import annotations

import datetime as dt

import swisseph as swe

BODY_IDS: dict[str, int] = {
    "sun": swe.SUN,
    "moon": swe.MOON,
    "mars": swe.MARS,
    "mercury": swe.MERCURY,
    "jupiter": swe.JUPITER,
    "venus": swe.VENUS,
    "saturn": swe.SATURN,
}

_PLANET_CALC_FLAGS = swe.FLG_SWIEPH | swe.FLG_SIDEREAL
_HOUSES_CALC_FLAGS = 0
ASCENDANT_METHOD = "tropical_houses_minus_ayanamsa"


def init_sidereal_lahiri(ephe_path: str) -> None:
    swe.set_ephe_path(ephe_path)
    swe.set_sid_mode(swe.SIDM_LAHIRI, 0, 0)


def julian_day_ut(utc_dt: dt.datetime) -> float:
    if utc_dt.tzinfo is None or utc_dt.utcoffset() != dt.timedelta(0):
        raise ValueError("utc_dt must be timezone-aware UTC")
    hour = utc_dt.hour + utc_dt.minute / 60 + utc_dt.second / 3600
    return swe.julday(utc_dt.year, utc_dt.month, utc_dt.day, hour)


def sidereal_longitude(jd_ut: float, body_id: int) -> float:
    (lon, *_rest), _retflags = swe.calc_ut(jd_ut, body_id, _PLANET_CALC_FLAGS)
    return lon % 360.0


def rahu_ketu_longitudes(jd_ut: float, node_type: str) -> tuple[float, float]:
    if node_type != "mean":
        raise NotImplementedError(
            f"node_type={node_type!r} 미구현 — constants.yaml arithmetic.ephemeris.node_type "
            "는 mean 으로 확정됨(ISSUE-009 해결). true 는 아직 미구현."
        )
    rahu = sidereal_longitude(jd_ut, swe.MEAN_NODE)
    ketu = (rahu + 180.0) % 360.0
    return rahu, ketu


def ayanamsa_degrees(jd_ut: float) -> float:
    return swe.get_ayanamsa_ut(jd_ut)


def ascendant_longitude(jd_ut: float, lat: float, lon: float) -> float:
    """트로피컬 Asc에서 현재 라히리 아야남샤를 수동 차감한다."""
    _cusps, ascmc = swe.houses_ex(jd_ut, lat, lon, b"W", flags=_HOUSES_CALC_FLAGS)
    tropical_ascendant = ascmc[0]
    return (tropical_ascendant - swe.get_ayanamsa_ut(jd_ut)) % 360.0


def describe_swisseph_flags() -> dict:
    """실제 계산 호출에 쓰는 pyswisseph 플래그를 그대로 노출한다.

    engine_meta·릴리스 로그가 "apparent 를 쓴다"고 주장만 하지 않고, 실제 호출
    상수와 SEFLG_TRUEPOS 비트 여부를 직접 확인할 수 있게 한다.
    """
    return {
        "planet_calc_flags": _PLANET_CALC_FLAGS,
        "planet_calc_flags_names": ["FLG_SWIEPH", "FLG_SIDEREAL"],
        "houses_calc_flags": _HOUSES_CALC_FLAGS,
        "houses_calc_flags_names": [],
        "ascendant_method": ASCENDANT_METHOD,
        "seflg_trueops_used": bool(
            (_PLANET_CALC_FLAGS | _HOUSES_CALC_FLAGS) & swe.FLG_TRUEPOS
        ),
    }
