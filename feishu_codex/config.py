"""YAML configuration for feishu-codex."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


_SANDBOX_VALUES = {"read-only", "workspace-write", "danger-full-access"}
_APPROVAL_POLICY_VALUES = {"untrusted", "on-failure", "on-request", "never"}


@dataclass(frozen=True)
class FeishuAppConfig:
    key: str
    app_id: str
    app_secret: str


@dataclass(frozen=True)
class CodexRuntimeConfig:
    runtime_id: str
    app_key: str
    chat_id: str
    allowed_user_ids: frozenset[str]
    session_path: Path
    codex_bin: str
    cwd: Path
    sandbox: str
    approval_policy: str
    skip_git_repo_check: bool
    rpc_timeout_seconds: int
    turn_timeout_seconds: int
    compact_timeout_seconds: int
    stop_timeout_seconds: int


@dataclass(frozen=True)
class ServiceConfig:
    apps: dict[str, FeishuAppConfig]
    runtimes: dict[str, CodexRuntimeConfig]


def load_config(path: str | Path) -> ServiceConfig:
    config_path = Path(path).expanduser()
    with config_path.open("r", encoding="utf-8") as file:
        raw = yaml.safe_load(file)

    root = _mapping(raw, "config")
    apps = _load_apps(root.get("apps"))
    runtimes = _load_runtimes(root.get("runtimes"), apps)
    return ServiceConfig(apps=apps, runtimes=runtimes)


def _load_apps(raw: object) -> dict[str, FeishuAppConfig]:
    apps_raw = _mapping(raw, "apps")
    if not apps_raw:
        raise ValueError("apps must define at least one Feishu app")

    apps: dict[str, FeishuAppConfig] = {}
    for key, value in apps_raw.items():
        app_key = _key(key, "app key")
        item = _mapping(value, f"apps.{app_key}")
        apps[app_key] = FeishuAppConfig(
            key=app_key,
            app_id=_required_str(item, "app_id", f"apps.{app_key}"),
            app_secret=_required_str(item, "app_secret", f"apps.{app_key}"),
        )
    return apps


def _load_runtimes(raw: object, apps: dict[str, FeishuAppConfig]) -> dict[str, CodexRuntimeConfig]:
    runtimes_raw = _mapping(raw, "runtimes")
    if not runtimes_raw:
        raise ValueError("runtimes must define at least one Codex runtime")

    routes: set[tuple[str, str]] = set()
    runtimes: dict[str, CodexRuntimeConfig] = {}
    for key, value in runtimes_raw.items():
        runtime_id = _key(key, "runtime id")
        item = _mapping(value, f"runtimes.{runtime_id}")
        app_key = _required_str(item, "app", f"runtimes.{runtime_id}")
        if app_key not in apps:
            raise ValueError(f"runtimes.{runtime_id}.app references unknown app {app_key!r}")

        chat_id = _required_str(item, "chat_id", f"runtimes.{runtime_id}")
        route = (app_key, chat_id)
        if route in routes:
            raise ValueError(f"duplicate runtime route for app {app_key!r} and chat_id {chat_id!r}")
        routes.add(route)

        codex = _mapping(item.get("codex"), f"runtimes.{runtime_id}.codex")
        cwd = _directory(_required_str(codex, "cwd", f"runtimes.{runtime_id}.codex"), f"runtimes.{runtime_id}.codex.cwd")
        sandbox = _enum(
            _optional_str(codex, "sandbox", "workspace-write", f"runtimes.{runtime_id}.codex"),
            _SANDBOX_VALUES,
            f"runtimes.{runtime_id}.codex.sandbox",
        )
        approval_policy = _enum(
            _optional_str(codex, "approval_policy", "never", f"runtimes.{runtime_id}.codex"),
            _APPROVAL_POLICY_VALUES,
            f"runtimes.{runtime_id}.codex.approval_policy",
        )
        runtimes[runtime_id] = CodexRuntimeConfig(
            runtime_id=runtime_id,
            app_key=app_key,
            chat_id=chat_id,
            allowed_user_ids=frozenset(_str_list(item.get("allowed_user_ids"), f"runtimes.{runtime_id}.allowed_user_ids")),
            session_path=_session_path(runtime_id, item.get("session_path")),
            codex_bin=_optional_str(codex, "bin", "codex", f"runtimes.{runtime_id}.codex"),
            cwd=cwd,
            sandbox=sandbox,
            approval_policy=approval_policy,
            skip_git_repo_check=_optional_bool(codex, "skip_git_repo_check", True, f"runtimes.{runtime_id}.codex"),
            rpc_timeout_seconds=_optional_int(codex, "rpc_timeout_seconds", 60, f"runtimes.{runtime_id}.codex"),
            turn_timeout_seconds=_optional_int(codex, "turn_timeout_seconds", 30 * 60, f"runtimes.{runtime_id}.codex"),
            compact_timeout_seconds=_optional_int(codex, "compact_timeout_seconds", 10 * 60, f"runtimes.{runtime_id}.codex"),
            stop_timeout_seconds=_optional_int(codex, "stop_timeout_seconds", 10, f"runtimes.{runtime_id}.codex"),
        )
    return runtimes


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a mapping")
    return value


def _key(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _required_str(item: dict[str, Any], key: str, name: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}.{key} must be a non-empty string")
    return value.strip()


def _optional_str(item: dict[str, Any], key: str, default: str, name: str) -> str:
    value = item.get(key, default)
    if value is None:
        return default
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}.{key} must be a non-empty string")
    return value.strip()


def _enum(value: str, allowed: set[str], name: str) -> str:
    if value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"{name} must be one of: {choices}")
    return value


def _optional_bool(item: dict[str, Any], key: str, default: bool, name: str) -> bool:
    value = item.get(key, default)
    if isinstance(value, bool):
        return value
    raise ValueError(f"{name}.{key} must be true or false")


def _optional_int(item: dict[str, Any], key: str, default: int, name: str) -> int:
    value = item.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name}.{key} must be a positive integer")
    return value


def _str_list(value: object, name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list of strings")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{name}[{index}] must be a non-empty string")
        result.append(item.strip())
    return result


def _path(value: str) -> Path:
    return Path(value).expanduser()


def _directory(value: str, name: str) -> Path:
    path = _path(value)
    if not path.is_dir():
        raise ValueError(f"{name} must be an existing directory")
    return path


def _session_path(runtime_id: str, value: object) -> Path:
    if value is not None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"runtimes.{runtime_id}.session_path must be a non-empty string")
        return _path(value.strip())

    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", runtime_id).strip("._") or "runtime"
    return Path.home() / ".feishu-codex" / "runtimes" / safe_id / "session.json"
