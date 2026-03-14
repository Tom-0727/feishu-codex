"""Async wrapper around `codex exec --json`."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


CODEX_BIN = os.getenv("CODEX_BIN", "codex")
CODEX_CWD = os.getenv("CODEX_CWD") or os.path.expanduser("~")
CODEX_MODEL = os.getenv("CODEX_MODEL", "").strip()
CODEX_SEARCH = _env_flag("CODEX_SEARCH")
CODEX_FULL_AUTO = _env_flag("CODEX_FULL_AUTO", default=True)
CODEX_DANGEROUS = _env_flag("CODEX_DANGEROUS")
CODEX_EXTRA_ARGS = shlex.split(os.getenv("CODEX_EXTRA_ARGS", ""))


@dataclass
class CodexRunResult:
    thread_id: str | None = None
    final_text: str = ""
    errors: list[str] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    exit_code: int = 0
    stderr: str = ""


async def _read_stderr(stream: asyncio.StreamReader, sink: list[str]) -> None:
    while True:
        line = await stream.readline()
        if not line:
            return
        sink.append(line.decode("utf-8", errors="replace").rstrip())


def _build_command(prompt: str, thread_id: str | None, output_file: str) -> list[str]:
    cmd = [CODEX_BIN]
    if CODEX_SEARCH:
        cmd.append("--search")

    if thread_id:
        cmd.extend(["exec", "resume", "--json", "--skip-git-repo-check"])
    else:
        cmd.extend(["exec", "--json", "--skip-git-repo-check"])

    if CODEX_MODEL:
        cmd.extend(["-m", CODEX_MODEL])
    if CODEX_FULL_AUTO:
        cmd.append("--full-auto")
    if CODEX_DANGEROUS:
        cmd.append("--dangerously-bypass-approvals-and-sandbox")

    cmd.extend(CODEX_EXTRA_ARGS)
    cmd.extend(["--output-last-message", output_file])

    if thread_id:
        cmd.extend([thread_id, prompt])
    else:
        cmd.append(prompt)

    return cmd


async def run_codex(prompt: str, thread_id: str | None = None) -> CodexRunResult:
    result = CodexRunResult(thread_id=thread_id)

    with tempfile.NamedTemporaryFile(prefix="feishu-codex-", suffix=".txt", delete=False) as handle:
        output_file = handle.name

    cmd = _build_command(prompt=prompt, thread_id=thread_id, output_file=output_file)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=CODEX_CWD,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stderr_lines: list[str] = []
    stderr_task = asyncio.create_task(_read_stderr(proc.stderr, stderr_lines))

    assert proc.stdout is not None
    while True:
        line = await proc.stdout.readline()
        if not line:
            break

        text = line.decode("utf-8", errors="replace").strip()
        if not text:
            continue

        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            result.errors.append(text)
            continue

        result.events.append(event)
        event_type = event.get("type")

        if event_type == "thread.started":
            result.thread_id = event.get("thread_id") or result.thread_id
        elif event_type == "item.completed":
            item = event.get("item", {})
            if item.get("type") == "agent_message" and item.get("text"):
                result.final_text = item["text"]
        elif event_type == "turn.failed":
            error = event.get("error", {}).get("message")
            if error:
                result.errors.append(error)
        elif event_type == "error" and event.get("message"):
            result.errors.append(event["message"])

    result.exit_code = await proc.wait()
    await stderr_task
    result.stderr = "\n".join(stderr_lines).strip()

    output_path = Path(output_file)
    if output_path.exists():
        fallback_text = output_path.read_text(encoding="utf-8", errors="replace").strip()
        if fallback_text and not result.final_text:
            result.final_text = fallback_text
        output_path.unlink(missing_ok=True)

    if result.stderr and result.exit_code != 0:
        result.errors.append(result.stderr.splitlines()[-1])

    return result
