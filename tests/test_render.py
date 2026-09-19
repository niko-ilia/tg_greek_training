import pytest

from greek_trainer.bot.render import answer_text, question_text, word_text
from greek_trainer.db.models import Card, CardType, Example, Word
from greek_trainer.domain.greek import Verdict


def _card(card_type: CardType) -> Card:
    example = Example(text_el="Έχω ένα παιδί.", text_ru="У меня есть ребенок.")
    example.cloze_target = "Έχω"
    word = Word(lemma="έχω", translation="иметь", examples=[example])
    return Card(card_type=card_type, word=word, example=example)


@pytest.mark.parametrize("card_type", list(CardType))
@pytest.mark.parametrize("verdict", [None, Verdict.CORRECT, Verdict.WRONG])
def test_the_answer_starts_like_the_question(
    card_type: CardType, verdict: Verdict | None
) -> None:
    card = _card(card_type)
    mark = question_text(card).split()[0]
    typed = None if verdict is None else "ξέρω"
    assert answer_text(card, verdict, typed).startswith(f"{mark} <b>έχω</b> – иметь")


def test_word_text_without_a_mark_is_unchanged() -> None:
    assert word_text(_card(CardType.RECALL).word).startswith("<b>έχω</b> – иметь")
