"""Conservative credential-free public GET entries. No private APIs or browser actions."""

import hashlib
import http.client
import ipaddress
import re
import socket
import ssl
import time
from html.parser import HTMLParser
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit

from .security import Invalid, money, strict_json

PLATFORMS = ("pdd", "amazon", "rakuten", "mercari", "yahoo")


def search_url(platform, query):
    if platform == "amazon":
        return "https://www.amazon.co.jp/s?" + urlencode({"k": query})
    if platform == "rakuten":
        return "https://search.rakuten.co.jp/search/mall/" + quote(query, safe="") + "/"
    if platform == "mercari":
        return "https://jp.mercari.com/search?" + urlencode({"keyword": query})
    if platform == "yahoo":
        return "https://paypayfleamarket.yahoo.co.jp/search/" + quote(query, safe="")
    if platform == "pdd":
        return "https://mobile.yangkeduo.com/search_result.html?" + urlencode({"search_key": query})
    raise Invalid()


RULES = {
    "amazon": {
        "www.amazon.co.jp": [(r"/s", {"k"}), (r"/(?:dp|gp/product)/[A-Z0-9]{10}/?", set())],
        "amazon.co.jp": [(r"/(?:dp|gp/product)/[A-Z0-9]{10}/?", set())],
    },
    "rakuten": {
        "search.rakuten.co.jp": [(r"/search/mall/[^/]+/", set())],
        "item.rakuten.co.jp": [(r"/[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+/?", set())],
    },
    "mercari": {"jp.mercari.com": [(r"/search", {"keyword"}), (r"/item/m[0-9]+", set())]},
    "yahoo": {
        "paypayfleamarket.yahoo.co.jp": [(r"/search/[^/]+", set()), (r"/item/[a-z][0-9]+", set())]
    },
    "pdd": {
        "mobile.yangkeduo.com": [
            (r"/search_result.html", {"search_key"}),
            (r"/goods.html", {"goods_id"}),
        ]
    },
}


def allowed_url(platform, url, method="GET"):
    try:
        if (
            method != "GET"
            or not isinstance(url, str)
            or len(url) > 2048
            or re.search(r"[\x00-\x20\\]", url)
        ):
            raise Invalid("unsafe_navigation")
        u = urlsplit(url)
        if (
            u.scheme != "https"
            or u.username
            or u.password
            or u.port not in (None, 443)
            or u.fragment
        ):
            raise Invalid("unsafe_navigation")
        rules = RULES.get(platform, {}).get(u.hostname, [])
        args = parse_qsl(u.query, keep_blank_values=True, strict_parsing=True)
        if len({k for k, _ in args}) != len(args):
            raise Invalid("unsafe_navigation")
        if any(re.search(r"[\x00-\x1f\x7f]", v) or len(v) > 256 for _, v in args):
            raise Invalid("unsafe_navigation")
        if not any(
            re.fullmatch(pattern, u.path) and set(k for k, _ in args) == keys
            for pattern, keys in rules
        ):
            raise Invalid("unsafe_navigation")
        if any(k == "goods_id" and not v.isdigit() for k, v in args):
            raise Invalid("unsafe_navigation")
        return url
    except (ValueError, TypeError) as exc:
        if isinstance(exc, Invalid):
            raise
        raise Invalid("unsafe_navigation") from exc


def public_addresses(ips):
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise Invalid("unsafe_network")
    return ips


class PinnedHTTPS(http.client.HTTPSConnection):
    def connect(self):
        # Resolve once, reject mixed/private answers, connect only the validated IP; retain TLS SNI.
        ips = public_addresses(
            list(
                dict.fromkeys(
                    r[4][0] for r in socket.getaddrinfo(self.host, 443, type=socket.SOCK_STREAM)
                )
            )
        )
        self.sock = socket.create_connection((ips[0], 443), timeout=self.timeout)
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


def fetch_public(platform, url):
    for _ in range(4):
        allowed_url(platform, url)
        u = urlsplit(url)
        connection = PinnedHTTPS(u.hostname, timeout=12, context=ssl.create_default_context())
        try:
            connection.request(
                "GET",
                u.path + ("?" + u.query if u.query else ""),
                headers={
                    "User-Agent": "NanamiShoppingAgent/0.1 (read-only)",
                    "Accept": "text/html",
                },
            )
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                url = allowed_url(platform, urljoin(url, response.getheader("Location", "")))
                continue
            body = response.read(2_000_001)
            if len(body) > 2_000_000:
                raise Invalid("response_too_large")
            return response.status, body.decode("utf-8", errors="replace"), url
        finally:
            connection.close()
    raise Invalid("redirect_limit")


VERIFIED_LINKS = {"rakuten", "yahoo"}


def product_url(platform, base, href):
    url = urljoin(base, href)
    u = urlsplit(url)
    query = ("?" + u.query) if platform == "pdd" else ""
    url = f"{u.scheme}://{u.netloc}{u.path}" + query
    allowed_url(platform, url)
    if not (
        re.fullmatch(r"/(?:dp|gp/product)/[A-Z0-9]{10}/?", u.path)
        or (u.hostname == "item.rakuten.co.jp")
        or re.fullmatch(r"/item/[a-z][0-9]+", u.path)
        or (platform == "pdd" and u.path == "/goods.html")
    ):
        raise Invalid("not_product_url")
    return url


