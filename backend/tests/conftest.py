"""Test configuration: a database per test session, and no demo seeding.

The app seeds the sample runs at startup. Tests build their own database and
assert on exact counts, so seeding is switched off here rather than in every
test file.
"""

from __future__ import annotations

import os

os.environ.setdefault("SEED_DEMOS", "false")

from app.core.config import settings  # noqa: E402

settings.seed_demos = False
