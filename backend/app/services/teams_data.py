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
    ("Portugal", "🇵🇹"),
    ("Brazil", "🇧🇷"),
    ("Argentina", "🇦🇷"),
    ("Germany", "🇩🇪"),
    ("Netherlands", "🇳🇱"),
    ("Norway", "🇳🇴"),
    ("Belgium", "🇧🇪"),
    ("Colombia", "🇨🇴"),
    ("Japan", "🇯🇵"),
    ("Morocco", "🇲🇦"),
    ("USA", "🇺🇸"),
    ("Uruguay", "🇺🇾"),
    ("Mexico", "🇲🇽"),
    ("Switzerland", "🇨🇭"),
    ("Croatia", "🇭🇷"),
    ("Türkiye", "🇹🇷"),
    ("Ecuador", "🇪🇨"),
    ("Senegal", "🇸🇳"),
    ("Sweden", "🇸🇪"),
    ("Canada", "🇨🇦"),
    ("Austria", "🇦🇹"),
    ("Paraguay", "🇵🇾"),
    ("Scotland", "🏴󠁧󠁢󠁳󠁣󠁴󠁿"),
    ("Ivory Coast", "🇨🇮"),
    ("Egypt", "🇪🇬"),
    ("Czechia", "🇨🇿"),
    ("Bosnia & Herzegovina", "🇧🇦"),
    ("Ghana", "🇬🇭"),
    ("Algeria", "🇩🇿"),
    ("South Korea", "🇰🇷"),
    ("Tunisia", "🇹🇳"),
    ("Australia", "🇦🇺"),
    ("Iran", "🇮🇷"),
    ("DR Congo", "🇨🇩"),
    ("South Africa", "🇿🇦"),
    ("Saudi Arabia", "🇸🇦"),
    ("Panama", "🇵🇦"),
    ("Iraq", "🇮🇶"),
    ("Uzbekistan", "🇺🇿"),
    ("Qatar", "🇶🇦"),
    ("Cape Verde", "🇨🇻"),
    ("New Zealand", "🇳🇿"),
    ("Jordan", "🇯🇴"),
    ("Haiti", "🇭🇹"),
    ("Curacao", "🇨🇼"),
]

# Name -> flag lookup, handy for matching fixtures from the football API.
TEAM_FLAGS: dict[str, str] = {name: flag for name, flag in RANKED_TEAMS}
