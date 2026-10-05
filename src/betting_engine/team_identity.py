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
}


def canonical(name):
    value=compact(name)
    if value in ('laclippers','losangelesclippers'): return 'losangelesclippers'
    return MLS_CANONICAL.get(value, value)


def exact_team_name_match(left,right):
    return bool(compact(left)) and canonical(left)==canonical(right)


def team_name_variants(name):
    return CLIPPERS_NAMES if canonical(name)=='losangelesclippers' else (name,)
