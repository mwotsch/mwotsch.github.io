#!/usr/bin/env python3
"""
Convert a Coronate export (Options -> "Export data to a file") into games.txt lines.

    python3 coronate_import.py coronate-2026-09-16.json                      # list tournaments
    python3 coronate_import.py coronate-2026-09-16.json --tournament "Name"  # print games.txt lines
    python3 coronate_import.py coronate-2026-09-16.json --tournament "Name" --date 20260916

Lines are printed to stdout in Coronate's round order, byes as "Name - BYE 1-0 DATE".
Names that don't already appear in games.txt are reported on stderr so typos and
new players are caught before the lines are appended.
"""

import argparse
import json
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

DUMMY_ID = '________DUMMY________'
CLUB_TZ = ZoneInfo('America/New_York')
RESULTS = {'whiteWon': '1-0', 'blackWon': '0-1', 'draw': '0.5-0.5'}


class ImportError_(Exception):
    pass


def display_name(player):
    """Coronate stores first/last names; games.txt uses 'First L.'"""
    first = player['firstName'].strip()
    last = player['lastName'].strip()
    return f"{first} {last[0]}." if last else first


def name_map(export):
    """Map Coronate player ids to display names, refusing ambiguous ones."""
    names = {}
    for player_id, player in export['players'].items():
        name = display_name(player)
        clash = next((pid for pid, n in names.items() if n == name), None)
        if clash:
            raise ImportError_(f"Two players would both be '{name}': "
                               f"{export['players'][clash]['firstName']} {export['players'][clash]['lastName']} "
                               f"and {player['firstName']} {player['lastName']}")
        names[player_id] = name
    return names


def club_date(iso_timestamp):
    """Coronate stores a UTC creation timestamp; the club night is the local (Eastern) date."""
    utc = datetime.fromisoformat(iso_timestamp.replace('Z', '+00:00'))
    return utc.astimezone(CLUB_TZ).strftime('%Y%m%d')


def find_tournament(export, name):
    for tournament in export['tournaments'].values():
        if tournament['name'] == name:
            return tournament
    raise ImportError_(f"No tournament named '{name}'. Run without --tournament to list them.")


def games_lines(export, tournament_name, date=None):
    """Return (lines, notes): games.txt lines for one tournament plus anything skipped."""
    tournament = find_tournament(export, tournament_name)
    names = name_map(export)
    date = date or club_date(tournament['date'])
    lines, notes = [], []
    
    for round_number, matches in enumerate(tournament['roundList'], 1):
        if not matches:
            notes.append(f'Round {round_number}: empty, skipped')
            continue
        for match in matches:
            white, black, result = match['whiteId'], match['blackId'], match['result']
            if DUMMY_ID in (white, black):
                player = names[black if white == DUMMY_ID else white]
                lines.append(f'{player} - BYE 1-0 {date}')
            elif result in RESULTS:
                lines.append(f'{names[white]} - {names[black]} {RESULTS[result]} {date}')
            else:
                notes.append(f'Round {round_number}: {names[white]} - {names[black]} '
                             f'has result "{result}", skipped')
    return lines, notes


def list_tournaments(export):
    summary = []
    for tournament in export['tournaments'].values():
        rounds = tournament['roundList']
        matches = [m for r in rounds for m in r]
        byes = sum(1 for m in matches if DUMMY_ID in (m['whiteId'], m['blackId']))
        summary.append({
            'name': tournament['name'],
            'date': club_date(tournament['date']),
            'rounds': len(rounds),
            'players': len(tournament['playerIds']),
            'games': len(matches) - byes,
            'byes': byes,
        })
    return summary


LINE_RE = re.compile(r'^(.+?) - (.+?) [\d.]+-[\d.]+(?: \d{8})?$')


def known_names(games_path):
    """Every player name already used in games.txt (BYE excluded)."""
    names = set()
    with open(games_path) as f:
        for line in f:
            m = LINE_RE.match(line.strip())
            if m:
                names.update(n for n in m.groups() if n.upper() != 'BYE')
    return names


def unknown_names(lines, known):
    seen = []
    for line in lines:
        for name in LINE_RE.match(line).groups():
            if name.upper() != 'BYE' and name not in known and name not in seen:
                seen.append(name)
    return seen


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('export', help='Coronate JSON export')
    parser.add_argument('--tournament', help='name of the tournament to convert')
    parser.add_argument('--date', help='override the date (YYYYMMDD)')
    parser.add_argument('--games', default='games.txt', help='games.txt to check names against')
    args = parser.parse_args()
    
    with open(args.export) as f:
        export = json.load(f)
    
    try:
        if not args.tournament:
            for t in list_tournaments(export):
                print(f"{t['date']}  {t['name']!r}: {t['rounds']} rounds, {t['players']} players, "
                      f"{t['games']} games, {t['byes']} byes")
            return 0
        
        lines, notes = games_lines(export, args.tournament, args.date)
    except ImportError_ as e:
        print(f'Error: {e}', file=sys.stderr)
        return 1
    
    for line in lines:
        print(line)
    for note in notes:
        print(f'Note: {note}', file=sys.stderr)
    try:
        for name in unknown_names(lines, known_names(args.games)):
            print(f'Warning: {name!r} is not in {args.games} yet (new player or typo?)', file=sys.stderr)
    except FileNotFoundError:
        print(f'Warning: {args.games} not found, names not checked', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
