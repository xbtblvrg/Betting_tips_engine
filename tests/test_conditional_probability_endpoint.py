"""Decimal complement endpoints remain reproducible without widening support."""
import pytest

from betting_engine.domain import utcnow
from betting_engine.decisions import rank_and_size
from betting_engine.governance import EvidenceGuard, KnowledgeAgent, verify_publication
from betting_engine.market_anchor import conditional_model_probability
from test_probability_support import make


@pytest.mark.parametrize('win,push',[(.1,.9),(.2,.8),(.3,.7),(.8,.2),(.01,.99)])
def test_decimal_endpoint_candidate_guard_and_publication_agree(win,push):
    args=make(win,push,{'market_probability':.9,'conditional_push_free':True})
    tip=args[0]
    assert tip['market_anchor']['model_probability']==1.
    assert 0<tip['probability']<win
    assert tip['push_probability']==pytest.approx(push)
    result=EvidenceGuard(KnowledgeAgent()).check(*args)
    assert result['passed'],result['errors']
    rank_and_size([tip],args[-1])
    verify_publication(tip,utcnow(),args[-1])


@pytest.mark.parametrize('win,push',[(.1,.9000000001),(.2,.81),(-.1,.9),(.1,1.),
    (float('nan'),.9),(.1,float('inf')),(True,0.),(.1,True)])
def test_substantive_invalid_conditional_mass_rejected(win,push):
    with pytest.raises(ValueError):
        conditional_model_probability(win,push,allow_endpoints=True)


def test_no_normalization_of_interior_or_legacy_probabilities():
    assert conditional_model_probability(.1,.9)==.1/(1-.9)
    assert conditional_model_probability(.1,.8,allow_endpoints=True)==.1/(1-.8)
    assert conditional_model_probability(.099999999,.9,allow_endpoints=True)<1.
    assert conditional_model_probability(0.,.9,allow_endpoints=True)==0.
    assert conditional_model_probability(1e-100,1.-1e-15,allow_endpoints=True)<1e-80


def test_invalid_original_mass_still_rejected_before_candidate():
    with pytest.raises(ValueError):make(.2,.81,{'market_probability':.9,'conditional_push_free':True})
