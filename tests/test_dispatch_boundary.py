"""Real Chromium requests; every network route is fulfilled or aborted locally.

Route.fetch is intercepted in-process, so a request counts as released only when the production
guard asks to fetch it; nothing reaches a socket and Route.continue_ must never be used.
"""

import html
import json
import time
from urllib.parse import parse_qsl, urlsplit

import pytest
from playwright.sync_api import Browser, Route
from test_domain import candidate
from test_store import make_store, setup_target

from nanami_shopping.domain import exact_quote
from nanami_shopping.security import Invalid
from nanami_shopping.service import Service
from nanami_shopping.store import digest
from nanami_shopping.synthetic import SyntheticAdapter

pytestmark = pytest.mark.browser


def intercepted_purchase(tmp_path, monkeypatch, mutation="normal", ttl=120):
    store = make_store(tmp_path / "state")
    task = setup_target(store)
    adapter = SyntheticAdapter("http://127.0.0.1:12345")
    product = candidate()
    evidence = product.pop("evidence")
    quote = exact_quote({**product, "evidence": evidence}, store.task(task))
    quote.update(url=adapter.origin + "/product/W-1", synthetic_origin=adapter.origin)
    quote_id = store.quote(task, quote, ttl)
    quote = store.get(task, quote_id, "quote")
    store.grant(quote_id)
    events = {"created": [], "continued": [], "aborted": [], "reviewed": [], "passthrough": []}
    events["approved_at"] = time.time()
    events["expires_at"] = quote["expires_at"]
    original_context = Browser.new_context
    original_abort = Route.abort
    original_fulfill = Route.fulfill
    original_product = adapter._product

    def record(request):
        return dict(url=request.url, method=request.method, body=request.post_data, at=time.time())

    def new_context(browser, *args, **kwargs):
        context = original_context(browser, *args, **kwargs)
        # A fallback sink also aborts anything that misses the production guard.
        context.route("**/*", lambda route: route.abort())
        context.on("request", lambda request: events["created"].append(record(request)))
        return context

    def abort(route, *args, **kwargs):
        events["aborted"].append(record(route.request))
        return original_abort(route, *args, **kwargs)

    def continue_unguarded(route, *args, **kwargs):
        events["passthrough"].append(record(route.request))
        return original_abort(route)

    def fulfill(route, *args, response=None, **kwargs):
        if isinstance(response, LocalResponse):
            return original_fulfill(route, **response.fields)
        return original_fulfill(route, *args, response=response, **kwargs)

    def fetch_locally(route, *args, **kwargs):
        assert kwargs.get("max_redirects") == 0, "guard must not let fetch follow redirects"
        request = route.request
        events["continued"].append(record(request))
        if request.method == "GET" and urlsplit(request.url).path == "/checkout":
            fields = parse_qsl(urlsplit(request.url).query)
            button = ""
            action, method, enctype = "/purchase", "post", "application/x-www-form-urlencoded"
            if mutation == "quantity":
                fields = [(k, "100" if k == "quantity" else v) for k, v in fields]
            elif mutation == "task":
                fields = [(k, "task_" + "b" * 32 if k == "task" else v) for k, v in fields]
            elif mutation == "hash":
                fields = [(k, v[:16] if k == "hash" else v) for k, v in fields]
            elif mutation == "duplicate":
                fields.append(("quantity", "100"))
            elif mutation == "extra":
                fields.append(("coupon", "SYNTHETIC_EXTRA"))
            elif mutation == "missing":
                fields = [(k, v) for k, v in fields if k != "hash"]
            elif mutation == "formaction":
                button = 'formaction="/search?q=SYNTHETIC"'
            elif mutation == "formmethod":
                button = 'formmethod="get"'
                action = "/checkout"
            elif mutation == "formenctype":
                button = 'formenctype="text/plain"'
            elif mutation == "multipart":
                enctype = "multipart/form-data"
            elif mutation == "origin":
                button = 'formaction="http://127.0.0.1:12346/purchase"'
            shown, style = product, ""
            if mutation == "subresource_changed":
                # A blocked non-essential image must not mask the later review failure.
                shown = {**product, "price": "101"}
                style = '<img src="/blocked-pixel.png" alt="SYNTHETIC">'
            if mutation == "animation":
                style = """<style>@keyframes moving {from {transform:translateX(0)}
                    to {transform:translateX(80px)}}
                    button {animation:moving 3s linear}</style>"""
            inputs = "".join(
                f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">'
                for k, v in fields
            )
            body = (
                "<h1>SYNTHETIC — no real goods</h1>"
                f'<div data-product-json="{html.escape(json.dumps(shown), quote=True)}"></div>'
                f'{style}<form action="{action}" method="{method}" enctype="{enctype}">{inputs}'
                f'<button id="synthetic-final-submit" {button}>SYNTHETIC purchase</button></form>'
            )
            return LocalResponse(status=200, content_type="text/html", body=body)
        if request.method == "POST":
            receipt = dict(
                synthetic=True,
                task_id=task,
                quote_hash=digest(quote),
                **{
                    k: quote[k]
                    for k in (
                        "payable",
                        "currency",
                        "quantity",
                        "sku",
                        "variant",
                        "seller",
                        "condition",
                    )
                },
            )
            return LocalResponse(
                status=200, content_type="application/json", body=json.dumps(receipt)
            )
        return LocalResponse(status=404, body="SYNTHETIC blocked fixture")

    def reviewed(*args, **kwargs):
        result = original_product(*args, **kwargs)
        events["reviewed"].append(time.time())
        return result

    monkeypatch.setattr(Browser, "new_context", new_context)
    monkeypatch.setattr(Route, "continue_", continue_unguarded)
    monkeypatch.setattr(Route, "fetch", fetch_locally)
    monkeypatch.setattr(Route, "fulfill", fulfill)
    monkeypatch.setattr(Route, "abort", abort)
    monkeypatch.setattr(adapter, "_product", reviewed)
    result = Service(store, adapter).call(
        "shopping_commit_purchase", dict(task_id=task, quote_id=quote_id)
    )
    # These are synthetic timestamps and generated refs, suitable for private test evidence.
    print(json.dumps(dict(scenario=mutation, events=events, result=result), ensure_ascii=True))
    assert events["passthrough"] == [], "a route bypassed the redirect-checking fetch"
    return store, task, quote_id, result, events


