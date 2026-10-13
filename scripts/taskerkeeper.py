#!/usr/bin/env python3
"""Legacy shim: python scripts/taskerkeeper.py ... (use scripts/tidetable.py)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tidetable.cli import main

if __name__ == "__main__":
    main()
