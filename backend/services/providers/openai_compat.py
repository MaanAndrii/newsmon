from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone

from config import ai_raw_logs
from services.claude import _record_claude_call
from services.providers.base import DigestResult, ScoreResult

_RETRY_DELAYS = (2.0, 4.0, 8.0)


def _parse_json_response(payload: str) -> dict:
    """Extract a JSON object from an AI response string.

    Handles: bare JSON, markdown code fences, leading/trailing text.
    Always returns a dict (never raises).
    """
    text = payload.strip()
    # Strip markdown code fences: ```json ... ``` or ``` ... ```
    text = re.sub(r"```(?:\w+)?\s*", "", text).replace("```", "").strip()
    try:
        return json.loads(text or "{}")
    except json.JSONDecodeError:
        # Try to find the first JSON object in the text (non-greedy)
        match = re.search(r"\{[^{}]*\}", text) or re.search(r"\{.*?\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    return {}


def _parse_score_from_reasoning(text: str, categories: list[str]) -> dict:
    """Last-resort fallback: extract score and category from free-form reasoning text.

    Used when a model (e.g. deepseek-chat) writes prose before the JSON and
    max_tokens cuts the response before the JSON object is emitted.
    """
    result: dict = {}

    # Score: "score: 7", "score around 6-7", "Score 8/10", etc. — take first digit
    score_match = re.search(r'\bscore[^\d]{0,15}(\d+)', text, re.IGNORECASE)
    if not score_match:
        # "7/10", "6-7", plain digit near end
        score_match = re.search(r'\b([5-9]|10)\s*(?:/\s*10)?\b', text)
    if score_match:
        result['score'] = int(score_match.group(1))

    # Category: find the first known category name mentioned in the text
    for cat in categories:
        if cat.lower() in text.lower():
            result['category'] = cat
            break

    return result


def _log_raw(
    provider: str,
    model: str,
    system_prompt: str,
    user_message: str,
    raw_response: str,
    score: int | None = None,
    category: str | None = None,
    error: str | None = None,
) -> None:
    ai_raw_logs.append({
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "provider": provider,
        "model": model,
        "system_prompt": system_prompt,
        "user_message": user_message,
        "raw_response": raw_response,
        "score": score,
        "category": category,
        "error": error,
    })


class OpenAICompatProvider:
    def __init__(self, api_key: str, model: str, base_url: str, provider_name: str = "openai_compat") -> None:
        self.api_key = api_key
        self.model = model.strip()
        self.base_url = base_url
        self.provider_name = provider_name

    def has_credentials(self) -> bool:
        return bool(self.api_key and self.model)

    def _client(self):
        try:
            import openai
        except ImportError as exc:
            raise RuntimeError("Пакет openai не встановлено. Виконайте: pip install openai>=1.0") from exc
        return openai.OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=30.0)

    def score_message(
        self,
        text: str,
        categories: list[str],
        ai_prompt: str,
    ) -> ScoreResult:
        categories_text = ", ".join(categories) if categories else "Без категорії"
        base_prompt = ai_prompt or "Оціни медіа-важливість повідомлення від 1 до 10 і обери найкращу категорію."
        system_prompt = (
            f"{base_prompt}\n"
            f"Категорії: {categories_text}.\n"
            'Відповідай ВИКЛЮЧНО валідним JSON без будь-яких пояснень чи коментарів. '
            'Формат відповіді: {"score": 7, "category": "Економіка"}.'
        )

        client = self._client()
        last_exc: Exception | None = None
        response = None
        for delay in [0.0] + list(_RETRY_DELAYS):
            if delay:
                time.sleep(delay)
            try:
                response = client.chat.completions.create(
                    model=self.model,
                    max_tokens=200,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": text},
                    ],
                    # Disable chain-of-thought reasoning for scoring — we need
                    # a short deterministic JSON, not a deliberation process.
                    extra_body={"thinking": {"type": "disabled"}},
                )
                break
            except Exception as exc:
                last_exc = exc
                status = getattr(getattr(exc, "status_code", None), "real", None) or getattr(exc, "status_code", None)
                if status and status not in (429, 500, 503):
                    _log_raw(self.provider_name, self.model, system_prompt, text[:500], "", error=f"{type(exc).__name__}: {exc}")
                    raise
        else:
            _log_raw(self.provider_name, self.model, system_prompt, text[:500], "", error=f"{type(last_exc).__name__}: {last_exc}")
            raise RuntimeError(
                f"AI API не відповідає після {len(_RETRY_DELAYS) + 1} спроб"
            ) from last_exc

        usage = getattr(response, "usage", None)
        tok_in = int(getattr(usage, "prompt_tokens", 0) or 0)
        tok_out = int(getattr(usage, "completion_tokens", 0) or 0)
        _record_claude_call(tok_in, tok_out, provider=self.provider_name)

        msg = response.choices[0].message
        raw_content = msg.content or ""
        reasoning_content = getattr(msg, "reasoning_content", None) or ""
        # reasoning_content is the internal chain-of-thought (Chinese/English prose) — ignore it.
        # content should contain the clean JSON answer when thinking is disabled.
        if not raw_content.strip():
            raise RuntimeError(f"Модель {self.model} повернула порожню відповідь")
        payload = raw_content.strip()
        parsed = _parse_json_response(payload)

        # Last-resort heuristic: scan reasoning prose for score/category hints.
        if not parsed.get("score"):
            search_text = payload + "\n" + reasoning_content
            parsed = _parse_score_from_reasoning(search_text, categories)

        score = int(parsed.get("score") or 0)
        score = max(1, min(10, score))
        category = str(parsed.get("category") or "").strip() or None
        if categories and category not in categories:
            category = None

        _log_raw(self.provider_name, self.model, system_prompt, text[:500], payload, score=score, category=category)
        return ScoreResult(score=score, category=category, tokens_in=tok_in, tokens_out=tok_out)

    def generate_digest(
        self,
        messages_text: str,
        ai_prompt: str,
        format_style: str,
        date_label: str,
    ) -> DigestResult:
        fmt_map = {
            "article": "у форматі журналістської статті з підзаголовками по темах (300–600 слів)",
            "bullets": "у форматі маркованого списку, згрупованого по категоріях",
            "summary": "у форматі короткого executive summary до 200 слів",
        }
        fmt_instruction = fmt_map.get(format_style, fmt_map["article"])
        system_prompt = ai_prompt.strip() or (
            f"Ти редактор новинного видання. На основі повідомлень з моніторингу "
            f"напиши огляд подій за {date_label} {fmt_instruction} українською мовою. "
            f"Виділи найважливіше, вкажи конкретні факти і цифри. "
            f"Не вигадуй деталей, яких немає у вхідних даних."
        )

        client = self._client()
        last_exc: Exception | None = None
        response = None
        for delay in [0.0] + list(_RETRY_DELAYS):
            if delay:
                time.sleep(delay)
            try:
                response = client.chat.completions.create(
                    model=self.model,
                    max_tokens=4000,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": messages_text},
                    ],
                )
                break
            except Exception as exc:
                last_exc = exc
                status = getattr(getattr(exc, "status_code", None), "real", None) or getattr(exc, "status_code", None)
                if status and status not in (429, 500, 503):
                    raise
        else:
            raise RuntimeError(
                f"AI API не відповідає після {len(_RETRY_DELAYS) + 1} спроб"
            ) from last_exc

        usage = getattr(response, "usage", None)
        tok_in = int(getattr(usage, "prompt_tokens", 0) or 0)
        tok_out = int(getattr(usage, "completion_tokens", 0) or 0)
        _record_claude_call(tok_in, tok_out, provider=self.provider_name)

        content = (response.choices[0].message.content or "").strip()
        return DigestResult(content=content, tokens_in=tok_in, tokens_out=tok_out)
