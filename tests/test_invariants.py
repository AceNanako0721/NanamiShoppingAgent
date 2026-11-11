import pytest
from test_store import make_store, setup_target

from nanami_shopping.security import Invalid
from nanami_shopping.service import Service


def test_approval_binds_every_exact_quote_field_and_expiration(tmp_path):
    s = make_store(tmp_path / "state")
    task = setup_target(s)
    base = dict(
        payable="100",
        currency="JPY",
        seller="one",
        sku="one",
        variant="blue",
        condition="new",
        destination="dest-test",
        payment="pay-test",
        quantity=1,
    )
    first = s.quote(task, base, 60)
    s.grant(first)
    for key, value in dict(
        payable="101",
        seller="two",
        sku="two",
        variant="red",
        condition="used",
        destination="different",
        payment="different",
        quantity=2,
    ).items():
        q = s.quote(task, base | {key: value}, 60)
        with pytest.raises(Invalid, match="needs_owner_approval"):
            s.claim(task, q)
    expired = s.quote(task, base, -1)
    with pytest.raises(Invalid, match="quote_expired"):
        s.grant(expired)
    s.claim(task, first)


def test_uncertain_cannot_escape_via_platform_alias_seller_label_or_new_quote(tmp_path):
    s = make_store(tmp_path / "state")
    task = setup_target(s)
    base = dict(
        payable="100",
        currency="JPY",
        platform="amazon",
        url="https://www.amazon.co.jp/dp/B000000001",
        seller="one",
        label="one",
    )
    q = s.quote(task, base, 60)
    s.grant(q)
    s.claim(task, q)
    s.uncertain(task)
    alternatives = [
        dict(url="https://amazon.co.jp/dp/B000000001"),
        dict(url="https://www.amazon.co.jp/gp/product/B000000001"),
        dict(seller="other"),
        dict(label="other"),
        dict(platform="rakuten", url="https://item.rakuten.co.jp/test/item/"),
    ]
    for change in alternatives:
        q = s.quote(task, base | change, 60)
        s.grant(q)
        with pytest.raises(Invalid, match="purchase_locked"):
            s.claim(task, q)
    assert s.reserved("pool") == "100"
    assert setup_target(s) == task


def test_budget_excess_unknown_fx_cancel_and_no_model_approval(tmp_path):
    s = make_store(tmp_path / "state")
    task = setup_target(s)
    service = Service(s)
    assert (
        service.call(
            "shopping_commit_purchase",
            dict(task_id=task, quote_id="quote_" + "a" * 32, approved=True),
        )["blocked_reason"]
        == "invalid_input"
    )
    for payload, expected in [
        (dict(payable="1001", currency="JPY"), "target_budget_exceeded"),
        (dict(payable="1.00", currency="CNY"), "budget_fx_unavailable"),
    ]:
        q = s.quote(task, payload, 60)
        s.grant(q)
        with pytest.raises(Invalid, match=expected):
            s.claim(task, q)
    assert s.reserved("pool") == "0"
    s.cancel(task)
    q = s.quote(task, dict(payable="1", currency="JPY"), 60)
    s.grant(q)
    with pytest.raises(Invalid, match="task_cancelled"):
        s.claim(task, q)


def test_explicit_multi_principal_authorization_and_namespace_isolation(tmp_path):
    s = make_store(tmp_path / "state")
    task = setup_target(s)
    s.authorize(task, "host-b")
    assert make_store(s.root, "host-b").task(task)["task_id"] == task
    with pytest.raises(Invalid, match="not_authorized"):
        make_store(s.root, "host-b", "different").task(task)


def test_unverified_receipt_does_not_release_claim(tmp_path):
    from nanami_shopping.store import digest
    from nanami_shopping.synthetic import SyntheticAdapter

    adapter = SyntheticAdapter("http://127.0.0.1:12345")
    quote = dict(
        synthetic_origin=adapter.origin,
        payable="100",
        currency="JPY",
        quantity=1,
        sku="A",
        variant="blue",
        seller="S",
        condition="new",
    )
    receipt = dict(
        synthetic=True,
        task_id="task-test",
        quote_hash=digest(quote),
        **{
            k: quote[k]
            for k in ["payable", "currency", "quantity", "sku", "variant", "seller", "condition"]
        },
    )
    adapter.validate_receipt("task-test", quote, receipt)
    for changed in [
        dict(quote_hash="wrong"),
        dict(task_id="other"),
        dict(payable="101"),
        dict(quantity=True),
        dict(synthetic=False),
    ]:
        with pytest.raises(Invalid, match="receipt_mismatch"):
            adapter.validate_receipt("task-test", quote, receipt | changed)
