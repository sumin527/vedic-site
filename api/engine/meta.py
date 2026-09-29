"""engine_meta 블록 — 리포트 재현성 추적용.

constants.yaml arithmetic.ephemeris.reproducibility_note: backend·ayanamsa_constant·
node_type 이 바뀌면 다샤 경계와 라후·케투 배치가 달라져 과거 리포트를 재현할 수
없다. 발행 리포트 푸터에 이 값들을 기록한다. position_mode·swisseph_flags 도
같은 이유로 기록한다 — apparent/true 전환은 수십 각초 차이를 낸다.

node_type 은 ISSUE-009 확정(mean)이므로 provisional 폴백 로직은 없다 —
constants.yaml 이 결정 안 된 값을 null 로 두는 경우는 이제 없다는 전제다.

ephemeris_sha256 은 constants.yaml 의 값이 아니라 실제 사용된 de430.bsp 파일을 직접
해시한 값이다 — "설정값"이 아니라 "무엇이 실제로 계산에 쓰였는지"를 기록한다.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from engine.ephemeris import ASCENDANT_METHOD, describe_backend_flags

_EPHE_FILES = ("de430.bsp",)


def build_engine_meta(constants: dict, ephe_path: str | Path) -> dict:
    ephe_cfg = constants["arithmetic"]["ephemeris"]
    configured_ascendant_method = constants["arithmetic"]["ascendant_method"]
    if configured_ascendant_method != ASCENDANT_METHOD:
        raise ValueError(
            "constants.yaml arithmetic.ascendant_method와 실제 엔진 구현이 불일치: "
            f"configured={configured_ascendant_method!r}, implemented={ASCENDANT_METHOD!r}"
        )

    ephe_dir = Path(ephe_path)
    ephemeris_sha256 = {
        name: hashlib.sha256((ephe_dir / name).read_bytes()).hexdigest() for name in _EPHE_FILES
    }

    return {
        "backend": ephe_cfg["backend"],
        "ayanamsa_constant": ephe_cfg["ayanamsa_constant"],
        "node_type": ephe_cfg["node_type"],
        "position_mode": ephe_cfg["position_mode"],
        "backend_flags": describe_backend_flags(),
        "ascendant_method": ASCENDANT_METHOD,
        "ephemeris_sha256": ephemeris_sha256,
        "constants_version": constants["meta"]["version"],
    }
