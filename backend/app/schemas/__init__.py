"""Pydantic schemas — request bodies and response models."""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ---------- Auth / User ----------
class UserCreate(BaseModel):
    email: EmailStr
    username: str = Field(min_length=2, max_length=60)
    password: str = Field(min_length=6, max_length=128)


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: EmailStr
    username: str
    avatar_url: str | None = None
    avatar_color: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ---------- Sweepstake ----------
class PrizeTierIn(BaseModel):
    rank: int
    percentage: float


class SweepstakeCreate(BaseModel):
    name: str = Field(max_length=120)
    tournament_name: str = Field(max_length=120)
    competition_code: str | None = None
    entry_fee: float = 0
    currency: str = "EUR"
    max_participants: int = Field(default=10, ge=2, le=64)
    start_date: datetime | None = None
    prize_tiers: list[PrizeTierIn] = Field(default_factory=lambda: [
        PrizeTierIn(rank=1, percentage=60),
        PrizeTierIn(rank=2, percentage=25),
        PrizeTierIn(rank=3, percentage=15),
    ])


class PrizeTierOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    rank: int
    percentage: float


class ParticipantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    has_paid: bool
    user: UserOut


class TeamOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    flag_emoji: str
    crest_url: str | None = None
    stage: str
    eliminated: bool


class SweepstakeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    tournament_name: str
    competition_code: str | None
    entry_fee: float
    currency: str
    max_participants: int
    invite_code: str
    status: str
    draw_approved: bool
    start_date: datetime | None
    admin_id: uuid.UUID
    prize_pool: float
    participants: list[ParticipantOut] = []
    prize_tiers: list[PrizeTierOut] = []


class JoinRequest(BaseModel):
    invite_code: str


# ---------- Draw ----------
class AllocationOut(BaseModel):
    participant_id: uuid.UUID
    participant_name: str
    team_id: uuid.UUID
    team_name: str
    flag_emoji: str


# ---------- Leaderboard ----------
class LeaderboardRow(BaseModel):
    rank: int
    participant_id: uuid.UUID
    participant_name: str
    avatar_color: str
    team_name: str
    flag_emoji: str
    stage: str
    points: int
    wins: int = 0
    draws: int = 0
    losses: int = 0
    eliminated: bool
    potential_payout: float


# ---------- Fixtures ----------
class FixtureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    home_team: str
    away_team: str
    home_score: int | None
    away_score: int | None
    status: str
    stage: str
    venue: str | None = None
    referee: str | None = None
    detail: str | None = None
    kickoff: datetime | None = None
    kickoff: datetime | None


# ---------- Payment ----------
class PaymentUpdate(BaseModel):
    has_paid: bool


# ---------- Notifications ----------
class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    icon: str
    title: str
    body: str | None
    read: bool
    created_at: datetime


class CommentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    body: str
    created_at: datetime
    username: str = ""
    avatar_color: str = "#888"
    reactions: dict = {}


class CommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=500)


class PredictionIn(BaseModel):
    fixture_id: uuid.UUID
    home: int = Field(ge=0, le=20)
    away: int = Field(ge=0, le=20)


class PredictionOut(BaseModel):
    fixture_id: uuid.UUID
    home: int
    away: int


class PredBoardRow(BaseModel):
    username: str
    avatar_color: str = "#888"
    points: int = 0
    exact: int = 0
    results: int = 0
