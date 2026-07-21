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
    # A coherent finished-tournament state. Placements are internally consistent
    # with the bracket in FIXTURES below (Brazil champion, Argentina runner-up,
    # France 3rd, Croatia 4th, the rest out at the round they lost).
    ("Brazil", "🇧🇷", "Winner"), ("Argentina", "🇦🇷", "Runner-up"),
    ("France", "🇫🇷", "3rd"), ("Croatia", "🇭🇷", "4th"),
    ("Spain", "🇪🇸", "QF"), ("Germany", "🇩🇪", "QF"),
    ("Portugal", "🇵🇹", "R16"), ("Netherlands", "🇳🇱", "R16"),
    ("Belgium", "🇧🇪", "R16"), ("England", "🏴", "R16"),
]
# eliminated = everyone except the champion. Set explicitly per team below.
_NOT_ELIMINATED = {"Winner"}
FIXTURES = [
    # Semi-finals
    ("Brazil", "France", 2, 1, "FINISHED", "SF"),
    ("Argentina", "Croatia", 3, 2, "FINISHED", "SF"),
    # 3rd-place play-off: France beat Croatia → France 3rd, Croatia 4th
    ("France", "Croatia", 2, 0, "FINISHED", "3rd_playoff"),
    # Final: Brazil beat Argentina → Brazil champion, Argentina runner-up
    ("Brazil", "Argentina", 1, 0, "FINISHED", "Final"),
    # A couple of earlier-round results for flavour
    ("Spain", "Portugal", 2, 1, "FINISHED", "QF"),
    ("Germany", "Belgium", 1, 0, "FINISHED", "QF"),
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
            # Everyone except the champion is eliminated (their run has ended),
            # including finished placements Runner-up/3rd/4th and round exits.
            t = Team(sweepstake_id=sweep.id, name=name, flag_emoji=flag,
                     stage=stage, eliminated=(stage not in _NOT_ELIMINATED))
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
