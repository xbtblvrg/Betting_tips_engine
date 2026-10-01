#!/bin/zsh
cd "$(dirname "$0")/.." || exit 1
export PYTHONPATH="$PWD/src"
exec .venv/bin/python -m uvicorn betting_engine.server:app --host 127.0.0.1 --port 8765
