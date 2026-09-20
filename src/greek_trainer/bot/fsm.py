"""FSM storage in Postgres, so a restart keeps the card in flight."""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from greek_trainer.db.models import FsmState


def _key(key: StorageKey) -> str:
    parts = (
        key.bot_id,
        key.chat_id,
        key.user_id,
        key.thread_id or "",
        key.business_connection_id or "",
        key.destiny,
    )
    return ":".join(str(part) for part in parts)


class DbStorage(BaseStorage):
    """Keeps aiogram's per-chat state in `fsm_states`.

    It runs in its own transaction, outside the one the update handler gets:
    a handler that fails leaves the state it had already written in place.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        name = state.state if isinstance(state, State) else state
        await self._write(key, state=name)

    async def get_state(self, key: StorageKey) -> str | None:
        return await self._read(key, FsmState.state)

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        await self._write(key, data=dict(data))

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        return await self._read(key, FsmState.data) or {}

    async def close(self) -> None:
        pass

    async def _write(self, key: StorageKey, **values: Any) -> None:
        now = datetime.now(UTC)
        stmt = insert(FsmState).values(key=_key(key), updated_at=now, **values)
        async with self._session_factory() as session, session.begin():
            await session.execute(
                stmt.on_conflict_do_update(
                    index_elements=[FsmState.key], set_={**values, "updated_at": now}
                )
            )

    async def _read(self, key: StorageKey, column: Any) -> Any:
        async with self._session_factory() as session:
            return await session.scalar(select(column).where(FsmState.key == _key(key)))
