"""사인 / 낙샤트라 / 파다 / 홀사인 하우스. 2단계.

constants.yaml arithmetic.index_formula: 인덱스는 정수 개수를 경도에 곱해서
정수 나눗셈으로 구한다 (floor(longitude*N/360)). *_span_deg 값(반복소수를
7자리로 자른 근사값)은 표시·문서용이며 인덱스 계산에는 쓰지 않는다 —
그 값으로 나누면 각 구간 끝에서 절삭 오차 때문에 범위 밖 인덱스
(28번째 낙샤트라, 5번째 파다)가 나왔다.

범위를 벗어나면 조용히 클램프하지 않고 assert 로 막는다. 발동하면 이 공식
자체가 새로운 경계 사례에서 깨졌다는 뜻이므로 테스트 실패로 드러나야 한다
(constants.yaml note).
"""
from __future__ import annotations

_SIGN_COUNT = 12
_PADA_PER_NAKSHATRA = 4


def sign_index(longitude: float) -> int:
    pos = longitude % 360.0
    idx = int(pos * _SIGN_COUNT // 360.0)
    assert 0 <= idx < _SIGN_COUNT, f"sign index 범위 초과: longitude={longitude} idx={idx}"
    return idx + 1


def nakshatra_index(longitude: float, nakshatra_count: int) -> int:
    pos = longitude % 360.0
    idx = int(pos * nakshatra_count // 360.0)
    assert 0 <= idx < nakshatra_count, f"nakshatra index 범위 초과: longitude={longitude} idx={idx}"
    return idx + 1


def pada_index(longitude: float, pada_count_total: int) -> int:
    pos = longitude % 360.0
    global_idx = int(pos * pada_count_total // 360.0)
    assert 0 <= global_idx < pada_count_total, f"pada index 범위 초과: longitude={longitude} idx={global_idx}"
    return global_idx % _PADA_PER_NAKSHATRA + 1


def whole_sign_house(body_sign: int, asc_sign: int) -> int:
    return (body_sign - asc_sign) % 12 + 1
