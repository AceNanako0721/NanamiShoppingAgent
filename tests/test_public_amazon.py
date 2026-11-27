"""Synthetic public HTML only: never request marketplace pages in CI."""

import io

import pytest

from nanami_shopping import public
from nanami_shopping.security import Invalid


def mock_response(monkeypatch, body, *, status=200):
    reads = []

    class Response(io.BytesIO):
        def getheader(self, name, default=""):
            return default

        def read1(self, size):
            reads.append(size)
            return super().read1(size)

    response = Response(body)
    response.status = status

    class Connection:
        sock = None

        def __init__(self, *args, **kwargs):
            pass

        def request(self, method, path, headers):
            assert method == "GET"
            assert "Cookie" not in headers and "Authorization" not in headers

        def getresponse(self):
            return response

        def close(self):
            pass

    monkeypatch.setattr(public, "PinnedHTTPS", Connection)
    return reads


def test_amazon_large_but_bounded_html_get(monkeypatch):
    body = b"<!-- SYNTHETIC -->" + b" " * 2_500_000
    reads = mock_response(monkeypatch, body)
    status, html, _ = public.fetch_public("amazon", public.search_url("amazon", "test"))
    assert status == 200 and html.encode() == body
    assert reads and max(reads) <= 65536


@pytest.mark.parametrize("platform,limit", [("amazon", 4_000_000), ("rakuten", 2_000_000)])
def test_response_limits_still_reject_overflow(monkeypatch, platform, limit):
    mock_response(monkeypatch, b" " * (limit + 1))
    with pytest.raises(Invalid, match="response_too_large"):
        public.fetch_public(platform, public.search_url(platform, "test"))


def test_public_body_read_deadline(monkeypatch):
    mock_response(monkeypatch, b" " * 200_000)
    ticks = iter(range(0, 1000, 10))
    monkeypatch.setattr(public.time, "monotonic", lambda: next(ticks))
    with pytest.raises(Invalid, match="response_timeout"):
        public.fetch_public("amazon", public.search_url("amazon", "test"))


@pytest.mark.parametrize(
    "href",
    [
        "/SYNTHETIC-notebook/dp/B000000001/ref=sr_1_1?dib=synthetic&sr=8-1",
        "/%E3%83%8E%E3%83%BC%E3%83%88/dp/B000000001?th=1&psc=1",
        "https://amazon.co.jp/gp/product/B000000001/ref=synthetic?ref_=test",
        "/dp/B000000001/",
    ],
)
def test_amazon_card_link_canonicalization(href):
    assert public.product_url("amazon", public.search_url("amazon", "test"), href) == (
        "https://www.amazon.co.jp/dp/B000000001"
    )


@pytest.mark.parametrize(
    "href",
    [
        "https://evil.test/dp/B000000001",
        "https://www.amazon.co.jp.evil.test/dp/B000000001",
        "https://user@www.amazon.co.jp/dp/B000000001",
        "https://www.amazon.co.jp:444/dp/B000000001",
        "http://www.amazon.co.jp/dp/B000000001",
        "/ap/signin?asin=B000000001",
        "/cart/add?ASIN=B000000001",
        "/sspa/click?url=%2Fdp%2FB000000001",
        "/s?asin=B000000001",
        "/dp/B000000001?url=https://127.0.0.1",
        "/dp/B000000001?asin=B000000002",
        "/dp/B000000001?sr=1&sr=2",
        "/dp/B000000001?sr=%0a",
        "/dp/B000000001#account",
        "/dp/B000000001/../../ap/signin",
        "/%2e%2e/dp/B000000001",
        "/foo%2fap/dp/B000000001",
        "/dp/B000000001/extra",
        "/dp/b000000001",
        "/dp/B000000001X",
    ],
)
def test_amazon_unsafe_or_nonproduct_hrefs_rejected(href):
    with pytest.raises(Invalid):
        public.product_url("amazon", public.search_url("amazon", "test"), href)


