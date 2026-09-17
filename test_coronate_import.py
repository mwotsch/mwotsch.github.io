import unittest

import coronate_import as ci

DUMMY = '________DUMMY________'


def match(white, black, result):
    return {'whiteId': white, 'blackId': black, 'result': result}


EXPORT = {
    'config': {'byeValue': 1},
    'players': {
        'a': {'id': 'a', 'firstName': 'Marco', 'lastName': 'Wotschka'},
        'b': {'id': 'b', 'firstName': 'Kuba', 'lastName': 'Kowalski'},
        'c': {'id': 'c', 'firstName': 'Nick', 'lastName': 'Hart'},
    },
    'tournaments': {
        't1': {
            'name': 'Club Night',
            'date': '2026-09-17T02:30:00.000Z',   # 10:30 pm Eastern on Sep 16
            'playerIds': ['a', 'b', 'c'],
            'roundList': [
                [match('a', 'b', 'whiteWon'), match('c', DUMMY, 'whiteWon')],
                [match('b', 'c', 'draw'), match(DUMMY, 'a', 'blackWon')],
                [],
                [match('c', 'a', 'notSet')],
            ],
        },
    },
}


class NameTest(unittest.TestCase):
    def test_display_name_is_first_name_and_last_initial(self):
        self.assertEqual(ci.display_name({'firstName': 'Marco', 'lastName': 'Wotschka'}), 'Marco W.')

    def test_display_name_handles_missing_last_name(self):
        self.assertEqual(ci.display_name({'firstName': 'Marco', 'lastName': ''}), 'Marco')

    def test_name_map_rejects_collisions(self):
        export = {'players': {
            'x': {'id': 'x', 'firstName': 'Sam', 'lastName': 'Miller'},
            'y': {'id': 'y', 'firstName': 'Sam', 'lastName': 'Moore'},
        }}
        with self.assertRaises(ci.ImportError_) as ctx:
            ci.name_map(export)
        self.assertIn('Sam M.', str(ctx.exception))


class DateTest(unittest.TestCase):
    def test_utc_timestamp_converts_to_eastern_club_date(self):
        self.assertEqual(ci.club_date('2026-09-17T02:30:00.000Z'), '20260916')
        self.assertEqual(ci.club_date('2026-09-16T18:00:00.000Z'), '20260916')


class GamesLinesTest(unittest.TestCase):
    def test_emits_rounds_in_order_with_byes(self):
        lines, notes = ci.games_lines(EXPORT, 'Club Night')
        self.assertEqual(lines, [
            'Marco W. - Kuba K. 1-0 20260916',
            'Nick H. - BYE 1-0 20260916',
            'Kuba K. - Nick H. 0.5-0.5 20260916',
            'Marco W. - BYE 1-0 20260916',
        ])

    def test_reports_skipped_rounds_and_unplayed_games(self):
        _, notes = ci.games_lines(EXPORT, 'Club Night')
        self.assertEqual(notes, ['Round 3: empty, skipped',
                                 'Round 4: Nick H. - Marco W. has result "notSet", skipped'])

    def test_date_override(self):
        lines, _ = ci.games_lines(EXPORT, 'Club Night', date='20260101')
        self.assertTrue(all(l.endswith(' 20260101') for l in lines))

    def test_unknown_tournament(self):
        with self.assertRaises(ci.ImportError_):
            ci.games_lines(EXPORT, 'Nope')


class ListAndCheckTest(unittest.TestCase):
    def test_list_tournaments(self):
        self.assertEqual(ci.list_tournaments(EXPORT), [
            {'name': 'Club Night', 'date': '20260916', 'rounds': 4, 'players': 3, 'games': 3, 'byes': 2}])

    def test_unknown_names_against_games_txt(self):
        lines = ['Marco W. - Kuba K. 1-0 20260916', 'Nick H. - BYE 1-0 20260916']
        self.assertEqual(ci.unknown_names(lines, {'Marco W.', 'Nick H.'}), ['Kuba K.'])

    def test_known_names_reads_games_txt(self):
        import os, tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'games.txt')
            open(path, 'w').write('Marco W. - Kuba K. 1-0 20260101\nAri M. - BYE 1-0 20260101\n')
            self.assertEqual(ci.known_names(path), {'Marco W.', 'Kuba K.', 'Ari M.'})


class CheckTest(unittest.TestCase):
    """EXPORT's 'Club Night' (20260916) yields:
         Marco W. - Kuba K. 1-0 / Nick H. - BYE 1-0 / Kuba K. - Nick H. 0.5-0.5 / Marco W. - BYE 1-0
    """
    def check_with(self, games_text):
        import os, tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'games.txt')
            open(path, 'w').write(games_text)
            return ci.check(EXPORT, path)

    def test_matching_tournament_has_no_findings(self):
        report = self.check_with(
            'Marco W. - Kuba K. 1-0 20260916\n'
            'Nick H. - BYE 1-0 20260916\n'
            'Kuba K. - Nick H. 0.5-0.5 20260916\n'
            'Marco W. - BYE 1-0 20260916\n')
        self.assertEqual(report['tournaments'], [
            {'name': 'Club Night', 'date': '20260916', 'in_games_txt': True, 'findings': []}])
        self.assertEqual(report['uncovered_dates'], [])

    def test_reports_missing_byes_and_typos_with_suggestion(self):
        report = self.check_with(
            'Marco W. - Kuna K. 1-0 20260916\n'
            'Kuna K. - Nick H. 0.5-0.5 20260916\n')
        findings = report['tournaments'][0]['findings']
        self.assertIn("name 'Kuba K.' not in games.txt (closest: 'Kuna K.')", findings)
        self.assertIn('missing bye: Nick H. - BYE 1-0 20260916', findings)
        self.assertIn('missing bye: Marco W. - BYE 1-0 20260916', findings)
        self.assertIn('missing game: Marco W. - Kuba K. 1-0 20260916', findings)
        self.assertIn('extra game in games.txt: Marco W. - Kuna K. 1-0 20260916', findings)

    def test_reports_order_difference(self):
        report = self.check_with(
            'Kuba K. - Nick H. 0.5-0.5 20260916\n'
            'Marco W. - Kuba K. 1-0 20260916\n'
            'Nick H. - BYE 1-0 20260916\n'
            'Marco W. - BYE 1-0 20260916\n')
        self.assertEqual(report['tournaments'][0]['findings'],
                         ['game order differs from Coronate (round reconstruction may be wrong)'])

    def test_reports_tournament_absent_from_games_txt_and_uncovered_dates(self):
        report = self.check_with('Marco W. - Kuba K. 1-0 20260101\n')
        self.assertEqual(report['tournaments'][0]['in_games_txt'], False)
        self.assertEqual(report['uncovered_dates'], ['20260101'])


if __name__ == '__main__':
    unittest.main()
