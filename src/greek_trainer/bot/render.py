"""Message texts and keyboards."""

from __future__ import annotations

from datetime import timedelta
from html import escape

import fsrs
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from greek_trainer.db.models import Card, CardType, Example, User, Word
from greek_trainer.domain.greek import Verdict
from greek_trainer.services import Stats

RATING_LABELS = {
    fsrs.Rating.Again: "Забыл",
    fsrs.Rating.Hard: "Трудно",
    fsrs.Rating.Good: "Хорошо",
    fsrs.Rating.Easy: "Легко",
}

VERDICT_TEXT = {
    Verdict.CORRECT: "✅ Верно!",
    Verdict.ACCENT_ONLY: "🟡 Почти: проверь ударение.",
    Verdict.WRONG: "❌ Неверно.",
}

CLOZE_GAP = "____"


class ShowAnswer(CallbackData, prefix="show"):
    card_id: int
    version: int


class Rate(CallbackData, prefix="rate"):
    card_id: int
    version: int
    rating: int


class NextCard(CallbackData, prefix="next"):
    pass


class Check(CallbackData, prefix="check"):
    word_id: int
    action: str


def format_interval(delta: timedelta) -> str:
    minutes = max(1, round(delta.total_seconds() / 60))
    if minutes < 60:
        return f"{minutes} мин"
    hours = round(minutes / 60)
    if hours < 24:
        return f"{hours} ч"
    days = round(hours / 24)
    if days < 31:
        return f"{days} дн"
    if days < 365:
        return f"{round(days / 30)} мес"
    return f"{days / 365:.1f} г"


def cloze_sentence(example: Example) -> str:
    assert example.cloze_target is not None
    return example.text_el.replace(example.cloze_target, CLOZE_GAP, 1)


def expected_answer(card: Card) -> str:
    """What the learner must type: the translation, the lemma or the cloze form."""
    if card.card_type is CardType.RECOGNITION:
        return card.word.translation
    if card.card_type is CardType.CLOZE:
        assert card.example is not None and card.example.cloze_target is not None
        return card.example.cloze_target
    return card.word.lemma


def question_text(card: Card) -> str:
    word = card.word
    if card.card_type is CardType.RECOGNITION:
        return (
            f"🇬🇷 <b>{escape(word.lemma)}</b>\n\n"
            "Что это значит? Напиши перевод или нажми «Показать ответ»."
        )
    if card.card_type is CardType.RECALL:
        return f"🇷🇺 <b>{escape(word.translation)}</b>\n\nНапиши по-гречески."
    assert card.example is not None
    return (
        f"🧩 {escape(cloze_sentence(card.example))}\n"
        f"<i>{escape(card.example.text_ru)}</i>\n\n"
        f"Впиши пропущенное слово ({escape(word.translation)})."
    )


def word_text(word: Word) -> str:
    lines = [f"<b>{escape(word.lemma)}</b> – {escape(word.translation)}"]
    if word.notes:
        lines.append(f"<i>{escape(word.notes)}</i>")
    for example in word.examples:
        lines.append(f"• {escape(example.text_el)}\n   {escape(example.text_ru)}")
    return "\n".join(lines)


def answer_text(card: Card, verdict: Verdict | None, typed: str | None) -> str:
    parts = []
    if verdict is not None:
        parts.append(VERDICT_TEXT[verdict])
        if verdict is not Verdict.CORRECT and typed is not None:
            parts.append(
                f"Твой ответ: {escape(typed)}\n"
                f"Правильно: <b>{escape(expected_answer(card))}</b>"
            )
    parts.append(word_text(card.word))
    parts.append("Насколько легко было вспомнить?")
    return "\n\n".join(parts)


def show_answer_keyboard(card: Card) -> InlineKeyboardMarkup:
    data = ShowAnswer(card_id=card.id, version=card.version).pack()
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Показать ответ", callback_data=data)]
        ]
    )


def rating_keyboard(
    card: Card, previews: dict[fsrs.Rating, timedelta]
) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=f"{RATING_LABELS[rating]} · {format_interval(previews[rating])}",
            callback_data=Rate(
                card_id=card.id, version=card.version, rating=rating.value
            ).pack(),
        )
        for rating in fsrs.Rating
    ]
    return InlineKeyboardMarkup(inline_keyboard=[buttons[:2], buttons[2:]])


def next_card_keyboard(text: str = "Дальше") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=NextCard().pack())]
        ]
    )


def check_text(word: Word) -> str:
    return f"🇬🇷 <b>{escape(word.lemma)}</b> – {escape(word.translation)}"


def check_keyboard(word: Word) -> InlineKeyboardMarkup:
    def button(text: str, action: str) -> InlineKeyboardButton:
        data = Check(word_id=word.id, action=action).pack()
        return InlineKeyboardButton(text=text, callback_data=data)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button("Знаю", "know"), button("Учить", "learn")],
            [button("Хватит", "stop")],
        ]
    )


def settings_text(user: User) -> str:
    reminder = f"{user.reminder_time:%H:%M}" if user.reminder_time else "выключено"
    return (
        "⚙️ <b>Настройки</b>\n"
        f"Новых карточек в день: {user.daily_new_cards}\n"
        f"Напоминание: {reminder}\n\n"
        "Изменить: <code>/settings 20</code>, <code>/settings 20 19:30</code>, "
        "<code>/settings off</code>"
    )


def stats_text(stats: Stats) -> str:
    retention = (
        f"{stats.retention_30d:.0%}"
        if stats.retention_30d is not None
        else "пока нет данных"
    )
    return (
        "📊 <b>Статистика</b>\n"
        f"Слов в словаре: {stats.words}\n"
        f"Карточек: {stats.cards}, из них еще не начатых: {stats.new_cards}\n"
        f"К повторению сейчас: {stats.due_now}\n"
        f"Ответов сегодня: {stats.reviewed_today}\n"
        f"Запоминание за 30 дней: {retention}"
    )
