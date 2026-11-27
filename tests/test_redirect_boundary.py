"""Real Chromium against loopback servers: server redirects never leave the guarded origin."""

import asyncio
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from mcp.client import Client
from synthetic_shop import running_shop
from test_browser import attach, call, preview
from test_protocol import config, params

pytestmark = pytest.mark.browser


@contextmanager
def counting_server():
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def record(self):
            length = int(self.headers.get("Content-Length", "0") or 0)
            body = self.rfile.read(min(length, 4096)).decode(errors="replace")
            hits.append((self.command, self.path, body))
            raw = b"<h1>SYNTHETIC foreign origin</h1>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        do_GET = do_POST = record

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port), hits
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("path,status", [("/checkout", 302), ("/purchase", 307)])
async def test_server_redirect_is_not_followed_and_checkout_fails_closed(tmp_path, path, status):
    cfg, store, task = config(tmp_path)
    with running_shop(tmp_path) as shop, counting_server() as (foreign, hits):
        attach(cfg, shop.origin)
        async with Client(params(cfg)) as client:
            quote, _ = await preview(client, task)
            store.grant(quote)
            shop.redirects[path] = (status, foreign + path)
            result = await call(client, "commit_purchase", task_id=task, quote_id=quote)
            assert result["state"] == "uncertain", result
            assert result["blocked_reason"] == "dispatch_uncertain"
            await asyncio.sleep(0.2)
            assert hits == [], "a redirect released a request to another origin"
            assert shop.clicks == 0
            assert store.reserved("pool") == "120"
            assert (await call(client, "get_order", task_id=task))["state"] == "uncertain"
            assert hits == [] and shop.clicks == 0
