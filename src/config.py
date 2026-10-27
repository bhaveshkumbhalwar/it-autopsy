"""Central configuration and project paths.

Everything machine-specific is derived from this file's location or from an
environment variable, so the project runs from any directory.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
MODELS_DIR = PROJECT_ROOT / "models"
REPORTS_DIR = PROJECT_ROOT / "reports"
DOCS_DIR = PROJECT_ROOT / "docs"
KB_DIR = DATA_DIR / "knowledge"

RANDOM_SEED = 42

# ---------------------------------------------------------------------------
# Ticket categories
# ---------------------------------------------------------------------------
# The order matters: it is the label order used by the source dataset and by
# the persisted model. Do not reorder without retraining and re-exporting.
CATEGORIES: tuple[str, ...] = (
    "Active Directory",
    "Computer-Services",
    "EOL",
    "Fileservice",
    "O365",
    "Software",
    "Support general",
)

# Plain-English descriptions shown in the UI. These describe the source
# dataset's own labels; they are not invented subcategories.
CATEGORY_DESCRIPTIONS: dict[str, str] = {
    "Active Directory": "Accounts, groups, permissions and directory access.",
    "Computer-Services": "Hardware and workstation services such as printing or devices.",
    "EOL": "End-of-life hardware or software lifecycle requests.",
    "Fileservice": "File shares, folders and access to stored files.",
    "O365": "Microsoft 365 services, mainly email, Outlook and mailboxes.",
    "Software": "Software installation, licensing and application faults.",
    "Support general": "General service requests and uncategorised support work.",
}

# ---------------------------------------------------------------------------
# Operating systems
# ---------------------------------------------------------------------------
OS_MACOS = "macos"
OS_WINDOWS = "windows"
OS_UNKNOWN = "unknown"
SUPPORTED_OS = (OS_MACOS, OS_WINDOWS, OS_UNKNOWN)

OS_LABELS = {
    OS_MACOS: "macOS",
    OS_WINDOWS: "Windows",
    OS_UNKNOWN: "Not specified",
}

# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------
# Cosine similarity below this value is treated as "insufficient evidence".
# Chosen as a conservative start and documented; it is not tuned on the test
# split (there is no test split for retrieval).
RETRIEVAL_MIN_SCORE = 0.18
RETRIEVAL_TOP_K = 3

# ---------------------------------------------------------------------------
# LLM-assisted mode
# ---------------------------------------------------------------------------
# ``LLM_MODE`` controls whether an LLM is used for issue analysis.
# "deterministic" (default) — no API key required; all decisions are rule-based.
# "llm"                    — uses an OpenAI-compatible endpoint; requires LLM_API_KEY.
# Provider config (env vars): LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_TIMEOUT.
# These must never be committed to source control.
LLM_MODE_DETERMINISTIC = "deterministic"
LLM_MODE_LLM = "llm"

# ---------------------------------------------------------------------------
# Agent loop bounds
# ---------------------------------------------------------------------------
MAX_STEPS = 6
MAX_TOOL_RETRIES = 1
TOOL_TIMEOUT_SECONDS = 8

# ---------------------------------------------------------------------------
# Verification outcomes
# ---------------------------------------------------------------------------
OUTCOME_VERIFIED = "verified_resolved"
OUTCOME_NOT_RESOLVED = "not_resolved"
OUTCOME_NEEDS_CONFIRMATION = "needs_user_confirmation"
OUTCOME_ESCALATED = "escalated"
OUTCOME_UNABLE = "unable_to_verify"

OUTCOMES = (
    OUTCOME_VERIFIED,
    OUTCOME_NOT_RESOLVED,
    OUTCOME_NEEDS_CONFIRMATION,
    OUTCOME_ESCALATED,
    OUTCOME_UNABLE,
)

# ---------------------------------------------------------------------------
# Paths that can be overridden
# ---------------------------------------------------------------------------


def get_db_path() -> Path:
    """SQLite location, overridable with ``HDA_DB_PATH`` for tests."""
    override = os.environ.get("HDA_DB_PATH")
    if override:
        return Path(override).expanduser().resolve()
    return DATA_DIR / "cases.db"


def ensure_directories() -> None:
    for path in (DATA_DIR, RAW_DIR, PROCESSED_DIR, MODELS_DIR, REPORTS_DIR, KB_DIR):
        path.mkdir(parents=True, exist_ok=True)
