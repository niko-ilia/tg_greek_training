"""Bulk-import words from a text file: `python -m greek_trainer.seed seeds/a1.txt`.

Words already in the dictionary are skipped, so a seed file can be re-run.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from greek_trainer.config import load_settings
from greek_trainer.db.session import create_engine, create_session_factory
from greek_trainer.domain.word_input import parse_words
from greek_trainer.errors import DuplicateWordError
from greek_trainer.services import add_word


async def seed(path: Path) -> None:
    settings = load_settings()
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    drafts = parse_words(path.read_text(encoding="utf-8"))
    try:
        async with session_factory() as session, session.begin():
            for draft in drafts:
                try:
                    async with session.begin_nested():
                        word = await add_word(session, draft)
                except DuplicateWordError:
                    print(f"skip  {draft.lemma} (already added)")
                    continue
                print(f"added {word.lemma}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m greek_trainer.seed <file>")
    asyncio.run(seed(Path(sys.argv[1])))
