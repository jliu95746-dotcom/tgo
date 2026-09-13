"""Local verification plugin: stdlib only, no customer or business data access."""

import asyncio
import json
import os
import struct


async def main():
    reader, writer = await asyncio.open_connection(
        os.environ["TGO_TCP_HOST"], int(os.environ["TGO_TCP_PORT"])
    )

    async def send(message):
        content = json.dumps(message).encode()
        writer.write(struct.pack(">I", len(content)) + content)
        await writer.drain()

    async def receive():
        length = struct.unpack(">I", await reader.readexactly(4))[0]
        return json.loads(await reader.readexactly(length))

    await send(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "register",
            "params": {
                "id": os.environ["TGO_PLUGIN_ID"],
                "name": "隔离验证插件",
                "version": os.environ.get("PROBE_VERSION", "1"),
                "capabilities": [{"type": "visitor_panel", "title": "运行验证"}],
            },
        }
    )
    registered = await receive()
    assert registered.get("result", {}).get("success") is True
    print("PROBE_REGISTERED", flush=True)
    try:
        while True:
            request = await receive()
            result = {
                "template": "card",
                "data": {
                    "title": "验证通过",
                    "version": os.environ.get("PROBE_VERSION", "1"),
                    "echo": request.get("params", {}).get("context", {}).get("probe"),
                },
            }
            await send({"jsonrpc": "2.0", "id": request["id"], "result": result})
            if request.get("method") == "shutdown":
                break
    except asyncio.IncompleteReadError:
        pass
    finally:
        writer.close()
        await writer.wait_closed()


asyncio.run(main())
