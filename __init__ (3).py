"""Ranked World Cup 2026 contender list (most-likely-to-win first).

When a sweepstake's draw runs, the top-N teams from this list are used, where
N = number of participants. So with 10 players, the draw allocates the top 10
teams — everyone gets a genuine contender.

Edit RANKED_TEAMS to change the order or set.
"""

# (name, flag_emoji) in descending order of win likelihood.
RANKED_TEAMS: list[tuple[str, str]] = [
    ("Spain", "🇪🇸"),
    ("France", "🇫🇷"),
    ("England", "🏴󠁧󠁢󠁥󠁮󠁧󠁿"),
    ("Brazil", "🇧🇷"),
    ("Portugal", "🇵🇹"),
    ("Argentina", "🇦🇷"),
    ("Germany", "🇩🇪"),
    ("Netherlands", "🇳🇱"),
    ("Belgium", "🇧🇪"),
    ("Norway", "🇳🇴"),
    ("Japan", "🇯🇵"),
    ("Colombia", "🇨🇴"),
    ("USA", "🇺🇸"),
    ("Mexico", "🇲🇽"),
    ("Uruguay", "🇺🇾"),
    ("Morocco", "🇲🇦"),
    ("Switzerland", "🇨🇭"),
    ("Croatia", "🇭🇷"),
    ("Türkiye", "🇹🇷"),
    ("Ecuador", "🇪🇨"),
    ("Senegal", "🇸🇳"),
    ("Paraguay", "🇵🇾"),
    ("Canada", "🇨🇦"),
    ("Austria", "🇦🇹"),
    ("Sweden", "🇸🇪"),
    ("Ivory Coast", "🇨🇮"),
    ("Algeria", "🇩🇿"),
    ("Scotland", "🏴󠁧󠁢󠁳󠁣󠁴󠁿"),
    ("Australia", "🇦🇺"),
    ("Cape Verde", "🇨🇻"),
    ("Curacao", "🇨🇼"),
    ("DR Congo", "🇨🇩"),
    ("Haiti", "🇭🇹"),
    ("Iran", "🇮🇷"),
    ("Iraq", "🇮🇶"),
    ("Jordan", "🇯🇴"),
    ("New Zealand", "🇳🇿"),
    ("Panama", "🇵🇦"),
    ("Qatar", "🇶🇦"),
    ("Saudi Arabia", "🇸🇦"),
    ("South Africa", "🇿🇦"),
    ("Tunisia", "🇹🇳"),
    ("Uzbekistan", "🇺🇿"),
    ("Ghana", "🇬🇭"),
    ("Czechia", "🇨🇿"),
    ("Bosnia & Herzegovina", "🇧🇦"),
    ("South Korea", "🇰🇷"),
    ("Egypt", "🇪🇬"),
]

# Name -> flag lookup, handy for matching fixtures from the football API.
TEAM_FLAGS: dict[str, str] = {name: flag for name, flag in RANKED_TEAMS}
