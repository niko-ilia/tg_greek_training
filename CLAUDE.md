# CLAUDE.md

Telegram bot for memorizing Greek vocabulary with FSRS spaced repetition. The dictionary
(`words`, `examples`) is shared by every learner; cards and review logs are per Telegram user.
Open to anyone in private chats; `ADMIN_TELEGRAM_IDS` (old name `ALLOWED_TELEGRAM_IDS` still
read) lists admins, the only ones allowed to `/add` words. User-facing text is Russian; code, comments, tests,
migrations and this file are English. `README.md` is the human-facing doc, in Russian.

## Commands
- Local DB: `docker compose up -d` (Postgres 16 on 127.0.0.1:5447, DBs `greek_trainer` and
  `greek_trainer_test`). Tests and migrations fail without it.
- Tests: `.venv/bin/python -m pytest -q`
- Gates: `.venv/bin/ruff format --check . && .venv/bin/ruff check . && .venv/bin/mypy`
- Run: `.venv/bin/alembic upgrade head && .venv/bin/python -m greek_trainer`
- Seed words: `.venv/bin/python -m greek_trainer.seed seeds/*.txt` (idempotent). The Docker CMD
  runs it before the bot, so every file in `seeds/` is live after the next deploy.
- Deps: edit `requirements*.txt` (direct pins), then regenerate the lock:
  `uv pip compile requirements.txt --generate-hashes --python-version 3.12 -o requirements.lock`
  (same for `-dev`). Never edit `*.lock` by hand.
- Compare pins with PyPI: `uv pip compile requirements-dev.txt --upgrade -o /tmp/latest.txt` and diff.

## Workflow
- Feature work goes on a branch off `feat/mvp` (unmerged) or `main`. Do not merge; hand over
  with a summary of what changed and how it was verified.
- Done means: gates green, tests green, and for schema changes the migration check below passed.
- Commit only when asked. Never commit `.env`.
- Schema changes only via Alembic: `alembic revision --autogenerate -m "..."` and review the file.
  Autogenerate misses enum drops (0001) and data moves, write those by hand.
- Migration check (on the test DB, `DATABASE_URL=...greek_trainer_test`): reset schema, upgrade
  to the previous revision, insert a few rows, `alembic upgrade head`, `alembic check` (must print
  "No new upgrade operations detected"), `alembic downgrade <prev>`, `alembic upgrade head`.
  Run SQL through `docker compose exec -T postgres psql -U greek -d greek_trainer_test`.
- Verify concurrency claims with two real sessions racing on the test DB, not by reasoning.

## Layout
- `domain/` pure logic, no DB or Telegram: Greek normalization and answer check, `/add` text
  parser, FSRS bridge (`srs.py`).
- `services.py` DB use cases. No Telegram objects here.
- `bot/` aiogram: `handlers/`, `render.py` (texts and keyboards), `tts.py`, `reminders.py`,
  `middlewares.py`.
- `tests/` run against the real test DB; each test rolls back via a savepoint (`conftest.py`).
  Service tests call `add_word` then `deal_missing_cards` (see `_add` helper).

## Invariants
- Cards are dealt lazily: `DbSessionMiddleware` and the reminder loop run `deal_missing_cards`
  before handlers; `add_word` creates no cards. Filter a learner's cards by `cards.user_id`,
  never through `words`.
- `add_word` takes a `pg_advisory_xact_lock` on the lemma key, so a concurrent duplicate gets
  `DuplicateWordError`, not an `IntegrityError`.
- Card FSRS columns mirror `fsrs.Card`; convert only through `domain/srs.py`.
- `cards.version` bumps on every review; callback buttons carry it, stale taps are rejected.
- `review_logs` is append-only history for future FSRS parameter optimization. Never rewrite.
- `review_logs.state_before IS NULL` marks a card's first review. A word counts as a new word
  today when its first non-triage review is today and it had none before (`is_triage` = the
  "I know it" answer in `/check`, which rates every card of the word Easy).
- `/check` walks words with no reviewed card in `words.id` order after `users.check_cursor`;
  both "know" and "learn" move the cursor through `advance_check`, a conditional UPDATE that
  rejects double taps and stale buttons. A skipped word is not offered again.
- Handlers that take free text in an FSM state must exclude commands
  (`~F.text.startswith("/")`), or `/review` and friends get parsed as input.
