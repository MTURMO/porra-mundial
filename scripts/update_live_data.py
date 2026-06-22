#!/usr/bin/env python3
"""
Fetches FIFA World Cup 2026 fixtures/results AND top-scorer goal
tallies, writing both to live-data.json. Runs on a schedule via
GitHub Actions (see .github/workflows/update-live-data.yml).

Source priority:
  1. football-data.org (/v4/competitions/WC/matches + /scorers)
     - Free tier includes the World Cup, 10 req/min.
     - Needs a free token (sign up at
       https://www.football-data.org/client/register, no card)
       stored as the FOOTBALL_DATA_TOKEN repo secret.
     - Gives BOTH fixtures and top-scorer goals.
  2. worldcup26.ir (fixtures only, no key)
  3. openfootball/worldcup.json (fixtures only, no key, daily)

Because this runs server-side on a fixed schedule, the number of
people viewing the porra page has zero effect on API usage.
"""
import json
import os
import re
import sys
import unicodedata
import urllib.request
from datetime import datetime, timezone

FOOTBALL_DATA_BASE = "https://api.football-data.org/v4"
WORLDCUP26_URL = "https://worldcup26.ir/get/games"
OPENFOOTBALL_URL = "https://raw.githubusercontent.com/openfootball/worldcup.json/master/2026/worldcup.json"
OUTPUT_FILE = "live-data.json"

# Same alias table as index.html's TEAM_MAP, kept in sync so the
# normalized names line up with data.json's team1/team2 fields.
TEAM_MAP = {
    "south africa": "South Africa", "south korea": "South Korea",
    "korea republic": "South Korea", "republic of korea": "South Korea",
    "czech republic": "Czech Republic", "czechia": "Czech Republic",
    "ivory coast": "Ivory Coast", "cote d'ivoire": "Ivory Coast", "côte d'ivoire": "Ivory Coast",
    "usa": "USA", "united states": "USA", "united states of america": "USA",
    "netherlands": "Netherlands", "holland": "Netherlands",
    "new zealand": "New Zealand",
    "saudi arabia": "Saudi Arabia", "ksa": "Saudi Arabia",
    "cape verde": "Cape Verde", "cabo verde": "Cape Verde", "cape verde islands": "Cape Verde",
    "bosnia-herzegovina": "Bosnia Herzegovina", "bosnia & herzegovina": "Bosnia Herzegovina",
    "bosnia and herzegovina": "Bosnia Herzegovina",
    "curaçao": "Curaçao", "curacao": "Curaçao",
    "dr congo": "Congo", "congo dr": "Congo", "democratic republic of congo": "Congo",
    "ir iran": "Iran",
}


def norm_team(name):
    if not name:
        return ""
    return TEAM_MAP.get(name.strip().lower(), name.strip())


def normalize_scorer_name(name):
    """Same algorithm as normalizeScorerName() in index.html — strips
    accents, parentheses (e.g. "(COL)") and case."""
    if not name:
        return ""
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    name = re.sub(r"\(.*?\)", "", name)
    name = name.lower().strip()
    name = re.sub(r"\s+", " ", name)
    return name


