"""Deterministic cost arithmetic; no model, points discount, or guessed fees."""

import re
import time
from decimal import ROUND_CEILING, Decimal

from .security import Invalid, money


def quantity(value):
    if type(value) is not int or not 1 <= value <= 100:
        raise Invalid()
    return value


def landed(candidate, count):
    quantity(count)
    if any(candidate.get(key) is None for key in ("price", "shipping", "tax")):
        raise Invalid("unknown_fees")
    # Fee evidence explicitly describes one item; quantity multiplies all charges conservatively.
    return (
        sum(
            (money(candidate[k], candidate["currency"]) for k in ("price", "shipping", "tax")),
            Decimal(0),
        )
        * count
    )


def validate_fx(fx):
    if fx is None:
        raise Invalid("fx_missing")
    if (
        not fx.get("source")
        or type(fx.get("observed_at")) is not int
        or type(fx.get("expires_at")) is not int
    ):
        raise Invalid("fx_missing")
    if fx["expires_at"] <= time.time() or fx["observed_at"] > time.time():
        raise Invalid("fx_expired")
    for key in ("rate", "fee_rate", "reserve"):
        if not isinstance(fx.get(key), str) or not re.fullmatch(
            r"[0-9]{1,8}(\.[0-9]{1,8})?", fx[key]
        ):
            raise Invalid("fx_invalid")
    if Decimal(fx["rate"]) <= 0 or Decimal(fx["fee_rate"]) > 1:
        raise Invalid("fx_invalid")
    money(fx["reserve"], fx["quote"])
    return fx


def compare(candidates, confirmed, count, currency, fx, destination=None):
    quantity(count)
    if confirmed is not True or not candidates:
        raise Invalid("needs_interpretation")
    fingerprints = {tuple(c.get(k) for k in ("sku", "variant", "condition")) for _, c in candidates}
    if len(fingerprints) != 1 or any(v is None or v == "" for v in next(iter(fingerprints))):
        raise Invalid("needs_interpretation")
    ranking, excluded = [], []
    used_fx = None
    for ref, candidate in candidates:
        try:
            if destination is not None and candidate.get("destination") != destination:
                raise Invalid("destination_unverified")
            if candidate.get("platform") == "pdd" and candidate.get("eligibility") is not True:
                raise Invalid("variant_eligibility_unverified")
            total = landed(candidate, count)
            if candidate["currency"] != currency:
                valid = validate_fx(fx)
                if (valid["base"], valid["quote"]) != (candidate["currency"], currency):
                    raise Invalid("fx_missing")
                total = total * Decimal(valid["rate"]) * (1 + Decimal(valid["fee_rate"])) + Decimal(
                    valid["reserve"]
                )
                total = total.quantize(
                    Decimal("1") if currency == "JPY" else Decimal(".01"), rounding=ROUND_CEILING
                )
                used_fx = valid
            ranking.append(
                dict(
                    candidate_id=ref,
                    landed=str(total),
                    currency=currency,
                    points=candidate.get("points"),
                    quantity=count,
                )
            )
        except Invalid as exc:
            excluded.append(dict(candidate_id=ref, reason=exc.code))
    ranking.sort(key=lambda c: Decimal(c["landed"]))
    return dict(
        ranking=ranking,
        excluded=excluded,
        fx=used_fx,
        assumption="FX estimate includes conversion fee and reserve; card settlement may differ.",
    )


def exact_quote(candidate, target):
    if candidate["currency"] != target["currency"]:
        raise Invalid("budget_fx_unavailable")
    if candidate.get("destination") != target["destination"]:
        raise Invalid("destination_unverified")
    if candidate.get("eligibility") is not True:
        raise Invalid("variant_eligibility_unverified")
    for key in ("seller", "sku", "variant", "condition", "evidence"):
        if not candidate.get(key):
            raise Invalid("checkout_evidence_missing")
    amount = landed(candidate, target["quantity"])
    if amount > money(target["maximum"], target["currency"]):
        raise Invalid("target_budget_exceeded")
    return {
        k: candidate[k] for k in ("platform", "seller", "sku", "variant", "condition", "currency")
    } | dict(
        payable=str(amount),
        quantity=target["quantity"],
        destination=target["destination"],
        payment=target["payment"],
        price=candidate["price"],
        shipping=candidate["shipping"],
        tax=candidate["tax"],
        evidence=candidate["evidence"],
    )
