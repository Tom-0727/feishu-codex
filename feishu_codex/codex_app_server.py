"""Codex app-server JSON-RPC client."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


NotificationHandler = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class CodexAppServerConfig:
    runtime_id: str
    codex_bin: str
    cwd: Path
    sandbox: str
    approval_policy: str
    skip_git_repo_check: bool
    rpc_timeout_seconds: int
    turn_timeout_seconds: int
    compact_timeout_seconds: int
    stop_timeout_seconds: int


@dataclass
class CodexTurnResult:
    thread_id: str
    final_text: str = ""
    token_usage: dict[str, Any] | None = None


@dataclass
class CodexCompactResult:
    thread_id: str
    saw_compaction_item: bool = False


@dataclass
class _PendingRequest:
    method: str
    future: asyncio.Future[Any]


class CodexAppServerClient:
    def __init__(self, config: CodexAppServerConfig) -> None:
        self.config = config
        self._proc: asyncio.subprocess.Process | None = None
        self._stdout_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._exit_task: asyncio.Task[None] | None = None
        self._start_lock = asyncio.Lock()
        self._next_id = 1
        self._pending: dict[int, _PendingRequest] = {}
        self._notification_handlers: dict[str, set[NotificationHandler]] = {}
        self._exit_handlers: set[Callable[[Exception], None]] = set()
        self._known_threads: set[str] = set()
        self._initialized = False

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None and self._initialized

    async def start(self) -> None:
        if self.is_alive():
            return

        async with self._start_lock:
            if self.is_alive():
                return

            await self.stop()

            proc = await asyncio.create_subprocess_exec(
                self.config.codex_bin,
                "app-server",
                cwd=str(self.config.cwd),
                env=os.environ.copy(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=10 * 1024 * 1024,
            )
            self._proc = proc
            self._initialized = False
            self._known_threads.clear()
            self._stdout_task = asyncio.create_task(self._read_stdout(proc))
            self._stderr_task = asyncio.create_task(self._read_stderr(proc))
            self._exit_task = asyncio.create_task(self._watch_exit(proc))

            try:
                await self._send_request(
                    "initialize",
                    {
                        "clientInfo": {
                            "name": f"feishu-codex:{self.config.runtime_id}",
                            "title": f"feishu-codex:{self.config.runtime_id}",
                            "version": "0.1.0",
                        }
                    },
                )
                self._initialized = True
            except Exception:
                await self.stop()
                raise

    async def stop(self) -> None:
        proc = self._proc
        if proc is None:
            return

        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=self.config.stop_timeout_seconds)
            except TimeoutError:
                proc.kill()
                await proc.wait()

        self._clear_process_state()

    async def ensure_thread(self, thread_id: str | None) -> str:
        await self.start()
        if thread_id:
            if thread_id not in self._known_threads:
                await self.resume_thread(thread_id)
            return thread_id

        new_thread_id = await self.start_thread()
        return new_thread_id

    async def start_thread(self) -> str:
        result = await self.request(
            "thread/start",
            self._thread_options(),
        )
        thread_id = result.get("thread", {}).get("id")
        if not isinstance(thread_id, str) or not thread_id:
            raise RuntimeError("thread/start returned no thread id")
        self._known_threads.add(thread_id)
        return thread_id

    async def resume_thread(self, thread_id: str) -> None:
        params = self._thread_options()
        params["threadId"] = thread_id
        await self.request("thread/resume", params)
        self._known_threads.add(thread_id)

    async def run_turn(self, thread_id: str, text: str) -> CodexTurnResult:
        done: asyncio.Future[CodexTurnResult] = asyncio.get_running_loop().create_future()
        final_text = ""
        token_usage: dict[str, Any] | None = None
        disposers: list[Callable[[], None]] = []

        def finish(result: CodexTurnResult) -> None:
            if not done.done():
                done.set_result(result)

        def fail(error: Exception) -> None:
            if not done.done():
                done.set_exception(error)

        def on_item_completed(params: dict[str, Any]) -> None:
            nonlocal final_text
            if params.get("threadId") != thread_id:
                return
            item = params.get("item")
            if not isinstance(item, dict):
                return
            if item.get("type") == "agentMessage" and isinstance(item.get("text"), str):
                final_text = item["text"]

        def on_token_usage(params: dict[str, Any]) -> None:
            nonlocal token_usage
            if params.get("threadId") != thread_id:
                return
            value = params.get("tokenUsage")
            if isinstance(value, dict):
                token_usage = value

        def on_turn_completed(params: dict[str, Any]) -> None:
            if params.get("threadId") != thread_id:
                return
            finish(CodexTurnResult(thread_id=thread_id, final_text=final_text, token_usage=token_usage))

        def on_turn_failed(params: dict[str, Any]) -> None:
            if params.get("threadId") != thread_id:
                return
            error = params.get("error")
            message = error.get("message") if isinstance(error, dict) else None
            fail(RuntimeError(f"turn failed: {message or 'unknown error'}"))

        disposers.append(self.on_notification("item/completed", on_item_completed))
        disposers.append(self.on_notification("thread/tokenUsage/updated", on_token_usage))
        disposers.append(self.on_notification("turn/completed", on_turn_completed))
        disposers.append(self.on_notification("turn/failed", on_turn_failed))
        disposers.append(self.on_exit(fail))

        try:
            await self.request(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": text}],
                },
            )
            return await asyncio.wait_for(done, timeout=self.config.turn_timeout_seconds)
        except TimeoutError as exc:
            raise TimeoutError(f"turn timed out after {self.config.turn_timeout_seconds}s") from exc
        finally:
            for dispose in disposers:
                dispose()

    async def compact_thread(self, thread_id: str) -> CodexCompactResult:
        done: asyncio.Future[CodexCompactResult] = asyncio.get_running_loop().create_future()
        saw_compaction_item = False
        disposers: list[Callable[[], None]] = []

        def finish() -> None:
            if not done.done():
                done.set_result(
                    CodexCompactResult(
                        thread_id=thread_id,
                        saw_compaction_item=saw_compaction_item,
                    )
                )

        def fail(error: Exception) -> None:
            if not done.done():
                done.set_exception(error)

        def on_item_completed(params: dict[str, Any]) -> None:
            nonlocal saw_compaction_item
            if params.get("threadId") != thread_id:
                return
            item = params.get("item")
            if isinstance(item, dict) and item.get("type") == "contextCompaction":
                saw_compaction_item = True

        def on_turn_completed(params: dict[str, Any]) -> None:
            if params.get("threadId") == thread_id:
                finish()

        def on_turn_failed(params: dict[str, Any]) -> None:
            if params.get("threadId") != thread_id:
                return
            error = params.get("error")
            message = error.get("message") if isinstance(error, dict) else None
            fail(RuntimeError(f"compact failed: {message or 'unknown error'}"))

        disposers.append(self.on_notification("item/completed", on_item_completed))
        disposers.append(self.on_notification("turn/completed", on_turn_completed))
        disposers.append(self.on_notification("turn/failed", on_turn_failed))
        disposers.append(self.on_exit(fail))

        try:
            await self.request("thread/compact/start", {"threadId": thread_id})
            return await asyncio.wait_for(done, timeout=self.config.compact_timeout_seconds)
        except TimeoutError as exc:
            raise TimeoutError(f"compact timed out after {self.config.compact_timeout_seconds}s") from exc
        finally:
            for dispose in disposers:
                dispose()

    async def run(self, prompt: str, thread_id: str | None = None) -> CodexTurnResult:
        active_thread_id = await self.ensure_thread(thread_id)
        return await self.run_turn(active_thread_id, prompt)

    async def compact(self, thread_id: str) -> CodexCompactResult:
        active_thread_id = await self.ensure_thread(thread_id)
        return await self.compact_thread(active_thread_id)

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        await self.start()
        return await self._send_request(method, params)

    def on_notification(self, method: str, handler: NotificationHandler) -> Callable[[], None]:
        handlers = self._notification_handlers.setdefault(method, set())
        handlers.add(handler)

        def dispose() -> None:
            handlers.discard(handler)
            if not handlers:
                self._notification_handlers.pop(method, None)

        return dispose

    def on_exit(self, handler: Callable[[Exception], None]) -> Callable[[], None]:
        self._exit_handlers.add(handler)

        def dispose() -> None:
            self._exit_handlers.discard(handler)

        return dispose

    def _thread_options(self) -> dict[str, Any]:
        return {
            "cwd": str(self.config.cwd),
            "sandbox": self.config.sandbox,
            "approvalPolicy": self.config.approval_policy,
            "skipGitRepoCheck": self.config.skip_git_repo_check,
        }

    async def _send_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.returncode is not None:
            raise RuntimeError("codex app-server is not running")

        request_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending[request_id] = _PendingRequest(method=method, future=future)

        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            },
            ensure_ascii=False,
        )
        try:
            proc.stdin.write((body + "\n").encode("utf-8"))
            await proc.stdin.drain()
            result = await asyncio.wait_for(future, timeout=self.config.rpc_timeout_seconds)
        except TimeoutError as exc:
            self._pending.pop(request_id, None)
            raise TimeoutError(f"rpc {method} timed out after {self.config.rpc_timeout_seconds}s") from exc
        except Exception:
            self._pending.pop(request_id, None)
            raise

        if isinstance(result, dict):
            return result
        return {}

    async def _read_stdout(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                return
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                print(f"[codex app-server] invalid JSON: {text[:200]}")
                continue
            if not isinstance(message, dict):
                continue
            self._handle_message(message)

    async def _read_stderr(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stderr is not None
        while True:
            line = await proc.stderr.readline()
            if not line:
                return
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                print(f"[codex app-server] {text}")

    async def _watch_exit(self, proc: asyncio.subprocess.Process) -> None:
        await proc.wait()
        if self._proc is proc:
            error = RuntimeError(f"codex app-server exited with code {proc.returncode}")
            self._clear_process_state()
            self._reject_pending(error)
            self._notify_exit(error)

    def _handle_message(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        if isinstance(request_id, int):
            pending = self._pending.pop(request_id, None)
            if pending is None:
                return
            if "error" in message:
                error = message["error"]
                if isinstance(error, dict):
                    detail = error.get("message") or json.dumps(error, ensure_ascii=False)
                else:
                    detail = str(error)
                if not pending.future.done():
                    pending.future.set_exception(RuntimeError(f"rpc {pending.method} error: {detail}"))
            else:
                if not pending.future.done():
                    pending.future.set_result(message.get("result"))
            return

        method = message.get("method")
        if isinstance(method, str):
            params = message.get("params")
            if isinstance(params, dict):
                self._dispatch_notification(method, params)

    def _dispatch_notification(self, method: str, params: dict[str, Any]) -> None:
        for handler in list(self._notification_handlers.get(method, ())):
            try:
                handler(params)
            except Exception as exc:
                print(f"[codex app-server] notification {method} handler failed: {exc}")

    def _reject_pending(self, error: Exception) -> None:
        pending = list(self._pending.values())
        self._pending.clear()
        for item in pending:
            if not item.future.done():
                item.future.set_exception(error)

    def _notify_exit(self, error: Exception) -> None:
        for handler in list(self._exit_handlers):
            try:
                handler(error)
            except Exception as exc:
                print(f"[codex app-server] exit handler failed: {exc}")

    def _clear_process_state(self) -> None:
        self._proc = None
        self._initialized = False
        self._known_threads.clear()
