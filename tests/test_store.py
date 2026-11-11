import concurrent.futures
import multiprocessing
import os

import pytest

from nanami_shopping.security import Invalid
from nanami_shopping.store import Store


def make_store(path, who="host-a", namespace="synthetic"):
    return Store(path, who, namespace)


def claim_once(args):
    path, target, quote = args
    try:
        make_store(path).claim(target, quote)
        return True
    except Invalid:
        return False


def setup_target(store, key="goal", maximum="1000"):
    store.set_budget("pool", "2000", "JPY")
    return store.create_target(key, "pool", maximum, "JPY", "dest-test", "pay-test", 1)


def test_target_is_durable_and_scoped(tmp_path):
    s = make_store(tmp_path / "state")
    target = setup_target(s)
    assert setup_target(s) == target
    assert make_store(tmp_path / "state").task(target)["destination"] == "dest-test"
    with pytest.raises(Invalid, match="not_authorized"):
        make_store(tmp_path / "state", "host-b").task(target)
    with pytest.raises(Invalid, match="not_authorized"):
        make_store(tmp_path / "state", namespace="other").task(target)
    with pytest.raises(Invalid):
        s.create_target("goal", "pool", "999", "JPY", "dest-test", "pay-test", 1)
    assert (tmp_path / "state" / "shopping.sqlite3").stat().st_mode & 0o777 == 0o600


def test_atomic_target_lock_and_budget_survive_restarts(tmp_path):
    s = make_store(tmp_path / "state")
    target = setup_target(s)
    q = s.quote(target, {"payable": "700", "currency": "JPY", "candidate": "one"}, 120)
    with pytest.raises(Invalid, match="needs_owner_approval"):
        s.claim(target, q)
    s.grant(q)
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        assert sum(pool.map(claim_once, [(s.root, target, q)] * 8)) == 1
    s.uncertain(target)
    other = s.quote(
        target,
        {"payable": "600", "currency": "JPY", "candidate": "different-platform-url-seller-variant"},
        120,
    )
    s.grant(other)
    with pytest.raises(Invalid, match="purchase_locked"):
        make_store(s.root).claim(target, other)
    assert s.reserved("pool") == "700"
    s.cancel(target)
    assert s.reserved("pool") == "700"
    assert s.task(target)["state"] == "uncertain"


def test_process_claim_and_concurrent_budget(tmp_path):
    s = make_store(tmp_path / "state")
    first = setup_target(s, "one", "2000")
    second = setup_target(s, "two", "2000")
    quotes = [s.quote(t, {"payable": "1500", "currency": "JPY"}, 120) for t in [first, second]]
    for q in quotes:
        s.grant(q)
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(4) as pool:
        results = pool.map(
            claim_once, [(s.root, first, quotes[0])] * 4 + [(s.root, second, quotes[1])] * 4
        )
    assert sum(results) == 1
    assert s.reserved("pool") == "1500"


def test_unsafe_database_file_is_rejected(tmp_path):
    s = make_store(tmp_path / "state")
    os.chmod(s.root / "shopping.sqlite3", 0o644)
    with pytest.raises(Invalid):
        make_store(s.root)


def test_cancellation_is_latched_across_late_read_worker_completion(tmp_path):
    s = make_store(tmp_path / "state")
    task = setup_target(s)
    s.state(task, "searching")
    s.cancel(task)
    s.state(task, "candidates_ready")
    assert s.task(task)["state"] == "cancelled"
    q = s.quote(task, dict(payable="100", currency="JPY"), 60)
    s.grant(q)
    with pytest.raises(Invalid, match="task_cancelled"):
        s.claim(task, q)
