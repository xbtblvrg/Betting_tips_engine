"""Lossless publication storage; immutable decisions remain ordinary JSON."""
import gzip
import json

PREFIX = b'BQZ1\0'


def encode_publication(payload):
    raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
    return PREFIX + gzip.compress(raw, compresslevel=3, mtime=0)


def decode_publication(value):
    if isinstance(value, bytes) and value.startswith(PREFIX):
        value = gzip.decompress(value[len(PREFIX):])
    result = json.loads(value)
    if not isinstance(result, dict):
        raise ValueError('Publication must be an object')
    return result
