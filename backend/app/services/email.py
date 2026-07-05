"""Admin "League Update" emails.

One shared template — build_update_email(sweep, participant, shared) — produces
personalized HTML for a single recipient, reusing the SAME data the app already
computes (compute_leaderboard for standings, team.stage for progression, the
fixtures list for upcoming matches, participant.has_paid for the reminder).

Sending: if SMTP is configured (see core.config.email_configured) we dispatch
via smtplib in a threadpool. If NOT configured, we run in DRY-RUN mode — every
email is generated and logged, and we report what WOULD be sent, but nothing is
dispatched. We never guess a provider or key.
"""
from __future__ import annotations

import asyncio
import html
import logging
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.core.config import settings
from app.services.scoring import compute_leaderboard
from app.services.tournament import STAGE_LABELS, STAGE_ORDER

logger = logging.getLogger("luckpot.email")

_CUR = {"EUR": "\u20ac", "GBP": "\u00a3", "USD": "$"}
_TERMINAL = {"Out", "3rd", "4th", "Runner-up", "Winner"}


def _esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _kickoff_label(iso) -> str:
    if not iso:
        return "TBC"
    try:
        d = iso if isinstance(iso, datetime) else datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except Exception:
        return "TBC"
    return d.strftime("%a %d %b, %H:%M")


def compute_shared_context(sweep, fixtures) -> dict:
    """League-wide data computed ONCE and reused for every recipient's email.

    Reuses compute_leaderboard (the Board's source of truth). Derives the
    current tournament stage from the furthest stage any team has reached, and
    the next upcoming fixtures from scheduled matches.
    """
    board = compute_leaderboard(sweep, fixtures)

    # participant_id -> row, and team_name -> owner name (for fixture phrasing)
    owner_by_team = {}
    for part in sweep.participants:
        alloc = getattr(part, "allocation", None)
        if alloc and alloc.team:
            owner_by_team[alloc.team.name] = part.user.username

    # Current stage = furthest stage reached across all teams.
    furthest = "Group"
    for row in board:
        st = row.stage
        base = st if st in STAGE_ORDER else "Group"
        try:
            if STAGE_ORDER.index(base) > STAGE_ORDER.index(furthest):
                furthest = base
        except ValueError:
            pass
    stage_label = STAGE_LABELS.get(furthest, "Group stage")

    # Teams alive vs eliminated, with owner names.
    alive, eliminated = [], []
    for row in board:
        entry = (row.participant_name, row.team_name, row.stage)
        if row.eliminated or row.stage in _TERMINAL:
            eliminated.append(entry)
        else:
            alive.append(entry)

    # Next upcoming fixtures (soonest first), phrased with owners.
    upcoming = []
    fx_sorted = sorted(
        [f for f in fixtures if f.status == "SCHEDULED" and f.kickoff],
        key=lambda f: f.kickoff,
    )
    for f in fx_sorted[:8]:
        upcoming.append({
            "home": f.home_team, "away": f.away_team,
            "home_owner": owner_by_team.get(f.home_team),
            "away_owner": owner_by_team.get(f.away_team),
            "kickoff": _kickoff_label(f.kickoff),
        })

    return {
        "board": board, "stage_label": stage_label,
        "alive": alive, "eliminated": eliminated, "upcoming": upcoming,
        "currency": _CUR.get(sweep.currency, "\u20ac"),
    }


