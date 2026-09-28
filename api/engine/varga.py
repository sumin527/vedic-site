"""파라샤리 바르가 분할 계산. 5단계.

계산 규칙은 전부 ``constants.yaml.varga`` 에서 읽는다. 균등 분할은
``start_mode``(relative/absolute), ``start_rule``, ``step``을 공통 경로로
해석한다. D2(호라), D30(불균등 트림샴샤), D60(샤슈티암샤)은 서로 다른
산식을 가지므로 공통 경로에 넣지 않는다.
"""
from __future__ import annotations

import math
import re
from typing import Any, Mapping

_SIGN_COUNT = 12
_SIGN_SPAN_DEG = 30.0
_VARGA_NAME_PATTERN = re.compile(r"d\d+")


def _validate_position(sign: int, degree_in_sign: float) -> None:
    if isinstance(sign, bool) or not isinstance(sign, int) or not 1 <= sign <= 12:
        raise ValueError("sign must be an integer from 1 through 12")
    if not math.isfinite(degree_in_sign) or not 0.0 <= degree_in_sign < 30.0:
        raise ValueError("degree_in_sign must be finite and in [0, 30)")


def _division_index(degree_in_sign: float, divisions: int) -> int:
    """균등 분할의 1-based 인덱스.

    D7·D9의 ``size_deg``는 표시상 절단된 반복소수이므로 경계 계산에는
    ``floor(degree * divisions / 30)``의 정수 곱셈형을 사용한다.
    """
    index_zero_based = math.floor(degree_in_sign * divisions / _SIGN_SPAN_DEG)
    assert 0 <= index_zero_based < divisions, (
        f"varga division index 범위 초과: degree={degree_in_sign} "
        f"divisions={divisions} idx={index_zero_based}"
    )
    return index_zero_based + 1


def _sign_quality(sign: int, varga_config: dict[str, Any]) -> str:
    qualities = varga_config["sign_quality"]
    matches = [name for name in ("movable", "fixed", "dual") if sign in qualities[name]]
    if len(matches) != 1:
        raise ValueError(f"varga.sign_quality가 sign={sign}을 정확히 한 번 분류해야 함")
    return matches[0]


def _rule_branch(
    sign: int,
    start_rule: dict[str, int],
    varga_config: dict[str, Any],
) -> str:
    if "all" in start_rule:
        return "all"
    if "odd" in start_rule or "even" in start_rule:
        branch = "odd" if sign % 2 else "even"
        if branch not in start_rule:
            raise ValueError(f"start_rule에 {branch!r} 분기가 없음")
        return branch
    branch = _sign_quality(sign, varga_config)
    if branch not in start_rule:
        raise ValueError(f"start_rule에 {branch!r} 분기가 없음")
    return branch


def _calculate_equal_varga(
    sign: int,
    degree_in_sign: float,
    name: str,
    block: dict[str, Any],
    varga_config: dict[str, Any],
) -> dict[str, Any]:
    divisions = int(block["divisions"])
    division_index = _division_index(degree_in_sign, divisions)
    start_rule = block["start_rule"]
    branch = _rule_branch(sign, start_rule, varga_config)
    start_mode = block["start_mode"]
    step = int(block.get("step", 1))

    if start_mode == "relative":
        base_zero_based = sign - 1 + int(start_rule[branch])
    elif start_mode == "absolute":
        base_zero_based = int(start_rule[branch]) - 1
    else:
        raise ValueError(f"{name}.start_mode는 relative 또는 absolute여야 함")

    result_sign = (
        base_zero_based + (division_index - 1) * step
    ) % _SIGN_COUNT + 1
    return {
        "varga": name,
        "input_sign": sign,
        "degree_in_sign": degree_in_sign,
        "division_index": division_index,
        "result_sign": result_sign,
        "branch": branch,
        "start_mode": start_mode,
        "step": step,
    }


def _calculate_d2(
    sign: int,
    degree_in_sign: float,
    block: dict[str, Any],
) -> dict[str, Any]:
    """호라: 결과 사인보다 먼저 태양·달 지배를 산출한다."""
    division_index = _division_index(degree_in_sign, int(block["divisions"]))
    branch = "odd_sign" if sign % 2 else "even_sign"
    lords = block["lord_rule"][branch]
    if len(lords) != int(block["divisions"]):
        raise ValueError(f"d2.{branch} lord 수가 divisions와 일치하지 않음")
    lord = lords[division_index - 1]
    result_sign = int(block["sign_map"][lord])
    return {
        "varga": "d2",
        "input_sign": sign,
        "degree_in_sign": degree_in_sign,
        "division_index": division_index,
        "lord": lord,
        "result_sign": result_sign,
        "branch": branch,
    }


