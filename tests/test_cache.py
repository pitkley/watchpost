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

import re
from datetime import UTC, datetime, timedelta
from tempfile import TemporaryDirectory
from typing import cast

import pytest
import redis
from testcontainers.community.redis import RedisContainer

from watchpost.cache import (
    Cache,
    CacheEntry,
    CacheKey,
    ChainedStorage,
    DiskStorage,
    InMemoryStorage,
    RedisStorage,
)


class TestDiskStorage:
    def test_directory_handling(self):
        with TemporaryDirectory() as tmpdir:
            disk_storage = DiskStorage(tmpdir)
            assert disk_storage.directory.exists()
            assert disk_storage.directory.is_dir()

    def test_unknown_key(self):
        with TemporaryDirectory() as tmpdir:
            disk_storage = DiskStorage(tmpdir)
            cache = Cache(disk_storage)
            assert cache.get("key") is None

    def test_unknown_key_with_default(self):
        with TemporaryDirectory() as tmpdir:
            disk_storage = DiskStorage(tmpdir)
            cache = Cache(disk_storage)
            default = "default"
            cache_entry = cache.get("key", default=default)
            assert cache_entry is not None
            assert cache_entry.cache_key.key == "key"
            assert cache_entry.cache_key.package == __package__
            assert cache_entry.value == default
            assert cache_entry.added_at is None
            assert cache_entry.ttl is None

    def test_cache_key_uniqueness(self):
        with TemporaryDirectory() as tmpdir:
            disk_storage = DiskStorage(tmpdir)
            cache = Cache(disk_storage)

            cache.store("key", "value")
            cache.store("key", "updated-value")
            cache_entry = cache.get("key")

            assert cache_entry is not None
            assert cache_entry.cache_key.key == "key"
            assert cache_entry.cache_key.package == __package__
            assert cache_entry.value == "updated-value"
            assert cache_entry.added_at is not None
            assert cache_entry.ttl is None
            assert cache_entry.is_expired() is False

    def test_basic_store_and_get(self):
        with TemporaryDirectory() as tmpdir:
            disk_storage = DiskStorage(tmpdir)
            cache = Cache(disk_storage)

            stored_cache_entry = cache.store("key", "value")
            cache_entry = cache.get("key")

            assert cache_entry is not None
            assert stored_cache_entry is not None
            assert cache_entry == stored_cache_entry
            assert cache_entry.cache_key.key == "key"
            assert cache_entry.cache_key.package == __package__
            assert cache_entry.value == "value"
            assert cache_entry.added_at is not None
            assert cache_entry.ttl is None
            assert cache_entry.is_expired() is False

    def test_store_with_package(self):
        with TemporaryDirectory() as tmpdir:
            disk_storage = DiskStorage(tmpdir)
            cache = Cache(disk_storage)

            cache.store("key", "value", package="test")
            cache_entry = cache.get("key", package="test")

            assert cache_entry is not None
            assert cache_entry.cache_key.key == "key"
            assert cache_entry.cache_key.package == "test"
            assert cache_entry.value == "value"
            assert cache_entry.added_at is not None
            assert cache_entry.ttl is None
            assert cache_entry.is_expired() is False

    def test_ttl_works(self):
        with TemporaryDirectory() as tmpdir:
            disk_storage = DiskStorage(tmpdir)
            cache = Cache(disk_storage)

            cache.store("key", "value", ttl=timedelta(seconds=-1))
            cache_entry_path = disk_storage._get_file_path(
                CacheKey(key="key", package=cast(str, __package__))
            )

            assert cache_entry_path.exists()
            assert cache.get("key") is None
            assert not cache_entry_path.exists()

    def test_return_expired(self):
        with TemporaryDirectory() as tmpdir:
            disk_storage = DiskStorage(tmpdir)
            cache = Cache(disk_storage)

            cache.store("key", "value", ttl=timedelta(seconds=-1))
            cache_entry_path = disk_storage._get_file_path(
                CacheKey(key="key", package=cast(str, __package__))
            )

            assert cache_entry_path.exists()
            cache_entry = cache.get("key", return_expired=True)
            assert cache_entry is not None
            assert cache_entry.cache_key.key == "key"
            assert cache_entry.cache_key.package == __package__
            assert cache_entry.value == "value"
            assert cache_entry.added_at is not None
            assert cache_entry.ttl == timedelta(seconds=-1)
            assert cache_entry.is_expired() is True
            assert not cache_entry_path.exists()

    def test_memoize_with_key(self):
        with TemporaryDirectory() as tmpdir:
            disk_storage = DiskStorage(tmpdir)
            cache = Cache(disk_storage)

            @cache.memoize(key="memoize-key")
            def memoized_function(a):
                return a

            assert memoized_function(1) == 1
            cache_key = CacheKey(
                key="memoize-key",
                package=cast(str, __package__),
            )
            cache_entry_path = disk_storage._get_file_path(cache_key)
            assert cache_entry_path.exists()
            # memoize currently does not cache by the arguments provided, so providing
            # something else will still return the prior value while the cache is active
            assert memoized_function(2) == 1

            cache_entry = disk_storage.get(cache_key)
            assert cache_entry is not None
            disk_storage._remove_cache_entry_on_disk(cache_entry)
            assert memoized_function(2) == 2

    def test_memoize_with_key_generator(self):
        with TemporaryDirectory() as tmpdir:
            disk_storage = DiskStorage(tmpdir)
            cache = Cache(disk_storage)

            def key_generator(*args, **_kwargs) -> str:
                return str(args[0])

            @cache.memoize(key_generator=key_generator)
            def memoized_function(_a):
                return datetime.now(tz=UTC)

            call_1 = memoized_function(1)
            assert isinstance(call_1, datetime)
            cache_key = CacheKey(
                key="1",
                package=cast(str, __package__),
            )
            cache_entry_path = disk_storage._get_file_path(cache_key)
            assert cache_entry_path.exists()

            # Repeat calls return the same result
            assert memoized_function(1) == call_1
            # Calls with a different argument return a different result
            call_2 = memoized_function(2)
            assert isinstance(call_2, datetime)
            assert call_2 > call_1

    def test_memoize_without_key_or_key_generator(self):
        with TemporaryDirectory() as tmpdir:
            disk_storage = DiskStorage(tmpdir)
            cache = Cache(disk_storage)

            with pytest.raises(
                ValueError,
                match=re.escape("Either key or key_generator must be provided."),
            ):

                @cache.memoize()
                def memoized_function(a):
                    return a

    def test_memoize_with_both_key_and_key_generator(self):
        with TemporaryDirectory() as tmpdir:
            disk_storage = DiskStorage(tmpdir)
            cache = Cache(disk_storage)

            def key_generator(*_args, **_kwargs) -> str:
                return "something"

            with pytest.raises(
                ValueError,
                match=re.escape("Only one of key or key_generator can be provided."),
            ):

                @cache.memoize(key="something", key_generator=key_generator)
                def memoized_function(a):
                    return a

    def test_memoize_with_formattable_key(self):
        """Test that memoize formats a key string using function arguments."""
        with TemporaryDirectory() as tmpdir:
            disk_storage = DiskStorage(tmpdir)
            cache = Cache(disk_storage)

            @cache.memoize(key="user-{a}-{b}")
            def memoized_function(a, b):
                _ = a
                _ = b
                return datetime.now(tz=UTC)

            # First call should store using formatted key
            call_1 = memoized_function(1, 2)
            assert isinstance(call_1, datetime)
            cache_key = CacheKey(key="user-1-2", package=cast(str, __package__))
            cache_entry_path = disk_storage._get_file_path(cache_key)
            assert cache_entry_path.exists()

            # Repeated call with same args returns cached value
            assert memoized_function(1, 2) == call_1

            # Calling with kwargs in different order should resolve to same key/value
            assert memoized_function(b=2, a=1) == call_1

            # Different arguments produce a different cache entry and value
            call_2 = memoized_function(2, 2)
            assert isinstance(call_2, datetime)
            assert call_2 > call_1


