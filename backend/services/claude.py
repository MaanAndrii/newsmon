from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone

from config import (
    DEFAULT_CLAUDE_MODEL,
    claude_call_events,
    repo,
)

# Delays (seconds) between successive retry attempts: 2 s, 4 s, 8 s
_RETRY_DELAYS = (2.0, 4.0, 8.0)


def _resolve_claude_model(value: str | None) -> str:
    model = (value or "").strip()
    return model if model else DEFAULT_CLAUDE_MODEL


def _record_claude_call(input_tokens: int, output_tokens: int, provider: str = "claude") -> None:
    claude_call_events.append(
        {
            "at": datetime.now(timezone.utc),
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
            "provider": provider,
        }
    )
    try:
        repo.log_api_call("ai", int(input_tokens), int(output_tokens), provider=provider)
    except Exception:
        pass


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
) -> tuple[int, str | None, int, int]:
    """Score a message and return (score, category, tokens_in, tokens_out)."""
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
        'Поверни ТІЛЬКИ JSON без пояснень, формат: {"score": 7, "category": "Економіка"}.'
    )

    client = anthropic.Anthropic(api_key=api_key, timeout=25.0)
    last_exc: Exception | None = None
    response = None
    for attempt, delay in enumerate([0.0] + list(_RETRY_DELAYS)):
        if delay:
            time.sleep(delay)
        try:
            response = client.messages.create(
                model=model,
                max_tokens=120,
                system=system_prompt,
                messages=[{"role": "user", "content": text}],
            )
            break
        except anthropic.RateLimitError as exc:
            last_exc = exc
        except anthropic.APIStatusError as exc:
            if exc.status_code not in (500, 529):
                raise
            last_exc = exc
        except anthropic.APIConnectionError as exc:
            last_exc = exc
    else:
        raise RuntimeError(
            f"Claude API не відповідає після {len(_RETRY_DELAYS) + 1} спроб"
        ) from last_exc

    tok_in = int(getattr(response.usage, "input_tokens", 0) or 0)
    tok_out = int(getattr(response.usage, "output_tokens", 0) or 0)
    _record_claude_call(tok_in, tok_out)

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
    return score, category, tok_in, tok_out


def _call_claude_digest_sync(
    api_key: str,
    model: str,
    messages_text: str,
    custom_prompt: str,
    format_style: str,
    date_label: str,
) -> tuple[str, int, int]:
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError("Пакет anthropic не встановлено") from exc

    fmt_map = {
        "article": "у форматі журналістської статті з підзаголовками по темах (300–600 слів)",
        "bullets": "у форматі маркованого списку, згрупованого по категоріях",
        "summary": "у форматі короткого executive summary до 200 слів",
    }
    fmt_instruction = fmt_map.get(format_style, fmt_map["article"])
    system_prompt = custom_prompt.strip() or (
        f"Ти редактор новинного видання. На основі повідомлень з моніторингу "
        f"напиши огляд подій за {date_label} {fmt_instruction} українською мовою. "
        f"Виділи найважливіше, вкажи конкретні факти і цифри. "
        f"Не вигадуй деталей, яких немає у вхідних даних."
    )

    client = anthropic.Anthropic(api_key=api_key, timeout=90.0)
    response = client.messages.create(
        model=model,
        max_tokens=1500,
        system=system_prompt,
        messages=[{"role": "user", "content": messages_text}],
    )
    tok_in = int(getattr(response.usage, "input_tokens", 0))
    tok_out = int(getattr(response.usage, "output_tokens", 0))
    _record_claude_call(tok_in, tok_out)
    text = "".join(b.text for b in response.content if hasattr(b, "text")).strip()
    return text, tok_in, tok_out