- New material is paced by the learner's ratings, not a fixed card count (`Pace` in
  `services.py`): new cards stop while the FSRS forecast peak of the next 7 days (+ cards in
  learning) reaches `daily_review_budget`, or while 3 of the last 10 answers today are Again;
  brand-new words also stop at `daily_new_words` (NULL = no ceiling). Due learning and review
  cards are never blocked. "Всё равно дальше" sets `pace_override_on` = today (conditional
  UPDATE). When nothing is due, a learning step due within `LEARN_AHEAD` (20 min, as in Anki)
  is shown early, but never one answered less than `MIN_REPEAT_GAP` (3 min) ago: in a chat the
  answer is still visible above. Review-state cards are never pulled forward. The struggle
  check counts only answers on words started on earlier days (`_word_seen_before`).
- The session-end "Дальше" button appears only when `repeat_gap_ends_at` finds a card held
  back by that gap; `next_due_at` treats cards buried today as due at the next day start.
- Sibling burying: once any card of a word is reviewed today the word's other cards wait.
  The learning day rolls over at 04:00 local. Learning steps of the same card are not blocked.
- New cards are served in `cards.id` order, so `deal_missing_cards` inserts ordered by
  word, then card type (recognition before recall).
- One DB transaction per Telegram update; handlers receive `session` and `user`.
- `PrivateChatsOnlyMiddleware` drops group chats and senderless updates. `DbSessionMiddleware`
  refreshes the user's Telegram profile and `last_seen_at`, clears `blocked_at`, and writes one
  `usage_events` row per update (kind + action from `describe_update`). Never store message
  text in `usage_events`.
- Reminders run one short transaction per learner: lock the `users` row, claim the reminder
  (`last_reminded_on`), commit, then send outside any transaction. Never hold a row lock across
  a Telegram call: every update writes to `users`. A lost send skips that day's reminder (at
  most once, never repeated); `TelegramForbiddenError` sets `blocked_at`.
- Code that commits on its own (reminders) takes a session factory; tests pass the
  `session_factory` fixture, whose commits become savepoints of the per-test rollback.
- Races need real transactions: `tests/test_concurrency.py` commits for real and deletes its
  rows afterwards. Derive cleanup keys with `lemma_key()`, a leaked row breaks every later test.
- `get_or_create_user` inserts with ON CONFLICT DO NOTHING: a new learner's first tap arrives
  as two concurrent updates (`my_chat_member` + `/start`).
- The reminder claim locks the user row with SKIP LOCKED: a learner mid-update is retried next
  minute, never waited on. It checks cards due now only, without learn-ahead.
- One message per card: the question is a voice message with the text as caption (raw HTML
  under 1024 chars, otherwise plain text), edited in place into the answer and then the result.
  FSM data keeps `card_message_id` and `card_has_caption` for that. `/check` edits one message
  through the whole pass. Never add a second message where an edit will do, but never leave
  the learner without buttons either: when an edit fails or the caption would exceed 1024,
  `_replace_card` removes the old buttons and sends the text anew. "message is not modified"
  counts as success (repeated tap or Telegram retry).
- `show_next_card` stores the card in FSM data before sending it (synthesis is slow) and adds
  the message id after.
- Deploy: Coolify app `greek-trainer-bot` builds `main` on push (watch paths include `seeds/**`);
  env vars live in Coolify, never in the repo.
- Bot messages use HTML parse mode: pass every user- or dictionary-supplied string through
  `html.escape` (see `render.py`).
- Settings from `.env` are copied to a `users` row on first contact; later changes to `.env`
  do not touch existing users.

## Pitfalls
- asyncpg rejects two statements in one `execute`; send them separately.
- Async SQLAlchemy raises `MissingGreenlet` on any lazy load. Pass collections to constructors
  (`Word(cards=[...])`) or `selectinload` them; never touch an unloaded relationship.
- `pydantic-settings` decodes list-like env values as JSON; `ADMIN_TELEGRAM_IDS` is
  comma-separated via `NoDecode` plus a validator in `config.py`.
- Telegram may return a sent voice as `audio` or `document`; `tts.py` handles all three.
- Test fixtures build the schema with `metadata.create_all`, so a passing test suite does not
  prove a migration works. Run the migration check.

## Style
Shared Python conventions: `.claude/rules/code-style-python.md`. On top of that:
- Comments only for a why, an external constraint, or an invariant the code cannot express.
- Russian strings live in `render.py`, handler `HELP` text and error messages; keep them out
  of `domain/` and `services.py` except for `AppError` messages shown to the learner.
