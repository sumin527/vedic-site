"""빔쇼타리 다샤 3단계(MD·AD·PD) 계산. 3단계.

날짜 경계는 ``constants.yaml.timing.vimshottari.year_definition`` 의
``true_sidereal_solar_year`` 규칙을 따른다. 즉 1년을 고정 일수로 바꾸지 않고,
출발 시점의 사이데리얼 태양이 해당 다샤 연수에 대응하는 각도만큼 실제로
진행한 시점을 수치적으로 푼다. 평균 태양년과 전통 360일년은 읽지 않는다.

출생 당시 첫 마하다샤는 이미 진행 중일 수 있다. 달의 낙샤트라 진행률로
그 마하다샤의 명목상 시작점을 출생 이전에 복원한 뒤, 그 시작점에서 AD와
PD를 나눈다. 출생 시점부터 AD를 새로 시작시키지 않는다.

4단계 필수 검산(마하다샤 로드 == 달 낙샤트라 로드)은 이 모듈의 범위가
아니다. 이 모듈은 계산 결과만 반환하며 두 로드를 비교하거나 예외를 내지 않는다.
"""
from __future__ import annotations

import datetime as dt
import math
from decimal import Decimal
from typing import Any

import swisseph as swe

from engine.ephemeris import julian_day_ut, sidereal_longitude

_FULL_CIRCLE_DEG = 360.0
_MAX_SOLVER_STEP_DEG = 120.0
_SOLVER_ANGLE_TOLERANCE_DEG = 1e-9
_SOLVER_MAX_ITERATIONS = 16
_UNIX_EPOCH_JD = 2440587.5


def _as_decimal(value: int | float | str) -> Decimal:
    """YAML 숫자를 이진 부동소수점 오차 없이 Decimal로 옮긴다."""
    return Decimal(str(value))


