"""계산 엔진의 2계층 JSON 출력. 4·8단계.

``judgment_input``은 판정층이 소비하는 전체 계산값을 보존한다.
``narration_input``은 결론 코드와 근거 위치만 담아, 내부 점수·등급·계열
개수가 서술층으로 넘어갈 경로를 구조적으로 차단한다.
"""
from __future__ import annotations

import datetime as dt
import json
import math
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from engine.ashtakavarga import calculate_ashtakavarga
from engine.dasha import build_vimshottari_dasha
from engine.ephemeris import (
    BODY_IDS,
    ascendant_longitude,
    init_sidereal_lahiri,
    julian_day_ut,
    rahu_ketu_longitudes,
    sidereal_longitude,
)
from engine.karaka import calculate_chara_karakas, degree_in_sign
from engine.meta import build_engine_meta
from engine.positions import (
    nakshatra_index,
    pada_index,
    sign_index,
    whole_sign_house,
)
from engine.varga import calculate_dasavarga_positions


class MandatoryDashaValidationError(RuntimeError):
    """4단계 마하다샤-달 낙샤트라 로드 검산이 실패했을 때 발생한다."""


class OutputContractError(ValueError):
    """8단계 출력 입력이 두 계층의 계약을 충족하지 못할 때 발생한다."""


def validate_mandatory_dasha_lord(
    mahadasha_lord: str,
    moon_nakshatra_lord: str,
) -> dict[str, Any]:
    """두 로드가 같지 않으면 결과 생성을 중단한다."""
    if mahadasha_lord != moon_nakshatra_lord:
        raise MandatoryDashaValidationError(
            "출생 마하다샤 로드와 달 낙샤트라 로드 불일치: "
            f"mahadasha={mahadasha_lord!r}, moon_nakshatra={moon_nakshatra_lord!r}"
        )
    return {
        "check": "mahadasha_lord_equals_moon_nakshatra_lord",
        "passed": True,
        "mahadasha_lord": mahadasha_lord,
        "moon_nakshatra_lord": moon_nakshatra_lord,
    }


def _validate_coordinates(latitude: float, longitude: float) -> None:
    if not math.isfinite(latitude) or not -90.0 <= latitude <= 90.0:
        raise ValueError("latitude must be finite and in [-90, 90]")
    if not math.isfinite(longitude) or not -180.0 <= longitude <= 180.0:
        raise ValueError("longitude must be finite and in [-180, 180]")


def _nakshatra_lord(index: int, constants: dict[str, Any]) -> str:
    order = constants["timing"]["vimshottari"]["order"]
    return order[(index - 1) % len(order)]


def _position_record(
    longitude: float,
    lagna_sign: int,
    chandra_lagna_sign: int,
    constants: dict[str, Any],
) -> dict[str, Any]:
    nakshatra_count = int(constants["arithmetic"]["nakshatra_count"])
    pada_count_total = int(constants["arithmetic"]["pada_count_total"])
    sign_span_deg = float(constants["varga"]["d1"]["size_deg"])
    sign = sign_index(longitude)
    nakshatra = nakshatra_index(longitude, nakshatra_count)
    return {
        "longitude_deg": longitude % (sign_span_deg * 12),
        "sign": sign,
        "degree_in_sign": degree_in_sign(longitude, sign_span_deg),
        "nakshatra": nakshatra,
        "nakshatra_lord": _nakshatra_lord(nakshatra, constants),
        "pada": pada_index(longitude, pada_count_total),
        "whole_sign_houses": {
            "lagna": whole_sign_house(sign, lagna_sign),
            "chandra_lagna": whole_sign_house(sign, chandra_lagna_sign),
        },
    }


def _reference_points(
    ascendant: dict[str, Any],
    moon: dict[str, Any],
    constants: dict[str, Any],
) -> dict[str, Any]:
    policy = constants["evidence_policy"]["reference_points"]
    if not policy["both_written"]:
        raise OutputContractError("reference_points.both_written는 true여야 함")
    return {
        "policy": {
            "primary": policy["primary"],
            "secondary": policy["secondary"],
            "both_written": policy["both_written"],
            "tier_counting": policy["tier_counting"],
        },
        "lagna": {
            "longitude_deg": ascendant["longitude_deg"],
            "sign": ascendant["sign"],
        },
        "chandra_lagna": {
            "longitude_deg": moon["longitude_deg"],
            "sign": moon["sign"],
        },
    }


