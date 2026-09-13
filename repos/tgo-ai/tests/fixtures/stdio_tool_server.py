"""Isolated JSON-RPC child process; never contacts a business system."""

import json
import sys


def main() -> None:
    version = sys.argv[1]
    for line in sys.stdin:
        request = json.loads(line)
        if "id" not in request:
            continue
        method = request["method"]
        if method == "initialize":
            result = {
                "protocolVersion": request["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "isolated-stdio", "version": "1"},
            }
        elif method == "tools/list":
            names = ["unbound"] if version == "missing" else ["query", "unbound"]
            result = {"tools": [
                {"name": name, "inputSchema": {"type": "object", "properties": {
                    "tracking_no": {"type": "string"}}}}
                for name in names
            ]}
        elif method == "tools/call":
            assert request["params"]["name"] == "query"
            value = {"version": version, **request["params"].get("arguments", {})}
            result = {"content": [{"type": "text", "text": json.dumps(value)}],
                      "isError": version == "error"}
        else:
            result = {}
        print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)


if __name__ == "__main__":
    main()
