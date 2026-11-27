"""Real browser adapter restricted to an explicitly configured synthetic loopback shop."""

import hashlib
import re
import time
from contextlib import contextmanager, suppress
from urllib.parse import parse_qsl, urlencode, urlsplit

from .domain import exact_quote
from .security import Invalid, strict_json
from .store import digest


class SyntheticAdapter:
    def __init__(self, origin):
        if not isinstance(origin, str):
            raise Invalid("unsafe_synthetic_origin")
        u = urlsplit(origin)
        if (
            u.scheme != "http"
            or u.hostname != "127.0.0.1"
            or u.username
            or u.password
            or not u.port
            or u.port < 1024
            or u.path
            or u.query
            or u.fragment
            or origin != f"http://127.0.0.1:{u.port}"
        ):
            raise Invalid("unsafe_synthetic_origin")
        self.origin = origin

    def allowed(self, url, method="GET", dispatch=False):
        u = urlsplit(url)
        if f"{u.scheme}://{u.netloc}" != self.origin or u.username or u.fragment:
            raise Invalid("unsafe_navigation")
        allowed = (
            method == "GET"
            and (
                u.path in ("/search", "/checkout")
                or re.fullmatch(r"/product/[A-Z0-9-]{1,32}", u.path)
                or re.fullmatch(r"/receipt/task_[a-f0-9]{32}", u.path)
            )
        ) or (dispatch and method == "POST" and u.path == "/purchase" and not u.query)
        if not allowed:
            raise Invalid("unsafe_navigation")
        args = parse_qsl(u.query, keep_blank_values=True)
        if len({k for k, _ in args}) != len(args):
            raise Invalid("unsafe_navigation")
        keys = {"/search": {"q"}, "/checkout": {"task", "hash", "quantity"}}.get(u.path, set())
        if {k for k, _ in args} != keys:
            raise Invalid("unsafe_navigation")
        return url

    def _purchase_request(self, request, expected):
        if (
            request.method != "POST"
            or request.url != self.origin + "/purchase"
            or request.headers.get("content-type", "").lower()
            != "application/x-www-form-urlencoded"
        ):
            raise Invalid("unsafe_navigation")
        raw = request.post_data
        try:
            if not isinstance(raw, str) or len(raw.encode("utf-8", errors="strict")) > 4096:
                raise Invalid()
            fields = parse_qsl(
                raw, keep_blank_values=True, strict_parsing=True, errors="strict", max_num_fields=3
            )
        except (ValueError, UnicodeError) as exc:
            raise Invalid() from exc
        if len(fields) != 3 or dict(fields) != {k: str(v) for k, v in expected.items()}:
            raise Invalid()

    @contextmanager
    def page(self, dispatch=None, expires_at=None):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                java_script_enabled=False, service_workers="block", accept_downloads=False
            )
            context.set_default_timeout(5000)
            checkout_sent, purchase_sent, blocked = False, False, None

            def guard(route):
                nonlocal checkout_sent, purchase_sent, blocked
                try:
                    request = route.request
                    self.allowed(request.url, request.method, dispatch is not None)
                    if dispatch is not None:
                        checkout = self.origin + "/checkout?" + urlencode(dispatch)
                        if (
                            not checkout_sent
                            and request.method == "GET"
                            and request.url == checkout
                        ):
                            checkout_sent = True
                        else:
                            if not checkout_sent or purchase_sent:
                                raise Invalid("unsafe_navigation")
                            self._purchase_request(request, dispatch)
                            # click() may have waited for actionability after the DOM review.
                            # Check at the last boundary before releasing the actual POST.
                            if expires_at is None or expires_at <= time.time():
                                raise Invalid("quote_expired")
                            purchase_sent = True
                    # continue_() lets Chromium follow 3xx without re-entering this guard, which
                    # would replay a GET or the full POST body to any origin. Fetch unredirected.
                    response = route.fetch(max_redirects=0)
                    if 300 <= response.status < 400:
                        raise Invalid("unsafe_navigation")
                    route.fulfill(response=response)
                except Invalid as exc:
                    # Only a document/form navigation explains a failed step; a blocked image or
                    # stylesheet must not replace a later quote_changed or quote_expired.
                    if blocked is None or route.request.is_navigation_request():
                        blocked = exc
                    route.abort()
                except Exception:
                    # Transport failure: release nothing further; commit stays dispatch_uncertain.
                    with suppress(Exception):
                        route.abort()

            context.route("**/*", guard)
            page = context.new_page()
            try:
                yield page
            except Invalid:
                # The caller's own review result (quote_changed, quote_expired, ...) is decisive.
                raise
            except Exception:
                if blocked is not None:
                    raise blocked
                raise
            finally:
                context.close()
                browser.close()

    def _product(self, page, evidence=None):
        raw = page.locator("[data-product-json]").get_attribute("data-product-json")
        product = strict_json(raw)
        expected = {
            "title",
            "platform",
            "sku",
            "variant",
            "seller",
            "condition",
            "currency",
            "price",
            "shipping",
            "tax",
            "points",
            "destination",
            "eligibility",
        }
        if (
            not isinstance(product, dict)
            or set(product) != expected
            or product["platform"] != "synthetic"
        ):
            raise Invalid("needs_interpretation")
        for key in expected - {"eligibility"}:
            if not isinstance(product[key], str) or len(product[key]) > 256:
                raise Invalid("needs_interpretation")
        if type(product["eligibility"]) is not bool:
            raise Invalid("needs_interpretation")
        product["url"] = self.origin + "/product/" + product["sku"]
        self.allowed(product["url"])
        product["source_evidence"] = dict(
            url=page.url,
            observed_at=int(time.time()),
            synthetic=True,
            sha256=hashlib.sha256(raw.encode()).hexdigest(),
            product=product.copy(),
        )
        if evidence:
            product["evidence"] = evidence
        return product

    def search(self, query):
        with self.page() as page:
            page.goto(self.origin + "/search?" + urlencode({"q": query}))
            links = page.locator("a[data-product]").evaluate_all(
                '(items) => items.map(x => x.getAttribute("href"))'
            )
            candidates = []
            for href in links[:20]:
                if not isinstance(href, str) or not re.fullmatch(r"/product/[A-Z0-9-]{1,32}", href):
                    raise Invalid("unsafe_navigation")
                page.goto(self.allowed(self.origin + href))
                candidates.append(self._product(page))
            return dict(platform="synthetic", candidates=candidates, blocked_reason=None)

    def inspect(self, candidate):
        with self.page() as page:
            page.goto(self.allowed(candidate["url"]))
            return self._product(page, candidate["evidence"])

    def commit(self, task, quote):
        args = dict(task=task, hash=digest(quote), quantity=quote["quantity"])
        with self.page(dispatch=args, expires_at=quote["expires_at"]) as page:
            page.goto(self.origin + "/checkout?" + urlencode(args))
            product = self._product(page, quote["evidence"])
            final = exact_quote(product, quote["constraints"])
            if any(quote.get(key) != value for key, value in final.items()):
                raise Invalid("quote_changed")
            if quote["expires_at"] <= time.time():
                raise Invalid("quote_expired")
            # No generic click input exists. Exactly one fixture-only POST, after claim and review.
            with page.expect_response(
                lambda response: response.url == self.origin + "/purchase"
            ) as pending:
                page.locator("#synthetic-final-submit").click(no_wait_after=True)
            response = pending.value
            if response.status != 200:
                raise Invalid("dispatch_uncertain")
            return strict_json(response.text())

    def receipt(self, task):
        with self.page() as page:
            response = page.goto(self.allowed(self.origin + "/receipt/" + task))
            if response.status != 200:
                raise Invalid("receipt_missing")
            return strict_json(response.text())

    def validate_receipt(self, task, quote, receipt):
        if (
            quote.get("synthetic_origin") != self.origin
            or not isinstance(receipt, dict)
            or receipt.get("synthetic") is not True
            or receipt.get("task_id") != task
            or receipt.get("quote_hash") != digest(quote)
        ):
            raise Invalid("receipt_mismatch")
        for key in ("payable", "currency", "quantity", "sku", "variant", "seller", "condition"):
            if receipt.get(key) != quote[key] or type(receipt.get(key)) is not type(quote[key]):
                raise Invalid("receipt_mismatch")
