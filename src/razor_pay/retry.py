"""Retry with exponential backoff for Razorpay calls.

Seeding a 400-case batch makes 400 sequential order-creation calls. Without
backoff, one rate-limit response part-way through kills the run and leaves a
half-populated batch behind.

Only *transient* failures are retried. A 400 for a malformed request will fail
identically on the fourth attempt as on the first, so retrying it wastes time and
hides the real error. The classifier below is deliberately conservative: anything
it cannot positively identify as transient is treated as permanent and raised.
"""

from __future__ import annotations

import random
import time
from typing import Callable, TypeVar

T = TypeVar("T")

MAX_ATTEMPTS = 7
BASE_DELAY_SECONDS = 0.5
MAX_DELAY_SECONDS = 20.0

# Substrings that identify a retryable condition in an exception's repr.
_TRANSIENT_MARKERS = (
    "429",
    "too many requests",
    "rate limit",
    "500",
    "502",
    "503",
    "504",
    "gateway timeout",
    "service unavailable",
    "bad gateway",
    "timeout",
    "timed out",
    "connection reset",
    "connection aborted",
    "connection error",
    "temporarily unavailable",
)


# Conditions that look transient by status code but are hard product limits.
# Razorpay test mode caps payment links at 30 per account and returns that as a
# 5xx ServerError, so a naive status-code check burns every retry on something
# that can never succeed.
_PERMANENT_MARKERS = (
    "test mode limit",
    "limit of 30 reached",
    "authentication failed",
    "unauthorized",
    "invalid api key",
)


def is_transient(exc: BaseException) -> bool:
    """True only for failures a later identical attempt might survive."""
    text_all = f"{type(exc).__name__}: {exc}".lower()
    if any(marker in text_all for marker in _PERMANENT_MARKERS):
        return False

    # Network-layer failures are transient by nature.
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True

    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if isinstance(status, int):
        return status == 429 or 500 <= status < 600

    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in _TRANSIENT_MARKERS)


def backoff_delay(attempt: int, rng: random.Random | None = None) -> float:
    """Exponential backoff with full jitter.

    Jitter matters here: without it, a batch that trips a rate limit retries every
    call on the same schedule and hits the limit again in lockstep.
    """
    rng = rng or random
    ceiling = min(BASE_DELAY_SECONDS * (2**attempt), MAX_DELAY_SECONDS)
    return rng.uniform(0.0, ceiling)


def with_retry(
    call: Callable[[], T],
    *,
    description: str = "razorpay call",
    max_attempts: int = MAX_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
    on_retry: Callable[[int, float, BaseException], None] | None = None,
) -> T:
    """Run `call`, retrying transient failures with backoff.

    Raises the final exception once attempts are exhausted, or immediately for a
    failure that is not transient.
    """
    last: BaseException | None = None
    for attempt in range(max_attempts):
        try:
            return call()
        except Exception as exc:
            last = exc
            if not is_transient(exc) or attempt == max_attempts - 1:
                raise
            delay = backoff_delay(attempt, rng)
            if on_retry:
                on_retry(attempt + 1, delay, exc)
            sleep(delay)

    assert last is not None  # unreachable; the loop either returns or raises
    raise last


class Throttle:
    """Minimum spacing between calls, to stay under a rate limit rather than
    repeatedly discovering it.

    A 400-case seed pushed ~6 calls/second and drew 124 rate-limit responses.
    Backoff recovered all of them, but the run took 90 seconds of mostly waiting.
    Pacing up front is cheaper than retrying.
    """

    def __init__(self, min_interval_seconds: float = 0.12) -> None:
        self.min_interval = min_interval_seconds
        self._last: float | None = None

    def wait(self, now: Callable[[], float] = time.monotonic,
             sleep: Callable[[float], None] = time.sleep) -> None:
        current = now()
        if self._last is not None:
            elapsed = current - self._last
            if elapsed < self.min_interval:
                sleep(self.min_interval - elapsed)
                current = now()
        self._last = current
