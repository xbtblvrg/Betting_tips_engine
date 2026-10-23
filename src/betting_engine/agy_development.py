import hashlib
import json
from .claude_development import VERSION, MAX_REPLY_BYTES, _strict_json, _validate_schema, _edits, _sha


def finish_agy_step(stdout, context, model):
    if context.get('version') != VERSION:
        raise ValueError('Invalid Antigravity step context')
    body = stdout.encode('utf-8') if isinstance(stdout, str) else stdout
    if not isinstance(body, bytes) or len(body) > MAX_REPLY_BYTES:
        raise ValueError('Antigravity output bound exceeded')
    raw_str = body.decode('utf-8', errors='replace')
    try:
        envelope = _strict_json(raw_str)
        if isinstance(envelope, dict) and 'response' in envelope:
            resp_text = envelope['response']
        else:
            resp_text = raw_str
    except (ValueError, TypeError):
        resp_text = raw_str

    resp_text = str(resp_text).strip()
    if resp_text.startswith('```'):
        lines = resp_text.split('\n')
        if lines[0].startswith('```'):
            lines = lines[1:]
        if lines and lines[-1].startswith('```'):
            lines = lines[:-1]
        resp_text = '\n'.join(lines).strip()

    if not resp_text:
        from .provider_runtime import ProviderUnavailable
        raise ProviderUnavailable('Antigravity capacity returned empty response; candidate retained for retry')

    try:
        parsed = _strict_json(resp_text)
    except Exception as exc:
        from .provider_runtime import ProviderUnavailable
        raise ProviderUnavailable(f'Antigravity returned non-JSON output ({exc}); candidate retained for retry')

    if not isinstance(parsed, dict) or 'report' not in parsed or 'edits' not in parsed:
        raise ValueError('Antigravity output missing report or edits')

    _validate_schema(parsed, context['output_schema'])
    return {
        'provider': 'agy',
        'model': model,
        'report': parsed['report'],
        'edits': _edits(parsed['edits'], context),
        'stdout_sha256': _sha(body)
    }
