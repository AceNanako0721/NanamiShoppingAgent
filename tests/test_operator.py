import json
import subprocess
import sys

from test_protocol import config


def invoke(*args):
    return subprocess.run(
        [sys.executable, "-m", "nanami_shopping.operator", *map(str, args)],
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_control_plane_creates_stable_target_and_prints_no_secrets(tmp_path):
    cfg, store, task = config(tmp_path)
    p = invoke(
        "--config",
        cfg,
        "target",
        "--goal",
        "goal",
        "--budget",
        "pool",
        "--maximum",
        "1000",
        "--currency",
        "JPY",
        "--destination",
        "dest-test",
        "--payment",
        "pay-test",
        "--quantity",
        1,
    )
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout) == {"task_id": task}
    q = store.quote(task, dict(payable="100", currency="JPY", platform="synthetic"), 60)
    p = invoke("--config", cfg, "grant-synthetic", "--quote", q)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout) == {"synthetic_grant_issued": True}
    q = store.quote(task, dict(payable="100", currency="JPY", platform="amazon"), 60)
    p = invoke("--config", cfg, "grant-synthetic", "--quote", q)
    assert p.returncode == 2
    assert "real_checkout_disabled" in p.stderr
