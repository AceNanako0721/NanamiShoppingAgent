# NanamiShoppingAgent

Shopping domain capabilities over standard MCP. Your host agent reasons about the request; this service stores scoped tasks, gathers evidence, compares exact costs, and enforces purchase locks and budget reservations. It needs no model key and contains no LLM loop.

**Real marketplace checkout is disabled.** The complete ordering path is available only against an explicitly configured synthetic loopback shop. An operator grant in this release is a synthetic test facility, not an integration with real owner approval.

Python 3.11–3.13; official MCP Python SDK 2.3.0. The initial transport is stdio. Separate stdio processes share SQLite tasks and atomic locks when explicitly configured for the same state and authorized scope. Streamable HTTP is deferred until authentication, namespace isolation and host/origin controls can be verified together.

## Install and validate

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.lock
pip install --no-deps --no-build-isolation -e .
pytest -m 'not browser' -q
python -m playwright install chromium
pytest -m browser -q
python -m build --no-isolation
```

Default tests use synthetic data and loopback networking only. Dependency installation and Chromium download need network access. Browser tests use fresh contexts, no personal profiles, no external CDP and no real accounts.

## Start a scoped MCP server

Create an operator-owned config outside every Git checkout. This example uses a private application state directory and opaque destination/payment references, never addresses or payment details:

```sh
python - <<'PY'
import json, os
from pathlib import Path
os.umask(0o077)
root = Path.home() / '.local' / 'state' / 'nanami-shopping'
root.mkdir(parents=True, mode=0o700, exist_ok=True)
config = {'state_dir': str(root / 'runtime'), 'principal': 'host-a', 'namespace': 'personal'}
(root / 'config.json').write_text(json.dumps(config))
(root / 'config.json').chmod(0o600)
print(root / 'config.json')
PY
```

Set `NANAMI_CONFIG` to the printed config path, then run these trusted control-plane commands yourself:

```sh
nanami-operator --config "$NANAMI_CONFIG" budget --id monthly --maximum 10000 --currency JPY
nanami-operator --config "$NANAMI_CONFIG" target --goal notebook --budget monthly --maximum 1000 \
  --currency JPY --destination dest-opaque --payment pay-opaque --quantity 1
nanami-shopping --config "$NANAMI_CONFIG"
```

The target command prints a stable `task_id`. Repeating the same goal and constraints reuses it; changing constraints requires operator review. The model cannot create replacement targets, change the budget, authorize another principal, or issue grants through MCP. Configure your host's MCP command as `nanami-shopping` with arguments `--config` and the config path. Do not wrap the operator CLI in a model tool.

Public networking is disabled by default. Set `"live_reads": true` in the private config to enable allowlisted credential-free public GETs. No login, carts, checkout, private APIs or cookies are used. A query may also be an allowlisted public product URL for entry evidence; product details normally use stored candidate references. See [the contract](docs/interface.md).

## Current capability limits

| Platform | Search | Product reading | Checkout |
| --- | --- | --- | --- |
| Rakuten Japan | Public listing links, bounded and paginated | Partial structured fields; unknown fees/specifications remain unknown | Disabled |
| Yahoo! Flea Market | Public listing links, bounded and paginated | Partial structured fields; unknown fees/specifications remain unknown | Disabled |
| Amazon Japan | Public GET entry; response-size boundary may block | Entry implemented, extractor unverified | Disabled |
| Mercari Japan | Public GET entry; client-rendered results unverified | Entry implemented, extractor unverified | Disabled |
| PDD Japan free-shipping area | Public GET entry only; Japan-specific section unverified | Exact variant and Japan eligibility unverified | Disabled |
| Synthetic loopback shop | Browser verified | Browser verified | Independent synthetic operator grant only |

A successful homepage or HTTP 200 never proves search, shipping eligibility or checkout support. Page changes can return a controlled handoff at any time. These are engineering capability limits; account access and purchase authorization are separate future external dependencies.

## Tools and workflow

Nine tools cover capabilities, search, inspect, compare, preview, commit, task resumption, order reconciliation and task cancellation. Search results and quotes stay server-side; the host passes short references. Costs use Decimal original currency units. Unknown shipping/tax is excluded from landed-price ranking, points stay separate, and cross-currency comparison needs an operator-recorded, sourced, expiring FX reference with fee and reserve assumptions. Cross-currency purchase budgets are blocked in this release.

A commit atomically claims the **purchase target**, reserves budget, and records `submitting` before any browser effect. All aliases, sellers, platforms and replacement quotes for that target remain locked after uncertainty. A crash after merchant acceptance can finish only through a matching durable receipt. No write is automatically retried. A changed final price, SKU, variant, condition, seller or expired quote prevents the final synthetic click; its conservative reservation remains held for review. Cancellation does not cancel a merchant order or release an uncertain claim.

[中文快速上手](docs/quickstart.zh-CN.md) · [Architecture](docs/architecture.md) · [Contract](docs/interface.md) · [State machine](docs/state-machine.md) · [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md)
