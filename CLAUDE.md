# CLAUDE.md

## Project
Personal Telegram bot (single owner) for memorizing Greek vocabulary with FSRS spaced repetition.
User-facing texts are Russian; code, comments and docs for agents are English.

## Stack
- Python 3.12, aiogram 3, SQLAlchemy 2.0 async + asyncpg, Alembic, Postgres 16, `fsrs` (py-fsrs 6), `edge-tts`.
- Deps: `requirements.txt` / `requirements-dev.txt` are the human-edited source (direct pins);
  `*.lock` are generated with hashes. After editing a `.txt`:
  `uv pip compile requirements.txt --generate-hashes --python-version 3.12 -o requirements.lock` (same for dev).
- Local DB: `docker compose up -d` → 127.0.0.1:5447, DBs `greek_trainer` and `greek_trainer_test`.
- Tests: `.venv/bin/python -m pytest -q` – run against the real Postgres test DB (compose must be up).
- Gates: `ruff format --check . && ruff check . && mypy`.
- Run: `alembic upgrade head && python -m greek_trainer`. Docker CMD does the same.

## Code Style
Shared Python conventions: `.claude/rules/code-style-python.md` (copied from the shared KB).

## Architecture
- `domain/` – pure logic: Greek normalization/answer check, `/add` text parser, FSRS bridge.
- `services.py` – DB use cases (add word, pick next card, record review, stats). No Telegram here.
- `bot/` – aiogram routers, rendering, TTS, reminders loop, middlewares.
- One DB transaction per Telegram update (`DbSessionMiddleware`); handlers get `session` and `user`.
- Schema changes only via Alembic; review autogenerate output (it missed the enum drop in 0001).

## Invariants
- Card FSRS columns mirror `fsrs.Card`; convert only through `domain/srs.py`.
- `cards.version` bumps on each review; callback buttons carry it, so stale/double taps are rejected.
- `review_logs` is append-only history (needed to optimize FSRS parameters later) – never rewrite it.
- `review_logs.state_before IS NULL` marks a card's first review; the daily new-card limit counts these.
- Sibling burying: once any card of a word is reviewed today (day rolls over at 04:00 local), the
  word's other cards wait. Learning steps of the same card are not blocked.
