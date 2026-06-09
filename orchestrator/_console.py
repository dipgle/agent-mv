"""
Shared console setup — import early in every CLI entry to avoid mojibake
on Windows (default code page 1252 chokes on Vietnamese + emoji output).

Usage at the very top of a script:
    from orchestrator._console import ensure_utf8
    ensure_utf8()
"""

from __future__ import annotations
import sys


def ensure_utf8() -> None:
    # Python 3.7+ has reconfigure; on older or on streams without it, swallow.
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
