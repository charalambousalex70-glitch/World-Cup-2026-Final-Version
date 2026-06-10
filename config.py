"""Seed the database with a demo sweepstake mirroring the prototype.

Run:  python -m app.seed
Creates a demo admin user, a 'Office World Cup' sweepstake, 10 participants,
an approved draw, and sample finished/live fixtures so the leaderboard is
populated immediately.

Login afterwards with:  you@example.com / demo1234
"""
import asyncio

from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal, Base, engine
from app.core.security import hash_password
from app.models import (
    Allocation, Fixture, Participant, PrizeTier, Sweepstake, Team, User,
)

PLAYERS = [
    ("you@example.com", "You", "#ffc83d"), ("alex@x.com", "Alex", "#ffc83d"),
    ("maria@x.com", "Maria", "#4d8dff"), ("john@x.com", "John", "#2fe28a"),
    ("sofia@x.com", "Sofia", "#ff5b6e"), ("tom@x.com", "Tom", "#b07bff"),
    ("priya@x.com", "Priya", "#ff9d4d"), ("liam@x.com", "Liam", "#4de2d6"),
    ("noah@x.com", "Noah", "#e2c84d"), ("emma@x.com", "Emma", "#5d7bff"),
]
TEAMS = [
    ("Brazil", "🇧🇷", "Winner"), ("France", "🇫🇷", "SF"), ("England", "🏴", "Final"),
    ("Argentina", "🇦🇷", "QF"), ("Spain", "🇪🇸", "Out"), ("Germany", "🇩🇪", "QF"),
    ("Portugal", "🇵🇹", "R16"), ("Netherlands", "🇳🇱", "Out"),
    ("Belgium", "🇧🇪", "R16"), ("Croatia", "🇭🇷", "SF"),
]
FIXTURES = [
    ("Brazil", "Croatia", 2, 1, "FINISHED", "QF"),
    ("France", "England", 1, 1, "LIVE", "SF"),
    ("Spain", "Germany", 0, 2, "FINISHED", "QF"),
    ("Argentina", "Netherlands", 3, 2, "FINISHED", "QF"),
    ("Portugal", "Belgium", 1, 0, "FINISHED", "R16"),
]


async def seed() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        # Idempotent: clear any prior demo sweepstake.
        existing = (
            await db.execute(select(Sweepstake).where(Sweepstake.invite_code == "WC26DEMO"))
        ).scalar_one_or_none()
        if existing:
            await db.delete(existing)
            await db.flush()

        # Users (reuse if present).
        users = []
        for email, name, color in PLAYERS:
            u = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
            if not u:
                u = User(email=email, username=name,
                         hashed_password=hash_password("demo1234"), avatar_color=color)
                db.add(u)
                await db.flush()
            users.append(u)

        admin = users[0]
        sweep = Sweepstake(
            name="Office World Cup", tournament_name="World Cup 2026",
            entry_fee=20, currency="EUR", max_participants=10,
            invite_code="WC26DEMO", status="active", draw_approved=True,
            admin_id=admin.id,
        )
        db.add(sweep)
        await db.flush()

        for rank, pct in [(1, 60), (2, 25), (3, 15)]:
            db.add(PrizeTier(sweepstake_id=sweep.id, rank=rank, percentage=pct))

        teams = []
        for name, flag, stage in TEAMS:
            t = Team(sweepstake_id=sweep.id, name=name, flag_emoji=flag,
                     stage=stage, eliminated=(stage == "Out"))
            db.add(t)
            teams.append(t)
        await db.flush()

        # Participants + allocations (player i -> team i).
        for i, u in enumerate(users):
            part = Participant(sweepstake_id=sweep.id, user_id=u.id,
                               has_paid=(i not in (4, 8)))
            db.add(part)
            await db.flush()
            db.add(Allocation(sweepstake_id=sweep.id, participant_id=part.id, team_id=teams[i].id))

        for h, a, hs, as_, st, stage in FIXTURES:
            db.add(Fixture(sweepstake_id=sweep.id, home_team=h, away_team=a,
                           home_score=hs, away_score=as_, status=st, stage=stage))

        await db.commit()
        print("Seeded 'Office World Cup' (invite WC26DEMO).")
        print("Login: you@example.com / demo1234")


if __name__ == "__main__":
    asyncio.run(seed())
