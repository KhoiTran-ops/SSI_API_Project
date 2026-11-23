"""Điểm chạy chung: python main.py -h"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from vnmkt.cli import main  # noqa: E402

raise SystemExit(main())
