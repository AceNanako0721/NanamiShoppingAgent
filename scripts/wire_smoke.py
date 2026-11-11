"""Independent JSON-lines protocol client. No MCP SDK or model credentials."""

import argparse
import json
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--task", required=True)
    args = parser.parse_args()
    process = subprocess.Popen(
        [sys.executable, "-m", "nanami_shopping.server", "--config", args.config],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:

        def call(identifier, method, params=None, raw=None):
            process.stdin.write(
                (
                    raw
                    or json.dumps(
                        dict(jsonrpc="2.0", id=identifier, method=method, params=params or {})
                    )
                )
                + "\n"
            )
            process.stdin.flush()
            while True:
                line = process.stdout.readline()
                if not line:
                    raise RuntimeError("server disconnected")
                response = json.loads(line)
                if "id" in response:
                    return response

        initialized = call(
            1,
            "initialize",
            dict(
                protocolVersion="2025-11-25",
                capabilities={},
                clientInfo=dict(name="independent-wire-client", version="1"),
            ),
        )
        assert "result" in initialized
        process.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        process.stdin.flush()
        tools = call(2, "tools/list")["result"]["tools"]
        result = call(
            3, "tools/call", dict(name="shopping_get_task", arguments={"task_id": args.task})
        )["result"]["structuredContent"]
        duplicate = call(
            4,
            "tools/call",
            raw='{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"shopping_get_task","arguments":{"task_id":"bad","task_id":"'
            + args.task
            + '"}}}',
        )
        assert "error" in duplicate
        print(
            json.dumps(
                dict(
                    initialize=True,
                    tools=len(tools),
                    task_state=result["state"],
                    duplicate_rejected=True,
                )
            )
        )
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    main()