def fetch_json(url, headers=None):
    h = {"User-Agent": "porra-mundial-bot"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


# ─────────────────────────────────────────────────────────
# SOURCE 1: football-data.org — fixtures + top scorers
# ─────────────────────────────────────────────────────────
def from_football_data(token):
    headers = {"X-Auth-Token": token}

    matches_data = fetch_json(f"{FOOTBALL_DATA_BASE}/competitions/WC/matches", headers)
    fixtures = []
    for m in matches_data.get("matches", []):
        t1 = norm_team(m.get("homeTeam", {}).get("name", ""))
        t2 = norm_team(m.get("awayTeam", {}).get("name", ""))
        ft = (m.get("score") or {}).get("fullTime") or {}
        g1, g2 = ft.get("home"), ft.get("away")
        # Exclude scorelines while the match is still being played — otherwise
        # an in-play score gets locked in for an hour and points get computed
        # on a half-finished match. Only exclude explicitly live statuses;
        # treat anything else (including a missing/unexpected status) as final
        # if a fullTime score is already present, since the API has been seen
        # to omit/garble "status" for matches that finished hours ago.
        live_statuses = ("IN_PLAY", "PAUSED", "SUSPENDED")
        in_progress = m.get("status") in live_statuses
        played = (not in_progress) and g1 is not None and g2 is not None
        if t1 and t2:
            fixtures.append({"t1": t1, "t2": t2,
                              "g1": g1 if played else None,
                              "g2": g2 if played else None})
    if not fixtures:
        raise RuntimeError("football-data.org returned no matches")

    topscorers = {}
    try:
        scorers_data = fetch_json(f"{FOOTBALL_DATA_BASE}/competitions/WC/scorers?limit=100", headers)
        for s in scorers_data.get("scorers", []):
            name = normalize_scorer_name((s.get("player") or {}).get("name", ""))
            goals = s.get("goals") or 0
            if name:
                topscorers[name] = goals
    except Exception as e:
        print(f"  football-data.org scorers failed (fixtures still OK): {e}", file=sys.stderr)

    return fixtures, topscorers, "football-data.org"


# ─────────────────────────────────────────────────────────
# SOURCE 2: worldcup26.ir — fixtures only, no key
# ─────────────────────────────────────────────────────────
def from_worldcup26():
    data = fetch_json(WORLDCUP26_URL)
    arr = data if isinstance(data, list) else (data.get("games") or data.get("matches") or data.get("data") or [])

    out = []
    for m in arr:
        raw_t1 = m.get("homeTeam") or m.get("home_team") or m.get("team1") or {}
        raw_t2 = m.get("awayTeam") or m.get("away_team") or m.get("team2") or {}
        t1 = norm_team(raw_t1.get("name") if isinstance(raw_t1, dict) else raw_t1)
        t2 = norm_team(raw_t2.get("name") if isinstance(raw_t2, dict) else raw_t2)

        g1 = m.get("homeScore", m.get("home_score", m.get("score1")))
        g2 = m.get("awayScore", m.get("away_score", m.get("score2")))
        status = str(m.get("status", "")).strip().lower()
        # If the source reports a live/in-progress status, don't lock in a
        # half-time scoreline — wait until it's actually finished.
        in_progress = status in ("live", "in_play", "inplay", "in progress", "1h", "2h", "ht", "playing")
        played = g1 is not None and g2 is not None and not in_progress

        if t1 and t2:
            out.append({"t1": t1, "t2": t2,
                         "g1": int(g1) if played else None,
                         "g2": int(g2) if played else None})

    if not out:
        raise RuntimeError("worldcup26.ir returned no usable matches")
    return out, "worldcup26.ir"


# ─────────────────────────────────────────────────────────
# SOURCE 3: openfootball — fixtures only, no key, daily
# ─────────────────────────────────────────────────────────
def from_openfootball():
    data = fetch_json(OPENFOOTBALL_URL)
    arr = data.get("matches", [])

    out = []
    for m in arr:
        t1 = norm_team(m.get("team1", ""))
        t2 = norm_team(m.get("team2", ""))
        g1 = m.get("score1")
        g2 = m.get("score2")
        if g1 is None and isinstance(m.get("score"), dict) and m["score"].get("ft"):
            g1, g2 = m["score"]["ft"]
        played = g1 is not None and g2 is not None
        if t1 and t2:
            out.append({"t1": t1, "t2": t2,
                         "g1": int(g1) if played else None,
                         "g2": int(g2) if played else None})

    if not out:
        raise RuntimeError("openfootball returned no matches")
    return out, "openfootball"


def main():
    fixtures, topscorers, source = None, {}, None

    token = os.environ.get("FOOTBALL_DATA_TOKEN", "").strip()
    if token:
        try:
            fixtures, topscorers, source = from_football_data(token)
            print(f"  football-data.org OK: {len(fixtures)} fixtures, {len(topscorers)} scorers")
        except Exception as e:
            print(f"  football-data.org failed: {e}", file=sys.stderr)
    else:
        print("  FOOTBALL_DATA_TOKEN not set — skipping football-data.org", file=sys.stderr)

    if fixtures is None:
        for fn in (from_worldcup26, from_openfootball):
            try:
                fixtures, source = fn()
                print(f"  {fn.__name__} OK: {len(fixtures)} fixtures")
                break
            except Exception as e:
                print(f"  {fn.__name__} failed: {e}", file=sys.stderr)

    if fixtures is None:
        print("ERROR: all fixture sources failed", file=sys.stderr)
        sys.exit(1)

    out = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "fixtures": fixtures,
        "topscorers": topscorers,
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"Wrote {len(fixtures)} fixtures and {len(topscorers)} scorer tallies "
          f"(source: {source}) to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()