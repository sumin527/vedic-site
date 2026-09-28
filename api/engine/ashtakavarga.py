"""원 BAV와 SAV 계산. 6단계.

BAV 기여표는 ``constants.yaml``의 ``ashtakavarga.bav_contributions``를
단일 진실 공급원으로 사용한다. 각 칸은 기여 기준점의 사인에서 세는
1-based 상대 하우스 번호 목록이다. 라후·케투는 ``bav_excluded`` 계약에
따라 기여자에서 제외한다.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

_SIGN_COUNT = 12
_POINT_CONVENTION = "benefic_point"


class ContributionTableError(ValueError):
    """BAV 기여표의 구조·값이 계산 계약과 맞지 않을 때 발생한다."""


class IncompleteContributionTableError(ContributionTableError):
    """아직 입력되지 않은 기여표 칸이 있을 때 발생한다."""


class AshtakavargaChecksumError(ValueError):
    """BAV 또는 SAV 체크섬이 constants 계약과 다를 때 발생한다."""


def relative_house_to_sign(source_sign: int, relative_house: int) -> int:
    """기준 사인과 상대 하우스로 1-based 대상 사인을 산출한다."""
    if (
        isinstance(source_sign, bool)
        or not isinstance(source_sign, int)
        or not 1 <= source_sign <= _SIGN_COUNT
    ):
        raise ValueError("source_sign must be an integer from 1 through 12")
    if (
        isinstance(relative_house, bool)
        or not isinstance(relative_house, int)
        or not 1 <= relative_house <= _SIGN_COUNT
    ):
        raise ValueError("relative_house must be an integer from 1 through 12")
    return (source_sign - 1 + relative_house - 1) % _SIGN_COUNT + 1


def _ashtakavarga_contract(constants: dict[str, Any]) -> tuple[
    tuple[str, ...], tuple[str, ...], tuple[str, ...], dict[str, int], int
]:
    config = constants["ashtakavarga"]
    targets = tuple(config["bav_checksums"])
    contributors = tuple(config["bav_contributors"])
    excluded = tuple(config["bav_excluded"])
    sav_sources = tuple(config["sav_summed_from"])
    expected_checksums = {
        target: int(value) for target, value in config["bav_checksums"].items()
    }
    expected_sav_checksum = int(config["sav_checksum"])

    if set(contributors) & set(excluded):
        raise ValueError("bav_contributors와 bav_excluded가 겹치면 안 됨")
    if set(targets) != set(sav_sources):
        raise ValueError("BAV 대상 행성과 SAV 합산 행성이 일치해야 함")
    if sum(expected_checksums.values()) != expected_sav_checksum:
        raise ValueError("행성별 BAV 체크섬 합이 SAV 체크섬과 일치해야 함")
    return targets, contributors, excluded, expected_checksums, expected_sav_checksum


def load_benefic_point_contributions(
    constants: dict[str, Any],
) -> dict[str, dict[str, tuple[int, ...]]]:
    """constants의 실제 기여표를 읽고 구조·완전성·체크섬을 검증한다."""
    config = constants["ashtakavarga"]
    convention = config.get("point_convention", {}).get("engine", "")
    if not convention.startswith(_POINT_CONVENTION):
        raise ContributionTableError("point_convention은 benefic_point여야 함")
    return validate_benefic_point_contributions(
        config.get("bav_contributions"), constants
    )


def validate_benefic_point_contributions(
    raw_table: Mapping[str, Any],
    constants: dict[str, Any],
) -> dict[str, dict[str, tuple[int, ...]]]:
    """기여표 56칸을 정규화하고 행성별 정적 체크섬을 검증한다."""
    targets, contributors, excluded, expected_checksums, _expected_sav = (
        _ashtakavarga_contract(constants)
    )
    if not isinstance(raw_table, Mapping):
        raise ContributionTableError("bav_contributions 매핑이 필요함")
    if set(raw_table) != set(targets):
        raise ContributionTableError(
            f"BAV 대상은 정확히 {list(targets)}여야 함: actual={list(raw_table)}"
        )

    normalized: dict[str, dict[str, tuple[int, ...]]] = {}
    incomplete: list[str] = []
    for target in targets:
        raw_row = raw_table[target]
        if not isinstance(raw_row, Mapping):
            raise ContributionTableError(f"{target} 행은 매핑이어야 함")
        if set(raw_row) & set(excluded):
            raise ContributionTableError(
                f"{target} 행에 제외 기여자가 포함됨: "
                f"{sorted(set(raw_row) & set(excluded))}"
            )
        if set(raw_row) != set(contributors):
            raise ContributionTableError(
                f"{target} 기여자는 정확히 {list(contributors)}여야 함: "
                f"actual={list(raw_row)}"
            )

        normalized_row: dict[str, tuple[int, ...]] = {}
        for contributor in contributors:
            raw_houses = raw_row[contributor]
            path_label = f"{target}.{contributor}"
            if raw_houses is None:
                incomplete.append(path_label)
                continue
            if not isinstance(raw_houses, Sequence) or isinstance(
                raw_houses, (str, bytes)
            ):
                raise ContributionTableError(
                    f"{path_label}는 상대 하우스 목록이어야 함"
                )
            houses = tuple(raw_houses)
            if any(
                isinstance(house, bool)
                or not isinstance(house, int)
                or not 1 <= house <= _SIGN_COUNT
                for house in houses
            ):
                raise ContributionTableError(f"{path_label} 값은 1~12 정수여야 함")
            if len(set(houses)) != len(houses):
                raise ContributionTableError(f"{path_label}에 중복 상대 하우스가 있음")
            normalized_row[contributor] = houses
        normalized[target] = normalized_row

    if incomplete:
        preview = ", ".join(incomplete[:8])
        suffix = " ..." if len(incomplete) > 8 else ""
        raise IncompleteContributionTableError(
            f"BAV 기여표 미입력 {len(incomplete)}칸: {preview}{suffix}"
        )

    validate_contribution_checksums(normalized, expected_checksums)
    return normalized


def contribution_checksums(
    table: Mapping[str, Mapping[str, Sequence[int]]],
) -> dict[str, int]:
    """대상 행성별 benefic_point 기여 개수 합을 반환한다."""
    return {
        target: sum(len(houses) for houses in row.values())
        for target, row in table.items()
    }


def validate_contribution_checksums(
    table: Mapping[str, Mapping[str, Sequence[int]]],
    expected_checksums: Mapping[str, int],
) -> dict[str, int]:
    """기여표 체크섬이 48/49/39/54/56/52/39인지 확인한다."""
    actual = contribution_checksums(table)
    if actual != dict(expected_checksums):
        raise AshtakavargaChecksumError(
            f"BAV 기여표 체크섬 불일치: expected={dict(expected_checksums)}, "
            f"actual={actual}"
        )
    return actual


def calculate_bav(
    source_signs: Mapping[str, int],
    constants: dict[str, Any],
    *,
    contribution_table: Mapping[str, Mapping[str, Sequence[int]]] | None = None,
) -> dict[str, list[int]]:
    """constants의 실제 표로 7개 행성의 12사인 원 BAV를 계산한다."""
    targets, contributors, _excluded, expected_checksums, _expected_sav = (
        _ashtakavarga_contract(constants)
    )
    missing = [
        contributor for contributor in contributors if contributor not in source_signs
    ]
    if missing:
        raise ValueError(f"BAV 기준점 사인 누락: {missing}")
    for contributor in contributors:
        source_sign = source_signs[contributor]
        if (
            isinstance(source_sign, bool)
            or not isinstance(source_sign, int)
            or not 1 <= source_sign <= _SIGN_COUNT
        ):
            raise ValueError(f"{contributor} sign은 1~12 정수여야 함")

    if contribution_table is None:
        table = load_benefic_point_contributions(constants)
    else:
        table = validate_benefic_point_contributions(contribution_table, constants)

    result: dict[str, list[int]] = {}
    for target in targets:
        row = table[target]
        benefic_point_counts = [0] * _SIGN_COUNT
        for contributor in contributors:
            source_sign = source_signs[contributor]
            for relative_house in row[contributor]:
                target_sign = relative_house_to_sign(source_sign, relative_house)
                benefic_point_counts[target_sign - 1] += 1
        if any(
            not 0 <= count <= len(contributors) for count in benefic_point_counts
        ):
            raise AssertionError("BAV 사인별 benefic_point는 0~8이어야 함")
        if sum(benefic_point_counts) != expected_checksums[target]:
            raise AshtakavargaChecksumError(
                f"{target} BAV 체크섬 불일치: "
                f"expected={expected_checksums[target]}, "
                f"actual={sum(benefic_point_counts)}"
            )
        result[target] = benefic_point_counts
    return result


def calculate_sav(
    all_bav: Mapping[str, Sequence[int]],
    constants: dict[str, Any],
) -> list[int]:
    """constants가 지정한 7개 행성 BAV만 합산해 SAV를 계산한다."""
    config = constants["ashtakavarga"]
    sources = tuple(config["sav_summed_from"])
    expected_checksums = config["bav_checksums"]
    expected_sav_checksum = int(config["sav_checksum"])
    if set(all_bav) != set(sources):
        raise ValueError(f"SAV는 정확히 {list(sources)} BAV가 필요함")

    rows: dict[str, tuple[int, ...]] = {}
    for source in sources:
        row = tuple(all_bav[source])
        if len(row) != _SIGN_COUNT:
            raise ValueError(f"{source} BAV는 12개 사인 값이어야 함")
        if any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= 8
            for value in row
        ):
            raise ValueError(f"{source} BAV 값은 0~8 정수여야 함")
        if sum(row) != int(expected_checksums[source]):
            raise AshtakavargaChecksumError(
                f"{source} BAV 체크섬 불일치: "
                f"expected={expected_checksums[source]}, actual={sum(row)}"
            )
        rows[source] = row

    sav = [
        sum(rows[source][index] for source in sources)
        for index in range(_SIGN_COUNT)
    ]
    if sum(sav) != expected_sav_checksum:
        raise AshtakavargaChecksumError(
            f"SAV 체크섬 불일치: expected={expected_sav_checksum}, actual={sum(sav)}"
        )
    return sav


def calculate_ashtakavarga(
    source_signs: Mapping[str, int],
    constants: dict[str, Any],
) -> dict[str, Any]:
    """constants의 실제 표로 원 BAV와 SAV를 함께 계산한다."""
    all_bav = calculate_bav(source_signs, constants)
    sav = calculate_sav(all_bav, constants)
    return {
        "point_convention": _POINT_CONVENTION,
        "bav": all_bav,
        "sav": sav,
        "checksums": {
            "bav": {target: sum(row) for target, row in all_bav.items()},
            "sav": sum(sav),
        },
    }
