"""Single-flight TTL cache for builders that have no arguments.

If the cache is empty, the other callers wait for the result or the error of that build.
If the cache is stale, the other callers get the stale data.
"""

import functools
import threading
import time
from typing import Callable, Generic, TypeVar

T = TypeVar("T")
D = TypeVar("D", bound=dict)

_PURGERS: list[Callable[[], None]] = []


class TTLCache(Generic[T]):
    def __init__(self, build: Callable[[], T], ttl: float) -> None:
        self._build = build
        self._ttl = ttl
        self._lock = threading.Lock()
        self._build_lock = threading.Lock()
        self._entry: tuple[T, float] | None = None  # (data, built_at)
        self._gen = 0  # A purge increases it. A build that started before the purge stores no result.
        self._error: Exception | None = None  # the error of the last build
        _PURGERS.append(self.purge)

    def _fresh(self) -> bool:
        return self._entry is not None and time.time() - self._entry[1] < self._ttl

    def state(self) -> str:
        """Return the state: empty, building, fresh or stale."""
        with self._lock:
            if self._fresh():
                return "fresh"
            if self._build_lock.locked():
                return "building"
            return "stale" if self._entry else "empty"

    def get(self) -> T:
        with self._lock:
            entry, seen = self._entry, self._error
            if entry and self._fresh():
                return entry[0]
        if entry is None:
            self._build_lock.acquire()
        elif not self._build_lock.acquire(blocking=False):
            return entry[0]  # A different thread builds. Return the stale data.
        try:
            with self._lock:
                if (
                    self._entry and self._fresh()
                ):  # a build completed while this thread waited
                    return self._entry[0]
                # Give the error of that build to each waiter. If not, waiter k waits for k timeouts.
                if self._error is not None and self._error is not seen:
                    raise self._error
                gen = self._gen
            try:
                data = self._build()
            except Exception as exc:
                with self._lock:
                    self._error = exc
                raise
            with self._lock:
                self._error = None
                if gen == self._gen:
                    self._entry = (data, time.time())
            return data
        finally:
            self._build_lock.release()

    def purge(self) -> None:
        with self._lock:
            self._entry, self._gen = None, self._gen + 1


def ttl_cached(ttl: float) -> Callable[[Callable[[], T]], Callable[[], T]]:
    """Cache a function that has no arguments. purge_all() clears the cache."""

    def deco(build: Callable[[], T]) -> Callable[[], T]:
        cache = TTLCache(build, ttl)

        @functools.wraps(build)
        def wrapper() -> T:
            return cache.get()

        return wrapper

    return deco


def purgeable(lock: threading.Lock, cache: D) -> D:
    """Register a dict cache. purge_all() holds the lock while it clears the cache."""

    def clear() -> None:
        with lock:
            cache.clear()

    _PURGERS.append(clear)
    return cache


def purge_all() -> None:
    for purge in _PURGERS:
        purge()
