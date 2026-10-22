"""Explicit complete-name aliases observed across independent sports/Stake feeds.

This is not fuzzy city matching: Los Angeles Lakers never aliases Clippers.
The event join still independently requires both opponents and start time.
"""
import re
import unicodedata

CLIPPERS_NAMES=('LA Clippers','Los Angeles Clippers')


def compact(name):
    value=unicodedata.normalize('NFKD',str(name)).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]','',value)


MLS_CANONICAL = {
    'atlantaunited': 'atlantaunited',
    'atlantaunitedfc': 'atlantaunited',
    'austin': 'austin',
    'austinfc': 'austin',
    'charlotte': 'charlotte',
    'charlottefc': 'charlotte',
    'chicagofire': 'chicagofire',
    'chicagofirefc': 'chicagofire',
    'cincinnati': 'cincinnati',
    'fccincinnati': 'cincinnati',
    'coloradorapids': 'coloradorapids',
    'columbuscrew': 'columbuscrew',
    'dcunited': 'dcunited',
    'dallas': 'dallas',
    'fcdallas': 'dallas',
    'houstondynamo': 'houstondynamo',
    'houstondynamofc': 'houstondynamo',
    'intermiami': 'intermiami',
    'intermiamicf': 'intermiami',
    'kansascity': 'sportingkansascity',
    'sportingkansascity': 'sportingkansascity',
    'losangeles': 'lafc',
    'lafc': 'lafc',
    'losangelesgalaxy': 'lagalaxy',
    'lagalaxy': 'lagalaxy',
    'minnesotaunited': 'minnesotaunited',
    'minnesotaunitedfc': 'minnesotaunited',
    'montreal': 'cfmontreal',
    'cfmontreal': 'cfmontreal',
    'nashville': 'nashville',
    'nashvillesc': 'nashville',
    'newengland': 'newenglandrevolution',
    'newenglandrevolution': 'newenglandrevolution',
    'newyorkcity': 'newyorkcityfc',
    'newyorkcityfc': 'newyorkcityfc',
    'newyorkredbulls': 'redbullnewyork',
    'redbullnewyork': 'redbullnewyork',
    'orlandocity': 'orlandocity',
    'orlandocitysc': 'orlandocity',
    'philadelphiaunion': 'philadelphiaunion',
    'portlandtimbers': 'portlandtimbers',
    'realsaltlake': 'realsaltlake',
    'sandiego': 'sandiego',
    'sandiegofc': 'sandiego',
    'sanjose': 'sanjoseearthquakes',
    'sanjoseearthquakes': 'sanjoseearthquakes',
    'seattlesounders': 'seattlesounders',
    'seattlesoundersfc': 'seattlesounders',
    'stlouiscity': 'stlouiscity',
    'stlouiscitysc': 'stlouiscity',
    'toronto': 'toronto',
    'torontofc': 'toronto',
    'vancouverwhitecaps': 'vancouverwhitecaps',
    'vancouverwhitecapsfc': 'vancouverwhitecaps',
    # EPL Teams
    'brighton': 'brightonhovealbion',
    'brightonhovealbion': 'brightonhovealbion',
    'tottenham': 'tottenhamhotspur',
    'tottenhamhotspur': 'tottenhamhotspur',
    'bournemouth': 'afcbournemouth',
    'afcbournemouth': 'afcbournemouth',
    'manchesterunited': 'manchesterunited',
    'manchesterutd': 'manchesterunited',
    'manunited': 'manchesterunited',
    'manchestercity': 'manchestercity',
    'mancity': 'manchestercity',
    'newcastle': 'newcastleunited',
    'newcastleunited': 'newcastleunited',
    'westham': 'westhamunited',
    'westhamunited': 'westhamunited',
    'nottingham': 'nottinghamforest',
    'nottinghamforest': 'nottinghamforest',
    'wolverhampton': 'wolverhamptonwanderers',
    'wolves': 'wolverhamptonwanderers',
    'wolverhamptonwanderers': 'wolverhamptonwanderers',
    'leeds': 'leedsunited',
    'leedsunited': 'leedsunited',
    'ipswich': 'ipswichtown',
    'ipswichtown': 'ipswichtown',
    'leicester': 'leicestercity',
    'leicestercity': 'leicestercity',
    # UCL / European Teams
    'feyenoord': 'feyenoord',
    'feyenoordrotterdam': 'feyenoord',
    'skslovanbratislava': 'slovanbratislava',
    'slovanbratislava': 'slovanbratislava',
    'stuttgart': 'vfbstuttgart',
    'vfbstuttgart': 'vfbstuttgart',
    'fcshakhtardonetsk': 'shakhtardonetsk',
    'shakhtardonetsk': 'shakhtardonetsk',
    'porto': 'fcporto',
    'fcporto': 'fcporto',
    'fenerbahce': 'fenerbahce',
    'fenerbahceistanbul': 'fenerbahce',
    'roma': 'asroma',
    'asroma': 'asroma',
    'bodoglimt': 'bodoglimt',
    'parissaintgermain': 'parissaintgermain',
    'psg': 'parissaintgermain',
    'lask': 'lasklinz',
    'lasklinz': 'lasklinz',
    'como': 'como',
    'comofc': 'como',
    'calciocomo': 'como',
    'borussiadortmund': 'borussiadortmund',
    'dortmund': 'borussiadortmund',
    'bvb': 'borussiadortmund',
    'aek': 'aekathens',
    'aekathens': 'aekathens',
    'realbetis': 'realbetis',
    'betis': 'realbetis',
    'realmadrid': 'realmadrid',
    'astonvilla': 'astonvilla',
    'liverpool': 'liverpool',
    'liverpoolfc': 'liverpool',
}

import json
from pathlib import Path

_LEARNED_ALIASES_PATH = Path(__file__).resolve().parents[2] / 'data' / 'learned_aliases.json'
_LEARNED_CACHE = {}
_LAST_MTIME = 0.0


def get_learned_aliases():
    global _LAST_MTIME, _LEARNED_CACHE
    try:
        if _LEARNED_ALIASES_PATH.exists():
            mtime = _LEARNED_ALIASES_PATH.stat().st_mtime
            if mtime > _LAST_MTIME:
                _LEARNED_CACHE = json.loads(_LEARNED_ALIASES_PATH.read_text(encoding='utf-8'))
                _LAST_MTIME = mtime
    except Exception:
        pass
    return _LEARNED_CACHE


def register_learned_alias(canonical_name, alias_variant):
    c_val = compact(canonical_name)
    a_val = compact(alias_variant)
    if not c_val or not a_val or c_val == a_val:
        return
    aliases = dict(get_learned_aliases())
    aliases[a_val] = c_val
    try:
        from .io_utils import atomic_json
        atomic_json(_LEARNED_ALIASES_PATH, aliases)
        global _LEARNED_CACHE, _LAST_MTIME
        _LEARNED_CACHE = aliases
        _LAST_MTIME = _LEARNED_ALIASES_PATH.stat().st_mtime
    except Exception:
        pass


def canonical(name):
    value=compact(name)
    if value in ('laclippers','losangelesclippers'): return 'losangelesclippers'
    learned = get_learned_aliases()
    if value in learned:
        return learned[value]
    return MLS_CANONICAL.get(value, value)


def exact_team_name_match(left,right):
    return bool(compact(left)) and canonical(left)==canonical(right)


def team_name_variants(name):
    return CLIPPERS_NAMES if canonical(name)=='losangelesclippers' else (name,)

