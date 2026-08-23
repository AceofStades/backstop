"""Configuration and the test-mode safety interlock."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DEFAULT_DB = Path("data/recovery.db")


class LiveKeyRefused(RuntimeError):
    """Raised when credentials are not test-mode credentials."""


@dataclass(frozen=True)
class Settings:
    razorpay_key_id: str | None
    razorpay_key_secret: str | None
    anthropic_api_key: str | None
    db_path: Path

    @property
    def has_razorpay(self) -> bool:
        return bool(self.razorpay_key_id and self.razorpay_key_secret)

    @property
    def has_anthropic(self) -> bool:
        return bool(self.anthropic_api_key)


def load_settings(db_path: str | Path | None = None) -> Settings:
    return Settings(
        razorpay_key_id=os.getenv("RAZORPAY_KEY_ID") or None,
        razorpay_key_secret=os.getenv("RAZORPAY_KEY_SECRET") or None,
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        db_path=Path(db_path) if db_path else DEFAULT_DB,
    )


def assert_test_mode(key_id: str | None) -> None:
    """Hard interlock: this project must never touch live credentials.

    Razorpay key ids are mode-prefixed (`rzp_test_` / `rzp_live_`). Anything that
    is not explicitly a test key is refused before a client is constructed, so a
    misconfigured environment fails closed rather than moving real money.
    """
    if not key_id:
        raise LiveKeyRefused(
            "RAZORPAY_KEY_ID is not set. Copy .env.example to .env and supply "
            "TEST-mode credentials."
        )
    if not key_id.startswith("rzp_test_"):
        raise LiveKeyRefused(
            f"Refusing to start: RAZORPAY_KEY_ID '{key_id[:12]}...' is not a "
            f"test-mode key. This project only ever runs against rzp_test_ keys."
        )
