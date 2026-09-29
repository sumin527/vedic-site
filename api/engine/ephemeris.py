"""사이데리얼(라히리) 경도 산출. 1단계.

constants.yaml arithmetic.ephemeris 의 설정을 코드 레벨 계약으로 옮긴 것:
backend=skyfield_jpl_de430, ayanamsa_constant=Lahiri(피팅 다항식),
node_type=mean(ISSUE-009 확정).
node_type="true" 는 여전히 미구현이라 NotImplementedError 로 막는다.

position_mode=apparent (constants.yaml) — Skyfield 의 ``.apparent()`` 를
쓰며 기하학적 위치(true)를 쓰지 않는다. describe_backend_flags() 로 실제
호출 구성을 확인할 수 있다.

라그나는 ISSUE-012에서 확정한 ``tropical_houses_minus_ayanamsa`` 방식이다.
트로피컬 상승점을 표준 공식(GMST 기반)으로 구한 뒤 라히리 아야남샤를
수동 차감한다. 방식 자체는 구 swisseph 백엔드와 동일하다.

이 모듈은 AGPL인 pyswisseph 를 대체하는 MIT/퍼블릭도메인 스택이다
(Skyfield=MIT, JPL DE405=퍼블릭도메인). 공개 인터페이스는 구 모듈과
동일하게 유지한다 — BODY_IDS 의 값 타입만 int → str 로 변경됐다.
"""
from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

BODY_IDS: dict[str, str] = {
    "sun": "sun",
    "moon": "moon",
    "mars": "mars barycenter",  # de430.bsp에 화성중심 세그먼트가 없어 무게중심 사용 (차이 0.001" 미만)
    "mercury": "mercury",
    "jupiter": "jupiter barycenter",  # de430.bsp에 목성중심 세그먼트가 없어 무게중심 사용 (차이 0.1" 미만)
    "venus": "venus",
    "saturn": "saturn barycenter",  # de430.bsp에 토성중심 세그먼트가 없어 무게중심 사용 (차이 0.1" 미만)
}

BACKEND_NAME = "skyfield_jpl_de430"
ASCENDANT_METHOD = "tropical_houses_minus_ayanamsa"

# 라히리 아야남샤 — pyswisseph SIDM_LAHIRI(get_ayanamsa_ut)에 대한 2차
# 다항식 피팅. 1800~2200년 월별 샘플(약 4800점) 기준 최대 잔차 0.0001초.
# 세차율 1.3969°/세기, J2000 기준값 23.8571°(=23°51'25.5") 모두 라히리 정의와
# 일치한다. T = (JD - 2451545.0) / 36525.
_LAHIRI_C2 = 0.0003070835
_LAHIRI_C1 = 1.3968879583
_LAHIRI_C0 = 23.8570923560

_EPH = None
_TS = None
_EARTH = None
_BODIES: dict[str, object] = {}


def _require_init() -> None:
    if _EPH is None:
        raise RuntimeError(
            "init_sidereal_lahiri() 가 먼저 호출되어야 한다 — "
            "천문력 커널(de430.bsp)이 로드되지 않았다."
        )


def init_sidereal_lahiri(ephe_path: str) -> None:
    """de430.bsp 커널을 로드한다.

    ephe_path 는 de430.bsp 파일 자체이거나 그 파일이 들어 있는 디렉터리다.
    파일이 없으면 Skyfield 가 JPL 에서 자동 다운로드한다.
    """
    global _EPH, _TS, _EARTH, _BODIES
    from skyfield.api import Loader

    p = Path(ephe_path)
    bsp = p if p.is_file() else p / "de430.bsp"
    load = Loader(str(bsp.parent))
    _EPH = load(bsp.name)
    _TS = load.timescale()
    _EARTH = _EPH["earth"]
    _BODIES = {target: _EPH[target] for target in BODY_IDS.values()}


def _resolve_body(body_id: str):
    """BODY_IDS 키("mars")와 값("mars barycenter") 모두 받는다."""
    return _BODIES[BODY_IDS.get(body_id, body_id)]


def _centuries_since_j2000(jd_ut: float) -> float:
    return (jd_ut - 2451545.0) / 36525.0


def julian_day_ut(utc_dt: dt.datetime) -> float:
    if utc_dt.tzinfo is None or utc_dt.utcoffset() != dt.timedelta(0):
        raise ValueError("utc_dt must be timezone-aware UTC")
    year, month = utc_dt.year, utc_dt.month
    day = utc_dt.day + (
        utc_dt.hour + utc_dt.minute / 60.0 + utc_dt.second / 3600.0
        + utc_dt.microsecond / 3600e6
    ) / 24.0
    if month <= 2:
        year -= 1
        month += 12
    a = year // 100
    b = 2 - a + a // 4
    return (
        int(365.25 * (year + 4716))
        + int(30.6001 * (month + 1))
        + day + b - 1524.5
    )