def test_canonicalization_requires_trusted_base_and_does_not_expand_fetch_routes():
    with pytest.raises(Invalid):
        public.product_url("amazon", "https://evil.test/", "/dp/B000000001")
    with pytest.raises(Invalid):
        public.allowed_url("amazon", "https://www.amazon.co.jp/slug/dp/B000000001?sr=1")


def card(asin="B000000001", *, content=None, attrs="", reverse=False):
    href = f"/SYNTHETIC-notebook/dp/{asin}/ref=sr_1_1?sr=8-1"
    title = "<span>SYNTHETIC</span> notebook &amp; paper"
    if content is None:
        content = (
            f'<a href="{href}"><h2>{title}</h2></a>'
            if reverse
            else f'<h2><a href="{href}">{title}</a></h2>'
        )
    return (
        f'<div data-component-type="s-search-result" data-asin="{asin}" {attrs}>'
        + content
        + "</div>"
    )


def search_html(html):
    return public.PublicAdapter("amazon", fetch=lambda url: (200, html, url)).search("test")


@pytest.mark.parametrize("reverse", [False, True])
def test_amazon_trusted_cards_keep_unknowns_and_source_evidence(reverse):
    result = search_html("<!-- SYNTHETIC -->" + " " * 2_500_000 + card(reverse=reverse))
    assert result["blocked_reason"] is None
    assert len(result["candidates"]) == 1
    item = result["candidates"][0]
    assert item["url"] == "https://www.amazon.co.jp/dp/B000000001"
    assert item["title"] == "SYNTHETIC notebook & paper"
    for key in ("sku", "variant", "seller", "condition", "price", "shipping", "tax", "points"):
        assert item[key] is None
    assert item["eligibility"] is False
    assert "search_result_rank_unverified" in item["uncertainty"]
    evidence = item["source_evidence"]
    assert evidence["bytes"] > 2_000_000 and evidence["status"] == 200
    assert evidence["extraction"] == {"kind": "amazon_search_result_card", "asin": "B000000001"}


def test_advertisements_recommendations_and_untrusted_links_are_not_results():
    html = (
        '<a href="/dp/B000000002">SYNTHETIC outside recommendation</a>'
        + card("B000000002", attrs='class="AdHolder"')
        + card(
            "B000000003", content='<h2><a href="/dp/B000000003">ad</a></h2><span>スポンサー</span>'
        )
        + card("B000000004", content='<h2><a href="https://evil.test/dp/B000000004">evil</a></h2>')
        + card("B000000005", content='<h2><a href="/ap/signin?asin=B000000005">account</a></h2>')
        + card("B000000006", content='<h2><a href="/dp/B000000007">mismatched ASIN</a></h2>')
        + '<div data-component-type="recommendation"><h2><a href="/dp/B000000008">recommendation</a></h2></div>'
        + card()
    )
    result = search_html(html)
    assert [c["url"] for c in result["candidates"]] == ["https://www.amazon.co.jp/dp/B000000001"]


def test_missing_fields_and_incomplete_cards_are_skipped_without_discarding_valid_card():
    html = (
        card(content='<a href="/dp/B000000001"><img alt="not a title"></a>')
        + card(content="<h2>Title but no link</h2>")
        + card(content='<h2><a href="/dp/B000000001"></a></h2>')
        + card(asin="not-an-asin")
        + card()
        + card()
        + '<div data-component-type="s-search-result" data-asin="B000000002">'
        '<h2><a href="/dp/B000000002">SYNTHETIC truncated card</a></h2>'
    )
    assert len(search_html(html)["candidates"]) == 1


@pytest.mark.parametrize(
    "html,reason",
    [
        ("<html><title>SYNTHETIC no results</title></html>", "no_trusted_results"),
        ("<title>Robot Check</title>" + card(), "captcha"),
        (card() + '<input id="captchacharacters">', "captcha"),
        (card() + "<input id = 'captchacharacters'>", "captcha"),
        (card() + '<form action="/errors/validateCaptcha">', "captcha"),
        ("<title> Robot Check</title>" + card(), "captcha"),
    ],
)
def test_amazon_no_results_or_challenge_never_empty_success(html, reason):
    result = search_html(html)
    assert result["candidates"] == [] and result["blocked_reason"] == reason


