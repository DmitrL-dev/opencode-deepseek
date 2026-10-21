"""Load project-local environment settings before consumers read them."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent


def _skip_dotenv() -> bool:
    """Operators and the test suite can opt out of the project `.env` file.

    `BRIDGE_SKIP_DOTENV=1` keeps the process on explicit environment variables
    only (CI, containers, hermetic regressions).
    """
    return os.getenv("BRIDGE_SKIP_DOTENV", "0").strip().lower() not in (
        "", "0", "false", "no", "off")


def load_environment() -> None:
    if _skip_dotenv():
        return
    load_dotenv(ROOT / ".env")


load_environment()
