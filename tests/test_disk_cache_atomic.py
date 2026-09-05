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

import pickle
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from typing import Any, BinaryIO

import pytest

from watchpost._file_lock import exclusive_lock
from watchpost.cache import CacheEntry, CacheKey, DiskStorage


def entry(value: str) -> CacheEntry[str]:
    return CacheEntry(CacheKey("key", "test"), value, datetime.now(UTC), None)


def test_readers_see_complete_entries_during_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = DiskStorage(str(tmp_path))
    storage.store(entry("old"))
    started, finish = Event(), Event()
    original_dump = pickle.dump

    def blocked_dump(value: Any, file: BinaryIO) -> None:
        file.write(b"partial")
        file.flush()
        started.set()
        assert finish.wait(5)
        file.seek(0)
        file.truncate()
        original_dump(value, file)

    monkeypatch.setattr(pickle, "dump", blocked_dump)
    with ThreadPoolExecutor() as pool:
        writer = pool.submit(storage.store, entry("new"))
        try:
            assert started.wait(5)
            result = DiskStorage(str(tmp_path)).get(entry("old").cache_key)
            assert result is not None and result.value == "old"
        finally:
            finish.set()
        writer.result(timeout=5)
    result = storage.get(entry("new").cache_key)
    assert result is not None and result.value == "new"
    assert not list(tmp_path.rglob(".watchpost-*"))


def test_failed_write_preserves_previous_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = DiskStorage(str(tmp_path))
    storage.store(entry("old"))

    def failed_dump(_value: Any, file: BinaryIO) -> None:
        file.write(b"partial")
        raise RuntimeError("interrupted")

    monkeypatch.setattr(pickle, "dump", failed_dump)
    with pytest.raises(RuntimeError, match="interrupted"):
        storage.store(entry("new"))
    result = storage.get(entry("old").cache_key)
    assert result is not None and result.value == "old"
    assert not list(tmp_path.rglob(".watchpost-*"))


@pytest.mark.parametrize("data", [b"", b"broken pickle", pickle.dumps("wrong type")])
def test_corrupt_entries_are_cache_misses(tmp_path: Path, data: bytes) -> None:
    storage = DiskStorage(str(tmp_path))
    path = storage._get_file_path(entry("old").cache_key)
    path.parent.mkdir(parents=True)
    path.write_bytes(data)
    assert storage.get(entry("old").cache_key) is None
    storage.store(entry("repaired"))
    result = storage.get(entry("old").cache_key)
    assert result is not None and result.value == "repaired"


def test_concurrent_removal_is_harmless(tmp_path: Path) -> None:
    storage = DiskStorage(str(tmp_path))
    storage.store(entry("old"))
    with ThreadPoolExecutor() as pool:
        list(pool.map(storage._remove_cache_entry_on_disk, [entry("old")] * 20))
    assert storage.get(entry("old").cache_key) is None


def test_expiry_reader_preserves_concurrent_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = DiskStorage(str(tmp_path))
    old = entry("old")
    old.ttl = timedelta(seconds=-1)
    storage.store(old)
    read_expired, continue_read = Event(), Event()
    original_is_expired = CacheEntry.is_expired

    def pause_expired_read(value: CacheEntry[Any]) -> bool:
        expired = original_is_expired(value)
        if expired:
            read_expired.set()
            assert continue_read.wait(5)
        return expired

    monkeypatch.setattr(CacheEntry, "is_expired", pause_expired_read)
    with ThreadPoolExecutor() as pool:
        reader = pool.submit(storage.get, old.cache_key, return_expired=True)
        try:
            assert read_expired.wait(5)
            DiskStorage(str(tmp_path)).store(entry("new"))
        finally:
            continue_read.set()
        observed = reader.result(timeout=5)
    assert observed is not None and observed.value == "new"
    retained = storage.get(old.cache_key)
    assert retained is not None and retained.value == "new"


def test_disk_publication_coordinates_across_processes(tmp_path: Path) -> None:
    storage = DiskStorage(str(tmp_path))
    old = entry("old")
    storage.store(old)
    file_path = storage._get_file_path(old.cache_key)
    script = """
import sys
from watchpost.cache import CacheEntry, CacheKey, DiskStorage
storage = DiskStorage(sys.argv[1])
print('ready', flush=True)
storage.store(CacheEntry(CacheKey('key', 'test'), 'new', None, None))
"""
    process = None
    try:
        with exclusive_lock(file_path.parent / ".publication.lock"):
            process = subprocess.Popen(
                [sys.executable, "-c", script, str(tmp_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            assert process.stdout is not None
            assert process.stdout.readline().strip() == "ready"
            with pytest.raises(subprocess.TimeoutExpired):
                process.wait(timeout=0.1)
            # Model expiry cleanup while publication is excluded.
            file_path.unlink()
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, (stdout, stderr)
        retained = storage.get(old.cache_key)
        assert retained is not None and retained.value == "new"
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
