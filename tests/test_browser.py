import asyncio
import json
import os
import signal
import subprocess
import sys
import threading

import pytest
from mcp.client import Client
from synthetic_shop import running_shop
from test_protocol import config, params

pytestmark = pytest.mark.browser


async def call(client, name, **args):
    result = await client.call_tool("shopping_" + name, args)
    assert result.structured_content, result
    return result.structured_content


def attach(cfg, origin):
    data = json.loads(cfg.read_text())
    data["synthetic_origin"] = origin
    cfg.write_text(json.dumps(data))


async def preview(client, task):
    search = await call(client, "search", task_id=task, query="Widget", platforms=["synthetic"])
    assert search["state"] == "candidates_ready", search
    ref = search["data"]["candidates"][0]["candidate_id"]
    inspected = await call(client, "inspect", task_id=task, candidate_id=ref)
    assert inspected["data"]["candidate"]["sku"] == "W-1"
    comparison = await call(
        client, "compare", task_id=task, candidate_ids=[ref], confirmed_same_item=True
    )
    assert comparison["data"]["ranking"][0]["landed"] == "120"
    quote = await call(client, "preview_purchase", task_id=task, candidate_id=ref)
    assert quote["state"] == "needs_owner_approval", quote
    return quote["data"]["quote_id"], ref


async def test_browser_mcp_full_synthetic_flow_and_independent_grant(tmp_path):
    cfg, store, task = config(tmp_path)
    with running_shop(tmp_path) as shop:
        attach(cfg, shop.origin)
        async with Client(params(cfg)) as client:
            quote, _ = await preview(client, task)
            denied = await call(client, "commit_purchase", task_id=task, quote_id=quote)
            assert denied["blocked_reason"] == "needs_owner_approval"
            store.grant(quote)  # Separate trusted synthetic operator; not a model tool.
            result = await call(client, "commit_purchase", task_id=task, quote_id=quote)
            assert result["state"] == "completed", result
            assert result["data"]["receipt"]["synthetic"] is True
            assert shop.clicks == 1
        async with Client(params(cfg)) as client:
            assert (await call(client, "get_order", task_id=task))["state"] == "completed"
            assert (await call(client, "commit_purchase", task_id=task, quote_id=quote))[
                "blocked_reason"
            ] == "purchase_locked"
            assert shop.clicks == 1


@pytest.mark.parametrize("mutation", ["price", "variant", "broken_dom"])
async def test_final_review_change_blocks_real_browser_click(tmp_path, mutation):
    cfg, store, task = config(tmp_path)
    with running_shop(tmp_path) as shop:
        attach(cfg, shop.origin)
        async with Client(params(cfg)) as client:
            quote, _ = await preview(client, task)
            store.grant(quote)
            setattr(
                shop, mutation, {"price": "101", "variant": "red", "broken_dom": True}[mutation]
            )
            result = await call(client, "commit_purchase", task_id=task, quote_id=quote)
            assert result["state"] == "uncertain", result
            assert shop.clicks == 0
            assert store.reserved("pool") == "120"
            assert (await call(client, "get_order", task_id=task))["state"] == "uncertain"
            assert shop.clicks == 0
            assert store.reserved("pool") == "120"


async def test_accepted_disconnected_response_reconciles_without_second_dispatch(tmp_path):
    cfg, store, task = config(tmp_path)
    with running_shop(tmp_path) as shop:
        attach(cfg, shop.origin)
        async with Client(params(cfg)) as client:
            quote, _ = await preview(client, task)
            store.grant(quote)
            shop.drop_response = True
            result = await call(client, "commit_purchase", task_id=task, quote_id=quote)
            assert result["state"] == "uncertain", result
            assert shop.clicks == 1
        async with Client(params(cfg)) as client:
            result = await call(client, "get_order", task_id=task)
            assert result["state"] == "completed", result
            assert shop.clicks == 1


async def test_worker_killed_after_merchant_acceptance_restart_reconciles(tmp_path):
    cfg, store, task = config(tmp_path)
    with running_shop(tmp_path) as shop:
        attach(cfg, shop.origin)
        async with Client(params(cfg)) as client:
            quote, _ = await preview(client, task)
        store.grant(quote)
        shop.block_after_accept = threading.Event()
        process = subprocess.Popen(
            [sys.executable, "scripts/commit_once.py", str(cfg), task, quote],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            assert await asyncio.to_thread(shop.accepted.wait, 15)
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
            assert store.task(task)["state"] == "submitting"
            assert store.reserved("pool") == "120"
            shop.block_after_accept.set()
            async with Client(params(cfg)) as client:
                result = await call(client, "get_order", task_id=task)
                assert result["state"] == "completed", result
                assert shop.clicks == 1
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()


async def test_independent_mcp_processes_commit_once(tmp_path):
    cfg, store, task = config(tmp_path)
    with running_shop(tmp_path) as shop:
        attach(cfg, shop.origin)
        async with Client(params(cfg)) as client:
            quote, _ = await preview(client, task)
        store.grant(quote)
        async with Client(params(cfg)) as a, Client(params(cfg)) as b:
            results = await asyncio.gather(
                call(a, "commit_purchase", task_id=task, quote_id=quote),
                call(b, "commit_purchase", task_id=task, quote_id=quote),
            )
            assert sum(r["state"] == "completed" for r in results) == 1, results
            assert shop.clicks == 1


async def test_quote_expires_during_final_review_no_click(tmp_path):
    import time

    from nanami_shopping.store import encode

    cfg, store, task = config(tmp_path)
    with running_shop(tmp_path) as shop:
        attach(cfg, shop.origin)
        async with Client(params(cfg)) as client:
            quote_id, _ = await preview(client, task)
            quote = store.get(task, quote_id, "quote")
            quote["expires_at"] = int(time.time()) + 3
            with store.db() as db:
                db.execute("UPDATE objects SET payload=? WHERE id=?", (encode(quote), quote_id))
            store.grant(quote_id)
            shop.checkout_delay = 4
            result = await call(client, "commit_purchase", task_id=task, quote_id=quote_id)
            assert shop.clicks == 0, result
            assert result["state"] == "uncertain"