class TestInMemoryStorage:
    def test_unknown_key(self):
        in_memory_storage = InMemoryStorage()
        cache = Cache(in_memory_storage)
        assert cache.get("key") is None

    def test_unknown_key_with_default(self):
        in_memory_storage = InMemoryStorage()
        cache = Cache(in_memory_storage)
        default = "default"
        cache_entry = cache.get("key", default=default)
        assert cache_entry is not None
        assert cache_entry.cache_key.key == "key"
        assert cache_entry.cache_key.package == __package__
        assert cache_entry.value == default
        assert cache_entry.added_at is None
        assert cache_entry.ttl is None

    def test_cache_key_uniqueness(self):
        in_memory_storage = InMemoryStorage()
        cache = Cache(in_memory_storage)

        cache.store("key", "value")
        cache.store("key", "updated-value")
        cache_entry = cache.get("key")

        assert cache_entry is not None
        assert cache_entry.cache_key.key == "key"
        assert cache_entry.cache_key.package == __package__
        assert cache_entry.value == "updated-value"
        assert cache_entry.added_at is not None
        assert cache_entry.ttl is None
        assert cache_entry.is_expired() is False

    def test_basic_store_and_get(self):
        in_memory_storage = InMemoryStorage()
        cache = Cache(in_memory_storage)

        cache.store("key", "value")
        cache_entry = cache.get("key")

        assert cache_entry is not None
        assert cache_entry.cache_key.key == "key"
        assert cache_entry.cache_key.package == __package__
        assert cache_entry.value == "value"
        assert cache_entry.added_at is not None
        assert cache_entry.ttl is None
        assert cache_entry.is_expired() is False

    def test_store_with_package(self):
        in_memory_storage = InMemoryStorage()
        cache = Cache(in_memory_storage)

        cache.store("key", "value", package="test")
        cache_entry = cache.get("key", package="test")

        assert cache_entry is not None
        assert cache_entry.cache_key.key == "key"
        assert cache_entry.cache_key.package == "test"
        assert cache_entry.value == "value"
        assert cache_entry.added_at is not None
        assert cache_entry.ttl is None
        assert cache_entry.is_expired() is False

    def test_ttl_works(self):
        in_memory_storage = InMemoryStorage()
        cache = Cache(in_memory_storage)

        cache.store("key", "value", ttl=timedelta(seconds=-1))
        cache_key = CacheKey(key="key", package=cast(str, __package__))
        assert cache_key in in_memory_storage.cache
        assert cache.get("key") is None
        assert cache_key not in in_memory_storage.cache

    def test_return_expired(self):
        in_memory_storage = InMemoryStorage()
        cache = Cache(in_memory_storage)

        cache.store("key", "value", ttl=timedelta(seconds=-1))
        cache_key = CacheKey(key="key", package=cast(str, __package__))

        assert cache_key in in_memory_storage.cache
        cache_entry = cache.get("key", return_expired=True)
        assert cache_entry is not None
        assert cache_entry.cache_key.key == "key"
        assert cache_entry.cache_key.package == __package__
        assert cache_entry.value == "value"
        assert cache_entry.added_at is not None
        assert cache_entry.ttl == timedelta(seconds=-1)
        assert cache_entry.is_expired() is True
        assert cache_key not in in_memory_storage.cache

    def test_memoize(self):
        """Test that memoize works with a key."""
        in_memory_storage = InMemoryStorage()
        cache = Cache(in_memory_storage)

        @cache.memoize(key="memoize-key")
        def memoized_function(a):
            return a

        assert memoized_function(1) == 1
        cache_key = CacheKey(
            key="memoize-key",
            package=cast(str, __package__),
        )
        assert cache_key in in_memory_storage.cache
        # memoize currently does not cache by the arguments provided, so providing
        # something else will still return the prior value while the cache is active
        assert memoized_function(2) == 1

        del in_memory_storage.cache[cache_key]
        assert memoized_function(2) == 2

    def test_memoize_with_key_generator(self):
        """Test that memoize works with a key generator."""
        in_memory_storage = InMemoryStorage()
        cache = Cache(in_memory_storage)

        def key_generator(*args, **_kwargs) -> str:
            return str(args[0])

        @cache.memoize(key_generator=key_generator)
        def memoized_function(_a):
            return datetime.now(tz=UTC)

        call_1 = memoized_function(1)
        assert isinstance(call_1, datetime)
        cache_key = CacheKey(
            key="1",
            package=cast(str, __package__),
        )
        assert cache_key in in_memory_storage.cache

        # Repeat calls return the same result
        assert memoized_function(1) == call_1

        # Calls with a different argument return a different result
        call_2 = memoized_function(2)
        assert isinstance(call_2, datetime)
        assert call_2 > call_1

    def test_memoize_without_key_or_key_generator(self):
        """Test that memoize raises ValueError when neither key nor key_generator is provided."""
        in_memory_storage = InMemoryStorage()
        cache = Cache(in_memory_storage)

        with pytest.raises(
            ValueError,
            match=re.escape("Either key or key_generator must be provided."),
        ):

            @cache.memoize()
            def memoized_function(a):
                return a

    def test_memoize_with_both_key_and_key_generator(self):
        """Test that memoize raises ValueError when both key and key_generator are provided."""
        in_memory_storage = InMemoryStorage()
        cache = Cache(in_memory_storage)

        def key_generator(*_args, **_kwargs) -> str:
            return "something"

        with pytest.raises(
            ValueError,
            match=re.escape("Only one of key or key_generator can be provided."),
        ):

            @cache.memoize(key="something", key_generator=key_generator)
            def memoized_function(a):
                return a

    def test_memoize_with_formattable_key(self):
        """Test that memoize formats a key string using function arguments (in-memory)."""
        in_memory_storage = InMemoryStorage()
        cache = Cache(in_memory_storage)

        @cache.memoize(key="user-{a}-{b}")
        def memoized_function(a, b):
            _ = a
            _ = b
            return datetime.now(tz=UTC)

        call_1 = memoized_function(1, 2)
        assert isinstance(call_1, datetime)
        cache_key = CacheKey(key="user-1-2", package=cast(str, __package__))
        assert cache_key in in_memory_storage.cache

        # Repeat with same args
        assert memoized_function(1, 2) == call_1
        # And with kwargs order swapped
        assert memoized_function(b=2, a=1) == call_1

        # Different args -> new cache entry/value
        call_2 = memoized_function(2, 2)
        assert isinstance(call_2, datetime)
        assert call_2 > call_1


