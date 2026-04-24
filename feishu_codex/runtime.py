"""Runtime objects that bind one Feishu chat to one Codex app-server."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from .codex_app_server import CodexAppServerClient, CodexAppServerConfig
from .config import CodexRuntimeConfig
from .sessions import SessionStore


@dataclass
class FeishuCodexRuntime:
    config: CodexRuntimeConfig
    codex: CodexAppServerClient
    sessions: SessionStore
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def runtime_id(self) -> str:
        return self.config.runtime_id

    @property
    def chat_id(self) -> str:
        return self.config.chat_id

    @property
    def allowed_user_ids(self) -> frozenset[str]:
        return self.config.allowed_user_ids

    async def stop(self) -> None:
        await self.codex.stop()


def build_runtime(config: CodexRuntimeConfig) -> FeishuCodexRuntime:
    codex_config = CodexAppServerConfig(
        runtime_id=config.runtime_id,
        codex_bin=config.codex_bin,
        cwd=config.cwd,
        sandbox=config.sandbox,
        approval_policy=config.approval_policy,
        skip_git_repo_check=config.skip_git_repo_check,
        rpc_timeout_seconds=config.rpc_timeout_seconds,
        turn_timeout_seconds=config.turn_timeout_seconds,
        compact_timeout_seconds=config.compact_timeout_seconds,
        stop_timeout_seconds=config.stop_timeout_seconds,
    )
    return FeishuCodexRuntime(
        config=config,
        codex=CodexAppServerClient(codex_config),
        sessions=SessionStore(config.session_path),
    )
