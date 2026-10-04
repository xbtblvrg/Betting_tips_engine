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


def canonical(name):
    value=compact(name)
    return 'losangelesclippers' if value in ('laclippers','losangelesclippers') else value


def exact_team_name_match(left,right):
    return bool(compact(left)) and canonical(left)==canonical(right)


def team_name_variants(name):
    return CLIPPERS_NAMES if canonical(name)=='losangelesclippers' else (name,)
