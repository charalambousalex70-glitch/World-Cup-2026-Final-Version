"""Remove the demo 'Office World Cup' sweepstake and its fake players.

Run once in the Render Shell to clean out the seeded demo data while keeping
any REAL accounts people have registered:

    python -m app.clean_demo

It deletes:
  - the demo sweepstake (invite code WC26DEMO) and everything attached to it
  - the demo player accounts (you@example.com, alex@x.com, maria@x.com, ...)
It does NOT touch real accounts created via the app's Create Account button.
"""
import asyncio

from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal
from app.models import Sweepstake, User

DEMO_EMAILS = [
    "you@example.com", "alex@x.com", "maria@x.com", "john@x.com",
    "sofia@x.com", "tom@x.com", "priya@x.com", "liam@x.com",
    "noah@x.com", "emma@x.com",
]


async def clean() -> None:
    async with AsyncSessionLocal() as db:
        # Delete the demo sweepstake (cascades to participants, teams,
        # allocations, fixtures, prize tiers, notifications).
        demo = (
            await db.execute(
                select(Sweepstake).where(Sweepstake.invite_code == "WC26DEMO")
            )
        ).scalar_one_or_none()
        if demo:
            await db.delete(demo)
            print("Deleted demo sweepstake 'Office World Cup'.")
        else:
            print("No demo sweepstake found (already clean).")

        # Delete the demo user accounts.
        removed = 0
        for email in DEMO_EMAILS:
            u = (
                await db.execute(select(User).where(User.email == email))
            ).scalar_one_or_none()
            if u:
                await db.delete(u)
                removed += 1
        await db.commit()
        print(f"Removed {removed} demo accounts. Real accounts are untouched.")
        print("Done — the app now starts clean for real users.")


if __name__ == "__main__":
    asyncio.run(clean())
