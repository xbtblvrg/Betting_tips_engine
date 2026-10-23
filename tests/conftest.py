import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
src = str(ROOT / "src")
if src not in sys.path:
    sys.path.insert(0, src)
cur_pypath = os.environ.get("PYTHONPATH", "")
if src not in cur_pypath.split(":"):
    os.environ["PYTHONPATH"] = f"{src}:{cur_pypath}" if cur_pypath else src
