import os

import pytest

from nanami_shopping.security import Invalid, money, private_directory, strict_json


@pytest.mark.parametrize(
    "raw",
    [
        '{"a":1,"a":2}',
        '{"a":NaN}',
        '{"a":Infinity}',
        "[" * 40 + "0" + "]" * 40,
        '"' + "x" * 70000 + '"',
    ],
)
def test_untrusted_json_is_bounded_and_unambiguous(raw):
    with pytest.raises(Invalid):
        strict_json(raw)


def test_json_and_money_are_exact():
    assert strict_json('{"value":"100"}') == {"value": "100"}
    assert str(money("120", "JPY")) == "120"
    assert str(money("1.20", "CNY")) == "1.20"
    for amount, currency in [
        ("1.1", "JPY"),
        ("NaN", "JPY"),
        ("1e3", "JPY"),
        (True, "JPY"),
        ("-1", "JPY"),
        ("1", "USD"),
    ]:
        with pytest.raises(Invalid):
            money(amount, currency)


def test_private_state_rejects_permissions_symlinks_and_checkouts(tmp_path):
    root = tmp_path / "state"
    private_directory(root)
    assert root.stat().st_mode & 0o777 == 0o700
    os.chmod(root, 0o755)
    with pytest.raises(Invalid):
        private_directory(root)
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(Invalid):
        private_directory(link)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: anything")
    with pytest.raises(Invalid):
        private_directory(repo / "state")


def test_private_state_files_reject_symlinks_and_hardlinks(tmp_path):
    from nanami_shopping.security import private_file

    source = tmp_path / "source"
    source.write_text("synthetic")
    source.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(source)
    with pytest.raises(Invalid):
        private_file(link)
    hard = tmp_path / "hard"
    os.link(source, hard)
    with pytest.raises(Invalid):
        private_file(hard)
