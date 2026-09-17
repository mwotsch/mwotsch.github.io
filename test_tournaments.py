import unittest

from chess_rating_system import ChessRatingSystem


def system_with(lines):
    rs = ChessRatingSystem()
    for i, line in enumerate(lines, 1):
        rs.process_game(line, i)
    return rs


def row(tournament, name):
    return next(r for r in tournament['standings'] if r['name'] == name)


class BuildTournamentsTest(unittest.TestCase):
    def test_splits_rounds_by_file_order(self):
        rs = system_with([
            'A - B 1-0 20260101',
            'C - D 0.5-0.5 20260101',
            'B - C 0-1 20260101',
            'D - A 0-1 20260101',
        ])
        t = rs.build_tournaments()[0]
        self.assertEqual(t['rounds'], 2)
        self.assertEqual(t['games'], 4)
        self.assertEqual(row(t, 'A')['rounds'], [
            {'opponent': 'B', 'color': 'W', 'score': 1.0},
            {'opponent': 'D', 'color': 'B', 'score': 1.0},
        ])
        self.assertEqual(row(t, 'C')['rounds'], [
            {'opponent': 'D', 'color': 'W', 'score': 0.5},
            {'opponent': 'B', 'color': 'B', 'score': 1.0},
        ])

    def test_missing_round_is_none(self):
        rs = system_with([
            'A - B 1-0 20260101',
            'C - D 1-0 20260101',
            'A - C 1-0 20260101',   # B and D sit out round 2
            'B - A 0-1 20260101',
        ])
        t = rs.build_tournaments()[0]
        self.assertEqual(t['rounds'], 3)
        self.assertEqual(row(t, 'D')['rounds'], [
            {'opponent': 'C', 'color': 'B', 'score': 0.0}, None, None])
        self.assertEqual(row(t, 'B')['rounds'][1], None)

    def test_standings_sorted_by_points_then_wins_then_name(self):
        rs = system_with([
            'A - B 1-0 20260101',
            'C - D 0.5-0.5 20260101',
            'A - C 0.5-0.5 20260101',
            'D - B 1-0 20260101',
        ])
        t = rs.build_tournaments()[0]
        # A: 1.5 (1 win), D: 1.5 (1 win), C: 1.0 (0 wins), B: 0
        self.assertEqual([r['name'] for r in t['standings']], ['A', 'D', 'C', 'B'])
        self.assertEqual(row(t, 'A')['points'], 1.5)
        self.assertEqual((row(t, 'C')['wins'], row(t, 'C')['draws'], row(t, 'C')['losses']), (0, 2, 0))

    def test_tournaments_newest_first_with_formatted_date(self):
        rs = system_with([
            'A - B 1-0 20260101',
            'A - B 1-0 20260301',
        ])
        ts = rs.build_tournaments()
        self.assertEqual([t['date_raw'] for t in ts], ['20260301', '20260101'])
        self.assertEqual(ts[0]['date'], 'Mar 1, 2026')
        self.assertEqual(ts[0]['players'], 2)

    def test_undated_games_are_ignored(self):
        rs = system_with(['A - B 1-0', 'A - B 1-0 20260101'])
        ts = rs.build_tournaments()
        self.assertEqual(len(ts), 1)
        self.assertEqual(ts[0]['games'], 1)


class ByeParsingTest(unittest.TestCase):
    def test_bye_line_records_bye_without_touching_ratings(self):
        rs = ChessRatingSystem()
        self.assertIsNone(rs.process_game('A - BYE 1-0 20260101', 1))
        self.assertEqual(rs.players, {})
        self.assertEqual(rs.games, [])
        self.assertEqual(rs.byes, [{'player': 'A', 'date_raw': '20260101', 'sequence': 1}])

    def test_bye_is_case_insensitive(self):
        rs = ChessRatingSystem()
        rs.process_game('A - bye 1-0 20260101', 1)
        self.assertEqual(rs.byes[0]['player'], 'A')

    def test_game_numbers_stay_sequential_across_bye_lines(self):
        import os, tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'games.txt')
            with open(path, 'w') as f:
                f.write('A - B 1-0 20260101\nC - BYE 1-0 20260101\nA - C 1-0 20260101\n')
            rs = ChessRatingSystem()
            self.assertTrue(rs.load_games_file(path))
        self.assertEqual([g['game_number'] for g in rs.games], [1, 2])
        self.assertEqual(rs.byes[0]['sequence'], 2)