class TestChainedStorage:
    def test_constructor_requires_at_least_one_storage(self):
        """Test that the constructor raises ValueError when no storages are provided."""
        with pytest.raises(
            ValueError,
            match=re.escape("At least one storage must be provided"),
        ):
            ChainedStorage([])

    def test_get_tries_storages_in_order(self):
        """Test that get() tries each storage in order and returns the first hit."""
        # Create two storage backends
        storage1 = InMemoryStorage()
        storage2 = InMemoryStorage()

        # Create a chained storage with both backends
        chained_storage = ChainedStorage([storage1, storage2])

        # Create a cache key and entry
        cache_key = CacheKey(key="test-key", package="test-package")
        cache_entry = CacheEntry(
            cache_key=cache_key,
            value="test-value",
            added_at=datetime.now(tz=UTC),
            ttl=None,
        )

        # Store the entry only in the second storage
        storage2.store(cache_entry)

        # The chained storage should find it
        result = chained_storage.get(cache_key)
        assert result is not None
        assert result.value == "test-value"

        # And it should have been propagated to the first storage
        assert cache_key in storage1.cache

    def test_propagate_to_earlier_storages(self):
        """Test that entries found in later storages are propagated to earlier ones."""
        # Create three storage backends
        storage1 = InMemoryStorage()
        storage2 = InMemoryStorage()
        storage3 = InMemoryStorage()

        # Create a chained storage with all three backends
        chained_storage = ChainedStorage([storage1, storage2, storage3])

        # Create a cache key and entry
        cache_key = CacheKey(key="test-key", package="test-package")
        cache_entry = CacheEntry(
            cache_key=cache_key,
            value="test-value",
            added_at=datetime.now(tz=UTC),
            ttl=None,
        )

        # Store the entry only in the third storage
        storage3.store(cache_entry)

        # The entry should not be in the first two storages yet
        assert cache_key not in storage1.cache
        assert cache_key not in storage2.cache

        # Get the entry from the chained storage
        result = chained_storage.get(cache_key)
        assert result is not None
        assert result.value == "test-value"

        # The entry should now be in all storages
        assert cache_key in storage1.cache
        assert cache_key in storage2.cache
        assert cache_key in storage3.cache

    def test_store_stores_in_all_storages(self):
        """Test that store() stores the entry in all storage backends."""
        # Create two storage backends
        storage1 = InMemoryStorage()
        storage2 = InMemoryStorage()

        # Create a chained storage with both backends
        chained_storage = ChainedStorage([storage1, storage2])

        # Create a cache key and entry
        cache_key = CacheKey(key="test-key", package="test-package")
        cache_entry = CacheEntry(
            cache_key=cache_key,
            value="test-value",
            added_at=datetime.now(tz=UTC),
            ttl=None,
        )

        # Store the entry in the chained storage
        chained_storage.store(cache_entry)

        # The entry should be in both storages
        assert cache_key in storage1.cache
        assert cache_key in storage2.cache
        assert storage1.cache[cache_key].value == "test-value"
        assert storage2.cache[cache_key].value == "test-value"

    def test_get_returns_none_if_not_found(self):
        """Test that get() returns None if the entry is not found in any storage."""
        # Create two storage backends
        storage1 = InMemoryStorage()
        storage2 = InMemoryStorage()

        # Create a chained storage with both backends
        chained_storage = ChainedStorage([storage1, storage2])

        # Create a cache key
        cache_key = CacheKey(key="test-key", package="test-package")

        # The entry should not be found
        assert chained_storage.get(cache_key) is None

    def test_expired_entries_are_handled_correctly(self):
        """Test that expired entries are handled correctly by the chained storage."""
        # Create two storage backends
        storage1 = InMemoryStorage()
        storage2 = InMemoryStorage()

        # Create a chained storage with both backends
        chained_storage = ChainedStorage([storage1, storage2])

        # Create a cache key and an expired entry
        cache_key = CacheKey(key="test-key", package="test-package")
        cache_entry = CacheEntry(
            cache_key=cache_key,
            value="test-value",
            added_at=datetime.now(tz=UTC),
            ttl=timedelta(seconds=-1),  # Expired
        )

        # Test 1: Normal get should return None for expired entries
        # Store the entry in the second storage
        storage2.store(cache_entry)
        # The entry should not be found by default
        assert chained_storage.get(cache_key) is None

        # Test 2: get with return_expired=True should return the expired entry
        # Store the entry again since the previous get removed it
        storage2.store(cache_entry)
        # Now it should be found with return_expired=True
        result = chained_storage.get(cache_key, return_expired=True)
        assert result is not None
        assert result.value == "test-value"
        assert result.is_expired() is True


