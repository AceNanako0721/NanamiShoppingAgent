"""A visibly synthetic loopback merchant, with durable receipts and fault controls."""

import html
import json
import socket
import sqlite3
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


class Shop:
    def __init__(self, root):
        self.root = Path(root)
        self.checkout_delay = 0
        self.price = "100"
        self.variant = "blue"
        self.broken_dom = False
        self.drop_response = False
        self.block_after_accept = None
        self.accepted = threading.Event()
        self.clicks = 0
        self.lock = threading.Lock()
        with sqlite3.connect(self.root / "merchant.sqlite3") as db:
            db.execute("CREATE TABLE receipts(task TEXT PRIMARY KEY, payload TEXT)")

    def product(self):
        return dict(
            title="SYNTHETIC Widget",
            platform="synthetic",
            sku="W-1",
            variant=self.variant,
            seller="synthetic-shop",
            condition="new",
            currency="JPY",
            price=self.price,
            shipping="20",
            tax="0",
            points="5",
            destination="dest-test",
            eligibility=True,
        )

    def receipt(self, task):
        with sqlite3.connect(self.root / "merchant.sqlite3") as db:
            r = db.execute("SELECT payload FROM receipts WHERE task=?", (task,)).fetchone()
            return json.loads(r[0]) if r else None


def handler(shop):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, body, status=200, content="text/html"):
            raw = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", content)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            u = urlsplit(self.path)
            args = parse_qs(u.query)
            if u.path == "/search":
                return self.respond(
                    '<h1>SYNTHETIC SHOP</h1><a data-product href="/product/W-1">SYNTHETIC Widget</a>'
                )
            if u.path == "/product/W-1" or u.path == "/checkout":
                if u.path == "/checkout":
                    time.sleep(shop.checkout_delay)
                product = shop.product()
                tag = "data-broken" if shop.broken_dom else "data-product-json"
                body = (
                    "<h1>SYNTHETIC SHOP — no real goods</h1><div "
                    + tag
                    + '="'
                    + html.escape(json.dumps(product), quote=True)
                    + '"></div>'
                )
                if u.path == "/checkout":
                    body += '<form method="post" action="/purchase">'
                    for key in ["task", "hash", "quantity"]:
                        body += (
                            '<input type="hidden" name="'
                            + key
                            + '" value="'
                            + html.escape(args.get(key, [""])[0], quote=True)
                            + '">'
                        )
                    body += (
                        '<button id="synthetic-final-submit">Place SYNTHETIC order</button></form>'
                    )
                return self.respond(body)
            if u.path.startswith("/receipt/"):
                receipt = shop.receipt(u.path.removeprefix("/receipt/"))
                return self.respond(
                    json.dumps(receipt), 200 if receipt else 404, "application/json"
                )
            self.respond("SYNTHETIC not found", 404)

        def do_POST(self):
            if self.path != "/purchase":
                return self.respond("SYNTHETIC disallowed", 405)
            body = self.rfile.read(min(int(self.headers.get("Content-Length", "0")), 4096)).decode()
            args = parse_qs(body)
            task, fingerprint = args["task"][0], args["hash"][0]
            count = int(args["quantity"][0])
            product = shop.product()
            from nanami_shopping.domain import landed

            receipt = dict(
                synthetic=True,
                task_id=task,
                quote_hash=fingerprint,
                payable=str(landed(product, count)),
                currency="JPY",
                quantity=count,
                sku=product["sku"],
                variant=product["variant"],
                seller=product["seller"],
                condition=product["condition"],
            )
            with shop.lock:
                shop.clicks += 1
                with sqlite3.connect(shop.root / "merchant.sqlite3") as db:
                    db.execute(
                        "INSERT OR IGNORE INTO receipts VALUES(?,?)", (task, json.dumps(receipt))
                    )
            shop.accepted.set()
            if shop.block_after_accept:
                shop.block_after_accept.wait(timeout=20)
            if shop.drop_response:
                self.connection.shutdown(socket.SHUT_RDWR)
                return self.connection.close()
            self.respond(json.dumps(receipt), content="application/json")

    return Handler


@contextmanager
def running_shop(root):
    shop = Shop(root)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(shop))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    shop.origin = "http://127.0.0.1:" + str(server.server_port)
    try:
        yield shop
    finally:
        if shop.block_after_accept:
            shop.block_after_accept.set()
        server.shutdown()
        server.server_close()
        thread.join()
