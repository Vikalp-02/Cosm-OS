"""Password hashing, session tokens and the sign-in throttle."""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()
# Checked when the email is unknown, so a miss takes as long as a wrong password
# and the response time does not reveal which emails have accounts.
_DECOY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DECOY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def new_session_token() -> tuple[str, str]:
    """A fresh token for the cookie, and the hash of it to store."""
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Throttle:
    """Pauses a key once it has too many events in a window.

    Held in memory, which is right for one process. Several API processes would
    each keep their own count and need a shared store instead.
    """

    _MAX_KEYS = 10_000

    def __init__(self, attempts: int, window_seconds: float) -> None:
        self._attempts = attempts
        self._window = window_seconds
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def retry_after(self, key: str) -> int | None:
        """Seconds until `key` may try again, or None if it may try now."""
        now = time.monotonic()
        with self._lock:
            failures = self._recent(key, now)
            if len(failures) < self._attempts:
                return None
            return max(1, int(failures[0] + self._window - now) + 1)

    def record(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            if key not in self._failures and len(self._failures) >= self._MAX_KEYS:
                self._failures.pop(next(iter(self._failures)))
            self._recent(key, now).append(now)

    def clear(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)

    def _recent(self, key: str, now: float) -> deque[float]:
        failures = self._failures.setdefault(key, deque())
        while failures and failures[0] <= now - self._window:
            failures.popleft()
        return failures
