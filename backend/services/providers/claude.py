from __future__ import annotations

from services.claude import (
    _call_claude_digest_sync,
    _call_claude_score_sync,
    _resolve_claude_model,
)
from services.providers.base import DigestResult, ScoreResult


class ClaudeProvider:
    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = _resolve_claude_model(model)

    def has_credentials(self) -> bool:
        return bool(self.api_key)

    def score_message(
        self,
        text: str,
        categories: list[str],
        ai_prompt: str,
        keywords: list[str] | None = None,
    ) -> ScoreResult:
        score, category, matched_keyword = _call_claude_score_sync(
            self.api_key,
            self.model,
            text,
            categories,
            ai_prompt,
            keywords,
        )
        return ScoreResult(score=score, category=category, matched_keyword=matched_keyword)

    def generate_digest(
        self,
        messages_text: str,
        ai_prompt: str,
        format_style: str,
        date_label: str,
    ) -> DigestResult:
        content, tok_in, tok_out = _call_claude_digest_sync(
            self.api_key,
            self.model,
            messages_text,
            ai_prompt,
            format_style,
            date_label,
        )
        return DigestResult(content=content, tokens_in=tok_in, tokens_out=tok_out)
