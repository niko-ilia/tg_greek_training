"""Parsing of the plain-text word format used by /add and the seed files.

Format (one word per block)::

    ξέρω
    знать; уметь
    Примечание: ξέρω να + глагол = уметь
    Δεν *ξέρω* πού είναι η στάση. | Я не знаю, где остановка.

Line 1 is the Greek word, line 2 its translation. A line with `|` is an example
("Greek | Russian"); the form wrapped in `*...*` becomes a cloze drill. Any
other line is a free-form note.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from greek_trainer.errors import ParseError

_CLOZE = re.compile(r"\*([^*]+)\*")


@dataclass(frozen=True)
class ExampleDraft:
    text_el: str
    text_ru: str
    cloze_target: str | None


@dataclass(frozen=True)
class WordDraft:
    lemma: str
    translation: str
    notes: str | None = None
    examples: list[ExampleDraft] = field(default_factory=list)


def parse_example(line: str) -> ExampleDraft:
    greek, _, russian = line.partition("|")
    greek, russian = greek.strip(), russian.strip()
    if not greek or not russian:
        raise ParseError(f"Пример должен быть в виде «греческий | русский»: {line}")
    targets = _CLOZE.findall(greek)
    if len(targets) > 1:
        raise ParseError(
            f"В примере можно выделить *звездочками* только одно слово: {line}"
        )
    return ExampleDraft(
        text_el=_CLOZE.sub(r"\1", greek),
        text_ru=russian,
        cloze_target=targets[0].strip() if targets else None,
    )


def parse_word(text: str) -> WordDraft:
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if len(lines) < 2:
        raise ParseError("Нужно минимум две строки: слово по-гречески и перевод.")
    lemma, translation, *rest = lines
    if "|" in lemma or "|" in translation:
        raise ParseError("Первые две строки: слово и перевод, без «|».")
    examples = [parse_example(line) for line in rest if "|" in line]
    notes = [line for line in rest if "|" not in line]
    return WordDraft(
        lemma=lemma,
        translation=translation,
        notes="\n".join(notes) or None,
        examples=examples,
    )


def parse_words(text: str) -> list[WordDraft]:
    """Parse several blank-line-separated word blocks (seed files)."""
    blocks = re.split(r"\n\s*\n", text.strip())
    return [parse_word(block) for block in blocks if block.strip()]
