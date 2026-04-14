from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from urllib import request

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
    body = {
        "model": model,
        "max_tokens": 120,
        "system": [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            },
        ],
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": text,
                        "cache_control": {"type": "ephemeral"},
                    },
                ],
            }
        ],
    }
    req = request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "prompt-caching-2024-07-31",
            "content-type": "application/json",
        },
        method="POST",
    )
    with request.urlopen(req, timeout=25) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    usage = raw.get("usage") or {}
    _record_claude_call(
        int(usage.get("input_tokens") or 0),
        int(usage.get("output_tokens") or 0),
    )
    content = raw.get("content") or []
    text_payload = ""
    if content and isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text_payload += str(block.get("text") or "")
    payload = text_payload.strip()
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
    system_prompt = (
        "Отримай текст новини та список ключових слів. "
        "Визнач, чи є в тексті одне з ключових слів з урахуванням відмінків/словоформ. "
        "Поверни ТІЛЬКИ JSON формату {\"matched_keyword\": \"...\"} або {\"matched_keyword\": null}."
    )
    body = {
        "model": model,
        "max_tokens": 80,
        "system": [{"type": "text", "text": system_prompt}],
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {"keywords": normalized_keywords, "text": text[:2000]},
                            ensure_ascii=False,
                        ),
                    }
                ],
            }
        ],
    }
    req = request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    with request.urlopen(req, timeout=25) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    usage = raw.get("usage") or {}
    _record_claude_call(
        int(usage.get("input_tokens") or 0),
        int(usage.get("output_tokens") or 0),
    )
    payload = ""
    for block in raw.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            payload += str(block.get("text") or "")
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