def build_update_email(sweep, participant, shared: dict) -> tuple[str, str]:
    """Return (subject, html_body) personalized for one participant.

    Uses the shared league context plus this participant's own team + payment
    status. Reminder tone is deliberately friendly; never alarming.
    """
    board = shared["board"]
    cur = shared["currency"]
    me_row = next((r for r in board if r.participant_id == participant.id), None)
    my_team = me_row.team_name if me_row else None
    my_rank = me_row.rank if me_row else None

    g = "#ffc83d"
    navy = "#0a0f1d"
    body = []
    body.append(f'<div style="font-family:Arial,Helvetica,sans-serif;max-width:600px;margin:0 auto;'
                f'background:{navy};color:#eaf0ff;padding:24px;border-radius:14px">')
    body.append(f'<h1 style="color:{g};font-size:22px;margin:0 0 4px">{_esc(sweep.name)}</h1>')
    body.append(f'<p style="color:#9fb0d4;font-size:13px;margin:0 0 20px">League update &middot; '
                f'{_esc(shared["stage_label"])}</p>')

    # 1) Leaderboard snapshot (top 10, highlight recipient even if outside it)
    body.append(f'<h2 style="color:#fff;font-size:16px;margin:18px 0 8px">\U0001F3C6 Leaderboard</h2>')
    body.append('<table style="width:100%;border-collapse:collapse;font-size:14px">')
    top = board[:10]
    for r in top:
        mine = (my_rank is not None and r.rank == my_rank)
        bg = f'background:rgba(255,200,61,.14);' if mine else ''
        strong = 'font-weight:700;' if mine else ''
        body.append(
            f'<tr style="{bg}"><td style="padding:6px 8px;{strong}color:{g}">{r.rank}</td>'
            f'<td style="padding:6px 8px;{strong}">{_esc(r.participant_name)}{" (you)" if mine else ""}</td>'
            f'<td style="padding:6px 8px;color:#9fb0d4">{_esc(r.team_name)}</td>'
            f'<td style="padding:6px 8px;text-align:right;{strong}">{r.points} pts</td></tr>')
    # If the recipient is outside the top 10, append their own row.
    if my_rank and my_rank > 10 and me_row:
        body.append('<tr><td colspan="4" style="padding:4px 8px;color:#5f6b86;text-align:center">&middot;&middot;&middot;</td></tr>')
        body.append(
            f'<tr style="background:rgba(255,200,61,.14)"><td style="padding:6px 8px;font-weight:700;color:{g}">{me_row.rank}</td>'
            f'<td style="padding:6px 8px;font-weight:700">{_esc(me_row.participant_name)} (you)</td>'
            f'<td style="padding:6px 8px;color:#9fb0d4">{_esc(me_row.team_name)}</td>'
            f'<td style="padding:6px 8px;text-align:right;font-weight:700">{me_row.points} pts</td></tr>')
    body.append('</table>')

    # 2) Progression status
    body.append(f'<h2 style="color:#fff;font-size:16px;margin:22px 0 8px">\u26bd Tournament status</h2>')
    body.append(f'<p style="font-size:14px;margin:0 0 8px">Stage: <b style="color:{g}">{_esc(shared["stage_label"])}</b></p>')
    if shared["alive"]:
        alive_str = ", ".join(f'{_esc(o)}\u2019s {_esc(t)}' for (o, t, _s) in shared["alive"])
        body.append(f'<p style="font-size:13px;margin:0 0 6px"><span style="color:#2fe28a">Still in:</span> {alive_str}</p>')
    if shared["eliminated"]:
        elim_str = ", ".join(f'{_esc(o)}\u2019s {_esc(t)}' for (o, t, _s) in shared["eliminated"])
        body.append(f'<p style="font-size:13px;margin:0 0 6px;color:#9fb0d4"><span>Eliminated:</span> {elim_str}</p>')

    # 3) Upcoming fixtures
    if shared["upcoming"]:
        body.append(f'<h2 style="color:#fff;font-size:16px;margin:22px 0 8px">\U0001F4C5 Coming up</h2>')
        for u in shared["upcoming"]:
            home = f'{_esc(u["home_owner"])}\u2019s {_esc(u["home"])}' if u["home_owner"] else _esc(u["home"])
            away = f'{_esc(u["away_owner"])}\u2019s {_esc(u["away"])}' if u["away_owner"] else _esc(u["away"])
            body.append(f'<p style="font-size:13px;margin:0 0 6px">{home} <span style="color:#5f6b86">vs</span> {away} '
                        f'<span style="color:#9fb0d4">&mdash; {_esc(u["kickoff"])}</span></p>')

    # 4) Payment reminder (conditional, friendly)
    has_paid = bool(getattr(participant, "has_paid", False))
    if has_paid:
        body.append(f'<p style="font-size:13px;margin:22px 0 0;color:#2fe28a">Thanks for settling your entry \U0001F64C</p>')
    else:
        fee = getattr(sweep, "entry_fee", None)
        amount = f'{cur}{fee}' if fee is not None else 'your entry fee'
        link = getattr(sweep, "pay_link", None)
        link_html = (f' You can pay here: <a href="{_esc(link)}" style="color:{g}">{_esc(link)}</a>.'
                     if link else '')
        body.append(
            f'<div style="margin:22px 0 0;padding:12px 14px;background:rgba(255,255,255,.05);border-radius:10px">'
            f'<p style="font-size:13px;margin:0;color:#cdd6ee">Quick reminder: your entry fee of {amount} is still '
            f'outstanding.{link_html}</p></div>')

    body.append(f'<p style="font-size:11px;color:#5f6b86;margin:24px 0 0">You\u2019re receiving this because you\u2019re in '
                f'{_esc(sweep.name)}.</p>')
    body.append('</div>')

    subject = f'{sweep.name}: league update ({shared["stage_label"]})'
    return subject, "".join(body)


def _send_one_smtp(to_email: str, subject: str, html_body: str) -> None:
    """Blocking SMTP send for a single message (run in a threadpool)."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_FROM_EMAIL
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20) as s:
        if settings.SMTP_USE_TLS:
            s.starttls()
        s.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        s.sendmail(settings.SMTP_FROM_EMAIL, [to_email], msg.as_string())


async def send_update_emails(sweep, fixtures) -> dict:
    """Generate a personalized email for every participant and send it.

    Returns a summary: {dry_run, sent, failed, total, log:[...]}. In dry-run
    mode (no SMTP configured) nothing is dispatched but every email is built and
    logged, so the admin sees exactly what would go out.
    """
    shared = compute_shared_context(sweep, fixtures)
    dry_run = not settings.email_configured
    sent = failed = 0
    log = []

    for part in sweep.participants:
        email = getattr(part.user, "email", None)
        name = getattr(part.user, "username", "player")
        if not email:
            failed += 1
            log.append({"recipient": name, "email": None, "ok": False,
                        "detail": "no email on file",
                        "at": datetime.now(timezone.utc).isoformat()})
            continue
        try:
            subject, body = build_update_email(sweep, part, shared)
            if dry_run:
                logger.info("DRY-RUN update email for %s <%s> (%d chars)", name, email, len(body))
                ok, detail = True, "generated (dry-run — not sent)"
            else:
                await asyncio.to_thread(_send_one_smtp, email, subject, body)
                ok, detail = True, "sent"
                logger.info("Sent update email to %s <%s>", name, email)
            sent += 1 if not dry_run else 0
            log.append({"recipient": name, "email": email, "ok": ok, "detail": detail,
                        "at": datetime.now(timezone.utc).isoformat()})
        except Exception as e:  # per-recipient failure never aborts the batch
            failed += 1
            logger.warning("Update email FAILED for %s <%s>: %s", name, email, e)
            log.append({"recipient": name, "email": email, "ok": False,
                        "detail": str(e)[:200],
                        "at": datetime.now(timezone.utc).isoformat()})

    return {
        "dry_run": dry_run,
        "configured": settings.email_configured,
        "sent": sent,
        "failed": failed,
        "generated": len([x for x in log if x["ok"]]),
        "total": len(sweep.participants),
        "log": log,
    }