def _require_utc(value: dt.datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != dt.timedelta(0):
        raise ValueError("birth_utc must be timezone-aware UTC")


def _datetime_from_jd(jd_ut: float) -> dt.datetime:
    return dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(
        days=jd_ut - _UNIX_EPOCH_JD
    )


def _iso_utc_from_jd(jd_ut: float) -> str:
    return _datetime_from_jd(jd_ut).isoformat().replace("+00:00", "Z")


def _parse_iso_utc(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _signed_angle_degrees(value: float) -> float:
    return (value + 180.0) % _FULL_CIRCLE_DEG - 180.0


def _solve_solar_step(jd0: float, progress_degrees: float) -> float:
    """120° 이하의 한 구간을 Newton 법으로 푼다.

    태양은 항상 순행하고 한 조각의 초기 추정 오차는 수일 이내이므로 목표
    경도의 인접한 교차점을 안정적으로 찾는다. 도 단위 진행량을 일 단위 초기
    추정으로만 사용하며, 반환 경계는 실제 태양 경도의 근으로 결정된다.
    """
    if progress_degrees == 0.0:
        return jd0
    if abs(progress_degrees) > _MAX_SOLVER_STEP_DEG:
        raise ValueError("internal solar step must not exceed 120 degrees")

    target_longitude = (
        sidereal_longitude(jd0, swe.SUN) + progress_degrees
    ) % _FULL_CIRCLE_DEG
    guess = jd0 + progress_degrees

    for _iteration in range(_SOLVER_MAX_ITERATIONS):
        longitude = sidereal_longitude(guess, swe.SUN)
        error = _signed_angle_degrees(longitude - target_longitude)
        if abs(error) <= _SOLVER_ANGLE_TOLERANCE_DEG:
            return guess

        half_window_days = 0.05
        before = sidereal_longitude(guess - half_window_days, swe.SUN)
        after = sidereal_longitude(guess + half_window_days, swe.SUN)
        speed = _signed_angle_degrees(after - before) / (2.0 * half_window_days)
        if not 0.9 < speed < 1.1:
            raise RuntimeError(f"비정상적인 사이데리얼 태양 속도: {speed} deg/day")
        guess -= error / speed

    final_error = _signed_angle_degrees(
        sidereal_longitude(guess, swe.SUN) - target_longitude
    )
    raise RuntimeError(
        "사이데리얼 태양 진행 solver가 수렴하지 않음: "
        f"jd0={jd0}, progress={progress_degrees}, error={final_error}"
    )


def solve_sidereal_solar_progress(jd0: float, progress_degrees: float) -> float:
    """태양이 사이데리얼로 ``progress_degrees`` 진행한 UT Julian day를 반환한다.

    큰 진행량은 120° 이하 조각으로 나누어 같은 경도의 다음/이전 교차점을
    순서대로 푼다. 따라서 360°의 배수에서도 회전 횟수를 잃지 않으며 고정
    ``days_per_year`` 또는 평균 태양년을 경계 계산에 사용하지 않는다.
    """
    if not math.isfinite(jd0) or not math.isfinite(progress_degrees):
        raise ValueError("jd0 and progress_degrees must be finite")
    if progress_degrees == 0.0:
        return jd0

    remaining = progress_degrees
    current_jd = jd0
    direction = 1.0 if remaining > 0.0 else -1.0
    while abs(remaining) > _MAX_SOLVER_STEP_DEG:
        step = direction * _MAX_SOLVER_STEP_DEG
        current_jd = _solve_solar_step(current_jd, step)
        remaining -= step
    return _solve_solar_step(current_jd, remaining)


def _rotated_order(order: tuple[str, ...], first_lord: str) -> tuple[str, ...]:
    start = order.index(first_lord)
    return order[start:] + order[:start]


def _vimshottari_config(constants: dict[str, Any]) -> tuple[
    tuple[str, ...], dict[str, Decimal], Decimal, int
]:
    timing = constants["timing"]
    if timing["dasha_system"] != "vimshottari":
        raise ValueError("timing.dasha_system must be vimshottari")

    cfg = timing["vimshottari"]
    if cfg["year_definition"] != "true_sidereal_solar_year":
        raise ValueError(
            "timing.vimshottari.year_definition must be true_sidereal_solar_year"
        )
    order = tuple(cfg["order"])
    years = {lord: _as_decimal(value) for lord, value in cfg["years"].items()}
    total_years = _as_decimal(cfg["total_years"])
    nakshatra_count = int(constants["arithmetic"]["nakshatra_count"])

    if not order or tuple(years) != order:
        raise ValueError("timing.vimshottari.order와 years 키 순서가 일치해야 함")
    if sum(years.values(), Decimal(0)) != total_years:
        raise ValueError("timing.vimshottari.years 합이 total_years와 일치해야 함")
    if nakshatra_count % len(order) != 0:
        raise ValueError("낙샤트라 수가 빔쇼타리 로드 수로 나누어떨어져야 함")

    return order, years, total_years, nakshatra_count


def nakshatra_progress(
    moon_longitude: float, nakshatra_count: int
) -> tuple[int, Decimal, Decimal]:
    """달 경도에서 1-based 낙샤트라 인덱스와 진행·잔여 비율을 구한다.

    ``floor(longitude * count / 360)`` 정수 곱셈 공식을 그대로 사용한다.
    잘린 반복소수인 ``nakshatra_span_deg`` 로 나누지 않는다.
    """
    if not math.isfinite(moon_longitude):
        raise ValueError("moon_longitude must be finite")
    if nakshatra_count <= 0:
        raise ValueError("nakshatra_count must be positive")

    longitude = moon_longitude % _FULL_CIRCLE_DEG
    scaled = longitude * nakshatra_count / _FULL_CIRCLE_DEG
    index_zero_based = math.floor(scaled)
    assert 0 <= index_zero_based < nakshatra_count, (
        f"nakshatra index 범위 초과: longitude={moon_longitude} "
        f"idx={index_zero_based}"
    )

    elapsed_fraction = _as_decimal(scaled - index_zero_based)
    remaining_fraction = Decimal(1) - elapsed_fraction
    return index_zero_based + 1, elapsed_fraction, remaining_fraction


def calculate_birth_balance(
    moon_longitude: float, constants: dict[str, Any]
) -> dict[str, Any]:
    """출생 달 경도에 따른 첫 마하다샤 로드와 잔여 비율·연수를 계산한다."""
    order, years, _total_years, nakshatra_count = _vimshottari_config(constants)
    nakshatra, elapsed_fraction, remaining_fraction = nakshatra_progress(
        moon_longitude, nakshatra_count
    )
    lord = order[(nakshatra - 1) % len(order)]
    remaining_years = years[lord] * remaining_fraction

    return {
        "nakshatra_index": nakshatra,
        "starting_dasha": lord,
        "elapsed_fraction": float(elapsed_fraction),
        "remaining_fraction": float(remaining_fraction),
        "remaining_years": float(remaining_years),
    }


def _period_boundaries(
    parent_start_jd: float,
    progress_angles: list[Decimal],
    parent_end_jd: float | None = None,
) -> list[float]:
    boundaries = [parent_start_jd]
    cumulative_angle = Decimal(0)
    for index, angle in enumerate(progress_angles):
        cumulative_angle += angle
        if parent_end_jd is not None and index == len(progress_angles) - 1:
            boundary = parent_end_jd
        else:
            boundary = solve_sidereal_solar_progress(
                parent_start_jd, float(cumulative_angle)
            )
        boundaries.append(boundary)
    return boundaries


def _duration_days(start_jd: float, end_jd: float) -> float:
    return end_jd - start_jd


def _build_pratyantardashas(
    ad_start_jd: float,
    ad_end_jd: float,
    md_years: Decimal,
    ad_years: Decimal,
    ad_lord: str,
    order: tuple[str, ...],
    years: dict[str, Decimal],
    total_years: Decimal,
) -> list[dict[str, Any]]:
    pd_order = _rotated_order(order, ad_lord)
    ad_nominal_years = md_years * ad_years / total_years
    angles = [
        ad_nominal_years * years[lord] / total_years * Decimal(360)
        for lord in pd_order
    ]
    boundaries = _period_boundaries(ad_start_jd, angles, ad_end_jd)

    return [
        {
            "lord": lord,
            "start": _iso_utc_from_jd(boundaries[index]),
            "end": _iso_utc_from_jd(boundaries[index + 1]),
            "duration_days": _duration_days(boundaries[index], boundaries[index + 1]),
        }
        for index, lord in enumerate(pd_order)
    ]


def _build_antardashas(
    md_start_jd: float,
    md_end_jd: float,
    md_lord: str,
    order: tuple[str, ...],
    years: dict[str, Decimal],
    total_years: Decimal,
) -> list[dict[str, Any]]:
    ad_order = _rotated_order(order, md_lord)
    md_years = years[md_lord]
    angles = [
        md_years * years[lord] / total_years * Decimal(360)
        for lord in ad_order
    ]
    boundaries = _period_boundaries(md_start_jd, angles, md_end_jd)

    periods: list[dict[str, Any]] = []
    for index, lord in enumerate(ad_order):
        start_jd, end_jd = boundaries[index], boundaries[index + 1]
        periods.append(
            {
                "lord": lord,
                "start": _iso_utc_from_jd(start_jd),
                "end": _iso_utc_from_jd(end_jd),
                "duration_days": _duration_days(start_jd, end_jd),
                "pratyantardashas": _build_pratyantardashas(
                    start_jd,
                    end_jd,
                    md_years,
                    years[lord],
                    lord,
                    order,
                    years,
                    total_years,
                ),
            }
        )
    return periods


def _active_period(periods: list[dict[str, Any]], at_utc: dt.datetime) -> dict[str, Any]:
    for period in periods:
        if _parse_iso_utc(period["start"]) <= at_utc < _parse_iso_utc(period["end"]):
            return period
    raise RuntimeError(f"활성 다샤 구간을 찾지 못함: {at_utc.isoformat()}")


def _period_summary(period: dict[str, Any]) -> dict[str, str]:
    return {"lord": period["lord"], "start": period["start"], "end": period["end"]}


def build_vimshottari_dasha(
    birth_utc: dt.datetime,
    moon_longitude: float,
    constants: dict[str, Any],
) -> dict[str, Any]:
    """출생 시점을 포함하는 120년 MD→AD→PD 전체 주기를 JSON형 dict로 만든다."""
    _require_utc(birth_utc)
    order, years, total_years, nakshatra_count = _vimshottari_config(constants)
    nakshatra, elapsed_fraction, remaining_fraction = nakshatra_progress(
        moon_longitude, nakshatra_count
    )
    starting_lord = order[(nakshatra - 1) % len(order)]

    birth_jd = julian_day_ut(birth_utc)
    elapsed_angle = years[starting_lord] * elapsed_fraction * Decimal(360)
    cycle_start_jd = solve_sidereal_solar_progress(birth_jd, -float(elapsed_angle))

    md_order = _rotated_order(order, starting_lord)
    md_angles = [years[lord] * Decimal(360) for lord in md_order]
    md_boundaries = _period_boundaries(cycle_start_jd, md_angles)
    cycle_end_jd = md_boundaries[-1]

    timeline: list[dict[str, Any]] = []
    for index, lord in enumerate(md_order):
        start_jd, end_jd = md_boundaries[index], md_boundaries[index + 1]
        timeline.append(
            {
                "lord": lord,
                "start": _iso_utc_from_jd(start_jd),
                "end": _iso_utc_from_jd(end_jd),
                "duration_days": _duration_days(start_jd, end_jd),
                "antardashas": _build_antardashas(
                    start_jd,
                    end_jd,
                    lord,
                    order,
                    years,
                    total_years,
                ),
            }
        )

    birth_md = _active_period(timeline, birth_utc)
    birth_ad = _active_period(birth_md["antardashas"], birth_utc)
    birth_pd = _active_period(birth_ad["pratyantardashas"], birth_utc)
    balance_end = _parse_iso_utc(birth_md["end"])
    balance_days = (balance_end - birth_utc).total_seconds() / 86400.0

    return {
        "system": "vimshottari",
        "birth_datetime_utc": birth_utc.isoformat().replace("+00:00", "Z"),
        "moon_longitude": moon_longitude % _FULL_CIRCLE_DEG,
        "birth_nakshatra_index": nakshatra,
        "starting_dasha": starting_lord,
        "date_basis": {"year_definition": "true_sidereal_solar_year"},
        "cycle": {
            "start": _iso_utc_from_jd(cycle_start_jd),
            "end": _iso_utc_from_jd(cycle_end_jd),
            "total_years": float(total_years),
        },
        "balance_at_birth": {
            "elapsed_fraction": float(elapsed_fraction),
            "remaining_fraction": float(remaining_fraction),
            "remaining_years": float(years[starting_lord] * remaining_fraction),
            "remaining_days": balance_days,
            "end": birth_md["end"],
        },
        "birth_periods": {
            "mahadasha": _period_summary(birth_md),
            "antardasha": _period_summary(birth_ad),
            "pratyantardasha": _period_summary(birth_pd),
        },
        "full_timeline": timeline,
    }