class TiebreakTest(unittest.TestCase):
    def setUp(self):
        # Final scores: A 2, D 2, B 1, C 1.
        # A/D tie on Median (1) and Solkoff (4); Cumulative separates them (A 5, D 3).
        # B/C tie on Median (2) and Solkoff (5); Cumulative separates them (C 3, B 1).
        self.t = system_with([
            'A - B 1-0 20260101',
            'C - D 1-0 20260101',
            'A - C 1-0 20260101',
            'D - B 1-0 20260101',
            'D - A 1-0 20260101',
            'B - C 1-0 20260101',
        ]).build_tournaments()[0]

    def test_median_drops_highest_and_lowest_opponent_score(self):
        self.assertEqual(row(self.t, 'A')['median'], 1.0)  # opps 1,1,2 -> 1
        self.assertEqual(row(self.t, 'B')['median'], 2.0)  # opps 2,2,1 -> 2

    def test_solkoff_sums_all_opponent_scores(self):
        self.assertEqual(row(self.t, 'A')['solkoff'], 4.0)
        self.assertEqual(row(self.t, 'B')['solkoff'], 5.0)

    def test_cumulative_sums_running_score(self):
        self.assertEqual(row(self.t, 'A')['cumulative'], 5.0)  # 1,2,2
        self.assertEqual(row(self.t, 'D')['cumulative'], 3.0)  # 0,1,2
        self.assertEqual(row(self.t, 'B')['cumulative'], 1.0)  # 0,0,1

    def test_cumulative_of_opposition(self):
        # B's opponents A, D, C have cumulative 5, 3, 3
        self.assertEqual(row(self.t, 'B')['cumulative_opp'], 11.0)

    def test_standings_use_coronate_tiebreak_order(self):
        self.assertEqual([r['name'] for r in self.t['standings']], ['A', 'D', 'C', 'B'])


class ByeInTournamentTest(unittest.TestCase):
    def setUp(self):
        self.t = system_with([
            'A - BYE 1-0 20260101',
            'B - C 1-0 20260101',
            'A - B 1-0 20260101',
            'C - BYE 1-0 20260101',
        ]).build_tournaments()[0]

    def test_bye_counts_as_a_win_in_score(self):
        a = row(self.t, 'A')
        self.assertEqual(a['points'], 2.0)
        self.assertEqual((a['wins'], a['draws'], a['losses']), (2, 0, 0))
        self.assertEqual(a['rounds'][0], {'opponent': 'BYE', 'color': None, 'score': 1.0, 'bye': True})

    def test_bye_occupies_a_round_slot(self):
        self.assertEqual(self.t['rounds'], 2)
        self.assertEqual(self.t['games'], 2)
        self.assertEqual(self.t['players'], 3)

    def test_bye_is_excluded_from_tiebreaks(self):
        a = row(self.t, 'A')
        self.assertEqual(a['solkoff'], 1.0)      # only B (score 1); bye ignored
        self.assertEqual(a['median'], 0.0)       # one opponent -> nothing left after trimming
        self.assertEqual(a['cumulative'], 1.0)   # running score over non-bye rounds: 1
        c = row(self.t, 'C')
        self.assertEqual(c['cumulative'], 0.0)   # 0 from R1; bye round skipped
        self.assertEqual(row(self.t, 'B')['cumulative_opp'], a['cumulative'] + c['cumulative'])


class GenerateHtmlTest(unittest.TestCase):
    def test_embeds_tournaments_and_section(self):
        import os, tempfile
        rs = system_with(['A - B 1-0 20260101'])
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, 'out.html')
            self.assertTrue(rs.generate_html(out))
            html = open(out).read()
        self.assertIn('let tournaments = [{"date": "Jan 1, 2026"', html)
        self.assertIn('<h2>Tournaments</h2>', html)
        self.assertIn('id="tournaments-container"', html)
        self.assertIn("'MMed', 'Solk', 'Cum', 'CumOpp'", html)


if __name__ == '__main__':
    unittest.main()
