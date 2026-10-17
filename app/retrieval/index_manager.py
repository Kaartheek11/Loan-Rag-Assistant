"""FAISS index ownership and consistency with Postgres (§5.5).

- Single writer: a Postgres session-level advisory lock (pg_try_advisory_lock).
- Atomic saves: write a temp file in the same directory, fsync, os.replace().
- Version tracking: index_state.version is bumped in the same transaction as status changes.
- Reader reload: the API compares its loaded version with index_state.version before each
  query and reloads under a write lock, so in-flight searches finish first.
"""

from __future__ import annotations

import contextlib
import os
import threading
import time
from collections.abc import Iterator, Sequence
from pathlib import Path

import faiss
import numpy as np
from sqlalchemy import Connection, Engine, text

WRITER_LOCK_KEY = 0x4C4F414E524147  # "LOANRAG"


class WriterLockHeld(RuntimeError):
    """Another process holds the index writer lock."""


class IndexUnavailable(RuntimeError):
    pass


class FaissIndex:
    """IndexIDMap2(IndexFlatIP(dim)) keyed by chunk_id. Vectors must be L2-normalized."""

    def __init__(self, index: faiss.Index) -> None:
        self._index = index

    @classmethod
    def new(cls, dim: int) -> FaissIndex:
        return cls(faiss.IndexIDMap2(faiss.IndexFlatIP(dim)))

    @classmethod
    def load(cls, path: Path) -> FaissIndex:
        return cls(faiss.read_index(str(path)))

    @classmethod
    def load_or_new(cls, path: Path, dim: int) -> FaissIndex:
        if path.exists():
            idx = cls.load(path)
            if idx.dim != dim:
                raise IndexUnavailable("index dimension does not match the embedding model")
            return idx
        return cls.new(dim)

    @property
    def dim(self) -> int:
        return int(self._index.d)

    @property
    def ntotal(self) -> int:
        return int(self._index.ntotal)

    def add(self, ids: Sequence[int], vectors: np.ndarray) -> None:
        if len(ids) == 0:
            return
        vecs = np.ascontiguousarray(vectors, dtype=np.float32)
        self._index.add_with_ids(vecs, np.asarray(ids, dtype=np.int64))

    def remove(self, ids: Sequence[int]) -> int:
        if len(ids) == 0:
            return 0
        return int(self._index.remove_ids(np.asarray(ids, dtype=np.int64)))  # type: ignore[arg-type]

    def search(self, vector: np.ndarray, n: int) -> list[int]:
        if self.ntotal == 0 or n <= 0:
            return []
        q = np.ascontiguousarray(vector.reshape(1, -1), dtype=np.float32)
        _, ids = self._index.search(q, min(n, self.ntotal))
        return [int(i) for i in ids[0] if i >= 0]

    def ids(self) -> set[int]:
        id_map = faiss.vector_to_array(self._index.id_map)  # type: ignore[attr-defined]
        return {int(i) for i in id_map}

    def save_atomic(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
        faiss.write_index(self._index, str(tmp))
        with open(tmp, "rb") as f:
            os.fsync(f.fileno())
        os.replace(tmp, path)


@contextlib.contextmanager
def writer_lock(engine: Engine, wait_seconds: float = 0.0) -> Iterator[None]:
    """Hold the single-writer advisory lock for the duration of the block."""
    with engine.connect() as conn:
        deadline = time.monotonic() + wait_seconds
        while True:
            got = conn.execute(
                text("SELECT pg_try_advisory_lock(:k)"), {"k": WRITER_LOCK_KEY}
            ).scalar_one()
            conn.commit()
            if got:
                break
            if time.monotonic() >= deadline:
                raise WriterLockHeld("the index writer lock is held by another process")
            time.sleep(0.2)
        try:
            yield
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": WRITER_LOCK_KEY})
            conn.commit()


def read_version(conn: Connection) -> int | None:
    v = conn.execute(text("SELECT version FROM index_state WHERE id = 1")).scalar()
    return None if v is None else int(v)


def bump_version(conn: Connection, embedding_model: str | None = None) -> int:
    if embedding_model is None:
        sql = (
            "UPDATE index_state SET version = version + 1, updated_at = now() WHERE id = 1 "
            "RETURNING version"
        )
        return int(conn.execute(text(sql)).scalar_one())
    sql = (
        "UPDATE index_state SET version = version + 1, embedding_model = :m, updated_at = now() "
        "WHERE id = 1 RETURNING version"
    )
    return int(conn.execute(text(sql), {"m": embedding_model}).scalar_one())


class _RWLock:
    """Writer-preferring readers/writer lock."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._readers = 0
        self._writer = False
        self._waiting_writers = 0

    @contextlib.contextmanager
    def read(self) -> Iterator[None]:
        with self._cond:
            while self._writer or self._waiting_writers:
                self._cond.wait()
            self._readers += 1
        try:
            yield
        finally:
            with self._cond:
                self._readers -= 1
                self._cond.notify_all()

    @contextlib.contextmanager
    def write(self) -> Iterator[None]:
        with self._cond:
            self._waiting_writers += 1
            while self._writer or self._readers:
                self._cond.wait()
            self._waiting_writers -= 1
            self._writer = True
        try:
            yield
        finally:
            with self._cond:
                self._writer = False
                self._cond.notify_all()


class IndexHolder:
    """The API's in-memory copy of the index plus the version it was loaded at."""

    def __init__(self, path: Path, dim: int) -> None:
        self.path = Path(path)
        self.dim = dim
        self._index: FaissIndex | None = None
        self._version: int | None = None
        self._lock = _RWLock()
        self.reloads = 0

    @property
    def version(self) -> int | None:
        return self._version

    def load(self, version: int) -> None:
        with self._lock.write():
            self._load_locked(version)

    def _load_locked(self, version: int) -> None:
        if self.path.exists():
            idx = FaissIndex.load(self.path)
            if idx.dim != self.dim:
                raise IndexUnavailable("index dimension does not match the embedding model")
        elif version == 0:
            idx = FaissIndex.new(self.dim)
        else:
            raise IndexUnavailable("index file missing; run rebuild-index")
        self._index = idx
        self._version = version
        self.reloads += 1

    def ensure_current(self, conn: Connection) -> int:
        version = read_version(conn)
        if version is None:
            raise IndexUnavailable("index_state is missing")
        if version != self._version:
            with self._lock.write():
                if version != self._version:
                    self._load_locked(version)
        return version

    def search(self, vector: np.ndarray, n: int) -> list[int]:
        with self._lock.read():
            if self._index is None:
                raise IndexUnavailable("index not loaded")
            return self._index.search(vector, n)

    def ntotal(self) -> int:
        with self._lock.read():
            return 0 if self._index is None else self._index.ntotal