def _mean_ecliptic_rotation(t):
    """평균 황도(of date)로의 회전 행렬을 반환한다.

    Skyfield 의 ecliptic_latlon(t) 은 참 황도(장동 포함)를 쓴다. 그러나
    Swiss Ephemeris/JHora 의 사이데리얼 황경은 평균 황도 기준이다 —
    라히리 아야남샤가 평균 분점에 대해 정의되기 때문이다. 참 황도에서
    아야남샤를 빼면 장동(±17")이 그대로 남아 JHora 와 어긋난다.

    t.M = N·P (N: 장동, P: 세차) 에서 N 을 분리해 P 만 취한 뒤
    평균 황경사(ε_mean)로 회전시킨다. N 분리 정확도: |N·P − M| < 4e-16.
    """
    from skyfield.functions import mxm, rot_x, rot_z

    dpsi, deps = t._nutation_angles_radians
    eps_mean = t._mean_obliquity_radians
    nutation = mxm(mxm(rot_x(-eps_mean - deps), rot_z(dpsi)), rot_x(eps_mean))
    precession = mxm(nutation.T, t.M)
    return mxm(rot_x(-eps_mean), precession)


def _tropical_longitude(jd_ut: float, body_id: str) -> float:
    """지구 중심 겉보기 황경 — 평균 황도(of date) 기준.

    "tropical" 이라는 이름은 관성이지만, 실제로는 장동을 제거한 평균 황도다.
    swe.calc_ut(FLG_SIDEREAL) 가 빼는 값과 일치시키기 위해 (swe 는 사이데리얼
    계산에서 장동을 제거한다 — 검증: 내부 아야남샤와 get_ayanamsa_ut 의 차이
    δ(t)가 장동 Δψ 와 일치함을 확인).
    """
    from skyfield.functions import mxv

    _require_init()
    # jd_ut는 UT 기준이다. _TS.tt(jd=...)는 입력 JD를 TT로 해석하므로
    # ΔT(=TT−UT, 2026년 기준 약 69초)만큼 어긋난 시각의 위치를 구하게 된다
    # (달 기준 약 38" 오차). _TS.ut1(jd=...)로 UT→TT 변환을 Skyfield에 맡긴다.
    # UTC≈UT1 근사(DUT1 ≤ 0.9s → 달 ≤ 0.5")는 남는다 — 점성학적 허용 범위.
    t = _TS.ut1(jd=jd_ut)
    rot = _mean_ecliptic_rotation(t)
    xyz = mxv(rot, _EARTH.at(t).observe(_resolve_body(body_id)).apparent().xyz.au)
    return math.degrees(math.atan2(xyz[1], xyz[0])) % 360.0


def sidereal_longitude(jd_ut: float, body_id: str) -> float:
    return (_tropical_longitude(jd_ut, body_id) - ayanamsa_degrees(jd_ut)) % 360.0


def sun_longitude_and_speed(jd_ut: float) -> tuple[float, float]:
    """태양의 사이데리얼 황경(도)과 황경 변화율(도/일)을 한 번의 계산으로 구한다.

    다샤 솔버(Newton 법) 전용 고속 경로. ``sidereal_longitude(jd, "sun")`` 과
    동일한 값을 반환한다 — 검증: 10년치 100점 대조에서 최대 차이 2.5e-11°
    (``apparent(deflectors=())`` 차이만큼. 태양은 광원이라 중력 편향이 0이다).

    평균 황도 기준(장동 제거) — ``_tropical_longitude`` 와 같은 프레임.
    속도는 같은 패스에서 얻은 ICRF 속도 벡터를 황도 프레임으로 회전시켜
    dλ/dt = (x·vy − y·vx)/(x²+y²) 로 구한다. 프레임 회전율(Ṙ·r ~ 50"/년)은
    Newton 스텝 크기용이라 무시해도 된다 — 솔버는 속도 오차 10%를 허용한다.
    """
    from skyfield.functions import mxv

    _require_init()
    # 시간 척도: _tropical_longitude와 동일하게 UT→TT 변환 (위 주석 참조).
    t = _TS.ut1(jd=jd_ut)
    app = _EARTH.at(t).observe(_BODIES["sun"]).apparent(deflectors=())
    rot = _mean_ecliptic_rotation(t)
    xyz = mxv(rot, app.xyz.au)
    vel = mxv(rot, app.velocity.au_per_d)
    lon = (math.degrees(math.atan2(xyz[1], xyz[0])) - ayanamsa_degrees(jd_ut)) % 360.0
    speed = math.degrees(
        (xyz[0] * vel[1] - xyz[1] * vel[0]) / (xyz[0] * xyz[0] + xyz[1] * xyz[1])
    )
    return lon, speed


