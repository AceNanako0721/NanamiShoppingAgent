import pytest
from test_protocol import INVALID_TASK_IDS

from nanami_shopping.security import Invalid, private_directory
from nanami_shopping.synthetic import SyntheticAdapter


def test_untrusted_writable_state_ancestor_is_rejected(tmp_path):
    parent = tmp_path / "shared"
    parent.mkdir(mode=0o777)
    parent.chmod(0o777)
    with pytest.raises(Invalid, match="unsafe_state_path"):
        private_directory(parent / "state")


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:1234",
        "http://127.0.0.1:80",
        "http://user@127.0.0.1:1234",
        "http://127.0.0.1:1234/",
        "https://127.0.0.1:1234",
        "http://192.168.1.1:1234",
    ],
)
def test_synthetic_origin_cannot_reach_real_merchants(origin):
    with pytest.raises(Invalid):
        SyntheticAdapter(origin)


@pytest.mark.parametrize(
    "url,method,dispatch",
    [
        ("http://127.0.0.1:1235/search?q=x", "GET", False),
        ("http://evil.test/search?q=x", "GET", False),
        ("http://127.0.0.1:1234/purchase", "POST", False),
        ("http://127.0.0.1:1234/purchase", "GET", True),
        ("http://127.0.0.1:1234/search?q=x&q=y", "GET", False),
        ("http://127.0.0.1:1234/receipt/task_" + "a" * 32, "DELETE", True),
    ],
)
def test_browser_network_guard(url, method, dispatch):
    with pytest.raises(Invalid):
        SyntheticAdapter("http://127.0.0.1:1234").allowed(url, method, dispatch)


@pytest.mark.parametrize("task_id", INVALID_TASK_IDS)
def test_direct_invalid_task_ids_do_not_echo_and_follow_output_schema(tmp_path, task_id):
    from test_protocol import assert_invalid_task_result, config

    from nanami_shopping.service import Service

    _, store, _ = config(tmp_path)
    assert_invalid_task_result(Service(store).call("shopping_get_task", {"task_id": task_id}))


def test_valid_missing_and_other_scope_task_refs_reveal_no_data(tmp_path):
    from jsonschema import Draft202012Validator
    from test_protocol import config

    from nanami_shopping.contract import TOOLS
    from nanami_shopping.service import Service
    from nanami_shopping.store import Store

    _, store, task = config(tmp_path)
    for scoped_store, ref in [
        (store, "task_" + "a" * 32),
        (Store(store.root, "other-host", "synthetic"), task),
        (Store(store.root, "host-a", "other-namespace"), task),
    ]:
        result = Service(scoped_store).call("shopping_get_task", {"task_id": ref})
        Draft202012Validator(TOOLS["shopping_get_task"]["outputSchema"]).validate(result)
        assert result["task_id"] == ref
        assert result["blocked_reason"] == "not_authorized"
        assert result["data"] == {} and result["evidence_refs"] == []
