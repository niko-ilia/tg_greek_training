from pathlib import Path

import pytest

from greek_trainer.domain.word_input import parse_word, parse_words
from greek_trainer.errors import ParseError

SEED = Path(__file__).parent.parent / "seeds" / "a1.txt"


def test_parse_seed_file() -> None:
    [word] = parse_words(SEED.read_text(encoding="utf-8"))
    assert word.lemma == "ξέρω"
    assert word.translation == "знать; уметь"
    assert word.notes is not None and "ήξερα" in word.notes
    assert len(word.examples) == 5
    first = word.examples[2]
    assert first.text_el == "Δεν ξέρω πού είναι η στάση."
    assert first.text_ru == "Я не знаю, где остановка."
    assert first.cloze_target == "ξέρω"


def test_example_without_cloze() -> None:
    word = parse_word("ναι\nда\nΝαι, ευχαριστώ. | Да, спасибо.")
    assert word.examples[0].cloze_target is None


@pytest.mark.parametrize(
    "text",
    [
        "ξέρω",
        "ξέρω\n| знать",
        "ξέρω\nзнать\nΔεν ξέρω |",
        "ξέρω\nзнать\n*Δεν* *ξέρω* | Не знаю",
    ],
)
def test_invalid_input(text: str) -> None:
    with pytest.raises(ParseError):
        parse_word(text)
