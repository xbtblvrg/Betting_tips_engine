"""Load local dotenv values as data, never as executable shell commands."""
import os
from pathlib import Path
import re
import shlex


def load_environment(path, environ=None):
    env = os.environ if environ is None else environ
    path = Path(path)
    if not path.is_file():
        return
    values = {}
    for number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].strip()
        key, separator, value = line.partition('=')
        key = key.strip()
        if not separator or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
            raise ValueError(f'Invalid dotenv entry at line {number}')
        parts = shlex.split(value, comments=True, posix=True)
        if len(parts) > 1:
            raise ValueError(f'Quote dotenv values containing spaces at line {number}')
        values[key] = parts[0] if parts else ''
    for key, value in values.items():
        env.setdefault(key, value)