class LocalResponse:
    """Stands in for the APIResponse returned by Route.fetch; fulfilled without a network."""

    def __init__(self, **fields):
        self.fields = fields
        self.status = fields["status"]


def posts(events, kind):
    return [request for request in events[kind] if request["method"] == "POST"]


def assert_conservative_claim(store, task, quote_id):
    assert store.task(task)["state"] == "uncertain"
    assert store.order(task)["state"] == "uncertain"
    assert store.reserved("pool") == "120"
    # A restart cannot release the target-wide lock or reservation.
    restarted = make_store(store.root)
    with pytest.raises(Invalid, match="purchase_locked"):
        restarted.claim(task, quote_id)
    assert restarted.reserved("pool") == "120"


@pytest.mark.parametrize(
    "mutation",
    [
        "quantity",
        "task",
        "hash",
        "duplicate",
        "extra",
        "missing",
        "formaction",
        "formmethod",
        "formenctype",
        "multipart",
        "origin",
    ],
)
def test_final_request_must_match_approved_purchase(tmp_path, monkeypatch, mutation):
    store, task, quote_id, result, events = intercepted_purchase(tmp_path, monkeypatch, mutation)
    assert events["reviewed"], "real Chromium must finish the unchanged product review"
    assert len(events["created"]) == 2
    assert len(posts(events, "created")) == (0 if mutation == "formmethod" else 1)
    assert len(posts(events, "continued")) == 0, "unapproved POST passed route.continue_"
    assert len(events["aborted"]) == 1
    # GET form overrides must not escape through the read-only navigation allowlist either.
    assert len(events["continued"]) == 1, "only the checkout document may be released"
    assert result["state"] == "uncertain"
    assert_conservative_claim(store, task, quote_id)


def test_valid_final_request_is_released_once(tmp_path, monkeypatch):
    store, task, _, result, events = intercepted_purchase(tmp_path, monkeypatch)
    assert result["state"] == "completed", result
    assert len(posts(events, "created")) == len(posts(events, "continued")) == 1
    assert dict(parse_qsl(posts(events, "continued")[0]["body"])) == dict(
        task=task, hash=result["data"]["receipt"]["quote_hash"], quantity="1"
    )
    assert store.reserved("pool") == "120"


def test_aborted_subresource_does_not_mask_review_change(tmp_path, monkeypatch):
    store, task, quote_id, result, events = intercepted_purchase(
        tmp_path, monkeypatch, "subresource_changed"
    )
    assert any(r["url"].endswith("/blocked-pixel.png") for r in events["aborted"])
    assert posts(events, "created") == []
    assert result["blocked_reason"] == "quote_changed", result
    assert_conservative_claim(store, task, quote_id)


def test_quote_expiring_during_click_actionability_never_releases_post(tmp_path, monkeypatch):
    store, task, quote_id, result, events = intercepted_purchase(
        tmp_path, monkeypatch, "animation", ttl=2
    )
    assert events["approved_at"] < events["reviewed"][0] < events["expires_at"]
    assert len(posts(events, "created")) == 1, "Chromium must reach the final request boundary"
    assert posts(events, "created")[0]["at"] > events["expires_at"]
    assert posts(events, "created")[0]["at"] - events["reviewed"][0] >= 2.5
    assert len(posts(events, "continued")) == 0, "expired POST passed route.continue_"
    assert len(posts(events, "aborted")) == 1
    assert result["blocked_reason"] == "quote_expired"
    assert_conservative_claim(store, task, quote_id)
