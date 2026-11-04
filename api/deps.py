"""Request dependencies: database session, player directory, Sleeper client."""

from __future__ import annotations

from collections.abc import Iterator
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


def sleeper_client(request: Request) -> Iterator[SleeperClient]:
    client = request.app.state.sleeper_factory()
    try:
        yield client
    finally:
        client.close()


DbSession = Annotated[Session, Depends(db_session)]
Directory = Annotated[PlayerDirectory, Depends(player_directory)]
Sleeper = Annotated[SleeperClient, Depends(sleeper_client)]
