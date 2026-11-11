"""Explicit, credential-free public GET smoke. Never part of default tests or CI."""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from nanami_shopping.public import PLATFORMS, PublicAdapter


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-public-network", action="store_true", required=True)
    parser.add_argument("--query", default="ノート")
    args = parser.parse_args()

    def read(platform):
        adapter = PublicAdapter(platform)
        result = adapter.search(args.query)
        evidence = result.get("evidence")
        return dict(
            platform=platform,
            search=dict(blocked_reason=result["blocked_reason"], evidence=evidence),
            product_read="not_verified_no_trusted_product_fixture",
            candidates=len(result["candidates"]),
            capability=adapter.capability(),
        )

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(read, PLATFORMS))
    print(
        json.dumps(
            dict(
                observed_at=datetime.now(timezone.utc).isoformat(),
                environment="direct HTTPS, no proxy, no browser, no account, no cookies",
                results=results,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
