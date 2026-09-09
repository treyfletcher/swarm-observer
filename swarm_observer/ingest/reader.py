"""Line-wise JSONL reading, size caps, hashing, identity and atomic writes (R5, R6, R11).

Everything here is adapter-agnostic: it turns a set of paths into hashed
:class:`~swarm_observer.model.trace.SourceFile` provenance plus a flat sequence
of JSON objects in canonical order (R6). What those objects *mean* is the
adapter's business (``ingest.claude_code``), which is why no Claude Code field
name appears in this module.

Two design points worth stating, because both are security properties rather
than conveniences:

* **The line cap is enforced while reading, not after.** Lines are assembled
  from bounded chunks and a line is rejected the moment the buffer passes
  ``max_line_bytes``, so a 40 GB single-line "trace" costs 8 MiB of memory and
  one error, not an OOM. Reading the file and *then* measuring would make the
  cap decorative.
* **Outputs are staged and renamed, never opened for writing in place.** R11
  requires that a failed run leaves no output file created or truncated, so
  :func:`atomic_write_texts` writes every temporary file first and renames only
  after all of them are complete.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from swarm_observer.ingest.source import (
    IngestLimits,
    TraceLimitError,
    TraceParseError,
    TraceReadError,
)
from swarm_observer.model.trace import SourceFile

#: Read granularity. Independent of the caps: it only bounds a single `read`.
_CHUNK_BYTES = 1 << 20


@dataclass(frozen=True, slots=True)
class JsonRecord:
    """One JSON object read from a trace file, with its canonical position (R6)."""

    #: Index of the file in the basename-sorted source file list.
    file_index: int
    #: 1-based line number within that file. Used only in error messages.
    line: int
    data: dict[str, Any]


@dataclass(frozen=True, slots=True)
class LoadedInput:
    """Everything the reader hands an adapter: provenance plus ordered records."""

    source_files: tuple[SourceFile, ...]
    records: tuple[JsonRecord, ...]


def resolve_inputs(paths: Sequence[Path], limits: IngestLimits) -> tuple[Path, ...]:
    """Validate the named inputs and return them in canonical order (R6, R11).

    Canonical order is by basename, and basenames must be unique: two files
    called ``agent-1.jsonl`` from different directories would collide in
    :class:`~swarm_observer.model.trace.SourceFile` (R2) and make ``trace_id``
    ambiguous (R5), so that is a fail-closed condition rather than a merge.

    A symlink is accepted only when it resolves to another named input. That is
    strict on purpose: the analyst names the files they mean, and a directory
    expansion that picks up ``agent-9.jsonl -> /etc/shadow`` must not read it.
    """
    if not paths:
        raise TraceReadError("empty_input", note="no input paths given")
    if len(paths) > limits.max_files:
        raise TraceLimitError("too_many_files", limit=limits.max_files)

    # The set a symlink is allowed to point into: the *non-symlink* paths the
    # caller named. Resolving the symlinks themselves into this set would make
    # the check vacuous — every symlink trivially resolves to its own target.
    named = {os.path.realpath(path) for path in paths if not path.is_symlink()}

    seen: dict[str, Path] = {}
    for path in paths:
        name = path.name
        if not path.exists():
            raise TraceReadError("unreadable_path", source=name)
        if path.is_symlink() and os.path.realpath(path) not in named:
            raise TraceReadError("symlink_escape", source=name)
        if not path.is_file():
            raise TraceReadError("not_a_regular_file", source=name)
        if not os.access(path, os.R_OK):
            raise TraceReadError("unreadable_path", source=name)
        if name in seen and os.path.realpath(seen[name]) != os.path.realpath(path):
            raise TraceReadError("duplicate_input", source=name, note="basename is not unique")
        seen[name] = path
    return tuple(sorted(seen.values(), key=lambda item: item.name))


def _iter_lines(
    handle: BinaryIO,
    name: str,
    limits: IngestLimits,
    digest: hashlib._Hash,
) -> Iterator[tuple[int, bytes]]:
    """Yield ``(line_number, line_bytes)`` under the byte caps (R11).

    ``digest`` is updated with every chunk exactly as read, so the resulting
    hash is the file's own SHA-256 and never a reconstruction from the split
    lines (which would differ on ``\\r\\n`` or a missing trailing newline).
    """
    buffer = bytearray()
    line_number = 0
    total = 0
    while True:
        chunk = handle.read(_CHUNK_BYTES)
        if not chunk:
            break
        digest.update(chunk)
        total += len(chunk)
        if total > limits.max_file_bytes:
            raise TraceLimitError("file_too_large", source=name, limit=limits.max_file_bytes)
        buffer += chunk
        while True:
            newline = buffer.find(b"\n")
            if newline == -1:
                break
            line = bytes(buffer[:newline])
            del buffer[: newline + 1]
            line_number += 1
            if len(line) > limits.max_line_bytes:
                raise TraceLimitError(
                    "line_too_long", source=name, line=line_number, limit=limits.max_line_bytes
                )
            yield line_number, line
        if len(buffer) > limits.max_line_bytes:
            raise TraceLimitError(
                "line_too_long", source=name, line=line_number + 1, limit=limits.max_line_bytes
            )
    if buffer:
        line_number += 1
        yield line_number, bytes(buffer)


def read_jsonl(
    path: Path,
    file_index: int,
    limits: IngestLimits,
    *,
    records_budget: int,
) -> tuple[SourceFile, tuple[JsonRecord, ...]]:
    """Read one JSONL file: caps, SHA-256, JSON objects (R11).

    ``records_budget`` is what remains of ``limits.max_records`` after earlier
    files, so the cap bounds the whole input set rather than each file.
    Blank lines are skipped and not counted: a trailing newline is not a record.
    """
    name = path.name
    try:
        size = path.stat().st_size
    except OSError:
        raise TraceReadError("unreadable_path", source=name) from None
    if size > limits.max_file_bytes:
        raise TraceLimitError("file_too_large", source=name, limit=limits.max_file_bytes)

    digest = hashlib.sha256()
    records: list[JsonRecord] = []
    try:
        handle = path.open("rb")
    except OSError:
        raise TraceReadError("unreadable_path", source=name) from None
    with handle:
        for line_number, raw in _iter_lines(handle, name, limits, digest):
            stripped = raw.rstrip(b"\r")
            if not stripped.strip():
                continue
            if len(records) >= records_budget:
                raise TraceLimitError(
                    "too_many_records", source=name, line=line_number, limit=limits.max_records
                )
            try:
                text = stripped.decode("utf-8")
            except UnicodeDecodeError:
                raise TraceParseError(
                    "invalid_encoding", source=name, line=line_number, note="not valid UTF-8"
                ) from None
            try:
                payload = json.loads(text)
            except (ValueError, RecursionError):
                # RecursionError, not just ValueError: a few kilobytes of nested
                # brackets is far under `max_line_bytes`, so no byte cap catches
                # a nesting bomb, and `json.loads` blows the interpreter stack
                # rather than reporting a syntax error. R11 admits exactly one
                # outcome for a line the reader cannot decode — a sanitized
                # TraceError — and "the parser could not decode this line" is
                # what `invalid_json` means. The durable fix is a depth cap in
                # IngestLimits, which is a spec amendment (see review S4).
                raise TraceParseError("invalid_json", source=name, line=line_number) from None
            if not isinstance(payload, dict):
                raise TraceParseError("not_an_object", source=name, line=line_number)
            records.append(JsonRecord(file_index=file_index, line=line_number, data=payload))

    source = SourceFile(
        name=name,
        sha256=digest.hexdigest(),
        bytes=size,
        records=len(records),
    )
    return source, tuple(records)


def load_input(paths: Sequence[Path], limits: IngestLimits) -> LoadedInput:
    """Read every named file and return provenance plus records in canonical order (R6)."""
    resolved = resolve_inputs(paths, limits)
    sources: list[SourceFile] = []
    records: list[JsonRecord] = []
    for file_index, path in enumerate(resolved):
        source, file_records = read_jsonl(
            path,
            file_index,
            limits,
            records_budget=limits.max_records - len(records),
        )
        sources.append(source)
        records.extend(file_records)
    return LoadedInput(source_files=tuple(sources), records=tuple(records))


def compute_trace_id(source_files: Sequence[SourceFile]) -> str:
    """R5: 16 hex characters over ``name:sha256`` lines, sorted by name.

    Derived from content and basenames only, so the same trace analyzed from a
    different directory, a different machine or a different argument order
    yields the same id (R47).
    """
    ordered = sorted(source_files, key=lambda item: item.name)
    payload = "\n".join(f"{item.name}:{item.sha256}" for item in ordered)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def digest_id(*parts: str) -> str:
    """16 hex characters over ``|``-joined parts — the R5 id construction."""
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def atomic_write_texts(outputs: Mapping[Path, str]) -> None:
    """Write every output atomically, or write none of them (R11).

    Each file is staged as a temporary file **in its destination directory** so
    the final step is a same-filesystem :func:`os.replace`, which is atomic; a
    failure part-way through removes every temporary file and leaves any
    pre-existing output untouched.
    """
    staged: list[tuple[str, Path]] = []
    renamed = 0
    try:
        for destination, text in outputs.items():
            directory = destination.parent if str(destination.parent) else Path()
            handle_fd, temporary = tempfile.mkstemp(
                dir=str(directory), prefix=f".{destination.name}.", suffix=".tmp"
            )
            # Recorded before the write, so a failure *during* rendering still
            # leaves the temporary file to the cleanup below rather than on disk.
            staged.append((temporary, destination))
            with os.fdopen(handle_fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            # mkstemp creates 0600; a report is meant to be readable and shared,
            # and a fixed mode keeps two machines' outputs comparable (R47).
            os.chmod(temporary, 0o644)
        for temporary, destination in staged:
            os.replace(temporary, destination)
            renamed += 1
    finally:
        for temporary, _ in staged[renamed:]:
            with contextlib.suppress(OSError):  # best effort cleanup
                os.unlink(temporary)


def atomic_write_text(destination: Path, text: str) -> None:
    """Single-file convenience over :func:`atomic_write_texts`."""
    atomic_write_texts({destination: text})


__all__ = [
    "JsonRecord",
    "LoadedInput",
    "atomic_write_text",
    "atomic_write_texts",
    "compute_trace_id",
    "digest_id",
    "load_input",
    "read_jsonl",
    "resolve_inputs",
]
