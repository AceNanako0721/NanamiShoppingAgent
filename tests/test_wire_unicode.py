"""Bounded stdlib wire probes against the real MCP subprocess, with clean EOF."""

import json
import os
import select
import subprocess
import sys
import time
from contextlib import contextmanager

import pytest
from test_protocol import config


@contextmanager
def wire_connection(tmp_path):
    cfg, _, _ = config(tmp_path)
    with (tmp_path / "synthetic-stderr.log").open("wb+") as errors:
        process = subprocess.Popen(
            [sys.executable, "-m", "nanami_shopping.server", "--config", str(cfg)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=errors,
            bufsize=0,
        )

        def send(frame, ensure_ascii=True):
            raw = json.dumps(frame, ensure_ascii=ensure_ascii).encode("utf-8") + b"\n"
            process.stdin.write(raw)

        def receive():
            deadline, raw = time.monotonic() + 5, bytearray()
            while not raw.endswith(b"\n"):
                left = deadline - time.monotonic()
                assert left > 0 and select.select([process.stdout], [], [], max(0, left))[0], (
                    "MCP frame received no response within 5 seconds"
                )
                chunk = os.read(process.stdout.fileno(), 1)
                assert chunk, "MCP writer closed before responding"
                raw.extend(chunk)
                assert len(raw) <= 65536
            return json.loads(raw)

        try:
            send(
                dict(
                    jsonrpc="2.0",
                    id=1,
                    method="initialize",
                    params=dict(
                        protocolVersion="2025-11-25",
                        capabilities={},
                        clientInfo=dict(name="SYNTHETIC unicode regression", version="1"),
                    ),
                )
            )
            assert receive()["id"] == 1
            send(dict(jsonrpc="2.0", method="notifications/initialized"))
            yield send, receive
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()
        errors.seek(0)
        assert process.returncode == 0, errors.read().decode("utf-8", errors="replace")


BAD_UNICODE_FRAMES = [
    dict(
        jsonrpc="2.0",
        id=2,
        method="tools/call",
        params=dict(
            name="shopping_get_task",
            arguments={"task_id": surrogate},
        ),
    )
    for surrogate in ["\ud800", "\udfff"]
] + [
    dict(
        jsonrpc="2.0",
        id=2,
        method="tools/call",
        params=dict(
            name="shopping_capabilities",
            arguments={"nested": [{"\ud800": "SYNTHETIC"}]},
        ),
    ),
    dict(
        jsonrpc="2.0",
        id=2,
        method="tools/call",
        params=dict(
            name="shopping_capabilities",
            arguments={"nested": [{"SYNTHETIC": "\udfff"}]},
        ),
    ),
    dict(
        jsonrpc="2.0",
        id="\ud800",
        method="tools/call",
        params=dict(
            name="shopping_capabilities",
            arguments={},
        ),
    ),
    dict(jsonrpc="2.0", id=2, method="\udfff"),
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "\ud800": "SYNTHETIC"},
]


@pytest.mark.parametrize(
    "frame",
    BAD_UNICODE_FRAMES,
    ids=[
        "high-value",
        "low-value",
        "nested-key",
        "nested-value",
        "outer-id",
        "outer-method",
        "outer-key",
    ],
)
def test_escaped_surrogates_are_rejected_and_same_wire_connection_recovers(tmp_path, frame):
    with wire_connection(tmp_path) as (send, receive):
        send(frame)
        rejected = receive()
        assert rejected == dict(
            jsonrpc="2.0", id=None, error=dict(code=-32700, message="Invalid JSON frame")
        )
        assert "SYNTHETIC" not in json.dumps(rejected)
        send(
            dict(
                jsonrpc="2.0",
                id=3,
                method="tools/call",
                params=dict(
                    name="shopping_capabilities",
                    arguments={},
                ),
            )
        )
        recovered = receive()
        assert recovered["id"] == 3
        assert recovered["result"]["structuredContent"]["data"]["transport"] == "stdio"


@pytest.mark.parametrize("ensure_ascii", [True, False], ids=["escaped-pair", "literal-non-bmp"])
def test_valid_non_bmp_strings_and_keys_survive_wire(tmp_path, ensure_ascii):
    with wire_connection(tmp_path) as (send, receive):
        send(
            dict(
                jsonrpc="2.0",
                id="SYNTHETIC_😀",
                method="tools/call",
                params=dict(
                    name="shopping_capabilities",
                    arguments={"nested": [{"SYNTHETIC_😀": "😀"}]},
                ),
            ),
            ensure_ascii=ensure_ascii,
        )
        result = receive()
        assert result["id"] == "SYNTHETIC_😀"
        assert result["result"]["structuredContent"]["blocked_reason"] == "invalid_input"
        send(
            dict(
                jsonrpc="2.0",
                id="😀",
                method="tools/call",
                params=dict(
                    name="shopping_capabilities",
                    arguments={},
                ),
            ),
            ensure_ascii=ensure_ascii,
        )
        assert receive()["result"]["structuredContent"]["data"]["transport"] == "stdio"
