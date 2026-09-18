"""Greek text normalization and typed-answer checking."""

from __future__ import annotations

import enum
import unicodedata

_TRAILING_PUNCTUATION = " .,!?;:·;«»\"'"


class Verdict(enum.StrEnum):
    CORRECT = "correct"
    ACCENT_ONLY = "accent_only"
    WRONG = "wrong"


def _clean(text: str) -> str:
    collapsed = " ".join(text.split())
    return unicodedata.normalize("NFC", collapsed).strip(_TRAILING_PUNCTUATION)


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def lemma_key(text: str) -> str:
    """Accent- and case-insensitive key used to detect duplicate words.

    `casefold()` also folds final sigma ς into σ.
    """
    return strip_accents(_clean(text)).casefold()


def check_answer(given: str, expected: str) -> Verdict:
    """Compare a typed answer with the expected Greek form.

    Accents are part of Greek spelling, so a missing or misplaced accent is
    reported separately instead of being silently accepted.
    """
    given_clean, expected_clean = _clean(given).casefold(), _clean(expected).casefold()
    if given_clean == expected_clean:
        return Verdict.CORRECT
    if strip_accents(given_clean) == strip_accents(expected_clean):
        return Verdict.ACCENT_ONLY
    return Verdict.WRONG
