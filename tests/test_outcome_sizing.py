import copy
import hashlib
import json
import unittest

from betting_engine.outcome_sizing import POLICY, apply_adjustment, build_snapshot, summary_for, verify_summary

AS_OF = '2026-10-03T10:00:00+00:00'


def decision(i, status='won', stake=1., odds=2., game=None, **changes):
    tip = {'id': str(i), 'game_id': game or str(i), 'league': 'MLB', 'market': 'moneyline',
           'model_scope': 'full_game', 'start': '2026-10-01T10:00:00+00:00',
           'ownership': 'engine', 'stake': stake, 'reference_odds': odds}
    tip.update(changes)
    return {'family': 'contract:' + str(i), 'tip_id': str(i), 'issued_at': '2026-10-01T09:00:00+00:00',
            'payload': tip, 'status': status,
            'pnl': stake * (odds - 1) if status == 'won' else -stake if status == 'lost' else 0.,
            'outcome_evidence': {'settled_at': '2026-10-01T12:00:00+00:00', 'source': 'https://statsapi.mlb.com/api/v1/schedule', 'fetched_at': '2026-10-01T11:59:00+00:00', 'home_score': 1, 'away_score': 0}}


class OutcomeSizingTests(unittest.TestCase):
    def summary(self, rows):
        return summary_for(build_snapshot(rows, AS_OF), decision('new')['payload'])

    def test_time_money_manual_and_duplicate_exclusions(self):
        rows = [decision(1), decision(1), decision(2), decision(3), decision(4), decision(5), decision(6), decision(7)]
        rows[2]['outcome_evidence']['settled_at'] = '2026-10-04T00:00:00Z'
        rows[3]['pnl'] = 9
        rows[4]['outcome_evidence']['settled_at'] = 'invalid'
        rows[5]['issued_at'] = '2026-10-02T00:00:00Z'
        rows[6]['payload']['start'] = '2026-10-02T00:00:00Z'
        rows[7]['outcome_evidence']['manual'] = True
        snap = build_snapshot(rows, AS_OF)
        self.assertEqual(len(snap['accepted']), 1)
        self.assertEqual(len(snap['excluded']), 7)
        self.assertEqual(snap['exclusion_counts']['duplicate_family'], 1)
        self.assertEqual(snap, build_snapshot(reversed(rows), AS_OF))
        self.assertEqual(len(snap['inputs']), 8)

    def test_push_and_equal_game_weighting(self):
        rows = [decision(1, stake=1, game='a'), decision(2, stake=9, game='a'),
                decision(3, 'lost', game='b'), decision(4, 'push', game='c')]
        summary = self.summary(rows)
        self.assertEqual(summary['independent_games'], 3)
        self.assertEqual(summary['decisive_games'], 2)
        self.assertEqual(summary['observations'], 4)
        self.assertEqual(summary['stake'], 12)
        self.assertEqual(summary['pnl'], 9)
        self.assertEqual(summary['game_roi'], 0)
        self.assertEqual(summary['game_wr'], .5)
        self.assertEqual(summary['break_even_wr'], .5)
        self.assertEqual(summary['pushes'], 1)
        self.assertEqual(summary['factor'], 1)
        self.assertTrue(verify_summary(summary))

    def test_alias_and_scope_segmentation(self):
        rows = [decision(1, market='spreads', line=-.5), decision(2),
                decision(3, market='innings_1_5:moneyline', model_scope='innings_1_5'),
                decision(4, league='NFL')]
        summary = self.summary(rows)
        self.assertEqual(summary['independent_games'], 2)
        self.assertEqual(summary['segment'], 'MLB|full_game|moneyline')

    def test_sparse_shrinkage_and_downward_change(self):
        self.assertEqual(self.summary([decision(1)])['factor'], 1)
        tiny = self.summary([decision(1), decision(2)])['factor']
        self.assertAlmostEqual(tiny, 1 + (2 / 22 / .20) * (1 / 22 / .10))
        larger = self.summary([decision(i) for i in range(20)])['factor']
        self.assertGreater(larger, tiny)
        self.assertLessEqual(larger, 2)
        self.assertLess(self.summary([decision(1), decision(2), decision(3, 'lost'), decision(4, 'lost')])['factor'], tiny)

    def test_caps_bankroll_no_revival_and_original_fields(self):
        tip = decision('new')['payload']
        tip['outcome_sizing_evidence'] = self.summary([decision(i) for i in range(20)])
        settings = {'outcome_sizing_policy': POLICY, 'max_bet': 5}
        base = {'amount': 4, 'note': 'base', 'maximum_stake': 5, 'probability': .6}
        result = apply_adjustment(base, tip, settings, float('inf'))
        self.assertEqual(result['amount'], 5)
        self.assertEqual(result['probability'], .6)
        self.assertEqual(result['outcome_adjustment']['base_amount'], 4)
        self.assertEqual(base['amount'], 4)
        self.assertEqual(apply_adjustment(base, tip, settings, 2.345)['amount'], 2.34)
        self.assertEqual(apply_adjustment(base, tip, {**settings, 'bankroll': 100}, 5)['amount'], 4)
        self.assertEqual(apply_adjustment({**base, 'amount': 0}, tip, settings, 5)['amount'], 0)
        self.assertEqual(apply_adjustment(base, tip, settings, .99)['amount'], 0)

    def test_legacy_equality_and_unavailable(self):
        base = {'amount': 1.23, 'note': 'unchanged'}
        self.assertEqual(apply_adjustment(base, {}, {}, 5), base)
        active = apply_adjustment(base, decision('new')['payload'], {'outcome_sizing_policy': POLICY}, 5)
        self.assertEqual(active['amount'], base['amount'])
        self.assertEqual(active['outcome_adjustment']['factor'], 1)
        self.assertEqual(active['outcome_adjustment']['state'], 'unavailable')

    def test_tampering_nonfinite_unknown_failclosed(self):
        summary = self.summary([decision(1), decision(2)])
        forged = copy.deepcopy(summary)
        forged['factor'] = 1.9
        self.assertFalse(verify_summary(forged))
        tip = {**decision('new')['payload'], 'outcome_sizing_evidence': forged}
        with self.assertRaises(ValueError):
            apply_adjustment({'amount': 1}, tip, {'outcome_sizing_policy': POLICY}, 5)
        with self.assertRaises(ValueError):
            apply_adjustment({'amount': 1}, tip, {'outcome_sizing_policy': 'unknown'}, 5)
        with self.assertRaises(ValueError):
            apply_adjustment({'amount': float('nan')}, tip, {'outcome_sizing_policy': POLICY}, 5)
        snapshot = build_snapshot([decision(1)], AS_OF)
        snapshot['as_of'] = '2026-10-04T00:00:00Z'
        with self.assertRaises(ValueError):
            summary_for(snapshot, decision('new')['payload'])

    def test_nonfinite_original_money_excluded(self):
        rows = [decision(1), decision(2), decision(3)]
        rows[0]['payload']['stake'] = float('inf')
        rows[1]['payload']['reference_odds'] = float('nan')
        rows[2]['pnl'] = float('inf')
        self.assertEqual(build_snapshot(rows, AS_OF)['segments'], {})

    def test_rehashed_inconsistent_summary_and_wrong_segment_rejected(self):
        summary = self.summary([decision(1), decision(2)])
        forged = copy.deepcopy(summary)
        forged['game_wr'] = .99
        forged['summary_sha256'] = hashlib.sha256(json.dumps(
            {k: v for k, v in forged.items() if k != 'summary_sha256'},
            sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        self.assertFalse(verify_summary(forged))
        tip = {**decision('new', league='NFL')['payload'], 'outcome_sizing_evidence': summary}
        with self.assertRaises(ValueError):
            apply_adjustment({'amount': 1}, tip, {'outcome_sizing_policy': POLICY}, 5)

    def test_pushes_never_supply_decisive_evidence_and_zero_not_funded(self):
        summary = self.summary([decision(1, 'push'), decision(2, 'push'), decision(3, stake=0)])
        self.assertEqual(summary['independent_games'], 2)
        self.assertEqual(summary['decisive_games'], 0)
        self.assertEqual(summary['factor'], 1)
        self.assertEqual(summary['stake'], 2)

    def test_original_odds_determine_break_even_and_input_hash(self):
        rows = [decision(1, odds=1.5), decision(2, odds=3)]
        snapshot = build_snapshot(rows, AS_OF)
        summary = summary_for(snapshot, rows[0]['payload'])
        self.assertAlmostEqual(summary['break_even_wr'], .5)
        rows[1]['payload']['reference_odds'] = 4
        rows[1]['pnl'] = 3
        updated = build_snapshot(rows, AS_OF)
        self.assertNotEqual(snapshot['snapshot_sha256'], updated['snapshot_sha256'])
        self.assertNotEqual(snapshot['input_sha256'], updated['input_sha256'])

    def test_new_loss_cannot_increase_three_odds3_wins_stake(self):
        rows = [decision(i, odds=3) for i in range(3)]
        before = self.summary(rows)
        after = self.summary(rows + [decision(4, 'lost', odds=3)])
        self.assertLessEqual(after['factor'], before['factor'])
        base = {'amount': 4, 'maximum_stake': 5}
        settings = {'outcome_sizing_policy': POLICY}
        tip = decision('new')['payload']
        stake_before = apply_adjustment(base, {**tip, 'outcome_sizing_evidence': before}, settings, 5)['amount']
        stake_after = apply_adjustment(base, {**tip, 'outcome_sizing_evidence': after}, settings, 5)['amount']
        self.assertLessEqual(stake_after, stake_before)

    def test_loss_and_push_monotonic_grid(self):
        for wins in (2, 3, 5, 10, 25):
            for losses in (0, 1, 3, 10):
                for odds in (1.2, 1.5, 2., 3., 8.):
                    rows = [decision('w'+str(i), odds=odds) for i in range(wins)]
                    rows += [decision('l'+str(i), 'lost', odds=odds) for i in range(losses)]
                    before = self.summary(rows)['factor']
                    after = self.summary(rows + [decision('extra_loss', 'lost', odds=odds)])['factor']
                    pushed = self.summary(rows + [decision('extra_push', 'push', odds=odds)])['factor']
                    self.assertLessEqual(after, before + 1e-12)
                    self.assertLessEqual(pushed, before + 1e-12)

    def test_unavailable_rows_only_change_diagnostic_digest(self):
        rows = [decision(1), decision(2)]
        original = build_snapshot(rows, AS_OF)
        future = decision(3)
        future['outcome_evidence']['settled_at'] = '2026-10-04T00:00:00Z'
        opened = decision(4, 'open')
        changed = build_snapshot(rows + [future, opened], AS_OF)
        self.assertEqual(original['snapshot_sha256'], changed['snapshot_sha256'])
        self.assertNotEqual(original['diagnostics_sha256'], changed['diagnostics_sha256'])
        self.assertEqual(summary_for(original, rows[0]['payload']), summary_for(changed, rows[0]['payload']))

    def test_unknown_or_future_source_cannot_boost(self):
        for source in ('', 'unknown', 'http://sports.example/scores'):
            rows = [decision(1), decision(2)]
            for row in rows:
                row['outcome_evidence']['source'] = source
            self.assertEqual(self.summary(rows)['factor'], 1)
        rows = [decision(1), decision(2)]
        for row in rows:
            row['outcome_evidence']['fetched_at'] = '2026-10-02T00:00:00Z'
        self.assertEqual(self.summary(rows)['factor'], 1)
