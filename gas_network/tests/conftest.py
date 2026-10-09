import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG))

RAW = PKG / "data" / "raw"
needs_raw = pytest.mark.skipif(not (RAW / "eia" / "ARR_2024_TABLES_ALL.xlsx").exists(),
                               reason="raw data not downloaded (python -m gasnet.cli fetch)")
