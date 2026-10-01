import unittest
from datetime import datetime, timezone, timedelta
from betting_engine.quant import (Probabilities, decimal_payoffs, token_payoffs,
    expected_return, kelly_fraction, paper_stake, validate_asof, brier_score)


class QuantTests(unittest.TestCase):
    def test_negative_edge_abstains(self):
        p, r = Probabilities(.5, 0, .5), decimal_payoffs(1.9)
        self.assertAlmostEqual(expected_return(p, r), -.05)
        self.assertEqual(kelly_fraction(p, r), 0)

    def test_kelly_matches_analytic_binary_solution(self):
        for odds in (1.5, 2, 3.5):
            for win in (.4, .6, .9):
                expected = max(0, (win * odds - 1) / (odds - 1))
                self.assertAlmostEqual(kelly_fraction(Probabilities(win, 0, 1-win),
                                                      decimal_payoffs(odds)), expected)

    def test_push_probability_is_not_a_loss(self):
        p = Probabilities(.5, .1, .4)
        self.assertAlmostEqual(expected_return(p, decimal_payoffs(2)), .1)
        self.assertAlmostEqual(kelly_fraction(p, decimal_payoffs(2)), 1/9)

    def test_upfront_fee_can_remove_edge(self):
        p = Probabilities(.51, 0, .49)
        self.assertGreater(expected_return(p, decimal_payoffs(2)), 0)
        self.assertLess(expected_return(p, decimal_payoffs(2, .03)), 0)
        self.assertLess(decimal_payoffs(2, .03).push, 0)

    def test_token_cost_includes_fees(self):
        p = Probabilities(.6, 0, .4)
        self.assertAlmostEqual(expected_return(p, token_payoffs(.5)), .2)
        self.assertLess(expected_return(p, token_payoffs(.61)), 0)

    def test_haircut_conserves_probability(self):
        self.assertEqual(Probabilities(.5, .1, .4).conservative(.7), Probabilities(0, .1, .9))

    def test_small_bankroll_skips_instead_of_rounding_up(self):
        stake = paper_stake(Probabilities(.7, 0, .3), decimal_payoffs(2),
                            bankroll=50, daily_remaining=50, event_remaining=50)
        self.assertEqual(stake, 0)

    def test_stake_limits(self):
        p, r = Probabilities(.8, 0, .2), decimal_payoffs(2)
        for budget, expected in ((20, 5), (1.237, 1.23), (.99, 0)):
            self.assertEqual(paper_stake(p, r, bankroll=10000,
                daily_remaining=budget, event_remaining=10), expected)

    def test_rejects_bad_numbers(self):
        for value in (float("nan"), float("inf"), -.1, 1.1):
            with self.assertRaises(ValueError):
                Probabilities(value, 0, 1-value)
        with self.assertRaises(ValueError):
            Probabilities(.6, .1, .5)

    def test_asof_prevents_future_started_and_stale_data(self):
        now = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
        valid = dict(decision=now, starts_at=now+timedelta(hours=1),
                     information_at=now, quote_at=now)
        validate_asof(**valid)
        for field, stamp in (("information_at", now+timedelta(seconds=1)),
                              ("quote_at", now+timedelta(seconds=1)),
                              ("quote_at", now-timedelta(minutes=3)),
                              ("starts_at", now),
                              ("decision", now.replace(tzinfo=None))):
            with self.subTest(field=field, stamp=stamp):
                with self.assertRaises(ValueError):
                    validate_asof(**{**valid, field: stamp})

    def test_scoring_is_probability_sensitive(self):
        self.assertAlmostEqual(brier_score([.8, .2], [1, 0]), .04)
        with self.assertRaises(ValueError):
            brier_score([.8], [2])


if __name__ == "__main__":
    unittest.main()