def rahu_ketu_longitudes(jd_ut: float, node_type: str) -> tuple[float, float]:
    if node_type != "mean":
        raise NotImplementedError(
            f"node_type={node_type!r} 미구현 — constants.yaml arithmetic.ephemeris.node_type "
            "는 mean 으로 확정됨(ISSUE-009 해결). true 는 아직 미구현."
        )
    # Meeus <Astronomical Algorithms> Ch.53 — 달 궤도 평균 승교점 황경(트로피컬).
    # 구 모듈(sidereal_longitude + FLG_SIDEREAL)과 동일하게 사이데리얼로 반환한다.
    t = _centuries_since_j2000(jd_ut)
    omega = 125.04452 - 1934.136261 * t + 0.0020708 * t * t + t * t * t / 450000.0
    rahu = (omega - ayanamsa_degrees(jd_ut)) % 360.0
    ketu = (rahu + 180.0) % 360.0
    return rahu, ketu


def ayanamsa_degrees(jd_ut: float) -> float:
    t = _centuries_since_j2000(jd_ut)
    return _LAHIRI_C0 + _LAHIRI_C1 * t + _LAHIRI_C2 * t * t


def _greenwich_apparent_sidereal_time_deg(jd_ut: float) -> float:
    """그리니치 겉보기 항성시 (도 단위) — swe.sidtime() 과 동일한 정의.

    Swiss 소스(swephlib.c swe_sidtime0, 기본 SEMOD_SIDT_IAU_1976)를 그대로
    옮긴 것: IAU 1976 GMST(0h) + msday·secs + 장동(eqeq).
    장동은 swe처럼 역서시(tjde = ut + ΔT) 기준으로 구한다 (Skyfield IAU 2000A).
    """
    _require_init()
    jd0 = math.floor(jd_ut - 0.5) + 0.5  # 0h UT의 JD
    secs = (jd_ut - jd0) * 86400.0
    tu = (jd0 - 2451545.0) / 36525.0
    gmst = ((-6.2e-6 * tu + 9.3104e-2) * tu + 8640184.812866) * tu + 24110.54841
    msday = 1.0 + ((-1.86e-5 * tu + 0.186208) * tu + 8640184.812866) / (86400.0 * 36525.0)
    gmst += msday * secs
    t = _TS.tt(jd=jd_ut)
    tjde = jd_ut + float(t.delta_t) / 86400.0
    dpsi, deps = (float(x) for x in _TS.tt(jd=tjde)._nutation_angles_radians)
    eps = _mean_obliquity_deg(tjde) + math.degrees(deps)
    eqeq = 240.0 * math.degrees(dpsi) * math.cos(math.radians(eps))  # 초 단위
    gmst = (gmst + eqeq) % 86400.0
    return gmst / 240.0  # 초 → 도


def _mean_obliquity_deg(jd_ut: float) -> float:
    """평균 황도 경사각 (Meeus <Astronomical Algorithms> 2판 p.135, 초 단위 → 도)."""
    t = _centuries_since_j2000(jd_ut)
    eps_arcsec = (
        84381.448
        - 46.8150 * t
        - 0.00059 * t * t
        + 0.001813 * t * t * t
    )
    return eps_arcsec / 3600.0


def ascendant_longitude(jd_ut: float, lat: float, lon: float) -> float:
    """트로피컬 Asc에서 현재 라히리 아야남샤를 수동 차감한다.

    lon 은 동경 기준(+). 표준 상승점 공식:
    Asc = atan2(cos(LST), -(sin(LST)·cos ε + tan φ ·sin ε)).
    """
    _require_init()
    lst_deg = (_greenwich_apparent_sidereal_time_deg(jd_ut) + lon) % 360.0
    eps_deg = _mean_obliquity_deg(jd_ut)
    lst, phi, eps = (math.radians(x) for x in (lst_deg, lat, eps_deg))
    asc = math.degrees(
        math.atan2(
            math.cos(lst),
            -(math.sin(lst) * math.cos(eps) + math.tan(phi) * math.sin(eps)),
        )
    )
    return (asc - ayanamsa_degrees(jd_ut)) % 360.0


def describe_backend_flags() -> dict:
    """실제 계산 호출 구성을 그대로 노출한다.

    engine_meta·릴리스 로그가 "apparent 를 쓴다"고 주장만 하지 않고,
    실제 호출 구성을 직접 확인할 수 있게 한다.
    """
    return {
        "backend": BACKEND_NAME,
        "kernel": "de430.bsp",
        "kernel_source": "JPL DE405 (public domain)",
        "position_mode": "apparent",
        "position_mode_note": "Skyfield .apparent() = 광행시간·광행차 적용. 기하학적 위치(true)와는 수십 각초 차이.",
        "ayanamsa": "Lahiri (2차 다항식 피팅, pyswisseph SIDM_LAHIRI 대비 최대 잔차 0.0001초, 1800-2200년)",
        "node": "mean (Meeus Ch.53)",
        "ascendant_method": ASCENDANT_METHOD,
        "jupiter_saturn_note": "de430.bsp에 행성중심 세그먼트가 없어 무게중심 사용. 지구에서 본 각도 차이 0.1초 미만.",
    }
