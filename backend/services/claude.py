from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from config import (
    CLAUDE_MODELS,
    DEFAULT_CLAUDE_MODEL,
    claude_call_events,
)


def _resolve_claude_model(value: str | None) -> str:
    model = (value or "").strip()
    if model in CLAUDE_MODELS:
        return model
    return DEFAULT_CLAUDE_MODEL


def _record_claude_call(input_tokens: int, output_tokens: int) -> None:
    claude_call_events.append(
        {
            "at": datetime.now(timezone.utc),
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
        }
    )


def _prepare_ai_text(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        return ""
    lines = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]
    title = lines[0] if lines else ""
    paragraphs = [p.strip() for p in cleaned.split("\n\n") if p.strip()]
    first_paragraph = paragraphs[0] if paragraphs else title
    reduced = f"Заголовок: {title}\nПерший абзац: {first_paragraph}".strip()
    return reduced[:1600]


def _call_claude_score_sync(
    api_key: str,
    model: str,
    text: str,
    categories: list[str],
    custom_prompt: str,
) -> tuple[int, str | None]:
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError(
            "Пакет anthropic не встановлено. Виконайте: pip install -r backend/requirements.txt"
        ) from exc

    categories_text = ", ".join(categories) if categories else "Без категорії"
    base_prompt = (
        custom_prompt
        or "Оціни медіа-важливість повідомлення від 1 до 10 і обери найкращу категорію."
    )
    system_prompt = (
        f"{base_prompt}\n"
        f"Категорії: {categories_text}.\n"
        "Поверни ТІЛЬКИ JSON без пояснень, формат: {\"score\": 7, \"category\": \"Економіка\"}."
    )

    client = anthropic.Anthropic(api_key=api_key, timeout=25.0)
    response = client.messages.create(
        model=model,
        max_tokens=120,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": text,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            }
        ],
        extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"},
    )

    _record_claude_call(
        int(getattr(response.usage, "input_tokens", 0) or 0),
        int(getattr(response.usage, "output_tokens", 0) or 0),
    )

    payload = "".join(
        block.text for block in response.content if hasattr(block, "text")
    ).strip()

    try:
        parsed = json.loads(payload or "{}")
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", payload, flags=re.DOTALL)
        parsed = json.loads(match.group(0)) if match else {}

    score = int(parsed.get("score") or 0)
    score = max(1, min(10, score))
    category = str(parsed.get("category") or "").strip() or None
    if categories and category not in categories:
        category = None
    return score, category


def _call_claude_keyword_match_sync(
    api_key: str,
    model: str,
    text: str,
    keywords: list[str],
) -> str | None:
    if not api_key or not keywords:
        return None
    normalized_keywords = [k.strip() for k in keywords if k and k.strip()]
    if not normalized_keywords:
        return None

    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError(
            "Пакет anthropic не встановлено. Виконайте: pip install -r backend/requirements.txt"
        ) from exc

    system_prompt = (
        "Отримай текст новини та список ключових слів. "
        "Визнач, чи є в тексті одне з ключових слів з урахуванням відмінків/словоформ. "
        "Поверни ТІЛЬКИ JSON формату {\"matched_keyword\": \"...\"} або {\"matched_keyword\": null}."
    )
    user_content = json.dumps(
        {"keywords": normalized_keywords, "text": text[:2000]},
        ensure_ascii=False,
    )

    client = anthropic.Anthropic(api_key=api_key, timeout=25.0)
    response = client.messages.create(
        model=model,
        max_tokens=80,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
    )

    _record_claude_call(
        int(getattr(response.usage, "input_tokens", 0) or 0),
        int(getattr(response.usage, "output_tokens", 0) or 0),
    )

    payload = "".join(
        block.text for block in response.content if hasattr(block, "text")
    ).strip()

    try:
        parsed = json.loads(payload or "{}")
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", payload, flags=re.DOTALL)
        parsed = json.loads(match.group(0)) if match else {}

    matched = str(parsed.get("matched_keyword") or "").strip()
    if not matched:
        return None
    for kw in normalized_keywords:
        if kw.lower() == matched.lower():
            return kw
    return None
