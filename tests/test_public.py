import pytest

from nanami_shopping.public import PublicAdapter, allowed_url, public_addresses, search_url
from nanami_shopping.security import Invalid


@pytest.mark.parametrize(
    "url",
    [
        "http://www.amazon.co.jp/s?k=x",
        "https://www.amazon.co.jp.evil.test/s?k=x",
        "https://user@www.amazon.co.jp/s?k=x",
        "https://www.amazon.co.jp:444/s?k=x",
        "https://127.0.0.1/s?k=x",
        "https://www.amazon.co.jp/ap/signin",
        "https://www.amazon.co.jp/cart/add",
        "https://www.amazon.co.jp/s?k=x&redirect=http://127.0.0.1",
        "https://www.amazon.co.jp/dp/../cart/add",
        "https://www.amazon.co.jp/s?k=x#secret",
        "https://www.amazon.co.jp/s?k=x&k=y",
        "https://www.amazon.co.jp/s?k=%0afoo",
    ],
)
def test_navigation_and_methods_are_allowlisted(url):
    with pytest.raises(Invalid):
        allowed_url("amazon", url)


def test_public_search_entries_and_detail_paths():
    for platform in ["amazon", "rakuten", "mercari", "yahoo", "pdd"]:
        assert allowed_url(platform, search_url(platform, "テスト"))
        assert PublicAdapter(platform).capability()["checkout"] == "disabled"
    for platform, url in [
        ("amazon", "https://www.amazon.co.jp/dp/B000000001"),
        ("rakuten", "https://item.rakuten.co.jp/test/item/"),
        ("mercari", "https://jp.mercari.com/item/m123456"),
        ("yahoo", "https://paypayfleamarket.yahoo.co.jp/item/z123456"),
        ("pdd", "https://mobile.yangkeduo.com/goods.html?goods_id=12345"),
    ]:
        assert allowed_url(platform, url)


def test_dns_private_or_mixed_answers_are_rejected():
    for ips in [
        ["127.0.0.1"],
        ["::1"],
        ["169.254.169.254"],
        ["10.0.0.1"],
        ["8.8.8.8", "192.168.1.1"],
    ]:
        with pytest.raises(Invalid, match="unsafe_network"):
            public_addresses(ips)
    assert public_addresses(["8.8.8.8"]) == ["8.8.8.8"]


def test_failure_is_handoff_never_synthetic_success():
    def failing(url):
        raise OSError("SENSITIVE_EXCEPTION")

    result = PublicAdapter("amazon", fetch=failing).search("test")
    assert result["candidates"] == []
    assert result["blocked_reason"] == "network_unavailable"
    assert "SENSITIVE" not in str(result)

    def captcha(url):
        return 200, "captcha", url

    assert PublicAdapter("amazon", fetch=captcha).search("test")["blocked_reason"] == "captcha"


def test_verified_public_link_extraction_keeps_unknown_fees_and_no_fake_sku():
    def fixture(url):
        return (
            200,
            '<a href="https://item.rakuten.co.jp/synthetic/widget/" title="SYNTHETIC notebook">Notebook</a><a href="https://evil.test/item">Ignore</a>',
            url,
        )

    result = PublicAdapter("rakuten", fetch=fixture).search("notebook")
    assert len(result["candidates"]) == 1
    product = result["candidates"][0]
    assert product["title"] == "SYNTHETIC notebook"
    assert product["price"] is None and product["shipping"] is None
    assert product["sku"] is None and product["variant"] is None
    assert product["eligibility"] is False
    assert result["blocked_reason"] is None


def test_public_product_read_parses_only_evidenced_fields():
    def fixture(url):
        return (
            200,
            '<script type="application/ld+json">{"@type":"Product","name":"SYNTHETIC notebook","sku":"SYN-1","offers":{"price":"150","priceCurrency":"JPY"}}</script>',
            url,
        )

    adapter = PublicAdapter("rakuten", fetch=fixture)
    result = adapter.inspect(
        dict(
            url="https://item.rakuten.co.jp/synthetic/widget/", evidence="private-ref", title="old"
        )
    )
    assert result["price"] == "150"
    assert result["sku"] == "SYN-1"
    assert result["shipping"] is None and result["tax"] is None
    assert result["source_evidence"]["status"] == 200


def test_captcha_library_reference_is_not_claimed_as_captcha():
    def fixture(url):
        return (
            200,
            '<script src="/recaptcha.js"></script><a href="https://paypayfleamarket.yahoo.co.jp/item/z123" title="SYNTHETIC notebook">Test</a>',
            url,
        )

    result = PublicAdapter("yahoo", fetch=fixture).search("notebook")
    assert result["blocked_reason"] is None


def test_redirect_is_checked_before_any_second_request(monkeypatch):
    from nanami_shopping import public

    calls = []

    class Response:
        status = 302

        def getheader(self, name, default=""):
            return "https://127.0.0.1/private"

    class Connection:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, method, path, headers):
            calls.append((method, path))

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(public, "PinnedHTTPS", Connection)
    with pytest.raises(Invalid, match="unsafe_navigation"):
        public.fetch_public("amazon", search_url("amazon", "test"))
    assert len(calls) == 1
    with pytest.raises(Invalid):
        allowed_url("amazon", search_url("amazon", "test"), method="POST")


def test_pinned_connection_rejects_private_dns_before_connect(monkeypatch):
    from nanami_shopping import public

    monkeypatch.setattr(
        public.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 443))]
    )

    def no_connect(*a, **k):
        pytest.fail("private DNS must never reach connect")

    monkeypatch.setattr(public.socket, "create_connection", no_connect)
    with pytest.raises(Invalid, match="unsafe_network"):
        public.PinnedHTTPS("www.amazon.co.jp").connect()
