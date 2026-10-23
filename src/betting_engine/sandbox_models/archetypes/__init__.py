"""Model archetypes for parallel tournament exploration."""
from .negative_binomial import NegativeBinomialOverdispersionModel
from .situational_fatigue import SituationalFatigueModel
from .odds_damped_kelly import OddsDampedKellyShrinkageModel
from .bivariate_copula import BivariateCopulaHazardModel

__all__ = [
    'NegativeBinomialOverdispersionModel',
    'SituationalFatigueModel',
    'OddsDampedKellyShrinkageModel',
    'BivariateCopulaHazardModel',
]
