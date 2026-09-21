import pytest

from greek_trainer.bot.render import answer_text, pace_text, question_text, word_text
from greek_trainer.db.models import Card, CardType, Example, Word
from greek_trainer.domain.greek import Verdict
from greek_trainer.services import Pace


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


def _overloaded() -> Pace:
    return Pace(
        new_words_today=0,
        new_words_cap=30,
        forecast_peak=200,
        budget=150,
        struggling=False,
        overridden=False,
    )


def test_a_mode_that_cannot_work_off_the_load_is_told_where_to() -> None:
    assert "/mode" not in pace_text(_overloaded())
    assert "/mode" in pace_text(_overloaded(), CardType.RECALL)
