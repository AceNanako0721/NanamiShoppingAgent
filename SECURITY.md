# Security policy

This is an initial local stdio service, not a production shopping or multi-tenant authorization system. Real marketplace writes are disabled, and no genuine owner-approval provider is integrated.

The trust boundary is an operator-controlled local process configuration and private filesystem. MCP clients cannot select their principal/namespace, issue grants, change budgets, create targets, read credentials or execute arbitrary browser/shell operations. Task references alone confer no access. Namespace and ACL checks apply to every stored reference. Multiple explicitly authorized local processes share durable state and locks.

An agent with the same OS account and arbitrary filesystem/command access can edit its configuration or SQLite state. This release does not protect against that actor. Do not expose operator commands, database access or configuration writes as model tools. Synthetic grants are persisted operator records, not cryptographic proof of a real human decision. Real approval integration must isolate the authority from the model and bind the complete quote.

State directories must be outside all Git checkouts, owned by the operator, mode 0700, with 0600 regular single-link files. Symlink components, writable non-sticky ancestors and unsafe modes are refused. This does not defend against a malicious same-UID process racing filesystem operations. Use OS-user/container separation for a stronger future boundary.

Public reads are narrowly allowlisted GET requests with bounded responses, public-only DNS validation, pinned connection addresses, verified TLS, redirect validation, no cookie jar, no proxy inheritance and no private APIs. Synthetic browser traffic is restricted to one explicit numeric loopback origin, fixture paths and methods. JavaScript, service workers, downloads and persistent profiles are disabled. Never configure real accounts or external CDP.

Merchant titles, DOM and JSON are untrusted data. No page content is executed as instructions. Default results contain bounded fields and evidence references; raw pages, cookies, tokens, passwords, payment details and exception strings are never returned. Keep evidence private and never attach it to public reports.

A target claim is permanent through uncertainty. Neither new URLs nor quote IDs release the target lock or budget. Reconciliation requires a complete matching durable merchant receipt. No write operation is automatically retried. Quote changes/expiry are checked immediately before the only synthetic final action.

Report a vulnerability privately using GitHub's private vulnerability reporting feature if enabled. If unavailable, open a minimal issue asking for a private reporting channel; do not post exploit data, credentials or personal information publicly. Supported version: the current unmerged 0.1 development line; there are no production releases.
