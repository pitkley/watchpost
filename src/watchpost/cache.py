# Copyright 2025 TAKKT Industrial & Packaging GmbH
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# SPDX-License-Identifier: Apache-2.0

"""
Caching utilities and pluggable storage backends.

This module provides a small, consistent caching API (`Cache`) and a set of
storage backends (`Storage`) for in-memory, disk, and Redis persistence. It also
includes a memoization decorator for function-level caching.
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import os
import pickle
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar, cast

from ._file_lock import exclusive_lock

if TYPE_CHECKING:
    from redis import Redis

T = TypeVar("T")


def get_caller_package() -> str:
    """
    Return the caller's package name.

    This helper inspects the call stack to determine the package of the code
    invoking the cache. It falls back to the module name when no package is set.

    Returns:
        The package name of the caller.
    """

    current_frame = inspect.currentframe()
    assert current_frame
    assert current_frame.f_back
    assert current_frame.f_back.f_back
    caller_frame = current_frame.f_back.f_back
    caller_module = inspect.getmodule(caller_frame)
    assert caller_module is not None
    package_name = (
        caller_module.__package__
        if caller_module.__package__
        else caller_module.__name__
    )
    return package_name


@dataclass
class CacheKey:
    """
    A compound key that uniquely identifies a cache entry within a package.

    The combination of `key` and `package` forms the globally unique key used by
    storage backends.
    """

    key: Hashable
    """
    The user-provided key. It is unique within the given `package`.
    """
    package: str
    """
    The package namespace used to separate keys across applications or checks.
    """

    def __hash__(self) -> int:
        return hash((self.key, self.package))


@dataclass
class CacheEntry[T]:
    """
    Represents a cached value together with metadata.

    This object stores the value and all information needed by backends to
    determine freshness and validity.
    """

    # Increment the version if you change the serialization format, i.e. if the types,
    # meaning of values, or other aspects of this type are modified in a way that is not
    # backwards-compatible.
    VERSION = 1

    cache_key: CacheKey
    """
    The globally unique cache key for this entry.
    """
    value: T
    """
    The cached value.
    """

    added_at: datetime | None
    """
    The timestamp when this entry was stored. `None` means the value is a
    default and not persisted yet.
    """
    ttl: timedelta | None
    """
    The time-to-live for this entry. `None` means the value does not expire.
    """

    def is_expired(self) -> bool:
        """
        Determine whether the cache entry has expired.

        Returns:
            True if the entry is expired; otherwise False.
        """
        if not self.added_at:
            return False
        return self.ttl is not None and datetime.now(tz=UTC) - self.added_at > self.ttl


class Storage(ABC):
    """
    Base interface for cache storage backends.

    Implementations persist and retrieve `CacheEntry` objects.
    """

    @abstractmethod
    def get(
        self,
        cache_key: CacheKey,
        return_expired: bool = False,
    ) -> CacheEntry[T] | None:
        """
        Retrieve a cache entry by key.

        Parameters:
            cache_key:
                The cache key to look up.
            return_expired:
                Whether to return an expired entry once before it is removed.

        Returns:
            The cache entry if found, otherwise `None`.
        """

    @abstractmethod
    def store(
        self,
        entry: CacheEntry,
    ) -> None:
        """
        Persist a cache entry.

        Parameters:
            entry:
                The cache entry to store.
        """


class ChainedStorage(Storage):
    """
    A storage implementation that chains multiple storage backends together.

    On retrieval, it tries each storage in order until it finds a hit. On
    storage, it stores the value in all storage backends.
    """

    def __init__(self, storages: list[Storage]):
        """
        Initialize a chained storage with multiple storage backends.

        Parameters:
            storages:
                Storage backends to chain. The order defines lookup priority;
                the first backend with a hit will be used.
        """
        if not storages:
            raise ValueError("At least one storage must be provided")
        self.storages = storages

    def get(
        self,
        cache_key: CacheKey,
        return_expired: bool = False,
    ) -> CacheEntry[T] | None:
        for storage in self.storages:
            cache_entry: CacheEntry[T] | None = storage.get(
                cache_key,
                return_expired=return_expired,
            )
            if cache_entry:
                # Found in this storage, propagate to earlier storages
                self._propagate_to_earlier_storages(cache_entry, storage)
                return cache_entry
        return None

    def _propagate_to_earlier_storages(
        self,
        cache_entry: CacheEntry,
        found_in_storage: Storage,
    ) -> None:
        """
        Propagate a cache entry to all storages that come before the one where
        it was found. This ensures that future lookups will find the entry in
        the prioritized storages.
        """
        for storage in self.storages:
            if storage is found_in_storage:
                # Stop once we reach the storage where the entry was found
                break
            storage.store(cache_entry)

    def store(
        self,
        entry: CacheEntry,
    ) -> None:
        for storage in self.storages:
            storage.store(entry)


class InMemoryStorage(Storage):
    """
    A simple in-memory storage backend.

    Useful for tests or ephemeral caching within a single process. Entries are
    lost when the process exits.
    """

    def __init__(self) -> None:
        self.cache: dict[CacheKey, CacheEntry] = {}

    def get(
        self,
        cache_key: CacheKey,
        return_expired: bool = False,
    ) -> CacheEntry[T] | None:
        cache_entry: CacheEntry[T] | None = self.cache.get(cache_key)
        if not cache_entry:
            return None

        if cache_entry.is_expired():
            del self.cache[cache_key]
            if return_expired:
                return cache_entry
            return None

        return cache_entry

    def store(
        self,
        entry: CacheEntry,
    ) -> None:
        self.cache[entry.cache_key] = entry


class DiskStorage(Storage):
    """
    A disk-backed storage backend using Pickle-serialized `CacheEntry` objects.

    Entries are stored under a versioned directory and sharded by a hash prefix.
    Writes replace complete files atomically. Missing, corrupt, or incompatible
    entries are cache misses. As with all pickle storage, the directory must be
    trusted. Short publication and expiry transactions use advisory filesystem
    locks across threads and processes. Empty shard directories and their lock
    files are retained to avoid racing with writers.
    """

    def __init__(self, directory: str):
        """
        Initialize the disk storage.

        Parameters:
            directory:
                Directory root where cache files are written.
        """
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _get_file_path(self, cache_key: CacheKey) -> Path:
        key_hash = hashlib.sha256(
            str((cache_key.package, cache_key.key)).encode()
        ).hexdigest()
        prefix = key_hash[:2]
        return self.directory / f"v{CacheEntry.VERSION}" / prefix / key_hash

    def _remove_cache_entry_on_disk(self, cache_entry: CacheEntry) -> None:
        file_path = self._get_file_path(cache_entry.cache_key)
        with exclusive_lock(file_path.parent / ".publication.lock"):
            file_path.unlink(missing_ok=True)

    @staticmethod
    def _read_entry(file_path: Path, cache_key: CacheKey) -> CacheEntry[T] | None:
        with file_path.open("rb") as file:
            entry: CacheEntry[T] = pickle.load(file)
        if not isinstance(entry, CacheEntry) or entry.cache_key != cache_key:
            return None
        return entry

    def get(
        self,
        cache_key: CacheKey,
        return_expired: bool = False,
    ) -> CacheEntry[T] | None:
        file_path = self._get_file_path(cache_key)
        try:
            cache_entry: CacheEntry[T] | None = self._read_entry(file_path, cache_key)
            if cache_entry is None:
                return None
            if not cache_entry.is_expired():
                return cache_entry
            # Re-read under the same lock writers use for replacement. A fresh
            # value may have been published since the optimistic read above.
            with exclusive_lock(file_path.parent / ".publication.lock"):
                cache_entry = self._read_entry(file_path, cache_key)
                if cache_entry is None:
                    return None
                if not cache_entry.is_expired():
                    return cache_entry
                file_path.unlink(missing_ok=True)
                return cache_entry if return_expired else None
        except (
            OSError,
            EOFError,
            pickle.UnpicklingError,
            AttributeError,
            ImportError,
            ValueError,
            TypeError,
        ):
            # Do not unlink here: a writer may already have replaced the bad file.
            return None

    def store(
        self,
        entry: CacheEntry,
    ) -> None:
        file_path = self._get_file_path(entry.cache_key)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=file_path.parent, prefix=".watchpost-", delete=False
            ) as file:
                temporary_path = Path(file.name)
                pickle.dump(entry, file)
                file.flush()
                os.fsync(file.fileno())
            with exclusive_lock(file_path.parent / ".publication.lock"):
                os.replace(temporary_path, file_path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


class RedisStorage(Storage):
    """
    A Redis-backed storage backend.

    When `use_redis_ttl` is True, Redis key expiration is used so expired
    entries are never returned by `get`.
    """

    def __init__(
        self,
        redis_client: Redis,
        *,
        use_redis_ttl: bool = True,
        redis_key_infix: str | None = None,
    ):
        """
        Initialize the Redis storage.

        Parameters:
            redis_client:
                The Redis client instance.
            use_redis_ttl:
                Whether to use Redis TTL for automatic expiry. When `True`,
                expired entries are not returned.
            redis_key_infix:
                Optional infix to namespace keys to avoid collisions between
                multiple caches or watchposts.
        """
        self.redis = redis_client
        self._use_redis_ttl = use_redis_ttl
        self._redis_key_infix = redis_key_infix

    def _get_redis_key(self, cache_key: CacheKey) -> str:
        """
        Generate the Redis key for a given cache key.

        Parameters:
            cache_key:
                The cache key to encode.

        Returns:
            The namespaced Redis key.
        """
        key_hash = hashlib.sha256(
            str((cache_key.package, cache_key.key)).encode()
        ).hexdigest()

        infix = ""
        if self._redis_key_infix:
            infix = f"{self._redis_key_infix}:"

        return f"watchpost:cache:{infix}v{CacheEntry.VERSION}:{key_hash}"

    def get(
        self,
        cache_key: CacheKey,
        return_expired: bool = False,
    ) -> CacheEntry[T] | None:
        """
        Retrieve a cache entry from Redis.

        Parameters:
            cache_key:
                The key to retrieve the value for.
            return_expired:
                Whether to return an expired entry once before it is removed.
                (If Redis's TTL is used, an expired entry is never returned and
                this has no effect.)

        Returns:
            The cache entry if found, otherwise `None`.
        """
        redis_key = self._get_redis_key(cache_key)
        data: Any = self.redis.get(redis_key)

        if data is None:
            return None

        cache_entry: CacheEntry[T] = pickle.loads(data)

        if cache_entry.is_expired():
            self.redis.delete(redis_key)
            if return_expired:
                return cache_entry
            return None

        return cache_entry

    def store(
        self,
        entry: CacheEntry,
    ) -> None:
        """
        Store a cache entry in Redis.

        Parameters:
            entry:
                The cache entry to store.
        """
        redis_key = self._get_redis_key(entry.cache_key)
        data = pickle.dumps(entry)

        if self._use_redis_ttl and entry.ttl is not None and entry.added_at is not None:
            expiry_seconds = int(entry.ttl.total_seconds())
            if expiry_seconds > 0:
                self.redis.setex(redis_key, expiry_seconds, data)
            else:
                # If the entry is already expired, ensure we don't hold a
                # potentially old version in Redis anymore.
                self.redis.delete(redis_key)
        else:
            self.redis.set(redis_key, data)


class Cache:
    """
    High-level caching API using a pluggable storage backend.

    Use `get` and `store` to interact with the cache directly, or the `memoize`
    decorator to cache function results.
    """

    def __init__(self, storage: Storage):
        self.storage = storage

    def get(
        self,
        key: Hashable,
        default: T | None = None,
        *,
        package: str | None = None,
        return_expired: bool = False,
    ) -> CacheEntry[T] | None:
        """
        Retrieve a value from the cache.

        Parameters:
            key:
                The key to look up. Must be hashable and unique within the given
                package.
            default:
                An optional default value to return when the key is not found.
            package:
                The package namespace. If not provided, the package of the
                caller is used.
            return_expired:
                Whether to return an expired entry once before it is removed.
                (This behavior does depend on the storage backend supporting
                this.)

        Returns:
            The cache entry if found. If not found, returns `None` when no
            default is given, otherwise a cache entry wrapping the default
            value.
        """

        default_cache_entry = None
        if default is not None:
            default_cache_entry = CacheEntry(
                cache_key=CacheKey(
                    key=key,
                    package=package if package else get_caller_package(),
                ),
                value=cast(T, default),
                added_at=None,
                ttl=None,
            )

        cache_key = CacheKey(
            key=key,
            package=package if package else get_caller_package(),
        )

        cache_entry: CacheEntry[T] | None = self.storage.get(
            cache_key,
            return_expired=return_expired,
        )
        if cache_entry:
            return cache_entry
        return default_cache_entry

    def store(
        self,
        key: Hashable,
        value: T,
        *,
        package: str | None = None,
        ttl: timedelta | None = None,
    ) -> CacheEntry[T]:
        """
        Store a value in the cache.

        Parameters:
            key:
                The key under which to store the value. Must be hashable and
                unique within the given package.
            value:
                The value to store.
            package:
                The package namespace. If not provided, the package of the
                caller is used.
            ttl:
                Optional time-to-live for the entry. When omitted, the entry
                does not expire.

        Returns:
            The cache entry that was stored.
        """

        cache_entry = CacheEntry(
            cache_key=CacheKey(
                key=key,
                package=package if package else get_caller_package(),
            ),
            value=value,
            added_at=datetime.now(tz=UTC),
            ttl=ttl,
        )

        self.storage.store(cache_entry)

        return cache_entry

    def memoize[R, **P](
        self,
        *,
        key: Hashable | None = None,
        key_generator: Callable[P, Hashable] | None = None,
        package: str | None = None,
        return_expired: bool = False,
        ttl: timedelta | None = None,
    ) -> Callable[[Callable[P, R]], Callable[P, R]]:  # pylint: disable=too-many-arguments
        """
        Memoize a function by caching its return value.

        Use either a fixed `key` or a `key_generator` to compute the cache key
        from call arguments. If `key` contains format placeholders like "{arg}",
        they will be formatted from the function's bound arguments.

        Parameters:
            key:
                A static key or a format string used to build the key. Mutually
                exclusive with `key_generator`.
            key_generator:
                A callable that receives the function's arguments and returns a
                hashable key. Mutually exclusive with `key`.
            package:
                The package namespace. If not provided, the package of the
                caller is used.
            return_expired:
                Whether to return an expired entry once before it is recomputed
                and overwritten.
            ttl:
                Optional time-to-live for stored values.

        Returns:
            A decorator that wraps the function and provides cached results.

        Notes:
            - Only one of `key` or `key_generator` must be provided.
            - If `return_expired` is True, an expired value may be returned
              once; it is then replaced with a fresh value.
        """
        if key and key_generator:
            raise ValueError("Only one of key or key_generator can be provided.")
        if not key and not key_generator:
            raise ValueError("Either key or key_generator must be provided.")

        package = package or get_caller_package()

        def decorator(func: Callable[P, R]) -> Callable[P, R]:
            @functools.wraps(func)
            def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
                cache_key = key
                if key_generator:
                    cache_key = key_generator(*args, **kwargs)
                elif isinstance(cache_key, str) and "{" in cache_key:
                    # The key provided seems to be formattable.
                    bound_arguments = inspect.signature(func).bind(*args, **kwargs)
                    cache_key = cache_key.format(**bound_arguments.arguments)

                cache_entry: CacheEntry[R] | None = self.get(
                    key=cache_key,
                    package=package,
                    return_expired=return_expired,
                )
                if cache_entry:
                    return cache_entry.value

                value = func(*args, **kwargs)
                self.store(
                    key=cache_key,
                    value=value,
                    package=package,
                    ttl=ttl,
                )
                return value

            return wrapper

        return decorator
