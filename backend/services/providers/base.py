from __future__ import annotations

from typing import NamedTuple


class ScoreResult(NamedTuple):
    score: int
    category: str | None
    matched_keyword: str | None


class DigestResult(NamedTuple):
    content: str
    tokens_in: int
    tokens_out: int
