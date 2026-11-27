"""Domain orchestration. Transport and host reasoning are external."""

import json
import re
from concurrent.futures import ThreadPoolExecutor

from jsonschema import Draft202012Validator

from .contract import TOOLS
from .domain import compare, exact_quote
from .public import PLATFORMS, PublicAdapter
from .security import Invalid, strict_json
from .store import digest


def envelope(task=None, state="ready", data=None, reason=None, actions=None, evidence=None):
    return dict(
        schema_version="1",
        task_id=task,
        state=state,
        summary=reason or state,
        data=data or {},
        evidence_refs=evidence or [],
        blocked_reason=reason,
        allowed_next_actions=actions or [],
        retry=dict(safe=False, after_seconds=None),
    )


class Service:
    def __init__(self, store, synthetic=None, live=False):
        self.store, self.synthetic, self.live = store, synthetic, live

    def call(self, name, args):
        # Echo a task only for tools whose schema accepts one; never for unknown/other tools.
        accepts_task = (
            isinstance(name, str)
            and name in TOOLS
            and "task_id" in TOOLS[name]["inputSchema"].get("properties", {})
        )
        supplied_task = args.get("task_id") if accepts_task and isinstance(args, dict) else None
        task = (
            supplied_task
            if isinstance(supplied_task, str) and re.fullmatch(r"task_[a-f0-9]{32}", supplied_task)
            else None
        )
        try:
            if name not in TOOLS:
                raise Invalid("unknown_tool")
            strict_json(json.dumps(args, allow_nan=False))
            if isinstance(args, dict) and "task_id" in args and task is None:
                raise Invalid()
            if not Draft202012Validator(TOOLS[name]["inputSchema"]).is_valid(args):
                raise Invalid()
            if task:
                self.store.task(task)
            return getattr(self, name.removeprefix("shopping_"))(**args)
        except (Invalid, ValueError, TypeError) as exc:
            reason = exc.code if isinstance(exc, Invalid) else "invalid_input"
            state = "uncertain" if reason == "purchase_locked" else "blocked"
            return envelope(
                task,
                state,
                reason=reason,
                actions=["shopping_get_order"]
                if reason == "purchase_locked"
                else ["shopping_get_task"]
                if task and reason != "not_authorized"
                else [],
            )
        except Exception:
            # No merchant, filesystem, transport or exception text reaches the model.
            return envelope(
                task,
                "blocked",
                reason="internal_failure",
                actions=["shopping_get_task"] if task else [],
            )

    def capabilities(self):
        levels = {p: PublicAdapter(p).capability() for p in PLATFORMS}
        if self.synthetic:
            levels["synthetic"] = dict(
                search="verified_fixture",
                inspect="verified_fixture",
                checkout="synthetic_only",
                ready=True,
            )
        return envelope(
            data=dict(
                platforms=levels,
                transport="stdio",
                live_reads_enabled=self.live,
                owner_approval="independent_operator_only",
                http="deferred",
            )
        )

    def _adapter(self, platform):
        if platform == "synthetic" and self.synthetic:
            return self.synthetic
        if platform in PLATFORMS and self.live:
            return PublicAdapter(platform)
        raise Invalid("live_reads_disabled" if platform in PLATFORMS else "adapter_unavailable")

    def search(self, task_id, query, platforms):
        task = self.store.task(task_id)
        if task["state"] == "cancelled":
            raise Invalid("task_cancelled")
        key = digest(dict(query=query, platforms=sorted(platforms)))
        for _, cached in self.store.objects(task_id, "search"):
            if cached["key"] == key:
                return cached["result"]
        self.store.state(task_id, "searching")

        def read(platform):
            try:
                return self._adapter(platform).search(query)
            except Invalid as exc:
                return dict(platform=platform, candidates=[], blocked_reason=exc.code)

        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(read, platforms))
        candidates, evidence, blockers, entries = [], [], [], []
        for result in results:
            ref = None
            if result.get("evidence"):
                ref = self.store.put(task_id, "evidence", result["evidence"])
                evidence.append(ref)
                # A public entry is inspectable evidence, never a fabricated purchasable item.
                if not result["candidates"]:
                    entry = self.store.put(
                        task_id,
                        "candidate",
                        dict(
                            platform=result["platform"],
                            public_entry=True,
                            evidence=ref,
                            url=result["evidence"]["url"],
                        ),
                    )
                    entries.append(entry)
            for candidate in result["candidates"][:20]:
                candidate["evidence"] = self.store.put(
                    task_id, "evidence", candidate.pop("source_evidence", {})
                )
                evidence.append(candidate["evidence"])
                cid = self.store.put(task_id, "candidate", candidate)
                candidates.append(
                    dict(
                        candidate_id=cid,
                        title=candidate["title"][:160],
                        platform=candidate["platform"],
                        price=candidate.get("price"),
                        currency=candidate["currency"],
                        uncertainty=list(candidate.get("uncertainty", []))
                        + ["final_checkout_required"],
                    )
                )
            if result.get("blocked_reason"):
                blockers.append(dict(platform=result["platform"], reason=result["blocked_reason"]))
        state = "candidates_ready" if candidates else "handoff"
        self.store.state(task_id, state)
        if self.store.task(task_id)["state"] == "cancelled":
            raise Invalid("task_cancelled")
        response = envelope(
            task_id,
            state,
            dict(
                candidates=candidates[:20],
                total_candidates=len(candidates),
                next_offset=20 if len(candidates) > 20 else None,
                public_entry_refs=entries,
                blockers=blockers,
            ),
            reason="platform_handoff" if not candidates else None,
            actions=["shopping_inspect", "shopping_get_task"],
            evidence=evidence[:20],
        )
        self.store.put(task_id, "search", dict(key=key, result=response))
        return response

    def inspect(self, task_id, candidate_id):
        candidate = self.store.get(task_id, candidate_id, "candidate")
        if candidate.get("public_entry"):
            return envelope(
                task_id,
                "handoff",
                dict(
                    entry=candidate,
                    evidence=self.store.get(task_id, candidate["evidence"], "evidence"),
                ),
                reason="adapter_unverified",
                evidence=[candidate["evidence"]],
            )
        current = self._adapter(candidate["platform"]).inspect(candidate)
        current["evidence"] = self.store.put(task_id, "evidence", current.pop("source_evidence"))
        return envelope(
            task_id,
            "inspected",
            dict(
                candidate=current, evidence=self.store.get(task_id, current["evidence"], "evidence")
            ),
            evidence=[current["evidence"]],
            actions=["shopping_compare", "shopping_preview_purchase"],
        )

    def compare(self, task_id, candidate_ids, confirmed_same_item, fx_ref=None):
        task = self.store.task(task_id)
        candidates = [(ref, self.store.get(task_id, ref, "candidate")) for ref in candidate_ids]
        fx = self.store.get(task_id, fx_ref, "fx") if fx_ref else None
        result = compare(
            candidates,
            confirmed_same_item,
            task["quantity"],
            task["currency"],
            fx,
            destination=task["destination"],
        )
        return envelope(
            task_id, "compared", result, actions=["shopping_inspect", "shopping_preview_purchase"]
        )

    def preview_purchase(self, task_id, candidate_id):
        task = self.store.task(task_id)
        if task["state"] == "cancelled":
            raise Invalid("task_cancelled")
        if self.store.order(task_id):
            raise Invalid("purchase_locked")
        candidate = self.store.get(task_id, candidate_id, "candidate")
        if candidate["platform"] != "synthetic":
            raise Invalid("real_checkout_disabled")
        current = self._adapter(candidate["platform"]).inspect(candidate)
        current["evidence"] = self.store.put(task_id, "evidence", current.pop("source_evidence"))
        quote = exact_quote(current, task)
        quote.update(
            candidate_id=candidate_id, url=current["url"], synthetic_origin=self.synthetic.origin
        )
        ref = self.store.quote(task_id, quote, 120)
        self.store.state(task_id, "needs_owner_approval")
        return envelope(
            task_id,
            "needs_owner_approval",
            dict(quote_id=ref, quote=self.store.get(task_id, ref, "quote")),
            reason="needs_owner_approval",
            actions=["shopping_get_task", "shopping_commit_purchase"],
            evidence=[current["evidence"]],
        )

    def commit_purchase(self, task_id, quote_id):
        quote = self.store.get(task_id, quote_id, "quote")
        if (
            quote.get("platform") != "synthetic"
            or not self.synthetic
            or quote.get("synthetic_origin") != self.synthetic.origin
        ):
            raise Invalid("real_checkout_disabled")
        quote = self.store.claim(task_id, quote_id)
        try:
            receipt = self.synthetic.commit(task_id, quote)
            self.synthetic.validate_receipt(task_id, quote, receipt)
            self.store.complete(task_id, receipt)
        except Exception as exc:
            self.store.uncertain(task_id)
            reason = (
                exc.code
                if isinstance(exc, Invalid) and exc.code in ("quote_changed", "quote_expired")
                else "dispatch_uncertain"
            )
            return envelope(task_id, "uncertain", reason=reason, actions=["shopping_get_order"])
        return envelope(task_id, "completed", dict(receipt=receipt), actions=["shopping_get_order"])

    def get_task(self, task_id, offset=0):
        task = self.store.task(task_id)
        candidates = self.store.objects(task_id, "candidate")
        page = candidates[offset : offset + 20]
        refs = [
            dict(
                candidate_id=ref,
                platform=c["platform"],
                title=c.get("title"),
                public_entry=c.get("public_entry", False),
            )
            for ref, c in page
        ]
        return envelope(
            task_id,
            task["state"],
            dict(
                constraints=task,
                candidates=refs,
                next_offset=offset + 20 if len(candidates) > offset + 20 else None,
            ),
            actions=["shopping_get_order"]
            if self.store.order(task_id)
            else ["shopping_search", "shopping_cancel_task"],
        )

    def get_order(self, task_id):
        order = self.store.order(task_id)
        if order is None:
            return envelope(task_id, self.store.task(task_id)["state"], dict(order=None))
        if order["state"] in ("submitting", "uncertain") and self.synthetic:
            quote = self.store.get(task_id, order["quote"], "quote")
            try:
                receipt = self.synthetic.receipt(task_id)
                self.synthetic.validate_receipt(task_id, quote, receipt)
                self.store.complete(task_id, receipt)
                order = self.store.order(task_id)
            except Exception:
                pass
        return envelope(
            task_id,
            order["state"],
            dict(order=order),
            reason="reconciliation_required" if order["state"] != "completed" else None,
            actions=["shopping_get_order"],
        )

    def cancel_task(self, task_id):
        self.store.cancel(task_id)
        return envelope(
            task_id,
            self.store.task(task_id)["state"],
            dict(cancelled_before_dispatch=self.store.order(task_id) is None),
            actions=["shopping_get_order"],
        )
