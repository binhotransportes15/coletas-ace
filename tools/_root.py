"""Garante que a raiz do ACE está no sys.path ao rodar scripts desta pasta."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_root = str(ROOT)
if _root not in sys.path:
    sys.path.insert(0, _root)
