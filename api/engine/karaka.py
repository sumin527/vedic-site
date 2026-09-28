"""차라 카라카 계산. 7단계.

``constants.yaml``의 karaka 계약에 따라 지정된 행성의 사인 내 경도를
초 단위로 비교하고, 내림차순 순위를 카라카 역할에 대응한다. 이 모듈은
계산 결과만 반환하며 해석이나 판정을 만들지 않는다.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Mapping

_ARCSECONDS_PER_DEGREE = 3600.0


class KarakaConfigurationError(ValueError):
    """constants의 카라카 계약이 구현과 맞지 않을 때 발생한다."""


def degree_in_sign(longitude: float, sign_span_deg: float) -> float:
    """경도를 0° 이상 30° 미만의 사인 내 경도로 변환한다."""
    if isinstance(longitude, bool) or not isinstance(longitude, (int, float)):
        raise TypeError("longitude must be a finite number")
    value = float(longitude)
    if not math.isfinite(value):
        raise ValueError("longitude must be a finite number")
    if not math.isfinite(sign_span_deg) or sign_span_deg <= 0.0:
        raise ValueError("sign_span_deg must be a positive finite number")
    return value % sign_span_deg


def _comparison_seconds(value: float) -> float:
    """반올림하지 않은 사인 내 경도를 초 단위로 변환한다."""
    return value * _ARCSECONDS_PER_DEGREE


def _karaka_contract(constants: dict[str, Any]) -> tuple[
    dict[str, Any], tuple[str, ...], tuple[str, ...]
]:
    config = constants["karaka"]
    roles = tuple(config["order"])
    planets = tuple(config["sort_planets"])
    scheme = int(config["scheme"])

    if scheme != len(roles) or scheme != len(planets):
        raise KarakaConfigurationError(
            "karaka.scheme과 order·sort_planets 항목 수가 일치해야 함"
        )
    if len(set(roles)) != len(roles):
        raise KarakaConfigurationError("karaka.order 역할은 중복될 수 없음")
    if len(set(planets)) != len(planets):
        raise KarakaConfigurationError("karaka.sort_planets 행성은 중복될 수 없음")
    if config["sort_key"] != "descending_degree_in_sign":
        raise NotImplementedError(f"지원하지 않는 karaka.sort_key: {config['sort_key']}")
    if config["tie_precision"] != "seconds":
        raise NotImplementedError(
            f"지원하지 않는 karaka.tie_precision: {config['tie_precision']}"
        )
    if config["tie_behavior"] != "emit_warning_do_not_reorder":
        raise NotImplementedError(
            f"지원하지 않는 karaka.tie_behavior: {config['tie_behavior']}"
        )
    return config, roles, planets


def calculate_chara_karakas(
    longitudes: Mapping[str, float],
    constants: dict[str, Any],
) -> dict[str, Any]:
    """사인 내 경도 내림차순으로 차라 카라카 역할을 배정한다.

    초 단위 비교값이 같으면 ``karaka.sort_planets``의 기존 순서를 유지하고
    해당 순위 구간을 ``tie_warning``에 기록한다. 라후 등 목록 밖 입력은
    계산에 사용하지 않는다.
    """
    config, roles, planets = _karaka_contract(constants)
    missing = [planet for planet in planets if planet not in longitudes]
    if missing:
        raise ValueError(f"차라 카라카 경도 누락: {missing}")

    sign_span_deg = float(constants["varga"]["d1"]["size_deg"])
    degrees = {
        planet: degree_in_sign(longitudes[planet], sign_span_deg)
        for planet in planets
    }
    seconds = {
        planet: _comparison_seconds(degrees[planet]) for planet in planets
    }

    # Python 정렬은 안정 정렬이다. 동률에서는 constants의 sort_planets 순서를
    # 그대로 보존하므로 임의의 추가 타이브레이커가 개입하지 않는다.
    ranked_planets = sorted(planets, key=seconds.__getitem__, reverse=True)
    assignments = dict(zip(roles, ranked_planets, strict=True))

    tied_planets_by_second: dict[float, list[str]] = defaultdict(list)
    for planet in ranked_planets:
        tied_planets_by_second[seconds[planet]].append(planet)

    tie_warning: list[dict[str, Any]] = []
    for comparison_second, tied_planets in tied_planets_by_second.items():
        if len(tied_planets) < 2:
            continue
        tied_set = set(tied_planets)
        tied_roles = [
            role for role in roles if assignments[role] in tied_set
        ]
        tie_warning.append(
            {
                "comparison_second": comparison_second,
                "planets": tied_planets,
                "roles": tied_roles,
            }
        )

    return {
        "scheme": int(config["scheme"]),
        "scheme_variant": config["scheme_variant"],
        **assignments,
        "career_indicator": config["career_indicator"],
        "degree_in_sign": degrees,
        "comparison_seconds": seconds,
        "ranked_planets": ranked_planets,
        "tie_warning": tie_warning,
    }