class ProductHTML(HTMLParser):
    def __init__(self, platform, url):
        super().__init__(convert_charrefs=True)
        self.platform, self.url = platform, url
        self.links, self.products = {}, []
        self.anchor = None
        self.script = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            self.anchor = None
            try:
                url = product_url(self.platform, self.url, attrs.get("href", ""))
                self.anchor = [url, attrs.get("title", ""), []]
            except Invalid:
                pass
        if tag == "img" and self.anchor and attrs.get("alt"):
            self.anchor[2].append(attrs["alt"])
        if tag == "script" and attrs.get("type") == "application/ld+json":
            self.script = []

    def handle_data(self, data):
        if self.anchor and sum(len(x) for x in self.anchor[2]) < 2048:
            self.anchor[2].append(data.strip())
        if self.script is not None and sum(len(x) for x in self.script) < 65536:
            self.script.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.anchor:
            url, title, text = self.anchor
            title = " ".join((title or " ".join(text)).split())[:160]
            if title and len(self.links) < 100:
                self.links.setdefault(url, title)
            self.anchor = None
        if tag == "script" and self.script is not None:
            try:
                value = strict_json("".join(self.script))

                def walk(item):
                    if isinstance(item, dict):
                        if item.get("@type") == "Product" or "Product" in (item.get("@type") or []):
                            self.products.append(item)
                        for v in item.values():
                            walk(v)
                    elif isinstance(item, list):
                        for v in item:
                            walk(v)

                walk(value)
            except (Invalid, TypeError):
                pass
            self.script = None


def empty_candidate(platform, url, title):
    return dict(
        platform=platform,
        url=url,
        title=title,
        sku=None,
        variant=None,
        seller=None,
        condition=None,
        price=None,
        shipping=None,
        tax=None,
        points=None,
        currency="CNY" if platform == "pdd" else "JPY",
        destination=None,
        eligibility=False,
        uncertainty=["variant_condition_seller_and_fees_unverified"],
    )


class PublicAdapter:
    def __init__(self, platform, fetch=None):
        if platform not in PLATFORMS:
            raise Invalid()
        self.platform = platform
        self.fetch = fetch or (lambda url: fetch_public(platform, url))

    def capability(self):
        verified = self.platform in VERIFIED_LINKS
        return dict(
            search="public_listing_links" if verified else "public_entry_unverified",
            inspect="public_product_partial" if verified else "public_entry_unverified",
            checkout="disabled",
            ready=verified,
            blocked_reason=None if verified else "adapter_unverified",
            operations=["read_public_search", "read_public_product"],
            caveat="Read-only links and optional structured fields; variant, fees and checkout unverified."
            if verified
            else "No verified candidate extractor. Japan variant eligibility unverified.",
        )

    def _fetch(self, url):
        allowed_url(self.platform, url)
        status, body, final_url = self.fetch(url)
        allowed_url(self.platform, final_url)
        lower = body.lower()
        if status in (401, 403):
            reason = "access_denied"
        elif status >= 400:
            reason = "http_unavailable"
        elif lower.strip() == "captcha" or any(
            x in lower
            for x in (
                "<title>robot check",
                'id="captchacharacters"',
                "请完成验证码",
                "人間であることを確認してください",
            )
        ):
            reason = "captcha"
        else:
            reason = None
        evidence = dict(
            url=final_url,
            status=status,
            observed_at=int(time.time()),
            sha256=hashlib.sha256(body.encode()).hexdigest(),
            bytes=len(body.encode()),
        )
        return body, evidence, reason

    def search(self, query):
        url = (
            allowed_url(self.platform, query)
            if query.startswith("https://")
            else search_url(self.platform, query)
        )
        return self.read(url)

    def read(self, url):
        allowed_url(self.platform, url)
        try:
            body, evidence, reason = self._fetch(url)
            candidates = []
            if reason is None and self.platform in VERIFIED_LINKS:
                parser = ProductHTML(self.platform, evidence["url"])
                parser.feed(body)
                for link, title in list(parser.links.items())[:20]:
                    candidates.append(
                        empty_candidate(self.platform, link, title) | {"source_evidence": evidence}
                    )
            return dict(
                platform=self.platform,
                candidates=candidates,
                blocked_reason=reason or (None if candidates else "adapter_unverified"),
                evidence=evidence,
            )
        except Invalid as exc:
            return dict(platform=self.platform, candidates=[], blocked_reason=exc.code)
        except (OSError, http.client.HTTPException, ssl.SSLError):
            return dict(platform=self.platform, candidates=[], blocked_reason="network_unavailable")

    def inspect(self, candidate):
        try:
            body, evidence, reason = self._fetch(
                product_url(self.platform, candidate["url"], candidate["url"])
            )
        except (OSError, http.client.HTTPException) as exc:
            raise Invalid("network_unavailable") from exc
        if reason:
            raise Invalid(reason)
        parser = ProductHTML(self.platform, evidence["url"])
        parser.feed(body)
        current = empty_candidate(self.platform, evidence["url"], candidate["title"])
        if len(parser.products) == 1:
            product = parser.products[0]
            if isinstance(product.get("name"), str):
                current["title"] = product["name"][:160]
            if isinstance(product.get("sku"), str):
                current["sku"] = product["sku"][:160]
            offer = product.get("offers")
            if (
                isinstance(offer, dict)
                and offer.get("priceCurrency") in ("JPY", "CNY")
                and type(offer.get("price")) in (str, int)
            ):
                try:
                    current["price"] = str(money(str(offer["price"]), offer["priceCurrency"]))
                    current["currency"] = offer["priceCurrency"]
                except Invalid:
                    pass
        current.update(evidence=candidate["evidence"], source_evidence=evidence)
        return current
