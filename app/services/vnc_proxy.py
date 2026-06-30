from __future__ import annotations

import asyncio
import logging

from fastapi import WebSocket, WebSocketDisconnect

from app.config import get_settings, get_vnc_password

logger = logging.getLogger(__name__)


async def relay_vnc_websocket(websocket: WebSocket) -> None:
    if not get_vnc_password():
        await websocket.close(code=4403, reason="VNC is not configured.")
        return

    settings = get_settings().ibkr
    await websocket.accept()

    reader: asyncio.StreamReader | None = None
    writer: asyncio.StreamWriter | None = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(settings.vnc_host, settings.vnc_port),
            timeout=5,
        )
    except (OSError, asyncio.TimeoutError) as exc:
        logger.warning("Could not connect to VNC at %s:%s: %s", settings.vnc_host, settings.vnc_port, exc)
        await websocket.close(code=1011, reason="Could not reach IB Gateway VNC.")
        return

    async def websocket_to_vnc() -> None:
        assert writer is not None
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                data = message.get("bytes")
                if data is None:
                    data = message.get("text", "").encode("utf-8")
                if data:
                    writer.write(data)
                    await writer.drain()
        except WebSocketDisconnect:
            pass

    async def vnc_to_websocket() -> None:
        assert reader is not None
        try:
            while True:
                data = await reader.read(8192)
                if not data:
                    break
                await websocket.send_bytes(data)
        except WebSocketDisconnect:
            pass

    tasks = [
        asyncio.create_task(websocket_to_vnc()),
        asyncio.create_task(vnc_to_websocket()),
    ]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            task.exception()
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
