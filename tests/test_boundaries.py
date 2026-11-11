import pytest

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