@pytest.mark.parametrize(
    "html,reason",
    [
        (" " * 4_000_001, "response_too_large"),
        ("<i></i>" * 20_001 + card(), "parse_limit"),
        ("<div>" * 97 + card(), "parse_limit"),
        ("".join(card() for _ in range(101)), "parse_limit"),
    ],
)
def test_amazon_response_and_structure_limits_handoff(html, reason):
    result = search_html(html)
    assert result["candidates"] == [] and result["blocked_reason"] == reason


def test_amazon_search_result_count_is_bounded():
    result = search_html("".join(card(f"B{i:09}") for i in range(25)))
    assert len(result["candidates"]) == 20


def test_amazon_product_inspection_stays_unverified():
    with pytest.raises(Invalid, match="adapter_unverified"):
        public.PublicAdapter("amazon", fetch=lambda _: pytest.fail("no detail GET")).inspect(
            {
                "url": "https://www.amazon.co.jp/dp/B000000001",
                "title": "SYNTHETIC",
                "evidence": "ref",
            }
        )


def test_amazon_parsing_has_a_deadline(monkeypatch):
    ticks = iter([0, 3])
    monkeypatch.setattr(public.time, "monotonic", lambda: next(ticks))
    assert search_html(card())["blocked_reason"] == "parse_limit"


@pytest.mark.parametrize(
    "html,reason",
    [
        (card(), None),
        ("SYNTHETIC empty", "no_trusted_results"),
        ("<title>Robot Check</title>", "captcha"),
    ],
)
def test_service_preserves_public_unknowns_or_controlled_handoff(
    tmp_path, monkeypatch, html, reason
):
    from nanami_shopping.service import Service
    from nanami_shopping.store import Store

    store = Store(tmp_path / "state", "test-host", "synthetic-public")
    store.set_budget("pool", "1000", "JPY")
    task = store.create_target("goal", "pool", "500", "JPY", "unknown", "unknown", 1)
    service = Service(store, live=True)
    monkeypatch.setattr(
        service,
        "_adapter",
        lambda _: public.PublicAdapter("amazon", fetch=lambda url: (200, html, url)),
    )
    result = service.call("shopping_search", dict(task_id=task, query="test", platforms=["amazon"]))
    if reason:
        assert result["state"] == "handoff"
        assert result["data"]["candidates"] == []
        assert result["data"]["blockers"] == [{"platform": "amazon", "reason": reason}]
    else:
        item = result["data"]["candidates"][0]
        assert "search_result_rank_unverified" in item["uncertainty"]
        assert "variant_condition_seller_and_fees_unverified" in item["uncertainty"]
        stored = store.get(task, item["candidate_id"], "candidate")
        evidence = store.get(task, stored["evidence"], "evidence")
        assert evidence["extraction"]["asin"] == "B000000001"
        assert stored["price"] is None and stored["sku"] is None


def test_amazon_capabilities_only_advertise_verified_search_layer():
    capability = public.PublicAdapter("amazon").capability()
    assert capability["search"] == "public_search_result_links"
    assert capability["ready"] is True
    assert capability["inspect"] == "public_entry_unverified"
    assert capability["inspect_blocked_reason"] == "adapter_unverified"
    assert capability["checkout"] == "disabled"
    assert capability["operations"] == ["read_public_search"]
    for platform in ("mercari", "pdd"):
        assert public.PublicAdapter(platform).capability()["ready"] is False


def test_malformed_optional_card_attributes_do_not_escape_handoff():
    html = '<div data-component-type="s-search-result" data-asin><h2>missing</h2></div>' + card(
        attrs="class aria-label"
    )
    result = search_html(html)
    assert result["blocked_reason"] is None and len(result["candidates"]) == 1


def test_parse_limit_keeps_fetched_source_fingerprint():
    result = search_html("<i></i>" * 20_001)
    assert result["blocked_reason"] == "parse_limit"
    assert result["evidence"]["url"] == public.search_url("amazon", "test")
    assert result["evidence"]["bytes"] == len("<i></i>" * 20_001)
    assert len(result["evidence"]["sha256"]) == 64
