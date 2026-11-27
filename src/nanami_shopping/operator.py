"""Trusted local control plane. Never expose this CLI as a model tool."""

import argparse
import json
import os

from .domain import validate_fx
from .security import Invalid, strict_json
from .server import load_config
from .store import Store


def main():
    parser = argparse.ArgumentParser(
        description="Trusted local shopping operator; no real checkout authorization."
    )
    parser.add_argument("--config", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    budget = commands.add_parser("budget")
    budget.add_argument("--id", required=True)
    budget.add_argument("--maximum", required=True)
    budget.add_argument("--currency", required=True, choices=["JPY", "CNY"])
    target = commands.add_parser("target")
    for key in ("goal", "budget", "maximum", "destination", "payment"):
        target.add_argument("--" + key, required=True)
    target.add_argument("--currency", required=True, choices=["JPY", "CNY"])
    target.add_argument("--quantity", required=True, type=int)
    grant = commands.add_parser("grant-synthetic")
    grant.add_argument("--quote", required=True)
    authorize = commands.add_parser("authorize")
    authorize.add_argument("--task", required=True)
    authorize.add_argument("--principal", required=True)
    fx = commands.add_parser("fx")
    fx.add_argument("--task", required=True)
    fx.add_argument(
        "--json", required=True, help="Bounded sourced rate observation, fee assumption and expiry."
    )
    args = parser.parse_args()
    os.umask(0o077)
    try:
        config = load_config(args.config)
        store = Store(config["state_dir"], config["principal"], config["namespace"])
        if args.command == "budget":
            store.set_budget(args.id, args.maximum, args.currency)
            result = dict(budget_created=True)
        elif args.command == "target":
            task = store.create_target(
                args.goal,
                args.budget,
                args.maximum,
                args.currency,
                args.destination,
                args.payment,
                args.quantity,
            )
            result = dict(task_id=task)
        elif args.command == "authorize":
            store.authorize(args.task, args.principal)
            result = dict(scope_authorized=True)
        elif args.command == "fx":
            value = validate_fx(strict_json(args.json))
            result = dict(fx_ref=store.put(args.task, "fx", value))
        else:
            with store.db() as db:
                row = db.execute(
                    "SELECT task FROM objects WHERE id=? AND kind=?", (args.quote, "quote")
                ).fetchone()
            if row is None:
                raise Invalid("reference_missing")
            quote = store.get(row[0], args.quote, "quote")
            if quote.get("platform") != "synthetic":
                raise Invalid("real_checkout_disabled")
            store.grant(args.quote)
            result = dict(synthetic_grant_issued=True)
        print(json.dumps(result))
    except (Invalid, OSError, ValueError) as exc:
        parser.exit(2, (exc.code if isinstance(exc, Invalid) else "operator_failure") + "\n")


if __name__ == "__main__":
    main()
