# MCP interface version 1

Use ordinary initialize, tools/list and tools/call. No special client callbacks are needed. `tests/snapshots/tools.json` freezes all input schemas, annotations and output schemas. Results use the same nine fields:

| Field | Meaning |
| --- | --- |
| schema_version | `1` |
| task_id | Scoped opaque target ID, or null for capabilities |
| state | Current operation outcome or durable state |
| summary | Short outcome/stable reason |
| data | Bounded domain data; no raw HTML |
| evidence_refs | Private task-scoped evidence references |
| blocked_reason | Stable code or null |
| allowed_next_actions | Relevant MCP tool names; owner actions remain external |
| retry | Automatic write retry is always false |

All object schemas reject additional properties. JSON frames are limited to 64 KiB, depth 16 and 2048 values; duplicate keys, NaN/Infinity and invalid UTF-8 are rejected before dispatch. Quantity is an integer from 1 to 100, never a boolean. Monetary strings allow JPY integer units or CNY cents, no exponent/negative/nonfinite values. Default result pages contain at most 20 candidates. Inputs are also validated inside the domain service for non-MCP callers.

UTF-8 validation includes decoded strings and dictionary keys throughout the frame, including JSON-RPC IDs. Escaped lone surrogates receive a generic parse error; the connection remains usable. Valid surrogate pairs and non-BMP characters are accepted. Error envelopes return `task_id: null` unless the supplied value matches the complete `task_<32 lowercase hex digits>` format; invalid objects and strings are never copied into results.

| Tool | Required input | Result/purpose |
| --- | --- | --- |
| shopping_capabilities | none | Per-platform layers, blockers and enabled transports |
| shopping_search | task_id, query, platforms | Stored candidate IDs, unknowns and per-platform blockers; identical searches reuse persisted evidence |
| shopping_inspect | task_id, candidate_id | Fresh partial product fields and bounded evidence; entry references may yield handoff |
| shopping_compare | task_id, candidate_ids, confirmed_same_item | Same SKU/variant/condition only; complete cost ranking and excluded reasons; optional operator-issued fx_ref |
| shopping_preview_purchase | task_id, candidate_id | Synthetic exact quote ID, breakdown, constraints, expiry and approval requirement |
| shopping_commit_purchase | task_id, quote_id | At most one synthetic dispatch per stable target with a matching independent grant |
| shopping_get_task | task_id | Constraints/state and candidate references; optional offset with next_offset |
| shopping_get_order | task_id | Claim state; matching receipt reconciliation never dispatches |
| shopping_cancel_task | task_id | Cancels only before claim; never promises merchant cancellation |

Search includes `total_candidates` and `next_offset` for its batch. Get-task pagination traverses all task candidates, including older searches; candidate IDs in the initial search result provide exact references for that batch. A public product URL passed as query is validated by the same allowlist; unverified entry evidence is returned without inventing a purchasable candidate.

Amazon advertises `search=public_search_result_links`, `ready=true` for search only, and `inspect=public_entry_unverified` with `inspect_blocked_reason=adapter_unverified`. Its only advertised operation is `read_public_search`. Candidates retain unknown fields and `search_result_rank_unverified`; their private evidence records `extraction.kind=amazon_search_result_card` and the card ASIN. ASIN links do not establish same SKU, price, delivery or complete product support. No trusted cards return `no_trusted_results`; parse limits return `parse_limit`, retaining the fetched source fingerprint. Both cause a search handoff when no candidates exist.

Comparison requires both explicit host confirmation and complete equal SKU, variant and condition fields. Similar titles are insufficient. Public partial candidates normally require interpretation and cannot enter a complete comparison. Shipping/tax unknowns are excluded rather than zeroed. Points are displayed separately. Per-item fees multiply conservatively by quantity; a real adapter must eventually provide exact quantity-dependent checkout evidence.

FX observations are created only by the trusted operator with base, quote, rate, fee_rate, reserve, source, observed_at and expires_at. Rates must be positive, sourced, observed no later than now and unexpired. Conversion rounds the conservative estimate up to quote-currency precision. It is not a guarantee of card settlement. Cross-currency purchase previews are currently blocked rather than reserving a potentially incorrect JPY budget.

Common stable codes include invalid_input, not_authorized, reference_missing, needs_interpretation, unknown_fees, fx_missing, fx_expired, budget_fx_unavailable, needs_owner_approval, quote_expired, quote_changed, target_budget_exceeded, shared_budget_exceeded, purchase_locked, reconciliation_required, real_checkout_disabled, task_cancelled, live_reads_disabled, adapter_unverified, network_unavailable, access_denied, captcha, response_too_large, response_timeout, parse_limit, no_trusted_results and unsafe_navigation. No login automation or CAPTCHA bypass is offered; handoff requires human action and adapter-specific future verification.

Malformed protocol frames receive a generic JSON-RPC parse error; business failures receive this structured envelope with isError set. Internal exception text never crosses the boundary. The installed SDK client and the independent wire client both exercise actual subprocess protocol traffic. The independent client is not an agent harness; an isolated Claude harness attempt without credentials cannot complete a model-driven call and must not be counted as compatibility verification.
