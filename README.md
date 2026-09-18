# tg.greek-trainer

Личный Telegram-бот для запоминания греческих слов.

## Как учит
- **FSRS** (алгоритм из Anki 23+): у каждой карточки своя стабильность и сложность памяти, повтор
  назначается тогда, когда вероятность вспомнить падает до 90% (`DESIRED_RETENTION`).
- **Три упражнения на слово**: узнавание (EL→RU, с озвучкой), вспоминание (RU→EL, ввод с
  клавиатуры, ударения проверяются) и пропуск в предложении из ваших примеров.
- **Одна карточка слова в день**: после ответа на одну карточку слова остальные ждут следующего
  дня, чтобы узнавание не подсказывало ответ на вспоминание.
- **Лимит новых карточек** в день (`DAILY_NEW_CARDS`) и напоминание в `REMINDER_TIME`.
- Озвучка: бесплатный голос Microsoft Edge `el-GR-AthinaNeural`, кэшируется по `file_id`.

## Запуск локально
```bash
cp .env.example .env              # вписать BOT_TOKEN и OWNER_TELEGRAM_ID
docker compose up -d              # Postgres 16 на 127.0.0.1:5447
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python --require-hashes -r requirements-dev.lock
uv pip install --python .venv/bin/python --no-deps -e .
.venv/bin/alembic upgrade head
.venv/bin/python -m greek_trainer.seed seeds/a1.txt   # стартовые слова
.venv/bin/python -m greek_trainer
```

## Добавление слов
В боте: `/add`, затем блок текста (формат в `/help`). Оптом: блоки через пустую строку в файле
и `python -m greek_trainer.seed <файл>`, уже добавленные слова пропускаются.
