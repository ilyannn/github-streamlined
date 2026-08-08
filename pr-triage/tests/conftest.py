import sys
from pathlib import Path

# The tool lives in a hyphenated directory, so it is not importable as a
# package; put it on sys.path instead.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
