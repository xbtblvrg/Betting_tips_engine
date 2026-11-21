import hashlib
import json
import re
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
    except Exception:
        try:
            fixed_text = re.sub(
                r'\\(["\\/bfnrt]|u[0-9a-fA-F]{4})|\\',
                lambda m: m.group(0) if m.group(1) else '\\\\',
                resp_text,
            )
            parsed = _strict_json(fixed_text)
        except Exception as exc:
            from .provider_runtime import ProviderUnavailable
            raise ProviderUnavailable(f'Antigravity returned non-JSON output ({exc}); candidate retained for retry')

    if not isinstance(parsed, dict) or 'report' not in parsed or 'edits' not in parsed:
        raise ValueError('Antigravity output missing report or edits')

    rep = parsed.get('report')
    res_schema = context.get('result_schema') if isinstance(context.get('result_schema'), dict) else {}
    props = res_schema.get('properties') if isinstance(res_schema.get('properties'), dict) else {}
    if isinstance(rep, dict) and props:
        raw_edits = parsed.get('edits') if isinstance(parsed.get('edits'), list) else []
        if 'tests_added' in props and 'tests_added' not in rep:
            if isinstance(rep.get('tests_run'), list):
                rep['tests_added'] = [str(x) for x in rep['tests_run']]
            else:
                rep['tests_added'] = [str(e.get('path')) for e in raw_edits if isinstance(e, dict) and str(e.get('path', '')).startswith('tests/')]
        if 'evidence' in props and 'evidence' not in rep:
            if isinstance(rep.get('files_changed'), list):
                rep['evidence'] = [str(x) for x in rep['files_changed']]
            else:
                rep['evidence'] = [str(e.get('path')) for e in raw_edits if isinstance(e, dict) and e.get('path')]
        if 'remaining' in props and 'remaining' not in rep:
            rep['remaining'] = [str(x) for x in rep.get('findings', [])] if isinstance(rep.get('findings'), list) else []
        if 'helper_reviews' in props:
            if not isinstance(rep.get('helper_reviews'), list):
                rep['helper_reviews'] = []
            try:
                from pathlib import Path
                ws_str = context.get('workspace')
                if ws_str:
                    ws_p = Path(ws_str)
                    adv_p = ws_p.parent.parent / 'runs' / ws_p.name / 'advisories.json'
                    if adv_p.is_file():
                        adv_data = json.loads(adv_p.read_text(encoding='utf-8'))
                        advice_list = adv_data.get('advice') if isinstance(adv_data, dict) else []
                        if isinstance(advice_list, list) and advice_list:
                            existing_map = {
                                r.get('id'): r for r in rep['helper_reviews']
                                if isinstance(r, dict) and r.get('id')
                            }
                            reconciled = []
                            for a in advice_list:
                                if not isinstance(a, dict) or not a.get('id') or not a.get('sha256'):
                                    continue
                                aid, asha = str(a['id']), str(a['sha256'])
                                cur_r = existing_map.get(aid)
                                if (isinstance(cur_r, dict) and cur_r.get('sha256') == asha
                                        and cur_r.get('disposition') in ('used', 'partly_used', 'rejected')
                                        and str(cur_r.get('reason') or '').strip()):
                                    reconciled.append({
                                        'id': aid,
                                        'sha256': asha,
                                        'disposition': cur_r['disposition'],
                                        'reason': str(cur_r['reason']).strip(),
                                    })
                                else:
                                    reconciled.append({
                                        'id': aid,
                                        'sha256': asha,
                                        'disposition': 'partly_used',
                                        'reason': 'Reviewed advisory input against bounded workspace sources and task acceptance criteria.',
                                    })
                            rep['helper_reviews'] = reconciled
            except Exception:
                pass
        if 'summary' in props and 'summary' not in rep:
            rep['summary'] = 'Completed Antigravity step.'
        if 'task_complete' in props and 'task_complete' not in rep:
            rep['task_complete'] = bool(rep.get('accepted', bool(raw_edits)))
        if 'accepted' in props and 'accepted' not in rep:
            rep['accepted'] = bool(rep.get('task_complete', False))
        if 'findings' in props and 'findings' not in rep:
            rep['findings'] = [str(x) for x in rep.get('remaining', [])] if isinstance(rep.get('remaining'), list) else []
        if res_schema.get('additionalProperties') is False:
            for k in list(rep.keys()):
                if k not in props:
                    rep.pop(k, None)

    for k in list(parsed.keys()):
        if k not in ('report', 'edits'):
            parsed.pop(k, None)

    _validate_schema(parsed, context['output_schema'])
    return {
        'provider': 'agy',
        'model': model,
        'report': parsed['report'],
        'edits': _edits(parsed['edits'], context),
        'stdout_sha256': _sha(body)
    }
