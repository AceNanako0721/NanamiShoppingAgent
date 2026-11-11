"""Conservative credential-free public GET entries. No private APIs or browser actions."""

import hashlib
import http.client
import ipaddress
import re
import socket
import ssl
import time
from html.parser import HTMLParser
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlsplit

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
    # Measured Amazon search HTML exceeds 2 MB; keep other platforms' existing bound.
    limit = 4_000_000 if platform == "amazon" else 2_000_000
    deadline = time.monotonic() + 24
    for _ in range(4):
        if time.monotonic() >= deadline:
            raise Invalid("response_timeout")
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
            body = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise Invalid("response_timeout")
                if connection.sock is not None:
                    connection.sock.settimeout(min(12, remaining))
                chunk = response.read1(min(65536, limit + 1 - len(body)))
                if time.monotonic() >= deadline:
                    raise Invalid("response_timeout")
                body.extend(chunk)
                if len(body) > limit:
                    raise Invalid("response_too_large")
                if not chunk:
                    break
            return response.status, body.decode("utf-8", errors="replace"), url
        finally:
            connection.close()
    raise Invalid("redirect_limit")


VERIFIED_LINKS = {"rakuten", "yahoo"}


def amazon_product_url(base, href):
    """Normalize a product href from fetched HTML, never an arbitrary query's ASIN."""
    allowed_url("amazon", base)
    try:
        if not isinstance(href, str) or len(href) > 8192 or re.search(r"[\x00-\x20\\]", href):
            raise Invalid("unsafe_navigation")
        raw_path = urlsplit(href).path
        decoded = unquote(raw_path, errors="strict")
        if (
            re.search(r"[\x00-\x20\x7f\\]", decoded)
            or any(part in (".", "..") for part in decoded.split("/"))
            or decoded.count("/") != raw_path.count("/")
        ):
            raise Invalid("unsafe_navigation")
        u = urlsplit(urljoin(base, href))
        if (
            u.scheme != "https"
            or u.hostname not in RULES["amazon"]
            or u.username
            or u.password
            or u.port not in (None, 443)
            or u.fragment
        ):
            raise Invalid("unsafe_navigation")
        match = re.fullmatch(
            r"/(?:(?:[^/]+/)?dp|gp/product)/([A-Z0-9]{10})(?:/ref=[A-Za-z0-9_-]{1,128})?/?",
            u.path,
        )
        keys = {"dib", "dib_tag", "keywords", "qid", "sr", "th", "psc", "ref_", "crid", "sprefix"}
        args = parse_qsl(u.query, keep_blank_values=True, strict_parsing=True)
        if (
            not match
            or len({k for k, _ in args}) != len(args)
            or any(
                k not in keys or len(v) > 2048 or re.search(r"[\x00-\x1f\x7f]", v) for k, v in args
            )
        ):
            raise Invalid("not_product_url")
        return allowed_url("amazon", "https://www.amazon.co.jp/dp/" + match[1])
    except (ValueError, UnicodeError) as exc:
        if isinstance(exc, Invalid):
            raise
        raise Invalid("unsafe_navigation") from exc


def product_url(platform, base, href):
    if platform == "amazon":
        return amazon_product_url(base, href)
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


