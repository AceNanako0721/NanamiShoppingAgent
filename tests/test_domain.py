import time
from decimal import Decimal

import pytest

from nanami_shopping.domain import compare, exact_quote
from nanami_shopping.security import Invalid


def candidate(**overrides):
    return dict(
        title="SYNTHETIC Widget",
        platform="synthetic",
        sku="W-1",
        variant="blue",
        seller="test-shop",
        condition="new",
        currency="JPY",
        price="100",
        shipping="20",
        tax="0",
        points="50",
        destination="dest-test",
        eligibility=True,
        evidence="evidence-test",
        **overrides,
    )


def test_comparison_requires_explicit_equivalence_and_complete_costs():
    a = candidate()
    b = {**a, "price": "80", "shipping": None}
    with pytest.raises(Invalid, match="needs_interpretation"):
        compare([("a", a), ("b", b)], False, 1, "JPY", None)
    result = compare([("a", a), ("b", b)], True, 1, "JPY", None)
    assert result["ranking"][0]["landed"] == "120"
    assert result["ranking"][0]["points"] == "50"
    assert result["excluded"][0]["reason"] == "unknown_fees"
    with pytest.raises(Invalid, match="needs_interpretation"):
        compare([("a", a), ("b", {**a, "sku": "W-2"})], True, 1, "JPY", None)


def test_fx_is_expiring_sourced_and_includes_conversion_reserve():
    a = {**candidate(), "currency": "CNY", "price": "10.00", "shipping": "0.00", "tax": "0.00"}
    assert compare([("a", a)], True, 1, "JPY", None)["excluded"][0]["reason"] == "fx_missing"
    fx = dict(
        base="CNY",
        quote="JPY",
        rate="20.1",
        fee_rate="0.03",
        reserve="5",
        source="synthetic-rate",
        observed_at=int(time.time()),
        expires_at=int(time.time()) + 60,
    )
    result = compare([("a", a)], True, 1, "JPY", fx)
    assert Decimal(result["ranking"][0]["landed"]) == Decimal("213")
    assert result["fx"]["source"] == "synthetic-rate"
    fx["expires_at"] = 0
    assert compare([("a", a)], True, 1, "JPY", fx)["excluded"][0]["reason"] == "fx_expired"


def test_preview_binds_quantity_destination_and_pdd_specific_eligibility():
    t = dict(quantity=2, destination="dest-test", payment="pay-test", maximum="500", currency="JPY")
    q = exact_quote(candidate(), t)
    assert q["payable"] == "240"
    assert q["seller"] == "test-shop"
    assert q["quantity"] == 2
    for change in [
        dict(shipping=None),
        dict(destination="elsewhere"),
        dict(platform="pdd", eligibility=False),
    ]:
        with pytest.raises(Invalid):
            exact_quote({**candidate(), **change}, t)
    with pytest.raises(Invalid, match="budget_fx_unavailable"):
        exact_quote({**candidate(), "currency": "CNY"}, t)


def test_comparison_excludes_fees_for_a_different_destination():
    result = compare(
        [("wrong", {**candidate(), "destination": "dest-other"})],
        True,
        1,
        "JPY",
        None,
        destination="dest-test",
    )
    assert result["ranking"] == []
    assert result["excluded"] == [{"candidate_id": "wrong", "reason": "destination_unverified"}]


def test_pdd_shipping_label_cannot_replace_variant_eligibility():
    result = compare(
        [("pdd", {**candidate(), "platform": "pdd", "eligibility": False})], True, 1, "JPY", None
    )
    assert result["ranking"] == []
    assert result["excluded"][0]["reason"] == "variant_eligibility_unverified"