def _calculate_d30(
    sign: int,
    degree_in_sign: float,
    block: dict[str, Any],
) -> dict[str, Any]:
    """트림샴샤: 홀·짝 사인의 불균등 누적 구간으로 판정한다."""
    branch = "odd" if sign % 2 else "even"
    bands = block[f"{branch}_bands"]
    cumulative_end = 0.0
    for index_zero_based, (width, result_sign) in enumerate(bands):
        cumulative_end += float(width)
        if degree_in_sign < cumulative_end:
            return {
                "varga": "d30",
                "input_sign": sign,
                "degree_in_sign": degree_in_sign,
                "division_index": index_zero_based + 1,
                "result_sign": int(result_sign),
                "branch": branch,
                "band_start_deg": cumulative_end - float(width),
                "band_end_deg": cumulative_end,
            }
    raise ValueError(f"d30 구간이 degree={degree_in_sign}을 포함하지 않음")


def _calculate_d60(
    sign: int,
    degree_in_sign: float,
    block: dict[str, Any],
) -> dict[str, Any]:
    """샤슈티암샤 전용식: floor(degree*2)의 12 나머지를 원 사인에 더한다."""
    doubled_floor = math.floor(degree_in_sign * 2.0)
    division_index = doubled_floor + 1
    remainder = doubled_floor % _SIGN_COUNT
    result_sign = (sign - 1 + remainder) % _SIGN_COUNT + 1
    assert 1 <= division_index <= int(block["divisions"])
    return {
        "varga": "d60",
        "input_sign": sign,
        "degree_in_sign": degree_in_sign,
        "division_index": division_index,
        "result_sign": result_sign,
        "remainder": remainder,
        "branch": "odd_even_same",
    }


def calculate_varga(
    sign: int,
    degree_in_sign: float,
    name: str,
    constants: dict[str, Any],
) -> dict[str, Any]:
    """사인 번호와 사인 내 도수로 한 바르가 위치를 계산한다."""
    _validate_position(sign, degree_in_sign)
    normalized_name = name.lower()
    if not _VARGA_NAME_PATTERN.fullmatch(normalized_name):
        raise ValueError(f"잘못된 varga 이름: {name!r}")

    varga_config = constants["varga"]
    block = varga_config.get(normalized_name)
    if not isinstance(block, dict):
        raise ValueError(f"constants.yaml에 varga.{normalized_name} 규칙이 없음")

    if normalized_name == "d2":
        return _calculate_d2(sign, degree_in_sign, block)
    if normalized_name == "d30":
        return _calculate_d30(sign, degree_in_sign, block)
    if normalized_name == "d60":
        return _calculate_d60(sign, degree_in_sign, block)
    if block.get("special") or block.get("unequal"):
        raise ValueError(f"미지원 특수 varga 규칙: {normalized_name}")
    return _calculate_equal_varga(
        sign, degree_in_sign, normalized_name, block, varga_config
    )


def calculate_varga_from_longitude(
    longitude: float,
    name: str,
    constants: dict[str, Any],
) -> dict[str, Any]:
    """0~360° 사이데리얼 경도에서 한 바르가 위치를 계산한다."""
    if not math.isfinite(longitude):
        raise ValueError("longitude must be finite")
    normalized = longitude % 360.0
    sign_zero_based = math.floor(normalized * _SIGN_COUNT / 360.0)
    assert 0 <= sign_zero_based < _SIGN_COUNT
    degree_in_sign = normalized - sign_zero_based * _SIGN_SPAN_DEG
    return calculate_varga(sign_zero_based + 1, degree_in_sign, name, constants)


def calculate_dasavarga(
    longitude: float,
    constants: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """constants.yaml에 채택된 다샤바르가 10개를 한 경도에 대해 계산한다."""
    names = constants["varga"]["dasavarga_set"]
    if len(names) != 10 or len(set(names)) != 10:
        raise ValueError("varga.dasavarga_set은 중복 없는 10개 분할이어야 함")
    return {
        name: calculate_varga_from_longitude(longitude, name, constants)
        for name in names
    }


def calculate_dasavarga_positions(
    longitudes: Mapping[str, float],
    constants: dict[str, Any],
) -> dict[str, dict[str, dict[str, Any]]]:
    """행성명→경도 입력을 행성명→다샤바르가 결과로 변환한다."""
    return {
        body: calculate_dasavarga(longitude, constants)
        for body, longitude in longitudes.items()
    }
