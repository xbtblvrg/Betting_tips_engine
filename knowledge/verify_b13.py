"""Reproduce selected Soccermatics checks; no fitted model or betting signal."""
import json
import math
from pathlib import Path


def poisson(k, mean):
    return math.exp(-mean) * mean**k / math.factorial(k)


out = {}
out['independent_poisson_0.5_1.5'] = {
    name: sum(poisson(h, .5) * poisson(a, 1.5)
              for h in range(30) for a in range(30) if test(h, a))
    for name, test in [('home', lambda h, a: h > a),
                       ('draw', lambda h, a: h == a),
                       ('away', lambda h, a: h < a)]
}
out['xg_printed_coefficients'] = [sum(c*x for c, x in zip((.034, .124, .322), row))
                                for row in [(5, 5, 1), (4, 13, 1), (6, 15, 0), (2, 5, 0)]]
q = [[.25, .20, .10], [.10, .25, .20], [.10, .10, .25]]
r = [.05, .15, .05]
v = [0., 0., 0.]
for _ in range(200):
    v = [r[i] + sum(q[i][j]*v[j] for j in range(3)) for i in range(3)]
out['markov_goal_values_M_B_W'] = v
tail = 1 - math.exp(-math.exp(-(50-26.9)/5.44))
out['gumbel_correct_outer_minus'] = {'tail_above_50': tail, 'return_period': 1/tail,
                                    'one_or_more_in_73_at_1_over_73': 1-(1-1/73)**73}
out['binomial_at_least_25_of_28_p_0.725'] = sum(
    math.comb(28,k)*.725**k*.275**(28-k) for k in range(25,29))
out['penalty_count_density_ratio'] = (.61/(18*20))/(.39/(2*18*12))
overround_sum = sum(1/o for o in [2.3, 3.4, 3.1])
out['odds_and_returns'] = {
    'sum_inverse_odds': overround_sum, 'normalized_hold': 1-1/overround_sum,
    'arsenal_net_ev_per_unit': .3375*3.1-1,
    'full_kelly_p_0.64_odds_3': (.64*3-1)/2,
    'return_on_starting_bankroll': 108.30/400,
    'profit_over_turnover': 108.30/388.76,
    'five_week_net': sum([-18.06, 1.93, .33, 17.65, 14.49]),
    '52_full_bankroll_turnovers_2pct_hold': 10*.98**52,
    '52_full_bankroll_turnovers_6pct_hold': 10*.94**52,
}
out['projectile_height_at_27m_y0_2_v16_g9.8'] = {
    str(degrees): 2+27*math.tan(math.radians(degrees))
    -9.8*27**2/(2*16**2*math.cos(math.radians(degrees))**2)
    for degrees in (30,40,50)
}
out['independent_70pct_signals'] = {'error_given_agreement': .3**2/(.7**2+.3**2),
                                      'first_two_disagree': 2*.7*.3}
out['star_worker_endpoint_gain'] = {str(s): (2*s-10)/11 for s in (0,5,10)}
out['tracking_scalar_count'] = {
    'printed_formula': ((200*22*2)+(200*2*4)+2000)*3*60*95,
    'if_four_glove_sensors': ((200*22*2)+(200*4)+2000)*3*60*95,
}
out['expected_zero_goal_matches_of_380'] = {'poisson_2.79': 380*math.exp(-2.79),
                                          '90_bernoulli_0.031': 380*(1-.031)**90}
out['interpretation'] = ('Own calculations from printed inputs. See B13-notes.hu.md '
                         'for rounding, draw adjustment, exposure, selection and source caveats. '
                         'Numerical differences alone do not establish every discrepancy as a typo.')
target = Path(__file__).parent / 'reading_v2' / 'B13-numerical-audit.json'
target.write_text(json.dumps(out, ensure_ascii=False, indent=2)+'\n')
print(json.dumps(out, ensure_ascii=False, indent=2))
