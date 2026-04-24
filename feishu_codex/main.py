"""Entry point: start configured Feishu WebSocket long-connection bots."""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import threading
from concurrent.futures import Future
from pathlib import Path

import lark_oapi as lark
import lark_oapi.ws.client as lark_ws_client
from lark_oapi.api.im.v1 import P2ImMessageMessageReadV1
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

from .bridge import handle_message
from .config import ServiceConfig, load_config
from .runtime import build_runtime


class FeishuCodexService:
    def __init__(self, config: ServiceConfig) -> None:
        self.config = config
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._stop_event = threading.Event()
        self._stopping = False
        self._runtimes = {
            runtime_id: build_runtime(runtime_config)
            for runtime_id, runtime_config in config.runtimes.items()
        }
        self._routes = {
            (runtime.config.app_key, runtime.chat_id): runtime
            for runtime in self._runtimes.values()
        }
        self._ws_clients: list[lark.ws.Client] = []

    def start(self) -> None:
        self._loop_thread.start()
        future = asyncio.run_coroutine_threadsafe(self._start_feishu_apps(), self._loop)
        future.result(timeout=60)
        print(f"Started feishu-codex with {len(self.config.apps)} Feishu app(s) and {len(self._runtimes)} Codex runtime(s).")

    def wait(self) -> None:
        self._stop_event.wait()

    async def _start_feishu_apps(self) -> None:
        lark_ws_client.loop = asyncio.get_running_loop()
        for app in self.config.apps.values():
            api_client = lark.Client.builder().app_id(app.app_id).app_secret(app.app_secret).build()
            event_handler = self._build_event_handler(app.key, api_client)
            ws_client = lark.ws.Client(
                app.app_id,
                app.app_secret,
                event_handler=event_handler,
                log_level=lark.LogLevel.WARNING,
            )
            print(f"Starting Feishu app {app.key} (app_id={app.app_id[:8]}...).")
            await ws_client._connect()
            self._loop.create_task(ws_client._ping_loop())
            self._ws_clients.append(ws_client)
            print(f"Connected Feishu app {app.key}.")

    def shutdown(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        future = asyncio.run_coroutine_threadsafe(self._stop_runtimes(), self._loop)
        try:
            future.result(timeout=self._shutdown_timeout_seconds())
        except Exception as exc:
            print(f"[shutdown] codex runtime cleanup failed: {exc}")
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._stop_event.set()

    def _build_event_handler(self, app_key: str, api_client: lark.Client) -> lark.EventDispatcherHandler:
        def on_message(data: P2ImMessageReceiveV1) -> None:
            msg = data.event.message
            if msg.message_type != "text":
                return

            runtime = self._routes.get((app_key, msg.chat_id))
            if runtime is None:
                return

            try:
                content = json.loads(msg.content)
            except json.JSONDecodeError:
                return
            text = content.get("text", "")
            if not isinstance(text, str):
                return
            text = text.strip()
            if not text:
                return

            future = asyncio.run_coroutine_threadsafe(
                handle_message(
                    runtime=runtime,
                    sender_id=data.event.sender.sender_id.open_id,
                    text=text,
                    message_id=msg.message_id,
                    client=api_client,
                ),
                self._loop,
            )
            future.add_done_callback(lambda item: self._log_message_error(runtime, item))

        return (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_message_read_v1(_on_message_read)
            .register_p2_im_message_receive_v1(on_message)
            .build()
        )

    async def _stop_runtimes(self) -> None:
        for client in self._ws_clients:
            client._auto_reconnect = False
        await asyncio.gather(
            *(client._disconnect() for client in self._ws_clients),
            return_exceptions=True,
        )

        runtimes = list(self._runtimes.values())
        results = await asyncio.gather(
            *(runtime.stop() for runtime in runtimes),
            return_exceptions=True,
        )
        for runtime, result in zip(runtimes, results, strict=True):
            if isinstance(result, Exception):
                print(f"[runtime:{runtime.runtime_id}] stop failed: {result}")

    def _log_message_error(self, runtime: object, future: Future[object]) -> None:
        try:
            future.result()
        except Exception as exc:
            runtime_id = getattr(runtime, "runtime_id", "unknown")
            print(f"[runtime:{runtime_id}] message handling failed: {exc}")

    def _shutdown_timeout_seconds(self) -> int:
        runtimes = list(self.config.runtimes.values())
        if not runtimes:
            return 10
        return max(runtime.stop_timeout_seconds for runtime in runtimes) + 5


def _on_message_read(_: P2ImMessageMessageReadV1) -> None:
    # Feishu may still deliver read receipts for existing long connections.
    return


_service: FeishuCodexService | None = None


def _shutdown(signum: int, _frame: object) -> None:
    if _service is not None:
        _service.shutdown()
    raise SystemExit(128 + signum)


def main() -> None:
    args = _parse_args()
    config = load_config(args.config)

    global _service
    _service = FeishuCodexService(config)
    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    _service.start()
    try:
        _service.wait()
    finally:
        _service.shutdown()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run feishu-codex.")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("feishu-codex.yaml"),
        help="YAML config path. Defaults to ./feishu-codex.yaml.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
