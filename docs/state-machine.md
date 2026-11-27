# Durable states and recovery

| From | Event | To | Persistent effect |
| --- | --- | --- | --- |
| created / candidate states | Explicit search | searching → candidates_ready or handoff | Scoped evidence and candidates |
| candidates_ready | Preview synthetic candidate | needs_owner_approval | Immutable expiring quote |
| needs_owner_approval | Valid independent synthetic grant and atomic budget checks | submitting | One target claim and full budget reservation before browser effects |
| submitting | Matching durable merchant receipt | completed | Receipt retained, spending remains charged |
| submitting | Failure, changed checkout, expiration or lost response | uncertain | Claim and budget stay held |
| submitting / uncertain | Restart and matching durable receipt | completed | No dispatch during reconciliation |
| submitting / uncertain | Missing or mismatched receipt | unchanged | No unlock, release or retry |
| any unclaimed state | Cancel | cancelled | Subsequent dispatch rejected |
| submitting / uncertain / completed | Cancel | unchanged | Merchant orders are not cancelled |

A killed process may leave `submitting` without running recovery code. That is a deliberate durable lock, not permission to try again. New quotes and altered candidate aliases do not affect it. A missing receipt can mean an order has not happened **or** cannot yet be verified. Only matching merchant evidence can complete the target.

This version keeps preflight failures conservatively locked too. There is no automatic refund, reservation release, order cancellation, target reset or destructive recovery command. A future operator recovery workflow must establish nonacceptance with authoritative evidence and be independently reviewed before enabling a new dispatch.

The synthetic browser checks the final request immediately before network release: exact POST method, origin and path, form URL-encoded content type, and exactly one each of `task`, full quote `hash` and approved `quantity`, with no other fields. Only the initial checkout GET and one matching POST can be released in a commit context, so submit-button overrides cannot escape through read navigation. Quote expiration is checked again after any click actionability wait. A blocked request leaves the claim and reservation held, even when no POST was released.

Multiple goals share a named namespace budget. Its cap is immutable, uses the target currency and is checked in the same transaction as the claim. All claims count, including completed spending and uncertainty. The model cannot release a reservation by cancelling or switching to another quote. A trusted operator may create a distinct genuine goal; it must not use new goals to bypass an unresolved objective.
