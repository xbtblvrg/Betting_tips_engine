"""Model archetypes for parallel tournament exploration."""
from .negative_binomial import NegativeBinomialOverdispersionModel
from .situational_fatigue import SituationalFatigueModel
from .odds_damped_kelly import OddsDampedKellyShrinkageModel
from .bivariate_copula import BivariateCopulaHazardModel
from .zero_inflated import ZeroInflatedPoissonModel
from .extreme_value_gumbel import ExtremeValueGumbelModel
from .markov_chain_tennis import MarkovChainTennisModel
from .weibull_hazard import WeibullRenewalHazardModel

__all__ = [
    'NegativeBinomialOverdispersionModel',
    'SituationalFatigueModel',
    'OddsDampedKellyShrinkageModel',
    'BivariateCopulaHazardModel',
    'ZeroInflatedPoissonModel',
    'ExtremeValueGumbelModel',
    'MarkovChainTennisModel',
    'WeibullRenewalHazardModel',
]
