import pytest

from greek_trainer.domain.greek import (
    Verdict,
    check_answer,
    check_translation,
    lemma_key,
)


@pytest.mark.parametrize(
    ("given", "verdict"),
    [
        ("ξέρω", Verdict.CORRECT),
        ("  Ξέρω. ", Verdict.CORRECT),
        ("ξερω", Verdict.ACCENT_ONLY),
        ("ξερώ", Verdict.ACCENT_ONLY),
        ("ξέρεις", Verdict.WRONG),
        ("", Verdict.WRONG),
    ],
)
def test_check_answer(given: str, verdict: Verdict) -> None:
    assert check_answer(given, "ξέρω") is verdict


@pytest.mark.parametrize(
    ("given", "verdict"),
    [
        ("знать", Verdict.CORRECT),
        ("Уметь.", Verdict.CORRECT),
        ("ЗНАТЬ", Verdict.CORRECT),
        ("знать, уметь", Verdict.WRONG),
        ("", Verdict.WRONG),
    ],
)
def test_check_translation(given: str, verdict: Verdict) -> None:
    assert check_translation(given, "знать; уметь") is verdict


def test_check_translation_ignores_yo() -> None:
    assert check_translation("мед", "мёд") is Verdict.CORRECT


def test_check_answer_final_sigma_and_article() -> None:
    assert check_answer("η στάσης", "η στάσης") is Verdict.CORRECT
    assert check_answer("η στασησ", "η στάσης") is Verdict.ACCENT_ONLY
    assert check_answer("στάση", "η στάση") is Verdict.WRONG


def test_lemma_key_ignores_accents_and_case() -> None:
    assert lemma_key("Ξέρω") == lemma_key("ξερω") == "ξερω"
