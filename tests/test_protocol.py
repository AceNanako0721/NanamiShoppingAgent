import json
import os
import subprocess
import sys
from pathlib import Path

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

from nanami_shopping.store import Store


def config(tmp_path, principal="host-a"):
    root = tmp_path / "state"
    store = Store(root, "host-a", "synthetic")
    store.set_budget("pool", "2000", "JPY")
    task = store.create_target("goal", "pool", "1000", "JPY", "dest-test", "pay-test", 1)
    cfg = tmp_path / (principal + ".json")
    cfg.write_text(
        json.dumps(dict(state_dir=str(root), principal=principal, namespace="synthetic"))
    )
    cfg.chmod(0o600)
    return cfg, store, task


def params(cfg):
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "nanami_shopping.server", "--config", str(cfg)],
        env={"PATH": os.environ["PATH"]},
    )


async def test_sdk_real_stdio_initialize_discover_call_and_resume(tmp_path):
    cfg, _, task = config(tmp_path)
    async with Client(params(cfg)) as client:
        tools = await client.list_tools()
        assert len(tools.tools) == 9
        snapshot = [
            t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in tools.tools
        ]
        assert snapshot == json.loads((Path(__file__).parent / "snapshots/tools.json").read_text())
        result = await client.call_tool("shopping_get_task", {"task_id": task})
        data = result.structured_content
        assert data["task_id"] == task
        assert data["state"] == "created"
        normalized = json.loads(json.dumps(data).replace(task, "<task>"))
        assert normalized == json.loads(
            (Path(__file__).parent / "snapshots/task-result.json").read_text()
        )
        assert set(data) == {
            "schema_version",
            "task_id",
            "state",
            "summary",
            "data",
            "evidence_refs",
            "blocked_reason",
            "allowed_next_actions",
            "retry",
        }
        invalid = await client.call_tool(
            "shopping_preview_purchase", {"task_id": task, "candidate_id": "nope", "quantity": True}
        )
        assert invalid.structured_content["blocked_reason"] == "invalid_input"
    async with Client(params(cfg)) as client:
        assert (
            await client.call_tool("shopping_get_task", {"task_id": task})
        ).structured_content == data
    other, _, _ = config(tmp_path, "host-b")
    async with Client(params(other)) as client:
        result = await client.call_tool("shopping_get_task", {"task_id": task})
        assert result.structured_content["blocked_reason"] == "not_authorized"
    store = Store(tmp_path / "state", "host-a", "synthetic")
    store.authorize(task, "host-b")
    async with Client(params(other)) as client:
        assert (
            await client.call_tool("shopping_get_task", {"task_id": task})
        ).structured_content == data
    isolated = json.loads(other.read_text())
    isolated["namespace"] = "different"
    other.write_text(json.dumps(isolated))
    async with Client(params(other)) as client:
        assert (await client.call_tool("shopping_get_task", {"task_id": task})).structured_content[
            "blocked_reason"
        ] == "not_authorized"


def test_independent_wire_client_and_duplicate_json_rejection(tmp_path):
    cfg, _, task = config(tmp_path)
    p = subprocess.run(
        [sys.executable, "scripts/wire_smoke.py", "--config", str(cfg), "--task", task],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert p.returncode == 0, p.stderr
    report = json.loads(p.stdout)
    assert report == {
        "initialize": True,
        "tools": 9,
        "task_state": "created",
        "duplicate_rejected": True,
    }


INVALID_TASK_IDS = [
    {"password": "SYNTHETIC_SECRET_MARKER"},
    ["SYNTHETIC_SECRET_MARKER"],
    True,
    False,
    7,
    1.5,
    "SYNTHETIC_SECRET_MARKER" * 20,
    "SYNTHETIC_SECRET_MARKER",
    "task_" + "a" * 31,
    "task_" + "a" * 32 + "\n",
]


def assert_invalid_task_result(result):
    from jsonschema import Draft202012Validator

    from nanami_shopping.contract import TOOLS

    Draft202012Validator(TOOLS["shopping_get_task"]["outputSchema"]).validate(result)
    assert result["task_id"] is None
    assert result["blocked_reason"] == "invalid_input"
    assert "SYNTHETIC_SECRET_MARKER" not in json.dumps(result)
    assert result["data"] == {} and result["evidence_refs"] == []


async def test_sdk_invalid_task_ids_do_not_echo_and_follow_output_schema(tmp_path):
    cfg, _, _ = config(tmp_path)
    async with Client(params(cfg)) as client:
        for task_id in INVALID_TASK_IDS:
            result = await client.call_tool("shopping_get_task", {"task_id": task_id})
            assert_invalid_task_result(result.structured_content)
            assert all("SYNTHETIC_SECRET_MARKER" not in c.text for c in result.content)
        for task_id in ["task_" + "a" * 32]:
            result = (
                await client.call_tool("shopping_get_task", {"task_id": task_id})
            ).structured_content
            assert result["task_id"] == task_id
            assert result["blocked_reason"] == "not_authorized"
            assert result["data"] == {} and result["evidence_refs"] == []
