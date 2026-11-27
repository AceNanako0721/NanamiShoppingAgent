# Contributing

Use Python 3.11 or newer. Install the pinned development lock and editable package as shown in README. Keep source, synthetic fixtures and documentation public; keep machine-specific configuration, evidence, runtime state and command logs outside Git.

Begin each new behavior with an observed failing test, then implement it and run the relevant test. Add meaningful adversarial tests for authorization, target claims, budget accounting, network boundaries and uncertainty. Do not describe post-implementation regression tests as TDD evidence.

Before requesting review run:

```sh
ruff check src tests scripts
ruff format --check src tests scripts
pytest -m 'not browser' -q
pytest -m browser -q
python -m build --no-isolation
pip check
```

CI tests Python 3.11/3.12/3.13 and isolates the Chromium fixture job. It never accesses live marketplaces or uses shopping secrets. GitHub Actions are pinned to full officially verified commit SHAs. Do not upload private runtime artifacts.

The optional live smoke is explicit and read-only. Never record real merchant page bodies as test fixtures; create synthetic HTML instead. Do not access accounts, carts, credentials, seller messaging or real checkout. Do not automate login or solve CAPTCHA.

Keep changes on a feature branch, submit a draft PR with exact tested commit and limitations, and require independent review before merge. Do not weaken safety gates or remove failing checks to obtain a green build.
