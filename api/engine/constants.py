"""constants.yaml 로더. 이 파일 자체는 값을 만들지 않고 그대로 읽어 넘긴다."""
from __future__ import annotations

from pathlib import Path

import yaml


def load_constants(path: str | Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))
