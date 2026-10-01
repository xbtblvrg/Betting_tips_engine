"""Passive utility identity boundaries; no odds/model/settlement activation."""
import pytest
from betting_engine.period_contracts import scoped_total_line, validate_scoped_specifiers


@pytest.mark.parametrize('scope,key', [('period_1','periodnr=1'), ('quarter_4_ot','quarternr=4'), ('inning_1','inningnr=1')])
def test_numeric_total_and_marker_must_both_describe_exact_contract(scope,key):
    assert scoped_total_line('total=0.5|'+key,scope)==0.5
    validate_scoped_specifiers(key,scope)
    for wrong in ('period_2','quarter_1','regulation','innings_1_5'):
        with pytest.raises(ValueError): scoped_total_line('total=0.5|'+key,wrong)
    with pytest.raises(ValueError): validate_scoped_specifiers('total=0.5|'+key,scope)


@pytest.mark.parametrize('value',['-1','0.25','NaN','inf','1e2','1.5x','', '1'*400])
def test_total_parser_cannot_invent_or_round_unusable_line(value):
    with pytest.raises(ValueError): scoped_total_line('total='+value,'period_1')


@pytest.mark.parametrize('value',['total=1.5|total=1.5','total=1.5|quarternr=1',
    'total=1.5|periodnr=2','total=1.5|', 'total=1.5|other=1',{},True,None])
def test_ambiguous_scope_or_invalid_input_rejected(value):
    with pytest.raises(ValueError): scoped_total_line(value,'period_1')
