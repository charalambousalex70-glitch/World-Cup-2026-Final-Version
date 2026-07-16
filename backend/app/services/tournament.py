"""Single source of truth for the World Cup 2026 tournament structure.

Everything stage-related lives here so the scoring model, stage progression, and
the front-end progress bar all agree. The 2026 format:

    48 teams · 12 groups of 4 · 104 total matches
    Top 2 per group + 8 best 3rd-placed teams advance (32 teams to the knockouts)
    Knockouts: Round of 32 → Round of 16 → Quarter-finals → Semi-finals →
               (3rd-place play-off) → Final

Match-count layout (cumulative, used for the Home progress bar):
    Group stage    72   (matches   1– 72)
    Round of 32    16   (matches  73– 88)
    Round of 16     8   (matches  89– 96)
    Quarter-finals  4   (matches  97–100)
    Semi-finals     2   (matches 101–102)
    Final + 3rd     2   (matches 103–104)
                  ---
    TOTAL         104
"""

TOTAL_MATCHES = 104

# Progress phases for the Home tab, in order, with cumulative match counts.
# `upto` is the highest match number that still belongs to this phase.
PROGRESS_PHASES = [
    {"key": "Group", "label": "Groups", "matches": 72, "upto": 72},
    {"key": "R32",   "label": "R32",    "matches": 16, "upto": 88},
    {"key": "R16",   "label": "R16",    "matches": 8,  "upto": 96},
    {"key": "QF",    "label": "QF",     "matches": 4,  "upto": 100},
    {"key": "SF",    "label": "SF",     "matches": 2,  "upto": 102},
    {"key": "Final", "label": "Final",  "matches": 2,  "upto": 104},
]

# Ordered knockout progression a team moves THROUGH (used to advance winners).
# A win in round N moves a team to round N+1; winning the Final → "Winner".
STAGE_ORDER = ["Group", "R32", "R16", "QF", "SF", "Final", "Winner"]

# Map football-data.org stage labels → our compact codes. The 2026 feed adds
# LAST_32; we keep older labels too so nothing breaks if the feed varies.
STAGE_MAP = {
    "GROUP_STAGE": "Group",
    "LAST_32": "R32",
    "ROUND_OF_32": "R32",
    "LAST_16": "R16",
    "ROUND_OF_16": "R16",
    "QUARTER_FINALS": "QF",
    "SEMI_FINALS": "SF",
    "THIRD_PLACE": "3rd_playoff",
    "3RD_PLACE": "3rd_playoff",
    "FINAL": "Final",
}

# Stage bonus = the furthest stage a team has REACHED, scored as the minimum
# finish that reaching it guarantees. This is what makes the ranking correct
# DURING the tournament, not only after it:
#   · reaching the SF guarantees at least 4th place        → 120
#   · reaching the Final guarantees at least Runner-up     → 180
# Without these two entries, teams still alive in the SF/Final scored a bonus
# of 0 and ranked below R32 casualties (real bug, fixed here).
# Tournament achievement ALWAYS outranks raw match points: every band gap is
# ≥30, which exceeds the maximum ~24 match points a champion can accumulate,
# so a higher finish can never be overtaken on match points alone.
STAGE_BONUS = {
    "Group":     0,    # exited in the group stage
    "R32":       30,   # reached the Round of 32
    "R16":       60,
    "QF":        90,
    "SF":        120,  # reached the semi-finals (≥ 4th place guaranteed)
    "4th":       120,  # lost the 3rd-place play-off
    "3rd":       150,  # won the 3rd-place play-off
    "Final":     180,  # reached the Final (≥ Runner-up guaranteed)
    "Runner-up": 180,  # lost the Final
    "Winner":    210,  # World Cup champion
}

# Rank ordering for tie-breaks / "furthest reached" comparisons. Higher = better.
FINISH_RANK = {
    "Group": 0, "R32": 1, "R16": 2, "QF": 3,
    "4th": 4, "3rd": 5, "SF": 5, "Final": 6,
    "Runner-up": 6, "Winner": 7,
}

# Human labels for a team's current/-final status badge.
STAGE_LABELS = {
    "Group": "In Groups",
    "R32": "Round of 32",
    "R16": "Round of 16",
    "QF": "Quarter-final",
    "SF": "Semi-final",
    "Final": "In the Final",
    "3rd_playoff": "3rd-place play-off",
    "Winner": "🏆 Champion",
    "Runner-up": "Runner-up",
    "3rd": "3rd place",
    "4th": "4th place",
    "Out": "Eliminated",
}


def stage_bonus(stage: str) -> int:
    """Bonus points for a team's furthest stage reached (defensive default 0)."""
    return STAGE_BONUS.get(stage, 0)


def finish_rank(stage: str) -> int:
    return FINISH_RANK.get(stage, 0)


def phase_for_match_number(n: int) -> str:
    """Which progress phase key a given (1-based) match number falls in."""
    for ph in PROGRESS_PHASES:
        if n <= ph["upto"]:
            return ph["key"]
    return "Final"
