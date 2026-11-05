"""Request dependencies: database session, player directory, Sleeper client."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from api.services.players import PlayerDirectory
from ffml.data.sleeper import SleeperClient


def db_session(request: Request) -> Iterator[Session]:
    with request.app.state.db.session() as session:
        yield session


def player_directory(request: Request) -> PlayerDirectory:
    return request.app.state.players.get()


class SleeperHolder:
    """One SleeperClient (and so one D-025 token bucket) per process (QA-F04).

    The client is built on first use from ``app.state.sleeper_factory`` (tests
    swap the factory before their first request) and closed in the lifespan.
    """

    def __init__(self) -> None:
        self._client: SleeperClient | None = None
        self._lock = threading.Lock()

    def get(self, factory: Callable[[], SleeperClient]) -> SleeperClient:
        with self._lock:
            if self._client is None:
                self._client = factory()
            return self._client

    def close(self) -> None:
        with self._lock:
            client, self._client = self._client, None
        if client is not None:
            client.close()


def sleeper_client(request: Request) -> SleeperClient:
    return request.app.state.sleeper.get(request.app.state.sleeper_factory)


DbSession = Annotated[Session, Depends(db_session)]
Directory = Annotated[PlayerDirectory, Depends(player_directory)]
Sleeper = Annotated[SleeperClient, Depends(sleeper_client)]
