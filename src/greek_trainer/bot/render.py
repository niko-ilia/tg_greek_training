"""Message texts and keyboards."""

from __future__ import annotations

from datetime import timedelta
from html import escape

import fsrs
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from greek_trainer.db.models import Card, CardType, Example, User, Word
from greek_trainer.domain.greek import Verdict
from greek_trainer.services import Pace, Stats

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


class PaceOverride(CallbackData, prefix="pace"):
    pass


class Setting(CallbackData, prefix="set"):
    key: str
    value: str


def session_end_keyboard(
    *, held_back: bool, returns_soon: bool
) -> InlineKeyboardMarkup | None:
    """ "Дальше" asks again once a card is due; "Всё равно дальше" lifts the pace brakes."""
    rows = []
    if returns_soon:
        rows.append(
            [InlineKeyboardButton(text="Дальше", callback_data=NextCard().pack())]
        )
    if held_back:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Всё равно дальше", callback_data=PaceOverride().pack()
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def pace_text(pace: Pace) -> str:
    """Why no new card came, in the learner's terms."""
    if pace.struggling:
        return (
            "🧠 В последних ответах много «Забыл». Давай закрепим то, что уже "
            "есть: новые слова подождут."
        )
    if pace.forecast_peak >= pace.budget:
        return (
            f"📈 В ближайшие дни уже до {pace.forecast_peak} повторений в день "
            f"при бюджете {pace.budget}. Новые слова пока хватит."
        )
    return f"📚 Сегодня уже {pace.new_words_today} новых слов, это твой потолок."


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
    if card.card_type is CardType.RECOGNITION:
        return card.word.translation
    if card.card_type is CardType.CLOZE:
        assert card.example is not None and card.example.cloze_target is not None
        return card.example.cloze_target
    return card.word.lemma


EXERCISE_MARKS = {
    CardType.RECOGNITION: "🇬🇷",
    CardType.RECALL: "🇷🇺",
    CardType.CLOZE: "🧩",
}

MODE_LABELS = {
    None: "🔀 Всё вместе",
    CardType.RECOGNITION: "🇬🇷 Греческое слово → перевод",
    CardType.RECALL: "🇷🇺 Перевод → по-гречески",
    CardType.CLOZE: "🧩 Пропущенное слово",
}


def question_text(card: Card) -> str:
    word = card.word
    mark = EXERCISE_MARKS[card.card_type]
    if card.card_type is CardType.RECOGNITION:
        return (
            f"{mark} <b>{escape(word.lemma)}</b>\n\n"
            "Что это значит? Напиши перевод или нажми «Показать ответ»."
        )
    if card.card_type is CardType.RECALL:
        return f"{mark} <b>{escape(word.translation)}</b>\n\nНапиши по-гречески."
    assert card.example is not None
    return (
        f"{mark} {escape(cloze_sentence(card.example))}\n"
        f"<i>{escape(card.example.text_ru)}</i>\n\n"
        f"Впиши пропущенное слово ({escape(word.translation)})."
    )


def word_text(word: Word, mark: str | None = None) -> str:
    title = f"<b>{escape(word.lemma)}</b> – {escape(word.translation)}"
    lines = [f"{mark} {title}" if mark else title]
    if word.notes:
        lines.append(f"<i>{escape(word.notes)}</i>")
    for example in word.examples:
        lines.append(f"• {escape(example.text_el)}\n   {escape(example.text_ru)}")
    return "\n".join(lines)


def answer_text(card: Card, verdict: Verdict | None, typed: str | None) -> str:
    """The word first, as on the question, then the verdict just above the buttons."""
    parts = [word_text(card.word, EXERCISE_MARKS[card.card_type])]
    if verdict is not None:
        parts.append(VERDICT_TEXT[verdict])
        if verdict is not Verdict.CORRECT and typed is not None:
            parts.append(
                f"Твой ответ: {escape(typed)}\n"
                f"Правильно: <b>{escape(expected_answer(card))}</b>"
            )
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


