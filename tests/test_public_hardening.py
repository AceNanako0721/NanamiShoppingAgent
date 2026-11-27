"""Offline hardening for public reads: regex cost, deadlines, route scope, address classes."""

import http.client
import socket
import threading
import time

import pytest

from nanami_shopping import public
from nanami_shopping.security import Invalid


def adapter(platform, html, status=200):
    return public.PublicAdapter(platform, fetch=lambda url: (status, html, url))


def test_captcha_form_scan_is_linear_on_unclosed_form_tokens():
    html = "<form " * 12000
    for platform in public.PLATFORMS:
        started = time.monotonic()
        result = adapter(platform, html).search("test")
        assert time.monotonic() - started < 0.5, platform
        assert result["blocked_reason"] != "captcha"


@pytest.mark.parametrize(
    "html",
    [
        "captcha",
        "<title>Robot Check</title>",
        "<title> robot check</title>",
        '<input id="captchacharacters">',
        "<input name = 'captchacharacters'>",
        '<form method="get" action="/errors/validateCaptcha">',
        "<FORM class='a' ACTION = '/errors/validatecaptcha?x=1'>",
        "<p>人間であることを確認してください</p>",
        "<p>请完成验证码</p>",
    ],
)
def test_real_captcha_markers_still_detected(html):
    for platform in public.PLATFORMS:
        assert adapter(platform, html).search("test")["blocked_reason"] == "captcha", platform


def test_captcha_form_action_cannot_span_other_tags():
    html = "<form><a href='/x'>x</a> action='/errors/validatecaptcha'"
    assert adapter("rakuten", html).search("test")["blocked_reason"] != "captcha"


class RecordingSocket(socket.socket):
    def __init__(self, *args, events, **kwargs):
        super().__init__(*args, **kwargs)
        self.events = events

    def settimeout(self, value):
        self.events.append(("settimeout", value))
        return super().settimeout(value)


def test_connection_close_response_still_tightens_live_socket(monkeypatch):
    body = b"SYNTHETIC" * 100
    server = socket.create_server(("127.0.0.1", 0))
    port = server.getsockname()[1]

    def serve():
        conn, _ = server.accept()
        with conn:
            data = b""
            while b"\r\n\r\n" not in data:
                data += conn.recv(4096)
            conn.sendall(
                b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Type: text/html\r\n"
                + b"Content-Length: "
                + str(len(body)).encode()
                + b"\r\n\r\n"
                + body
            )

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    now = [0.0]
    events, detached = [], []
    monkeypatch.setattr(public.time, "monotonic", lambda: now[0])

    class Loopback(http.client.HTTPConnection):
        def __init__(self, host, timeout, context=None):
            super().__init__("127.0.0.1", port, timeout=timeout)

        def connect(self):
            self.sock = RecordingSocket(socket.AF_INET, socket.SOCK_STREAM, events=events)
            self.sock.settimeout(self.timeout)
            self.sock.connect(("127.0.0.1", port))

        def getresponse(self):
            response = super().getresponse()
            detached.append(self.sock is None)
            now[0] = 21.0  # 3 seconds of the 24 second budget remain.
            events.append(("headers_read", None))
            return response

    monkeypatch.setattr(public, "PinnedHTTPS", Loopback)
    try:
        status, html, _ = public.fetch_public("rakuten", public.search_url("rakuten", "x"))
    finally:
        thread.join(5)
        server.close()
    assert status == 200 and html.encode() == body
    assert detached == [True], "fixture must exercise HTTPConnection's Connection: close path"
    after = events[events.index(("headers_read", None)) + 1 :]
    assert after and all(kind == "settimeout" and value <= 3 for kind, value in after)


def scripted_connections(monkeypatch, responses, now):
    timeouts = []

    class Response:
        def __init__(self, status, location, body, on_read):
            self.status, self.location, self.body, self.on_read = status, location, body, on_read

        def getheader(self, name, default=""):
            return self.location if name == "Location" else default

        def read1(self, size):
            if self.on_read:
                self.on_read()
            chunk, self.body = self.body[:size], self.body[size:]
            return chunk

    class Connection:
        sock = None

        def __init__(self, host, timeout, context=None):
            timeouts.append(timeout)
            self.script = responses.pop(0)

        def request(self, method, path, headers):
            pass

        def getresponse(self):
            status, location, body, advance, on_read = self.script
            now[0] = advance
            return Response(status, location, body, on_read)

        def close(self):
            pass

    monkeypatch.setattr(public, "PinnedHTTPS", Connection)
    monkeypatch.setattr(public.time, "monotonic", lambda: now[0])
    return timeouts


def test_redirect_connection_timeout_uses_remaining_deadline(monkeypatch):
    now = [0.0]
    timeouts = scripted_connections(
        monkeypatch,
        [
            (302, "/search/mall/next/", b"", 20.0, None),
            (200, "", b"SYNTHETIC", 20.0, None),
        ],
        now,
    )
    status, _, final = public.fetch_public("rakuten", public.search_url("rakuten", "x"))
    assert status == 200 and final.endswith("/search/mall/next/")
    assert timeouts[0] == 12
    assert 0 < timeouts[1] <= 4


def test_socket_timeout_at_deadline_is_response_timeout(monkeypatch):
    now = [0.0]

    def expire():
        now[0] = 25.0
        raise TimeoutError("SYNTHETIC")

    scripted_connections(monkeypatch, [(200, "", b"x", 1.0, expire)], now)
    with pytest.raises(Invalid, match="response_timeout"):
        public.fetch_public("rakuten", public.search_url("rakuten", "x"))


@pytest.mark.parametrize("html", ["<" * 4_000_000, "</div>" * 666_666])
def test_amazon_parse_deadline_aborts_mid_input(monkeypatch, html):
    fed = []
    original = public.AmazonSearchHTML.feed

    def feed(self, data):
        fed.append(len(data))
        return original(self, data)

    clock = iter(x * 0.5 for x in range(10_000_000))
    monkeypatch.setattr(public.AmazonSearchHTML, "feed", feed)
    monkeypatch.setattr(public.time, "monotonic", lambda: next(clock))
    result = adapter("amazon", html).search("test")
    assert result["blocked_reason"] == "parse_limit"
    assert result["evidence"]["bytes"] == len(html)
    assert sum(fed) <= 2 * 65536, "deadline must stop before the whole body is parsed"


@pytest.mark.parametrize(
    "url",
    [
        "https://www.amazon.co.jp/dp/B000000001",
        "https://www.amazon.co.jp/gp/product/B000000001",
        "https://amazon.co.jp/dp/B000000001",
    ],
)
def test_amazon_product_pages_are_not_search_results(url):
    html = (
        '<div data-component-type="s-search-result" data-asin="B000000002">'
        '<h2><a href="/dp/B000000002">SYNTHETIC recommendation</a></h2></div>'
    )
    result = adapter("amazon", html).search(url)
    assert result["candidates"] == []
    assert result["blocked_reason"] == "adapter_unverified"
    assert result["evidence"]["url"] == url
    assert result["evidence"]["status"] == 200
    assert len(result["evidence"]["sha256"]) == 64


@pytest.mark.parametrize("ip", ["224.0.0.1", "239.255.255.250", "ff02::1", "ff0e::1"])
def test_multicast_answers_are_not_public(ip):
    with pytest.raises(Invalid, match="unsafe_network"):
        public.public_addresses([ip])
    with pytest.raises(Invalid, match="unsafe_network"):
        public.public_addresses(["8.8.8.8", ip])
