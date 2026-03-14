"""Core bridge: receive a Feishu message, call Codex CLI, reply back."""

from __future__ import annotations

import asyncio
import json
import os
from collections import defaultdict

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageReactionRequest,
    CreateMessageReactionRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
    DeleteMessageReactionRequest,
)
from lark_oapi.api.im.v1.model.emoji import Emoji

from . import sessions
from .codex_exec import run_codex

_chat_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

ALLOWED_USER_IDS: set[str] = set(
    uid.strip()
    for uid in os.getenv("ALLOWED_USER_IDS", "").split(",")
    if uid.strip()
)


async def handle_message(
    chat_id: str,
    sender_id: str,
    text: str,
    message_id: str,
    client: lark.Client,
) -> None:
    if ALLOWED_USER_IDS and sender_id not in ALLOWED_USER_IDS:
        return

    if text.strip() == "/reset":
        sessions.clear(chat_id)
        _send_text(client, chat_id, "✅ 对话已重置，开始新会话。")
        return

    async with _chat_locks[chat_id]:
        await _run_codex(chat_id, text, message_id, client)


async def _run_codex(chat_id: str, text: str, message_id: str, client: lark.Client) -> None:
    reaction_id = _add_reaction(client, message_id, "Typing")
    thread_id = sessions.get(chat_id)

    try:
        result = await run_codex(prompt=text, thread_id=thread_id)
    except Exception as exc:
        if reaction_id:
            _remove_reaction(client, message_id, reaction_id)
        _send_text(client, chat_id, f"❌ Codex 调用失败：{exc}")
        return

    if reaction_id:
        _remove_reaction(client, message_id, reaction_id)

    if result.thread_id:
        sessions.save(chat_id, result.thread_id)

    if result.exit_code != 0 and not result.final_text:
        details = result.errors[-1] if result.errors else "未知错误"
        _send_text(client, chat_id, f"❌ Codex 执行失败：{details}")
        return

    reply = result.final_text or "(无回复)"
    _send_text(client, chat_id, reply)


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
