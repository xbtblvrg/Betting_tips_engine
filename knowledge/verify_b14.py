"""Reproduce selected B14 arithmetic checks, not a trading model."""
import json
import math
from pathlib import Path


def decimal(a):
    if abs(a) < 100:
        raise ValueError('American odds require |a| >= 100')
    return 1 + (100 / -a if a < 0 else a / 100)


def normalized_ratio(p, c=0.5238):
    # Under fair odds q=p, the book's weighted wins/losses have this limit.
    return c*c / (c*c + p*(1-p))


out = {
    'minus110': {'decimal': decimal(-110), 'break_even': 1/decimal(-110),
                 'equal_action_hold': 1-decimal(-110)/2},
    'parlay_1_5_cubed': 1.5**3,
    'kelly_p55_minus110': (.55*decimal(-110)-1)/(decimal(-110)-1),
    'roi_table_5_1': {'gross_return': 1461, 'stake': 1000, 'profit': 461, 'yield': .461},
    'table_5_2_correct_net': 12.181818181818183,
    'roulette_11_black_american_percent': 100*(18/38)**11,
    'roulette_11_black_european_percent': 100*(18/37)**11,
    'plus200_break_even': 1/decimal(200),
    'fair_american_p45': 100*(1-.45)/.45,
    'normalized_ratio_no_skill': {str(p): normalized_ratio(p) for p in [.25, .5, .5238, .75]},
    'gambler_z_39_13': {'raw': (39-13)/math.sqrt(52),
                        'continuity_corrected': (39-26-.5)/math.sqrt(13)},
    'binomial_23_of_30_two_sided_p50': 2*sum(math.comb(30,k) for k in range(23,31))/2**30,
    'skew_breakpoint_minus110': 1/21,
    'hold_minus105_plus100': 1-1/(1/decimal(-105)+1/decimal(100)),
    'hold_minus110_plus100': 1-1/(1/decimal(-110)+1/decimal(100)),
    'sd_8_5_1_9_2': {'population': math.sqrt(10), 'sample': math.sqrt(12.5)},
}
out['double_before_ruin_100_units'] = {
    str(p): 1/(1+((1-p)/p)**100) for p in [.45,.49,.495,.5,.505,.51,.55]
}
scores = [16,24,31,33,17,21,36,42,21,19,27,17,20,9,10,33]
out['panthers_table_17_1_from_printed_scores'] = [
    {'week': i+1, 'score': value, 'prior_mean': sum(scores[:i])/i,
     'error': value-sum(scores[:i])/i} for i,value in enumerate(scores) if i
]
out['panthers_within_7_prior_mean'] = sum(abs(r['error'])<=7 for r in out['panthers_table_17_1_from_printed_scores'])
assert math.isclose(out['kelly_p55_minus110'], .055)
assert normalized_ratio(.25) > .59
assert math.isclose(out['minus110']['equal_action_hold'], 1/22)
dest = Path(__file__).parent/'reading_v2'/'B14-numerical-audit.json'
dest.write_text(json.dumps(out, ensure_ascii=False, indent=2)+'\n')
print(json.dumps(out, ensure_ascii=False, indent=2))
