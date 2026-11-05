"""Request bodies shared by routers (docs/API.md). Responses are plain dicts documented there."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

PlayerId = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9._-]{1,32}$")
]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]

#: League settings the app stores (copied from Sleeper, editable in the manual form). Free-form
#: keys and unbounded values are refused (QA-F02).
SLEEPER_SETTINGS_KEYS: tuple[str, ...] = (
    "num_teams",
    "playoff_week_start",
    "playoff_teams",
    "start_week",
    "leg",
    "last_scored_leg",
    "trade_deadline",
    "waiver_type",
    "waiver_budget",
    "reserve_slots",
    "taxi_slots",
    "best_ball",
    "type",
)
SettingKey = Literal[
    "num_teams",
    "playoff_week_start",
    "playoff_teams",
    "start_week",
    "leg",
    "last_scored_leg",
    "trade_deadline",
    "waiver_type",
    "waiver_budget",
    "reserve_slots",
    "taxi_slots",
    "best_ball",
    "type",
    "status",
]
SettingValue = (
    bool
    | Annotated[int, Field(ge=-1_000_000, le=1_000_000)]
    | Annotated[float, Field(allow_inf_nan=False, ge=-1_000_000, le=1_000_000)]
    | Annotated[str, StringConstraints(max_length=100)]
    | None
)
LeagueSettings = dict[SettingKey, SettingValue]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _unique(values: list[str]) -> list[str]:
    if len(set(values)) != len(values):
        raise ValueError("player ids must be distinct")
    return values


class StartSitRequest(_Body):
    player_ids: list[PlayerId] = Field(min_length=2, max_length=4)
    week: int | None = Field(default=None, ge=1, le=18)

    _distinct = field_validator("player_ids")(_unique)


class TradeRequest(_Body):
    give: list[PlayerId] = Field(min_length=1, max_length=8)
    receive: list[PlayerId] = Field(min_length=1, max_length=8)
    league_team_id: str | None = Field(default=None, max_length=64)
    partner_team_id: str | None = Field(default=None, max_length=64)

    _distinct_give = field_validator("give")(_unique)
    _distinct_receive = field_validator("receive")(_unique)


class SleeperImportRequest(_Body):
    league_id: Annotated[str, StringConstraints(pattern=r"^[0-9]{1,24}$")] | None = None
    username: (
        Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9_]{1,40}$")]
        | None
    ) = None
    season: int | None = Field(default=None, ge=2017, le=2100)


class ScoringInput(_Body):
    """Either a preset (optionally with overrides) or a full Sleeper-style key map."""

    preset: str | None = Field(default=None, pattern=r"^(ppr|half|std)$")
    overrides: dict[str, float] = Field(default_factory=dict, max_length=200)
    settings: dict[str, float] | None = Field(default=None, max_length=200)


class ManualTeamInput(_Body):
    name: ShortText
    is_me: bool = False
    players: list[PlayerId] = Field(default_factory=list, max_length=60)


class ManualLeagueRequest(_Body):
    name: ShortText
    season: int = Field(ge=2017, le=2100)
    num_teams: int = Field(ge=2, le=32)
    scoring: ScoringInput
    roster_positions: list[str] = Field(min_length=1, max_length=40)
    settings: LeagueSettings = Field(default_factory=dict, max_length=50)
    teams: list[ManualTeamInput] = Field(default_factory=list, max_length=32)


class LeagueSettingsUpdate(_Body):
    name: ShortText | None = None
    scoring: ScoringInput | None = None
    roster_positions: list[str] | None = Field(default=None, min_length=1, max_length=40)
    settings: LeagueSettings | None = Field(default=None, max_length=50)


class MyTeamRequest(_Body):
    league_team_id: str = Field(min_length=1, max_length=64)


__all__ = [
    "SLEEPER_SETTINGS_KEYS",
    "LeagueSettings",
    "LeagueSettingsUpdate",
    "ManualLeagueRequest",
    "ManualTeamInput",
    "MyTeamRequest",
    "ScoringInput",
    "SleeperImportRequest",
    "StartSitRequest",
    "TradeRequest",
]