def check_text(word: Word, remaining: int, previous: str | None = None) -> str:
    lines = [f"🔎 Проверка · осталось {remaining}"]
    if previous:
        lines.append(previous)
    lines += ["", f"🇬🇷 <b>{escape(word.lemma)}</b> – {escape(word.translation)}"]
    return "\n".join(lines)


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


SECONDS_PER_REVIEW = 6
WORD_PRESETS = ("10", "20", "30", "50", "off")
BUDGET_PRESETS = ("100", "150", "250", "400")
REMIND_PRESETS = ("09:00", "13:00", "19:00", "21:00", "off")
MODE_PRESETS = (
    ("🔀 Всё", "all"),
    ("🇬🇷 → 🇷🇺", "recognition"),
    ("🇷🇺 → 🇬🇷", "recall"),
    ("🧩 Пропуск", "cloze"),
)
ZONE_PRESETS = (
    ("Кипр/Греция", "Europe/Nicosia"),
    ("Москва", "Europe/Moscow"),
    ("Берлин", "Europe/Berlin"),
    ("Лондон", "Europe/London"),
    ("Дубай", "Asia/Dubai"),
)


def settings_text(user: User) -> str:
    words = (
        f"до {user.daily_new_words}"
        if user.daily_new_words is not None
        else "без потолка"
    )
    minutes = max(1, round(user.daily_review_budget * SECONDS_PER_REVIEW / 60))
    reminder = f"{user.reminder_time:%H:%M}" if user.reminder_time else "выключено"
    return (
        "⚙️ <b>Настройки</b>\n"
        f"📚 Новых слов в день: {words}\n"
        f"🔁 Бюджет повторений: {user.daily_review_budget} в день, ≈ {minutes} мин\n"
        f"🔔 Напоминание: {reminder}\n"
        f"🎯 Режим: {MODE_LABELS[user.exercise_mode]}, сменить /mode\n"
        f"🌍 Часовой пояс: {escape(user.timezone)}\n\n"
        "Новые слова идут, пока прогноз повторений на неделю вперёд укладывается "
        "в бюджет и ответы в основном верные. Чем легче тебе даются слова, тем "
        "реже они возвращаются и тем больше места для новых.\n\n"
        "Текстом: <code>/settings words 40</code>, <code>/settings budget 200</code>, "
        "<code>/settings remind 07:30</code>, <code>/settings tz Asia/Bangkok</code>, "
        "<code>/settings mode recall</code>"
    )


def settings_keyboard(user: User) -> InlineKeyboardMarkup:
    def button(label: str, key: str, value: str, current: bool) -> InlineKeyboardButton:
        # Callback data is ":"-separated, so a clock time travels as "0930".
        data = Setting(key=key, value=value.replace(":", "")).pack()
        return InlineKeyboardButton(
            text=f"✓ {label}" if current else label, callback_data=data
        )

    words = str(user.daily_new_words) if user.daily_new_words is not None else "off"
    remind = f"{user.reminder_time:%H:%M}" if user.reminder_time else "off"
    rows = [
        [
            button(f"📚 {'∞' if v == 'off' else v}", "words", v, v == words)
            for v in WORD_PRESETS
        ],
        [
            button(f"🔁 {v}", "budget", v, v == str(user.daily_review_budget))
            for v in BUDGET_PRESETS
        ],
        [
            button("🔕" if v == "off" else f"🔔 {v}", "remind", v, v == remind)
            for v in REMIND_PRESETS
        ],
        [
            button(f"🌍 {label}", "tz", zone, zone == user.timezone)
            for label, zone in ZONE_PRESETS
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def mode_keyboard(user: User) -> InlineKeyboardMarkup:
    current = user.exercise_mode.value if user.exercise_mode else "all"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"✓ {label}" if value == current else label,
                    callback_data=Setting(key="mode", value=value).pack(),
                )
                for label, value in MODE_PRESETS
            ]
        ]
    )


def mode_text(user: User) -> str:
    return (
        f"🎯 <b>Режим упражнений</b>\nСейчас: {MODE_LABELS[user.exercise_mode]}\n\n"
        "В отдельном режиме идут только его карточки. Остальные ждут: их сроки "
        "никуда не денутся, но и не подойдут, пока ты не вернёшься."
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
