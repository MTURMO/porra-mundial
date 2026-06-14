#!/usr/bin/env python3
"""
Fetches FIFA World Cup 2026 fixtures and top-scorer data from
API-Football (api-sports.io) and writes them to live-data.json.

This script is meant to be run on a schedule by a GitHub Action
(see .github/workflows/update-live-data.yml), so the API key stays
in GitHub Secrets and is NEVER exposed to website visitors. Because
it runs on a fixed schedule (e.g. hourly), the number of API calls
is independent of how many people open the porra page — perfect for
the free 100 requests/day tier even with hundreds of participants.

Requires the APIFOOTBALL_KEY environment variable.
"""
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

API_BASE = "https://v3.football.api-sports.io"
LEAGUE = 1      # FIFA World Cup
SEASON = 2026
OUTPUT_FILE = "live-data.json"


def fetch(endpoint, api_key):
    url = f"{API_BASE}{endpoint}"
    req = urllib.request.Request(url, headers={"x-apisports-key": api_key})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    if data.get("errors"):
        # API-Football returns errors as a dict (or empty list) even on HTTP 200
        errors = data["errors"]
        if errors:
            raise RuntimeError(f"API error on {endpoint}: {errors}")
    return data


def main():
    api_key = os.environ.get("APIFOOTBALL_KEY", "").strip()
    if not api_key:
        print("ERROR: APIFOOTBALL_KEY environment variable not set", file=sys.stderr)
        sys.exit(1)

    fixtures = fetch(f"/fixtures?league={LEAGUE}&season={SEASON}", api_key)
    topscorers = fetch(f"/players/topscorers?league={LEAGUE}&season={SEASON}", api_key)

    out = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "fixtures": {"response": fixtures.get("response", [])},
        "topscorers": {"response": topscorers.get("response", [])},
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"Wrote {len(out['fixtures']['response'])} fixtures "
          f"and {len(out['topscorers']['response'])} top scorers to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
