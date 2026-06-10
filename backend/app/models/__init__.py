"""SQLAlchemy ORM models — the full database schema.

Entity overview
---------------
User           registered account
Sweepstake     a single competition instance (created by an admin User)
Participant    a User's membership in a Sweepstake (join + payment + allocation)
Team           a tournament team (synced from football API per sweepstake's tournament)
Allocation     the permanent draw result linking a Participant to a Team
Fixture        a match (synced from football API)
PrizeTier      a row of the prize distribution (rank + percentage)
Notification   per-user activity feed item
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    username: Mapped[str] = mapped_column(String(60), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    avatar_color: Mapped[str] = mapped_column(String(9), default="#ffc83d")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    sweepstakes: Mapped[list["Sweepstake"]] = relationship(back_populates="admin")
    participations: Mapped[list["Participant"]] = relationship(back_populates="user")


class Sweepstake(Base):
    __tablename__ = "sweepstakes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    tournament_name: Mapped[str] = mapped_column(String(120), nullable=False)
    # external competition id from the football API (e.g. "WC" / 2000)
    competition_code: Mapped[str | None] = mapped_column(String(40))

    entry_fee: Mapped[float] = mapped_column(Float, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    max_participants: Mapped[int] = mapped_column(Integer, default=10)

    invite_code: Mapped[str] = mapped_column(String(16), unique=True, index=True, nullable=False)
    start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|drawn|active|finished
    draw_approved: Mapped[bool] = mapped_column(Boolean, default=False)

    admin_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    admin: Mapped["User"] = relationship(back_populates="sweepstakes")
    participants: Mapped[list["Participant"]] = relationship(
        back_populates="sweepstake", cascade="all, delete-orphan"
    )
    teams: Mapped[list["Team"]] = relationship(
        back_populates="sweepstake", cascade="all, delete-orphan"
    )
    prize_tiers: Mapped[list["PrizeTier"]] = relationship(
        back_populates="sweepstake", cascade="all, delete-orphan"
    )

    @property
    def prize_pool(self) -> float:
        # Guard against the relationship not being loaded (avoids triggering a
        # lazy DB load during serialization). Returns 0 rather than raising.
        try:
            return (self.entry_fee or 0) * len(self.participants)
        except Exception:
            return 0.0


class Participant(Base):
    __tablename__ = "participants"
    __table_args__ = (UniqueConstraint("sweepstake_id", "user_id", name="uq_part_user"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    sweepstake_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sweepstakes.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    has_paid: Mapped[bool] = mapped_column(Boolean, default=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    sweepstake: Mapped["Sweepstake"] = relationship(back_populates="participants")
    user: Mapped["User"] = relationship(back_populates="participations")
    allocation: Mapped["Allocation | None"] = relationship(
        back_populates="participant", cascade="all, delete-orphan", uselist=False
    )


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    sweepstake_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sweepstakes.id", ondelete="CASCADE"), index=True
    )
    external_id: Mapped[str | None] = mapped_column(String(40))  # football API team id
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    flag_emoji: Mapped[str] = mapped_column(String(8), default="🏳️")
    crest_url: Mapped[str | None] = mapped_column(String(500))
    # current tournament stage: Group|R16|QF|SF|Final|Winner|Out
    stage: Mapped[str] = mapped_column(String(12), default="Group")
    eliminated: Mapped[bool] = mapped_column(Boolean, default=False)

    sweepstake: Mapped["Sweepstake"] = relationship(back_populates="teams")
    allocation: Mapped["Allocation | None"] = relationship(
        back_populates="team", uselist=False
    )


class Allocation(Base):
    """The permanent, immutable result of the draw."""
    __tablename__ = "allocations"
    __table_args__ = (
        UniqueConstraint("sweepstake_id", "team_id", name="uq_alloc_team"),
        UniqueConstraint("participant_id", name="uq_alloc_participant"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    sweepstake_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sweepstakes.id", ondelete="CASCADE"), index=True
    )
    participant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("participants.id", ondelete="CASCADE")
    )
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    participant: Mapped["Participant"] = relationship(back_populates="allocation")
    team: Mapped["Team"] = relationship(back_populates="allocation")


class Fixture(Base):
    __tablename__ = "fixtures"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    sweepstake_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sweepstakes.id", ondelete="CASCADE"), index=True
    )
    external_id: Mapped[str | None] = mapped_column(String(40), index=True)
    home_team: Mapped[str] = mapped_column(String(80))
    away_team: Mapped[str] = mapped_column(String(80))
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="SCHEDULED")  # SCHEDULED|LIVE|FINISHED
    stage: Mapped[str] = mapped_column(String(20), default="GROUP")
    venue: Mapped[str | None] = mapped_column(String(160))  # stadium, city
    referee: Mapped[str | None] = mapped_column(String(120))
    detail: Mapped[str | None] = mapped_column(Text)  # JSON: goals, halftime score
    kickoff: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class PrizeTier(Base):
    __tablename__ = "prize_tiers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    sweepstake_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sweepstakes.id", ondelete="CASCADE"), index=True
    )
    rank: Mapped[int] = mapped_column(Integer)        # 1 = winner, 2 = runner-up...
    percentage: Mapped[float] = mapped_column(Float)  # e.g. 60.0

    sweepstake: Mapped["Sweepstake"] = relationship(back_populates="prize_tiers")


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    sweepstake_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sweepstakes.id", ondelete="CASCADE"))
    icon: Mapped[str] = mapped_column(String(8), default="🔔")
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str | None] = mapped_column(Text)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Comment(Base):
    """League chat: short messages between members of a sweepstake."""
    __tablename__ = "comments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    sweepstake_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sweepstakes.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    body: Mapped[str] = mapped_column(String(500))
    reactions: Mapped[str | None] = mapped_column(Text)  # JSON {"👍":["Alex"],...}
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    user: Mapped["User"] = relationship()
