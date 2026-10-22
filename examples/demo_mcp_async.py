"""Exercise real MCP and HTTP against demo_supervisor_async.py --serve."""

import asyncio
import json
from typing import Any
from uuid import uuid4

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main() -> None:
    async with (
        httpx.AsyncClient(base_url="http://127.0.0.1:8012", timeout=30) as http,
        streamable_http_client("http://127.0.0.1:8012/mcp/") as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        names = {item.name for item in (await session.list_tools()).tools}
        assert names == {"work_start", "work_get", "work_update", "work_cancel"}

        async def call(name: str, args: dict[str, Any]) -> dict[str, Any]:
            response = await session.call_tool(name, args)
            assert not response.isError, response
            assert response.structuredContent is not None
            return response.structuredContent

        command = {
            "command_id": f"mcp-{uuid4().hex}",
            "text": "Investiga pagos con métricas y logs",
            "delay_seconds": 12,
        }
        first = await call("work_start", {"command": command})
        work_id = first["work_id"]
        duplicate = await http.post("/work", json=command)
        duplicate.raise_for_status()
        assert duplicate.json()["work_id"] == work_id
        async with asyncio.timeout(60):
            while True:
                view = await call("work_get", {"work_id": work_id})
                if len(view["children"]) == 2:
                    break
                await asyncio.sleep(0.25)
        updated = await call(
            "work_update",
            {
                "work_id": work_id,
                "command": {
                    "command_id": f"u-{uuid4().hex}",
                    "expected_revision": 1,
                    "message": "Actualiza el hijo de logs para revisar últimos 5 minutos",
                },
            },
        )
        assert updated["revision"] == 2
        async with asyncio.timeout(60):
            while True:
                view = await call("work_get", {"work_id": work_id})
                if any(item["tool"] == "update_async_task" for item in view["decision_trace"]):
                    break
                await asyncio.sleep(0.25)
        cancelled = await call(
            "work_cancel",
            {
                "work_id": work_id,
                "command": {
                    "command_id": f"c-{uuid4().hex}",
                    "expected_revision": 2,
                },
            },
        )
        async with asyncio.timeout(60):
            while cancelled["status"] != "cancelled":
                await asyncio.sleep(0.25)
                cancelled = await call("work_get", {"work_id": work_id})
        assert cancelled["result"] is None
        assert (await http.get(f"/work/{work_id}")).json()["status"] == "cancelled"
        print(
            json.dumps(
                {
                    "mcp_tools": sorted(names),
                    "work_id": work_id,
                    "http_mcp_same_work": True,
                    "updated": True,
                    "status": "cancelled",
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
