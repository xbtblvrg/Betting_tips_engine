"""Strict specifier identity for already modeled scoped score contracts.

Recognition does not approve a model, refresh a quote, or authorize settlement.
Only the observed numeric line and period/quarter/inning markers are accepted.
"""
import math
import re


def scoped_total_line(specifiers, scope):
    """Return a nonnegative total with exact scope and unique specifier keys."""
    line = _scoped_line(specifiers, scope, 'total')
    if line < 0:
        raise ValueError('Negative score total')
    return line


def _scoped_line(specifiers, scope, line_key):
    """Parse the observed structured line, without outcome-label fallback.

    The title supplies the scope; a supplied period/quarter number must agree.
    Line-only contracts retain the existing explicit-title identity path.
    """
    values = _scoped_values(specifiers, scope, line_key)
    raw = values.get(line_key, '')
    if not re.fullmatch(r'[+-]?\d+(?:\.\d+)?', raw):
        raise ValueError('Invalid score line')
    line = float(raw)
    if not math.isfinite(line) or not math.isfinite(line * 2) or line * 2 != round(line * 2):
        raise ValueError('Unsupported score line')
    return line


def validate_scoped_specifiers(specifiers, scope):
    """Validate a line-free named score contract, without granting approval.

    A missing marker retains the explicit-title path. A supplied marker must
    agree with that title; line or unknown keys cannot be silently discarded.
    """
    _scoped_values('' if specifiers is None else specifiers, scope)


def _scoped_values(specifiers, scope, line_key=None):
    if not isinstance(specifiers, str) or len(specifiers) > 512:
        raise ValueError('Missing or oversized score specifiers')
    values = {}
    for field in specifiers.split('|') if specifiers else []:
        key, sep, value = field.partition('=')
        if not sep or key in values or key not in (line_key, 'periodnr', 'quarternr', 'inningnr', 'setnr', 'halfnr'):
            raise ValueError('Unknown or duplicate score specifier')
        values[key] = value
    for key in ('periodnr', 'quarternr', 'inningnr', 'setnr', 'halfnr'):
        if key not in values:
            continue
        expected = None
        if key == 'periodnr' and re.fullmatch(r'period_[123]', scope):
            expected = scope[-1]
        elif key == 'quarternr' and re.fullmatch(r'quarter_[1234](?:_ot)?', scope):
            expected = scope.split('_')[1]
        elif key == 'inningnr' and scope == 'inning_1':
            expected = '1'
        elif key == 'setnr' and re.fullmatch(r'period_[123]', scope):
            expected = scope[-1]
        elif key == 'halfnr' and re.fullmatch(r'half_[12](?:_ot)?', scope):
            expected = scope.split('_')[1]
        if expected is None or values[key] != expected:
            raise ValueError('Score specifier contradicts named scope')
    return values