@pytest.fixture(scope="class")
def redis_container():
    """
    Fixture that provides a Redis container for testing.

    This fixture is scoped to the class level, so the same container
    is used for all tests in a class.
    """
    with RedisContainer() as container:
        yield container


@pytest.fixture(scope="class")
def redis_client(redis_container):
    """
    Fixture that provides a Redis client connected to the Redis container.

    This fixture depends on the redis_container fixture.
    """
    client = redis.Redis(
        host=redis_container.get_container_host_ip(),
        port=redis_container.get_exposed_port(6379),
        decode_responses=False,
    )
    yield client


@pytest.mark.docker
class TestRedisStorage:
    def test_unknown_key(self, redis_client):
        redis_storage = RedisStorage(redis_client)
        cache = Cache(redis_storage)
        assert cache.get("key") is None

    def test_unknown_key_with_default(self, redis_client):
        redis_storage = RedisStorage(redis_client)
        cache = Cache(redis_storage)
        default = "default"
        cache_entry = cache.get("key", default=default)
        assert cache_entry is not None
        assert cache_entry.cache_key.key == "key"
        assert cache_entry.cache_key.package == __package__
        assert cache_entry.value == default
        assert cache_entry.added_at is None
        assert cache_entry.ttl is None

    def test_cache_key_uniqueness(self, redis_client):
        redis_storage = RedisStorage(redis_client)
        cache = Cache(redis_storage)

        cache.store("key", "value")
        cache.store("key", "updated-value")
        cache_entry = cache.get("key")

        assert cache_entry is not None
        assert cache_entry.cache_key.key == "key"
        assert cache_entry.cache_key.package == __package__
        assert cache_entry.value == "updated-value"
        assert cache_entry.added_at is not None
        assert cache_entry.ttl is None
        assert cache_entry.is_expired() is False

    def test_basic_store_and_get(self, redis_client):
        redis_storage = RedisStorage(redis_client)
        cache = Cache(redis_storage)

        cache.store("key", "value")
        cache_entry = cache.get("key")

        assert cache_entry is not None
        assert cache_entry.cache_key.key == "key"
        assert cache_entry.cache_key.package == __package__
        assert cache_entry.value == "value"
        assert cache_entry.added_at is not None
        assert cache_entry.ttl is None
        assert cache_entry.is_expired() is False

    def test_store_with_package(self, redis_client):
        redis_storage = RedisStorage(redis_client)
        cache = Cache(redis_storage)

        cache.store("key", "value", package="test")
        cache_entry = cache.get("key", package="test")

        assert cache_entry is not None
        assert cache_entry.cache_key.key == "key"
        assert cache_entry.cache_key.package == "test"
        assert cache_entry.value == "value"
        assert cache_entry.added_at is not None
        assert cache_entry.ttl is None
        assert cache_entry.is_expired() is False

    def test_ttl_works_with_redis_ttl_true(self, redis_client):
        """Test that with use_redis_ttl=True (default), expired entries are deleted from Redis."""
        redis_storage = RedisStorage(redis_client, use_redis_ttl=True)
        cache = Cache(redis_storage)

        cache.store("key", "value", ttl=timedelta(seconds=-1))
        cache_key = CacheKey(key="key", package=cast(str, __package__))
        redis_key = redis_storage._get_redis_key(cache_key)

        # The key should not exist in Redis because it's immediately deleted
        # when storing with a negative TTL
        assert redis_client.exists(redis_key) == 0

        # And get() should return None because it's expired
        assert cache.get("key") is None

    def test_ttl_works_with_redis_ttl_false(self, redis_client):
        """Test that with use_redis_ttl=False, expired entries are still stored in Redis."""
        redis_storage = RedisStorage(redis_client, use_redis_ttl=False)
        cache = Cache(redis_storage)

        cache.store("key", "value", ttl=timedelta(seconds=-1))
        cache_key = CacheKey(key="key", package=cast(str, __package__))
        redis_key = redis_storage._get_redis_key(cache_key)

        # The key should exist in Redis even though it's expired
        assert redis_client.exists(redis_key) == 1

        # But get() should return None because it's expired (Python-side check)
        assert cache.get("key") is None

        # And the key should be removed from Redis after the get() call
        assert redis_client.exists(redis_key) == 0

    def test_return_expired(self, redis_client):
        """Test that expired entries can be returned with return_expired=True."""
        redis_storage = RedisStorage(redis_client, use_redis_ttl=False)
        cache = Cache(redis_storage)

        cache.store("key", "value", ttl=timedelta(seconds=-1))
        cache_key = CacheKey(key="key", package=cast(str, __package__))
        redis_key = redis_storage._get_redis_key(cache_key)

        # The key should exist in Redis
        assert redis_client.exists(redis_key) == 1

        # get() with return_expired=True should return the expired entry
        cache_entry = cache.get("key", return_expired=True)
        assert cache_entry is not None
        assert cache_entry.cache_key.key == "key"
        assert cache_entry.cache_key.package == __package__
        assert cache_entry.value == "value"
        assert cache_entry.added_at is not None
        assert cache_entry.ttl == timedelta(seconds=-1)
        assert cache_entry.is_expired() is True

        # And the key should be removed from Redis
        assert redis_client.exists(redis_key) == 0

    def test_positive_ttl_with_redis_ttl_true(self, redis_client):
        """Test that with use_redis_ttl=True and a positive TTL, Redis's TTL mechanism is used."""
        redis_storage = RedisStorage(redis_client, use_redis_ttl=True)
        cache = Cache(redis_storage)

        # Store with a positive TTL (1 hour)
        cache.store("key", "value", ttl=timedelta(hours=1))
        cache_key = CacheKey(key="key", package=cast(str, __package__))
        redis_key = redis_storage._get_redis_key(cache_key)

        # The key should exist in Redis
        assert redis_client.exists(redis_key) == 1

        # The key should have a TTL set in Redis (between 0 and 3600 seconds)
        ttl = redis_client.ttl(redis_key)
        assert 0 < ttl <= 3600

        # We should be able to get the value
        cache_entry = cache.get("key")
        assert cache_entry is not None
        assert cache_entry.value == "value"

    def test_positive_ttl_with_redis_ttl_false(self, redis_client):
        """Test that with use_redis_ttl=False and a positive TTL, Python's TTL mechanism is used."""
        redis_storage = RedisStorage(redis_client, use_redis_ttl=False)
        cache = Cache(redis_storage)

        # Store with a positive TTL (1 hour)
        cache.store("key", "value", ttl=timedelta(hours=1))
        cache_key = CacheKey(key="key", package=cast(str, __package__))
        redis_key = redis_storage._get_redis_key(cache_key)

        # The key should exist in Redis
        assert redis_client.exists(redis_key) == 1

        # The key should NOT have a TTL set in Redis (TTL should be -1, meaning no expiry)
        ttl = redis_client.ttl(redis_key)
        assert ttl == -1

        # We should be able to get the value
        cache_entry = cache.get("key")
        assert cache_entry is not None
        assert cache_entry.value == "value"

        # The TTL should be stored in the cache entry
        assert cache_entry.ttl == timedelta(hours=1)

    def test_redis_key_infix(self, redis_client):
        """Test that redis_key_infix is correctly included in the Redis key."""
        # Create a storage with a key infix
        infix = "test-infix"
        redis_storage = RedisStorage(redis_client, redis_key_infix=infix)

        # Get the Redis key for a cache key
        cache_key = CacheKey(key="key", package=cast(str, __package__))
        redis_key = redis_storage._get_redis_key(cache_key)

        # Verify the infix is in the key
        assert f":{infix}:" in redis_key

        # Store a value and verify it's stored with the correct key
        cache = Cache(redis_storage)
        cache.store("key", "value")

        # The key should exist in Redis
        assert redis_client.exists(redis_key) == 1

        # We should be able to get the value
        cache_entry = cache.get("key")
        assert cache_entry is not None
        assert cache_entry.value == "value"

    def test_parallel_caches_with_different_infixes(self, redis_client):
        """Test that two caches with different infixes don't overwrite each other's values."""
        # Create two storages with different infixes
        infix1 = "infix1"
        infix2 = "infix2"
        redis_storage1 = RedisStorage(redis_client, redis_key_infix=infix1)
        redis_storage2 = RedisStorage(redis_client, redis_key_infix=infix2)

        # Create two caches
        cache1 = Cache(redis_storage1)
        cache2 = Cache(redis_storage2)

        # Store different values with the same key in both caches
        cache1.store("same-key", "value1")
        cache2.store("same-key", "value2")

        # Get the Redis keys
        cache_key = CacheKey(key="same-key", package=cast(str, __package__))
        redis_key1 = redis_storage1._get_redis_key(cache_key)
        redis_key2 = redis_storage2._get_redis_key(cache_key)

        # Verify the keys are different
        assert redis_key1 != redis_key2

        # Both keys should exist in Redis
        assert redis_client.exists(redis_key1) == 1
        assert redis_client.exists(redis_key2) == 1

        # We should get different values from each cache
        cache_entry1 = cache1.get("same-key")
        cache_entry2 = cache2.get("same-key")

        assert cache_entry1 is not None
        assert cache_entry2 is not None
        assert cache_entry1.value == "value1"
        assert cache_entry2.value == "value2"

    def test_memoize(self, redis_client):
        """Test that memoize works with a key."""
        redis_storage = RedisStorage(redis_client)
        cache = Cache(redis_storage)

        @cache.memoize(key="memoize-key")
        def memoized_function(a):
            return a

        assert memoized_function(1) == 1
        cache_key = CacheKey(
            key="memoize-key",
            package=cast(str, __package__),
        )
        redis_key = redis_storage._get_redis_key(cache_key)

        # The key should exist in Redis
        assert redis_client.exists(redis_key) == 1

        # memoize currently does not cache by the arguments provided, so providing
        # something else will still return the prior value while the cache is active
        assert memoized_function(2) == 1

        # Delete the key from Redis
        redis_client.delete(redis_key)
        assert memoized_function(2) == 2

    def test_memoize_with_key_generator(self, redis_client):
        """Test that memoize works with a key generator."""
        redis_storage = RedisStorage(redis_client)
        cache = Cache(redis_storage)

        def key_generator(*args, **_kwargs) -> str:
            return str(args[0])

        @cache.memoize(key_generator=key_generator)
        def memoized_function(_a):
            return datetime.now(tz=UTC)

        call_1 = memoized_function(1)
        assert isinstance(call_1, datetime)
        cache_key = CacheKey(
            key="1",
            package=cast(str, __package__),
        )
        redis_key = redis_storage._get_redis_key(cache_key)

        # The key should exist in Redis
        assert redis_client.exists(redis_key) == 1

        # Repeat calls return the same result
        assert memoized_function(1) == call_1

        # Calls with a different argument return a different result
        call_2 = memoized_function(2)
        assert isinstance(call_2, datetime)
        assert call_2 > call_1

    def test_memoize_without_key_or_key_generator(self, redis_client):
        """Test that memoize raises ValueError when neither key nor key_generator is provided."""
        redis_storage = RedisStorage(redis_client)
        cache = Cache(redis_storage)

        with pytest.raises(
            ValueError,
            match=re.escape("Either key or key_generator must be provided."),
        ):

            @cache.memoize()
            def memoized_function(a):
                return a

    def test_memoize_with_both_key_and_key_generator(self, redis_client):
        """Test that memoize raises ValueError when both key and key_generator are provided."""
        redis_storage = RedisStorage(redis_client)
        cache = Cache(redis_storage)

        def key_generator(*_args, **_kwargs) -> str:
            return "something"

        with pytest.raises(
            ValueError,
            match=re.escape("Only one of key or key_generator can be provided."),
        ):

            @cache.memoize(key="something", key_generator=key_generator)
            def memoized_function(a):
                return a

    def test_memoize_with_formattable_key(self, redis_client):
        """Test that memoize formats a key string using function arguments (Redis)."""
        redis_storage = RedisStorage(redis_client)
        cache = Cache(redis_storage)

        @cache.memoize(key="user-{a}-{b}")
        def memoized_function(a, b):
            _ = a
            _ = b
            return datetime.now(tz=UTC)

        call_1 = memoized_function(1, 2)
        assert isinstance(call_1, datetime)
        cache_key = CacheKey(key="user-1-2", package=cast(str, __package__))
        redis_key = redis_storage._get_redis_key(cache_key)
        assert redis_client.exists(redis_key) == 1

        # Repeat with same args
        assert memoized_function(1, 2) == call_1
        # And with kwargs order swapped
        assert memoized_function(b=2, a=1) == call_1

        # Different args -> new cache entry/value
        call_2 = memoized_function(2, 2)
        assert isinstance(call_2, datetime)
        assert call_2 > call_1
