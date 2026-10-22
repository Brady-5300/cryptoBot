"""Append-only raw message log: the source of truth for replay.

Every message is stored exactly as received, wrapped with local receive timestamps::

    {"t": <wall_ns>, "m": <mono_ns>, "d": <message JSON as received>}

Layout: ``<root>/raw/<venue>/<stream>/<YYYY-MM-DD>/<HH>.jsonl`` (UTC). The file for the
current hour is plain JSONL so a crash loses at most the unflushed buffer; when the hour rolls
over (or on the next start after a crash) it is compressed to ``.jsonl.zst`` and the plain file
is removed. Parsing/normalization happens later, from these files, so a parser bug never
destroys data.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from compression import zstd
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

log = logging.getLogger(__name__)

ZSTD_LEVEL = 9


def hour_key(wall_ns: int) -> tuple[str, str]:
    # Integer seconds: float division would round 12:59:59.9999999 into the next hour.
    dt = datetime.fromtimestamp(wall_ns // 1_000_000_000, tz=UTC)
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H")


def encode_line(raw: str, wall_ns: int, mono_ns: int) -> str:
    """Wrap one raw message. Compact single-line JSON is embedded verbatim (byte-exact);
    anything else is re-serialized, or stored as a string under ``"s"`` if it isn't JSON."""
    body = raw.strip()
    try:
        parsed = json.loads(body)  # validate: one bad frame must not corrupt the file
    except json.JSONDecodeError:
        return json.dumps({"t": wall_ns, "m": mono_ns, "s": raw}) + "\n"
    if "\n" in body or "\r" in body:
        body = json.dumps(parsed, separators=(",", ":"))
    return f'{{"t":{wall_ns},"m":{mono_ns},"d":{body}}}\n'


def wrap_rest(path: str, sent_ns: int, status: int, body: str) -> str:
    """Envelope for a polled REST response: which request, when it was sent, and the body
    verbatim (re-serialized only if it spans lines). ``t`` on the outer line is receive time,
    so ``t - sent_ns`` is the round-trip latency."""
    b = body.strip()
    if "\n" in b or "\r" in b or not b.startswith(("{", "[")):
        try:
            b = json.dumps(json.loads(b), separators=(",", ":"))
        except json.JSONDecodeError:
            b = json.dumps(body)
    return f'{{"path":{json.dumps(path)},"sent_ns":{sent_ns},"status":{status},"body":{b}}}'


def _compressed_name(src: Path) -> Path:
    """``HH.jsonl`` -> ``HH.jsonl.zst``; if that exists (a restart within the same hour),
    ``HH-1.jsonl.zst``, ``HH-2.jsonl.zst``... Never overwrites."""
    stem = src.name.removesuffix(".jsonl")
    dst, n = src.with_name(f"{stem}.jsonl.zst"), 0
    while dst.exists():
        n += 1
        dst = src.with_name(f"{stem}-{n}.jsonl.zst")
    return dst


def compress_file(src: Path) -> Path:
    """Compress ``x.jsonl`` to ``x.jsonl.zst`` atomically, then delete the original."""
    dst = _compressed_name(src)
    tmp = dst.with_name(dst.name + ".tmp")
    with src.open("rb") as fin, zstd.open(tmp, "wb", level=ZSTD_LEVEL) as fout:
        while chunk := fin.read(1 << 20):
            fout.write(chunk)
    os.replace(tmp, dst)
    src.unlink()
    return dst


class RawWriter:
    """Writes one stream (e.g. ``kalshi/ws``) with hourly rotation. Not thread-safe; owned by
    the event loop. Compression of closed hours is synchronous and returned to the caller via
    ``pending_compress`` so the recorder can push it to a worker thread."""

    def __init__(self, root: Path, venue: str, stream: str) -> None:
        self.dir = root / "raw" / venue / stream
        self.venue, self.stream = venue, stream
        self._fh: IO[str] | None = None
        self._key: tuple[str, str] | None = None
        self._path: Path | None = None
        self.pending_compress: list[Path] = []
        self.lines_written = 0
        self.last_write_mono_ns: int | None = None

    def path_for(self, key: tuple[str, str]) -> Path:
        day, hour = key
        return self.dir / day / f"{hour}.jsonl"

    def write(self, raw: str, wall_ns: int, mono_ns: int) -> None:
        key = hour_key(wall_ns)
        if key != self._key:
            self._rotate(key)
        assert self._fh is not None
        self._fh.write(encode_line(raw, wall_ns, mono_ns))
        self.lines_written += 1
        self.last_write_mono_ns = mono_ns

    def _rotate(self, key: tuple[str, str]) -> None:
        self.close()
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Appending after a crash: make sure a torn final line can't merge with the next one.
        needs_newline = False
        if path.exists() and path.stat().st_size > 0:
            with path.open("rb") as f:
                f.seek(-1, os.SEEK_END)
                needs_newline = f.read(1) != b"\n"
        self._fh = path.open("a", encoding="utf-8", newline="\n")
        if needs_newline:
            self._fh.write("\n")
        self._key, self._path = key, path

    def flush(self) -> None:
        if self._fh is not None:
            self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            assert self._path is not None
            self.pending_compress.append(self._path)
            self._fh = self._key = self._path = None

    def take_pending(self) -> list[Path]:
        out, self.pending_compress = self.pending_compress, []
        return out

    def shutdown(self) -> None:
        """Close without queueing the current hour for compression: a restart within the
        same hour keeps appending to it; the next start's sweep handles older hours."""
        current = self._path
        self.close()
        self.pending_compress = [p for p in self.pending_compress if p != current]

    def stale_uncompressed(self, current_wall_ns: int) -> list[Path]:
        """Plain ``.jsonl`` files left from earlier hours (e.g. after a crash)."""
        current = self.path_for(hour_key(current_wall_ns))
        if not self.dir.exists():
            return []
        return sorted(p for p in self.dir.glob("*/*.jsonl") if p != current)


def iter_raw(path: Path) -> Iterator[dict[str, Any]]:
    """Yield wrapped records from a ``.jsonl`` or ``.jsonl.zst`` file. A torn final line (crash
    mid-write) or truncated zstd frame is logged and skipped rather than raised."""
    opener = zstd.open if path.suffix == ".zst" else open
    with opener(path, "rt", encoding="utf-8") as f:
        lineno = 0
        try:
            for lineno, line in enumerate(f, 1):  # noqa: B007
                if not line.strip():
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    log.warning("skipping malformed line %s:%d", path, lineno)
        except (EOFError, zstd.ZstdError):
            log.warning("truncated compressed file %s after line %d", path, lineno)
