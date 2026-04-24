"""Core bridge: receive a Feishu message, call Codex, reply back."""

from __future__ import annotations

import json

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageReactionRequest,
    CreateMessageReactionRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
    DeleteMessageReactionRequest,
)
from lark_oapi.api.im.v1.model.emoji import Emoji

from .runtime import FeishuCodexRuntime


async def handle_message(
    runtime: FeishuCodexRuntime,
    sender_id: str,
    text: str,
    message_id: str,
    client: lark.Client,
) -> None:
    if runtime.allowed_user_ids and sender_id not in runtime.allowed_user_ids:
        return

    command = text.strip()
    if command.startswith("/"):
        if command == "/reset":
            async with runtime.lock:
                runtime.sessions.clear()
            _send_text(client, runtime.chat_id, "✅ 对话已重置，开始新会话。")
        elif command == "/compact":
            async with runtime.lock:
                await _compact_codex(runtime, message_id, client)
        else:
            _send_text(client, runtime.chat_id, f"未知指令：{command}")
        return

    async with runtime.lock:
        await _run_codex(runtime, text, message_id, client)


async def _run_codex(runtime: FeishuCodexRuntime, text: str, message_id: str, client: lark.Client) -> None:
    reaction_id = _add_reaction(client, message_id, "Typing")
    thread_id = runtime.sessions.get_thread_id()

    try:
        result = await runtime.codex.run(prompt=text, thread_id=thread_id)
    except Exception as exc:
        if reaction_id:
            _remove_reaction(client, message_id, reaction_id)
        _send_text(client, runtime.chat_id, f"❌ Codex 调用失败：{exc}")
        return

    if reaction_id:
        _remove_reaction(client, message_id, reaction_id)

    if result.thread_id:
        runtime.sessions.save_thread_id(result.thread_id)

    reply = result.final_text or "(无回复)"
    _send_text(client, runtime.chat_id, reply)


async def _compact_codex(runtime: FeishuCodexRuntime, message_id: str, client: lark.Client) -> None:
    thread_id = runtime.sessions.get_thread_id()
    if not thread_id:
        _send_text(client, runtime.chat_id, "当前会话还没有 Codex thread，无需 compact。")
        return

    reaction_id = _add_reaction(client, message_id, "Typing")
    try:
        await runtime.codex.compact(thread_id)
    except Exception as exc:
        if reaction_id:
            _remove_reaction(client, message_id, reaction_id)
        _send_text(client, runtime.chat_id, f"❌ Compact 失败：{exc}")
        return

    if reaction_id:
        _remove_reaction(client, message_id, reaction_id)

    _send_text(client, runtime.chat_id, "✅ 当前会话已 compact。")


def _add_reaction(client: lark.Client, message_id: str, emoji_type: str) -> str | None:
    req = (
        CreateMessageReactionRequest.builder()
        .message_id(message_id)
        .request_body(
            CreateMessageReactionRequestBody.builder()
            .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
            .build()
        )
        .build()
    )
    resp = client.im.v1.message_reaction.create(req)
    if not resp.success():
        print(f"[add_reaction] error {resp.code}: {resp.msg}")
        return None
    return resp.data.reaction_id


def _remove_reaction(client: lark.Client, message_id: str, reaction_id: str) -> None:
    req = (
        DeleteMessageReactionRequest.builder()
        .message_id(message_id)
        .reaction_id(reaction_id)
        .build()
    )
    resp = client.im.v1.message_reaction.delete(req)
    if not resp.success():
        print(f"[remove_reaction] error {resp.code}: {resp.msg}")


def _send_text(client: lark.Client, chat_id: str, text: str) -> None:
    req = (
        CreateMessageRequest.builder()
        .receive_id_type("chat_id")
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(json.dumps({"text": text}, ensure_ascii=False))
            .build()
        )
        .build()
    )
    resp = client.im.v1.message.create(req)
    if not resp.success():
        print(f"[send_text] error {resp.code}: {resp.msg}")
