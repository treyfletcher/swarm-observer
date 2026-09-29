"""Record builders for the ingestion tests. Not a test module.

Every test in this suite drives the product through real files on disk, because
that is what the reader and the adapter actually consume: there is no seam
between "a parsed record" and "a line of JSONL" worth mocking, and a builder
that produced already-validated objects would test the model rather than the
adapter.

The builders below emit the *observed* Claude Code shape (the spec's A1
inspection): camelCase record keys, a ``message`` object carrying ``id``,
``model``, ``content`` blocks and a ``usage`` block with the
``cache_creation.{ephemeral_5m,ephemeral_1h}_input_tokens`` breakdown. Keeping
that shape in one place means a test that needs a hostile variant overrides one
key rather than restating the format.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

#: A timestamp every builder defaults to, so a test that does not care about
#: timing does not have to invent one.
BASE_TIME = "2026-09-09T10:00:00.000Z"


def usage(
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read: int = 0,
    cache_5m: int = 0,
    cache_1h: int = 0,
) -> dict[str, Any]:
    """A ``message.usage`` block in the observed shape."""
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_5m + cache_1h,
        "cache_creation": {
            "ephemeral_5m_input_tokens": cache_5m,
            "ephemeral_1h_input_tokens": cache_1h,
        },
    }


def text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def thinking_block(text: str) -> dict[str, Any]:
    return {"type": "thinking", "thinking": text}


def tool_use_block(block_id: str, name: str, tool_input: Any) -> dict[str, Any]:
    return {"type": "tool_use", "id": block_id, "name": name, "input": tool_input}


def tool_result_block(tool_use_id: str, content: Any, *, is_error: bool = False) -> dict[str, Any]:
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
        "is_error": is_error,
    }


def assistant(
    uuid: str,
    *,
    timestamp: str = BASE_TIME,
    message_id: str | None = "msg-1",
    request_id: str | None = "req-1",
    session_id: str = "sess-1",
    agent_id: str = "root",
    model: str | None = "claude-sonnet-4-5-20250929",
    content: Iterable[Mapping[str, Any]] = (),
    usage_block: Mapping[str, Any] | None = None,
    stop_reason: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """One assistant record — a streamed fragment of one API response."""
    message: dict[str, Any] = {"role": "assistant", "content": list(content)}
    if message_id is not None:
        message["id"] = message_id
    if model is not None:
        message["model"] = model
    if usage_block is not None:
        message["usage"] = dict(usage_block)
    if stop_reason is not None:
        message["stop_reason"] = stop_reason
    record: dict[str, Any] = {
        "type": "assistant",
        "uuid": uuid,
        "timestamp": timestamp,
        "sessionId": session_id,
        "agentId": agent_id,
        "message": message,
    }
    if request_id is not None:
        record["requestId"] = request_id
    record.update(extra)
    return record


def user(
    uuid: str,
    *,
    timestamp: str = BASE_TIME,
    content: Any = "hello",
    agent_id: str = "root",
    session_id: str = "sess-1",
    **extra: Any,
) -> dict[str, Any]:
    """One user record; ``content`` may be a string or a list of blocks."""
    record: dict[str, Any] = {
        "type": "user",
        "uuid": uuid,
        "timestamp": timestamp,
        "sessionId": session_id,
        "agentId": agent_id,
        "message": {"role": "user", "content": content},
    }
    record.update(extra)
    return record


def system(
    uuid: str,
    *,
    timestamp: str = BASE_TIME,
    subtype: str | None = None,
    content: Any = "system note",
    agent_id: str = "root",
    **extra: Any,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "type": "system",
        "uuid": uuid,
        "timestamp": timestamp,
        "agentId": agent_id,
        "message": {"role": "system", "content": content},
    }
    if subtype is not None:
        record["subtype"] = subtype
    record.update(extra)
    return record


def attachment(uuid: str, *, timestamp: str = BASE_TIME, **extra: Any) -> dict[str, Any]:
    record: dict[str, Any] = {"type": "attachment", "uuid": uuid, "timestamp": timestamp}
    record.update(extra)
    return record


def api_error(
    uuid: str,
    *,
    timestamp: str = BASE_TIME,
    error: str = "rate_limit_error",
    status: str = "429 Too Many Requests",
    model: str = "<synthetic>",
    message_id: str | None = "msg-1",
    request_id: str | None = "req-1",
    **extra: Any,
) -> dict[str, Any]:
    """An ``isApiErrorMessage`` assistant record (the spec's synthetic call)."""
    record = assistant(
        uuid,
        timestamp=timestamp,
        model=model,
        content=[text_block("API Error")],
        message_id=message_id,
        request_id=request_id,
    )
    record["isApiErrorMessage"] = True
    record["error"] = error
    record["apiErrorStatus"] = status
    record.update(extra)
    return record


def write_jsonl(path: Path, records: Iterable[Mapping[str, Any]]) -> Path:
    """Write ``records`` as JSONL and return the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )
    return path


def write_trace(
    tmp_path: Path, records: Iterable[Mapping[str, Any]], name: str = "agent-1"
) -> Path:
    """The common case: one trace file called ``agent-1.jsonl`` under ``tmp_path``."""
    return write_jsonl(tmp_path / f"{name}.jsonl", records)


__all__ = [
    "BASE_TIME",
    "api_error",
    "assistant",
    "attachment",
    "system",
    "text_block",
    "thinking_block",
    "tool_result_block",
    "tool_use_block",
    "usage",
    "user",
    "write_jsonl",
    "write_trace",
]
