from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

# Matches Ukrainian words including apostrophe variants
_WORD_RE = re.compile(r"[а-яіїєґА-ЯІЇЄҐ'ʼ'-]+")

_morph = None
_morph_loaded = False


def _get_morph():
    global _morph, _morph_loaded
    if not _morph_loaded:
        _morph_loaded = True
        try:
            import pymorphy3
            _morph = pymorphy3.MorphAnalyzer(lang="uk")
            logger.info("pymorphy3 Ukrainian morphology analyzer loaded")
        except Exception as exc:
            logger.warning("pymorphy3 unavailable, falling back to raw tokens: %s", exc)
            _morph = None
    return _morph


def lemmatize(text: str) -> frozenset[str]:
    """Return frozenset of all possible lowercase lemmas for all Ukrainian words in text.

    Collects every parse variant per word so ambiguous forms (e.g. locative case)
    produce multiple candidate lemmas, maximising recall.
    """
    words = _WORD_RE.findall(text)
    if not words:
        return frozenset()
    morph = _get_morph()
    if morph is not None:
        lemmas: set[str] = set()
        for word in words:
            for parse in morph.parse(word):
                lemmas.add(parse.normal_form.lower())
        return frozenset(lemmas)
    return frozenset(w.lower() for w in words)


def _primary_lemma(word: str) -> str:
    """Return the most probable lemma for a single word."""
    morph = _get_morph()
    if morph is not None:
        parsed = morph.parse(word)
        if parsed:
            return parsed[0].normal_form.lower()
    return word.lower()


def keyword_to_lemma_json(keyword: str) -> str:
    """Convert keyword phrase to JSON-serialized sorted lemma list for DB storage.

    Uses primary lemmas only — the user entered the keyword in its base/canonical
    form, so the top parse is sufficient and avoids spurious alternatives.
    """
    words = _WORD_RE.findall(keyword)
    lemmas = sorted({_primary_lemma(w) for w in words} if words else set())
    return json.dumps(lemmas, ensure_ascii=False)


def lemmas_from_json(json_str: str | None) -> frozenset[str]:
    """Parse stored JSON lemma list back to frozenset."""
    if not json_str:
        return frozenset()
    try:
        return frozenset(json.loads(json_str))
    except Exception:
        return frozenset()


def match(message_lemmas: frozenset[str], keyword_lemmas: frozenset[str]) -> bool:
    """True if every keyword lemma is present in the message lemma set.

    Order-independent: "Маринюк Андрій" matches "Андрій Маринюк" and vice versa.
    Handles all word forms via pymorphy3 Ukrainian morphological analysis.
    """
    if not keyword_lemmas:
        return False
    return keyword_lemmas.issubset(message_lemmas)