def _normalize_conclusions(
    conclusions: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    if conclusions is None:
        return []
    if isinstance(conclusions, (str, bytes)) or not isinstance(conclusions, Sequence):
        raise OutputContractError("conclusions는 결론 매핑 목록이어야 함")

    normalized: list[dict[str, Any]] = []
    for index, conclusion in enumerate(conclusions):
        if not isinstance(conclusion, Mapping):
            raise OutputContractError(f"conclusions[{index}]는 매핑이어야 함")
        code = conclusion.get("code")
        locations = conclusion.get("evidence_locations")
        if not isinstance(code, str) or not code:
            raise OutputContractError(f"conclusions[{index}].code가 필요함")
        if (
            isinstance(locations, (str, bytes))
            or not isinstance(locations, Sequence)
            or not locations
            or any(not isinstance(location, str) or not location for location in locations)
        ):
            raise OutputContractError(
                f"conclusions[{index}].evidence_locations는 비어 있지 않은 문자열 목록이어야 함"
            )
        normalized.append(deepcopy(dict(conclusion)))
    return normalized


def build_narration_input(
    conclusions: Sequence[Mapping[str, Any]] | None,
) -> dict[str, list[dict[str, Any]]]:
    """결론 ID·코드·근거 위치 외의 모든 판정 필드를 제거한다."""
    normalized = _normalize_conclusions(conclusions)
    projected = []
    for index, conclusion in enumerate(normalized):
        item = {
            "code": conclusion["code"],
            "evidence_locations": list(conclusion["evidence_locations"]),
        }
        if "id" in conclusion:
            if not isinstance(conclusion["id"], str) or not conclusion["id"]:
                raise OutputContractError(f"conclusions[{index}].id는 문자열이어야 함")
            item = {"id": conclusion["id"], **item}
        projected.append(item)
    return {"conclusions": projected}


def build_engine_output(
    birth_utc: dt.datetime,
    latitude: float,
    longitude: float,
    ephe_path: str | Path,
    constants: dict[str, Any],
    conclusions: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """1~8단계 계산을 실행하고 판정층·서술층 JSON 입력을 반환한다."""
    if birth_utc.tzinfo is None or birth_utc.utcoffset() != dt.timedelta(0):
        raise ValueError("birth_utc must be timezone-aware UTC")
    _validate_coordinates(latitude, longitude)
    normalized_conclusions = _normalize_conclusions(conclusions)

    ephe_dir = Path(ephe_path)
    init_sidereal_lahiri(str(ephe_dir))
    jd_ut = julian_day_ut(birth_utc)

    longitudes = {
        name: sidereal_longitude(jd_ut, body_id)
        for name, body_id in BODY_IDS.items()
    }
    node_type = constants["arithmetic"]["ephemeris"]["node_type"]
    rahu, ketu = rahu_ketu_longitudes(jd_ut, node_type)
    longitudes.update({"rahu": rahu, "ketu": ketu})
    ascendant_longitude_deg = ascendant_longitude(
        jd_ut, latitude, longitude
    )

    lagna_sign = sign_index(ascendant_longitude_deg)
    chandra_lagna_sign = sign_index(longitudes["moon"])
    planet_records = {
        name: _position_record(
            value, lagna_sign, chandra_lagna_sign, constants
        )
        for name, value in longitudes.items()
    }
    ascendant_record = _position_record(
        ascendant_longitude_deg, lagna_sign, chandra_lagna_sign, constants
    )

    all_varga_longitudes = {**longitudes, "ascendant": ascendant_longitude_deg}
    dasha = build_vimshottari_dasha(
        birth_utc, longitudes["moon"], constants
    )
    mandatory_check = validate_mandatory_dasha_lord(
        dasha["birth_periods"]["mahadasha"]["lord"],
        planet_records["moon"]["nakshatra_lord"],
    )

    bav_source_signs = {
        name: planet_records[name]["sign"]
        for name in constants["ashtakavarga"]["bav_contributors"]
        if name != "lagna"
    }
    bav_source_signs["lagna"] = lagna_sign

    judgment_input = {
        "input": {
            "birth_datetime_utc": birth_utc.isoformat().replace("+00:00", "Z"),
            "latitude": latitude,
            "longitude": longitude,
        },
        "engine_meta": build_engine_meta(constants, ephe_dir),
        "mandatory_checks": [mandatory_check],
        "chart": {
            "planets": planet_records,
            "ascendant": ascendant_record,
            "reference_points": _reference_points(
                ascendant_record, planet_records["moon"], constants
            ),
        },
        "varga": {
            "set": list(constants["varga"]["dasavarga_set"]),
            "positions": calculate_dasavarga_positions(
                all_varga_longitudes, constants
            ),
        },
        "dasha": dasha,
        "ashtakavarga": calculate_ashtakavarga(bav_source_signs, constants),
        "karaka": calculate_chara_karakas(longitudes, constants),
        "conclusions": normalized_conclusions,
    }

    result = {
        "schema_version": 1,
        "judgment_input": judgment_input,
        "narration_input": build_narration_input(normalized_conclusions),
    }
    # 반환 직전 직렬화 가능성을 강제한다. 실패하면 부분 JSON을 쓰지 않는다.
    json.dumps(result, ensure_ascii=False, allow_nan=False)
    return result


def write_engine_output(path: str | Path, result: Mapping[str, Any]) -> None:
    """검증된 엔진 결과를 UTF-8 JSON 파일로 기록한다."""
    serialized = json.dumps(
        result, ensure_ascii=False, indent=2, allow_nan=False
    )
    Path(path).write_text(serialized + "\n", encoding="utf-8")