class AmazonSearchHTML(HTMLParser):
    """Bounded result-card titles only. No organic rank or offer inference."""

    VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())

    def __init__(self, url):
        super().__init__(convert_charrefs=True)
        self.url = allowed_url("amazon", url)
        self.stack, self.links = [], {}
        self.card = self.anchor = self.heading = None
        self.tags = self.cards = 0
        self.deadline = time.monotonic() + 2

    def check_time(self):
        if time.monotonic() >= self.deadline:
            raise Invalid("parse_limit")

    def handle_starttag(self, tag, attrs):
        self.tags += 1
        if self.tags > 20000 or len(self.stack) >= 96:
            raise Invalid("parse_limit")
        if self.tags % 1024 == 0:
            self.check_time()
        if tag not in self.VOID:
            self.stack.append(tag)
        attrs = dict(attrs)
        if tag == "div" and attrs.get("data-component-type") == "s-search-result":
            self.cards += 1
            if self.cards > 100:
                raise Invalid("parse_limit")
            if self.card is not None:
                self.card["blocked"] = True
            else:
                asin = attrs.get("data-asin") or ""
                self.card = dict(
                    depth=len(self.stack),
                    asin=asin,
                    headings={},
                    blocked=re.fullmatch(r"[A-Z0-9]{10}", asin) is None,
                )
        if self.card is None:
            return
        markers = " ".join(
            (attrs.get(k) or "") for k in ("class", "data-component-type", "aria-label")
        ).lower()
        if any(x in markers for x in ("adholder", "sponsored", "recommend", "スポンサー")):
            self.card["blocked"] = True
        if tag == "a":
            self.anchor = None
            try:
                link = amazon_product_url(self.url, attrs.get("href", ""))
                if link.rsplit("/", 1)[-1] == self.card["asin"]:
                    self.anchor = dict(url=link, depth=len(self.stack))
                    if self.heading is not None:
                        self.heading["url"] = link
            except Invalid:
                pass
        if tag == "h2":
            self.heading = dict(
                depth=len(self.stack),
                text=[],
                size=0,
                url=self.anchor["url"] if self.anchor else None,
            )

    def handle_data(self, data):
        if self.card is None:
            return
        if data.strip().lower() in ("sponsored", "スポンサー", "広告"):
            self.card["blocked"] = True
        if self.heading is not None and "script" not in self.stack and "style" not in self.stack:
            remaining = 2048 - self.heading["size"]
            if remaining > 0:
                self.heading["text"].append(data[:remaining])
                self.heading["size"] += len(data[:remaining])

    def handle_endtag(self, tag):
        if tag not in self.stack:
            return
        depth = len(self.stack) - self.stack[::-1].index(tag)
        if self.heading is not None and depth <= self.heading["depth"]:
            h = self.heading
            if tag == "h2" and depth == h["depth"] and h["url"] and self.card is not None:
                title = " ".join("".join(h["text"]).split())[:160]
                if title and len(self.card["headings"]) < 2:
                    self.card["headings"].setdefault(h["url"], title)
            self.heading = None
        if self.anchor is not None and depth <= self.anchor["depth"]:
            self.anchor = None
        if self.card is not None and depth <= self.card["depth"]:
            card = self.card
            if (
                depth == card["depth"]
                and tag == "div"
                and not card["blocked"]
                and len(card["headings"]) == 1
                and len(self.links) < 20
            ):
                link, title = next(iter(card["headings"].items()))
                self.links.setdefault(link, (title, card["asin"]))
            self.card = self.anchor = self.heading = None
        del self.stack[depth - 1 :]

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)


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
        if self.platform == "amazon":
            return dict(
                search="public_search_result_links",
                inspect="public_entry_unverified",
                inspect_blocked_reason="adapter_unverified",
                checkout="disabled",
                ready=True,
                blocked_reason=None,
                operations=["read_public_search"],
                caveat="Result-card title/link only; ads filtered where identified, rank unverified. "
                "Product inspection, SKU, variant, price, delivery and fees unverified.",
            )
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
        encoded = body.encode()
        if len(encoded) > (4_000_000 if self.platform == "amazon" else 2_000_000):
            raise Invalid("response_too_large")
        lower = body.lower()
        if status in (401, 403):
            reason = "access_denied"
        elif status >= 400:
            reason = "http_unavailable"
        elif (
            lower.strip() == "captcha"
            or re.search(
                r"<title>\s*robot check|(?:id|name)\s*=\s*['\"]captchacharacters['\"]"
                r"|<form\b[^>]*\baction\s*=\s*['\"]/errors/validatecaptcha",
                lower,
            )
            or any(
                x in lower
                for x in (
                    "<title>robot check",
                    'id="captchacharacters"',
                    "请完成验证码",
                    "人間であることを確認してください",
                )
            )
        ):
            reason = "captcha"
        else:
            reason = None
        evidence = dict(
            url=final_url,
            status=status,
            observed_at=int(time.time()),
            sha256=hashlib.sha256(encoded).hexdigest(),
            bytes=len(encoded),
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
        evidence = None
        try:
            body, evidence, reason = self._fetch(url)
            candidates = []
            if reason is None and self.platform == "amazon":
                parser = AmazonSearchHTML(evidence["url"])
                parser.feed(body)
                parser.close()
                parser.check_time()
                for link, (title, asin) in parser.links.items():
                    candidate = empty_candidate(self.platform, link, title)
                    candidate["uncertainty"].append("search_result_rank_unverified")
                    candidate["source_evidence"] = evidence | dict(
                        extraction=dict(kind="amazon_search_result_card", asin=asin)
                    )
                    candidates.append(candidate)
            elif reason is None and self.platform in VERIFIED_LINKS:
                parser = ProductHTML(self.platform, evidence["url"])
                parser.feed(body)
                for link, title in list(parser.links.items())[:20]:
                    candidates.append(
                        empty_candidate(self.platform, link, title) | {"source_evidence": evidence}
                    )
            return dict(
                platform=self.platform,
                candidates=candidates,
                blocked_reason=reason
                or (
                    None
                    if candidates
                    else "no_trusted_results"
                    if self.platform == "amazon"
                    else "adapter_unverified"
                ),
                evidence=evidence,
            )
        except Invalid as exc:
            result = dict(platform=self.platform, candidates=[], blocked_reason=exc.code)
            if evidence is not None:
                result["evidence"] = evidence
            return result
        except (OSError, http.client.HTTPException, ssl.SSLError):
            return dict(platform=self.platform, candidates=[], blocked_reason="network_unavailable")

    def inspect(self, candidate):
        if self.platform == "amazon":
            raise Invalid("adapter_unverified")
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
