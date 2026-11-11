"""SQLite control plane and atomic, target-wide purchase claims."""

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from decimal import Decimal

from .security import Invalid, money, private_directory, private_file


def identifier(prefix):
    return prefix + "_" + uuid.uuid4().hex


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


class Store:
    def __init__(self, root, principal, namespace):
        self.root = private_directory(root)
        self.principal, self.namespace = principal, namespace
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS budgets(
                    namespace TEXT, id TEXT, maximum TEXT, currency TEXT,
                    PRIMARY KEY(namespace,id));
                CREATE TABLE IF NOT EXISTS tasks(
                    id TEXT PRIMARY KEY, namespace TEXT, goal TEXT, payload TEXT,
                    state TEXT NOT NULL, UNIQUE(namespace,goal));
                CREATE TABLE IF NOT EXISTS acl(task TEXT, principal TEXT,
                    PRIMARY KEY(task,principal));
                CREATE TABLE IF NOT EXISTS objects(
                    id TEXT PRIMARY KEY, task TEXT, kind TEXT, payload TEXT);
                CREATE TABLE IF NOT EXISTS grants(quote TEXT PRIMARY KEY, hash TEXT);
                CREATE TABLE IF NOT EXISTS claims(
                    task TEXT PRIMARY KEY, quote TEXT, budget TEXT, amount TEXT,
                    state TEXT, receipt TEXT);
            """)

    @contextmanager
    def db(self):
        private_directory(self.root)
        path = private_file(self.root / "shopping.sqlite3")
        for suffix in ["-journal", "-wal", "-shm"]:
            side = self.root / (path.name + suffix)
            if side.exists() or side.is_symlink():
                private_file(side)
        connection = sqlite3.connect(path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA synchronous=FULL")
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            if connection.in_transaction:
                connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _task(self, db, task):
        row = db.execute(
            "SELECT t.* FROM tasks t JOIN acl a ON a.task=t.id "
            "WHERE t.id=? AND t.namespace=? AND a.principal=?",
            (task, self.namespace, self.principal),
        ).fetchone()
        if row is None:
            raise Invalid("not_authorized")
        return {**json.loads(row["payload"]), "task_id": task, "state": row["state"]}

    def task(self, task):
        with self.db() as db:
            return self._task(db, task)

    def set_budget(self, name, maximum, currency):
        maximum = str(money(maximum, currency))
        with self.db() as db:
            old = db.execute(
                "SELECT * FROM budgets WHERE namespace=? AND id=?", (self.namespace, name)
            ).fetchone()
            if old and (old["maximum"], old["currency"]) != (maximum, currency):
                raise Invalid("immutable_budget")
            db.execute(
                "INSERT OR IGNORE INTO budgets VALUES(?,?,?,?)",
                (self.namespace, name, maximum, currency),
            )

    def create_target(self, goal, budget, maximum, currency, destination, payment, quantity):
        maximum = str(money(maximum, currency))
        if type(quantity) is not int or not 1 <= quantity <= 100:
            raise Invalid()
        value = dict(
            budget=budget,
            maximum=maximum,
            currency=currency,
            destination=destination,
            payment=payment,
            quantity=quantity,
        )
        with self.db() as db:
            pool = db.execute(
                "SELECT currency FROM budgets WHERE namespace=? AND id=?", (self.namespace, budget)
            ).fetchone()
            if pool is None or pool["currency"] != currency:
                raise Invalid("budget_missing")
            old = db.execute(
                "SELECT * FROM tasks WHERE namespace=? AND goal=?", (self.namespace, goal)
            ).fetchone()
            if old:
                self._task(db, old["id"])
                if json.loads(old["payload"]) != value:
                    raise Invalid("immutable_target")
                return old["id"]
            task = identifier("task")
            db.execute(
                "INSERT INTO tasks VALUES(?,?,?,?,?)",
                (task, self.namespace, goal, encode(value), "created"),
            )
            db.execute("INSERT INTO acl VALUES(?,?)", (task, self.principal))
            return task

    def authorize(self, task, principal):
        with self.db() as db:
            self._task(db, task)
            db.execute("INSERT OR IGNORE INTO acl VALUES(?,?)", (task, principal))

    def put(self, task, kind, payload):
        with self.db() as db:
            self._task(db, task)
            ref = identifier(kind)
            db.execute("INSERT INTO objects VALUES(?,?,?,?)", (ref, task, kind, encode(payload)))
            return ref

    def _object(self, db, task, ref, kind):
        self._task(db, task)
        row = db.execute(
            "SELECT payload FROM objects WHERE id=? AND task=? AND kind=?", (ref, task, kind)
        ).fetchone()
        if row is None:
            raise Invalid("reference_missing")
        return json.loads(row[0])

    def get(self, task, ref, kind):
        with self.db() as db:
            return self._object(db, task, ref, kind)

    def objects(self, task, kind):
        with self.db() as db:
            self._task(db, task)
            return [
                (r["id"], json.loads(r["payload"]))
                for r in db.execute(
                    "SELECT id,payload FROM objects WHERE task=? AND kind=? ORDER BY rowid",
                    (task, kind),
                )
            ]

    def state(self, task, state):
        with self.db() as db:
            self._task(db, task)
            if not db.execute("SELECT 1 FROM claims WHERE task=?", (task,)).fetchone():
                db.execute(
                    "UPDATE tasks SET state=? WHERE id=? AND state<>'cancelled'", (state, task)
                )

    def quote(self, task, payload, ttl):
        constraints = self.task(task)
        constraints.pop("state")
        return self.put(
            task,
            "quote",
            {**payload, "constraints": constraints, "expires_at": int(time.time()) + ttl},
        )

    def grant(self, quote):
        # Trusted operator only. Never registered as an MCP tool.
        with self.db() as db:
            row = db.execute(
                "SELECT task FROM objects WHERE id=? AND kind=?", (quote, "quote")
            ).fetchone()
            if row is None:
                raise Invalid("reference_missing")
            value = self._object(db, row[0], quote, "quote")
            if value["expires_at"] <= time.time():
                raise Invalid("quote_expired")
            db.execute("INSERT OR REPLACE INTO grants VALUES(?,?)", (quote, digest(value)))

    def _reserved(self, db, budget):
        return sum(
            (
                Decimal(r[0])
                for r in db.execute(
                    "SELECT c.amount FROM claims c JOIN tasks t ON t.id=c.task "
                    "WHERE t.namespace=? AND c.budget=?",
                    (self.namespace, budget),
                )
            ),
            Decimal(0),
        )

    def reserved(self, budget):
        with self.db() as db:
            return str(self._reserved(db, budget))

    def claim(self, task, quote):
        with self.db() as db:
            target = self._task(db, task)
            if db.execute("SELECT 1 FROM claims WHERE task=?", (task,)).fetchone():
                raise Invalid("purchase_locked")
            if target["state"] == "cancelled":
                raise Invalid("task_cancelled")
            value = self._object(db, task, quote, "quote")
            if value["expires_at"] <= time.time():
                raise Invalid("quote_expired")
            grant = db.execute("SELECT hash FROM grants WHERE quote=?", (quote,)).fetchone()
            if grant is None or grant[0] != digest(value):
                raise Invalid("needs_owner_approval")
            if value["currency"] != target["currency"]:
                raise Invalid("budget_fx_unavailable")
            amount = money(value["payable"], value["currency"])
            if amount > money(target["maximum"], target["currency"]):
                raise Invalid("target_budget_exceeded")
            budget = db.execute(
                "SELECT * FROM budgets WHERE namespace=? AND id=?",
                (self.namespace, target["budget"]),
            ).fetchone()
            if self._reserved(db, target["budget"]) + amount > Decimal(budget["maximum"]):
                raise Invalid("shared_budget_exceeded")
            db.execute(
                "INSERT INTO claims VALUES(?,?,?,?,?,NULL)",
                (task, quote, target["budget"], str(amount), "submitting"),
            )
            db.execute("UPDATE tasks SET state=? WHERE id=?", ("submitting", task))
            return value

    def uncertain(self, task):
        with self.db() as db:
            self._task(db, task)
            db.execute(
                "UPDATE claims SET state=? WHERE task=? AND state=?",
                ("uncertain", task, "submitting"),
            )
            db.execute(
                "UPDATE tasks SET state=(SELECT state FROM claims WHERE task=?) "
                "WHERE id=? AND EXISTS(SELECT 1 FROM claims WHERE task=?)",
                (task, task, task),
            )

    def order(self, task):
        with self.db() as db:
            self._task(db, task)
            row = db.execute("SELECT * FROM claims WHERE task=?", (task,)).fetchone()
            return dict(row) if row else None

    def complete(self, task, receipt):
        with self.db() as db:
            self._task(db, task)
            db.execute(
                "UPDATE claims SET state=?,receipt=? WHERE task=?",
                ("completed", encode(receipt), task),
            )
            db.execute("UPDATE tasks SET state=? WHERE id=?", ("completed", task))

    def cancel(self, task):
        self.state(task, "cancelled")
