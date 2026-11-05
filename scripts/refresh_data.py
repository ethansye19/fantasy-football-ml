"""Thin wrapper: ``python scripts/refresh_data.py`` is ``python -m ffml.data.refresh``.

The refresh steps live in :mod:`ffml.data.refresh` (one implementation), so
the live-test runner can run them from the pinned worktree's own code with
``<pinned python> -m ffml.data.refresh``. This file stays for hands-on use
from the main checkout; the arguments are the same::

    python scripts/refresh_data.py --step ingest   [--seasons 2016-2026] [--pin-path P]
    python scripts/refresh_data.py --step sleeper
    python scripts/refresh_data.py --step process  [--seasons 2016-2026]
    python scripts/refresh_data.py --step validate [--seasons 2016-2026] [--report R]
    python scripts/refresh_data.py --step features [--seasons 2016-2026]

The last stdout line is ``REFRESH_RESULT {json}``; exit 0 on success, 1 on failure.
"""

from __future__ import annotations

import sys

from ffml.data.refresh import main

if __name__ == "__main__":
    sys.exit(main())
