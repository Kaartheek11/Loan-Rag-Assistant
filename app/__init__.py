"""Loan File RAG Assistant."""

import os
import sys

if sys.platform == "darwin":
    # torch and faiss-cpu wheels each bundle their own libomp on macOS; two OpenMP runtimes
    # in one process crash (segfault/abort) as soon as either runs multi-threaded. Keep both
    # single-threaded on macOS: ~50 chunks/s embedding, and a flat-index search is still
    # fast. Linux (Docker/CI) uses one shared libgomp and is unaffected. CLAUDE.md D6.
    os.environ.setdefault("OMP_NUM_THREADS", "1")
