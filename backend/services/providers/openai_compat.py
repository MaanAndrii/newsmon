from __future__ import annotations

import json
import re
import time

from services.claude import _record_claude_call
from services.providers.base import DigestResult, ScoreResult

_RETRY_DELAYS = (2.0, 4.0, 8.0)


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
        keywords: list[str] | None = None,
    ) -> ScoreResult:
        categories_text = ", ".join(categories) if categories else "Без категорії"
        base_prompt = ai_prompt or "Оціни медіа-важливість повідомлення від 1 до 10 і обери найкращу категорію."
        if keywords:
            keywords_text = ", ".join(f'"{k}"' for k in keywords)
            json_format = '{"score": 7, "category": "Економіка", "matched_keyword": "Харків"}'
            keyword_instruction = (
                f'Ключові слова для пошуку (з урахуванням відмінків/словоформ): {keywords_text}. '
                f'Якщо жодне не знайдено — matched_keyword: null. '
            )
        else:
            json_format = '{"score": 7, "category": "Економіка"}'
            keyword_instruction = ""

        system_prompt = (
            f"{base_prompt}\n"
            f"Категорії: {categories_text}.\n"
            f"{keyword_instruction}"
            f"Поверни ТІЛЬКИ JSON без пояснень, формат: {json_format}."
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
                    max_tokens=200 if keywords else 120,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": text},
                    ],
                )
                break
            except Exception as exc:
                last_exc = exc
                # Only retry on rate-limit / server errors
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

        payload = (response.choices[0].message.content or "").strip()
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
        matched_keyword: str | None = None
        if keywords:
            raw_match = str(parsed.get("matched_keyword") or "").strip()
            if raw_match:
                for kw in keywords:
                    if kw.lower() == raw_match.lower():
                        matched_keyword = kw
                        break
        return ScoreResult(score=score, category=category, matched_keyword=matched_keyword,
                           tokens_in=tok_in, tokens_out=tok_out)

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
                    max_tokens=1500,
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
        _record_claude_call(tok_in, tok_out)

        content = (response.choices[0].message.content or "").strip()
        return DigestResult(content=content, tokens_in=tok_in, tokens_out=tok_out)
