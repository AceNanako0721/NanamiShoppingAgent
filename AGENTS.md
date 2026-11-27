# Development rules

- Keep domain state and purchase invariants independent of MCP transport.
- Use a failing behavior test before implementing each new behavior; record actual evidence privately.
- Never use real accounts, carts, orders, browser profiles, credentials or archived implementations.
- Real marketplace checkout is disabled. Synthetic fixtures must be visibly synthetic.
- Keep runtime state outside every Git checkout, private to the operator. Never return credentials or raw DOM.
- Preserve target-level purchase locks and budget reservations across uncertainty and process failure.
- Run offline, protocol and browser suites after the final change. Do not merge without independent review.
- Worktrees follow the workspace rules. No sub-agents are required.
