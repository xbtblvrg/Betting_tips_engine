import json
import pytest


def test_finish_agy_step_decodes_markdown_fences_and_structured_output():
    from betting_engine.agy_development import finish_agy_step
    from betting_engine.claude_development import VERSION
    report_schema = {
        'type': 'object',
        'properties': {
            'summary': {'type': 'string'},
            'task_complete': {'type': 'boolean'},
            'tests_added': {'type': 'array', 'items': {'type': 'string'}},
            'remaining': {'type': 'array', 'items': {'type': 'string'}},
            'evidence': {'type': 'array', 'items': {'type': 'string'}},
            'helper_reviews': {'type': 'array', 'items': {'type': 'object'}}
        },
        'required': ['summary', 'task_complete', 'tests_added', 'remaining', 'evidence', 'helper_reviews'],
        'additionalProperties': False
    }
    edit_schema = {
        'type': 'object',
        'properties': {
            'path': {'type': 'string'},
            'before_sha256': {'type': ['string', 'null']},
            'content': {'type': 'string'}
        },
        'required': ['path', 'before_sha256', 'content'],
        'additionalProperties': False
    }
    envelope_schema = {
        'type': 'object',
        'properties': {
            'report': report_schema,
            'edits': {'type': 'array', 'items': edit_schema, 'maxItems': 10}
        },
        'required': ['report', 'edits'],
        'additionalProperties': False
    }
    context = {
        'version': VERSION,
        'output_schema': envelope_schema,
        'result_schema': report_schema,
        'readonly': False,
        'protected_paths': [],
        'source_bundle': {'files': [], 'inventory': set()}
    }
    payload = {
        'report': {
            'summary': 'Test summary',
            'task_complete': True,
            'tests_added': ['tests/test_foo.py'],
            'remaining': [],
            'evidence': ['docs/EVIDENCE.md'],
            'helper_reviews': []
        },
        'edits': [
            {
                'path': 'tests/test_foo.py',
                'before_sha256': None,
                'content': 'def test_foo(): pass\n'
            }
        ]
    }
    raw_response = {
        'conversation_id': 'test-conv-123',
        'status': 'SUCCESS',
        'response': '```json\n' + json.dumps(payload) + '\n```'
    }
    raw_str = json.dumps(raw_response)

    res = finish_agy_step(raw_str, context, 'gemini-3.8-flash-high')
    assert res['provider'] == 'agy'
    assert res['model'] == 'gemini-3.8-flash-high'
    assert res['report']['summary'] == 'Test summary'
    assert len(res['edits']) == 1
    assert res['edits'][0]['path'] == 'tests/test_foo.py'
