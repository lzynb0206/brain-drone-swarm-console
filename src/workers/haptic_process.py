"""Isolated asyncio device bridge, owned and leased by the Qt application."""
import asyncio
import json
import os
import sys
import threading

from src.integrations.haptics.service import Console


def emit(**message):
    sys.stdout.buffer.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


async def main():
    loop = asyncio.get_running_loop()
    inbox = asyncio.Queue()
    console = Console(emit)
    tasks = set()

    def read_requests():
        # A daemon thread must not hold sys.stdin.buffer's lock at interpreter
        # shutdown (the GUI lease can expire while the pipe is still open).
        buffer = bytearray()
        try:
            while chunk := os.read(sys.stdin.fileno(), 4096):
                buffer.extend(chunk)
                while b"\n" in buffer:
                    line, _, buffer = buffer.partition(b"\n")
                    try:
                        request = json.loads(line)
                        if not isinstance(request, dict) or type(request.get("id")) is not int:
                            raise ValueError("请求缺少整数 ID")
                    except ValueError:
                        continue
                    loop.call_soon_threadsafe(inbox.put_nowait, request)
                    if request.get("action") == "shutdown":
                        return
                if len(buffer) > 65536:
                    return
        except (OSError, RuntimeError):
            pass
        finally:
            try:
                loop.call_soon_threadsafe(inbox.put_nowait, None)
            except RuntimeError:
                pass

    async def handle(request):
        try:
            emit(id=request["id"], results=await console.dispatch(request))
        except Exception as exc:
            emit(id=request["id"], error=str(exc) or type(exc).__name__)

    threading.Thread(target=read_requests, daemon=True).start()
    shutdown = None
    emit(event="ready")
    try:
        while True:
            try:
                request = await asyncio.wait_for(inbox.get(), timeout=5)
            except TimeoutError:
                emit(event="error", message="界面心跳中断，正在停止触觉设备")
                break
            if request is None:
                break
            if request.get("action") == "heartbeat":
                continue
            if request.get("action") == "shutdown":
                shutdown = request
                break
            task = asyncio.create_task(handle(request))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
    finally:
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        results = await console.dispatch({"action": "shutdown"})
        if shutdown is not None:
            emit(id=shutdown["id"], results=results)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except BrokenPipeError:
        pass
