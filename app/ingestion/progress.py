"""Ingestion progress output (extension.md §17.4): documents done, pages per second, ETA.

Emits a line whenever at least `interval` seconds have passed since the last one, checked on
every page, so output appears at least every 30 seconds during a full run. Lines contain
counts only, never document content.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from typing import TextIO

DEFAULT_INTERVAL = 10.0


def _fmt_eta(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


class Progress:
    def __init__(
        self,
        total_docs: int,
        *,
        interval: float = DEFAULT_INTERVAL,
        stream: TextIO | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.total_docs = total_docs
        self.interval = interval
        self.stream = stream if stream is not None else sys.stderr
        self.clock = clock
        self.started = clock()
        self.last_emit = self.started
        self.docs_done = 0
        self.pages_done = 0
        self.lines: list[str] = []

    def page(self, n: int = 1) -> None:
        self.pages_done += n
        self._maybe_emit()

    def doc(self) -> None:
        self.docs_done += 1
        self._maybe_emit()

    def tick(self) -> None:
        """Call during long steps that are not page-based (for example, embedding)."""
        self._maybe_emit()

    def _maybe_emit(self, force: bool = False) -> None:
        now = self.clock()
        if not force and now - self.last_emit < self.interval:
            return
        self.last_emit = now
        elapsed = max(now - self.started, 1e-6)
        rate = self.pages_done / elapsed
        if self.docs_done:
            eta = (self.total_docs - self.docs_done) * elapsed / self.docs_done
            eta_text = _fmt_eta(eta)
        else:
            eta_text = "estimating"
        line = (
            f"progress: {self.docs_done}/{self.total_docs} documents | "
            f"{self.pages_done:,} pages | {rate:.1f} pages/s | ETA {eta_text}"
        )
        self.lines.append(line)
        print(line, file=self.stream, flush=True)

    def finish(self) -> None:
        self._maybe_emit(force=True)
