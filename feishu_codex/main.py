"""Entry point: start Feishu WebSocket long-connection bot."""

import asyncio
import json
import os
import threading

import lark_oapi as lark
from dotenv import load_dotenv
from lark_oapi.api.im.v1 import P2ImMessageMessageReadV1
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

load_dotenv()

from .bridge import handle_message

APP_ID = os.environ["FEISHU_APP_ID"]
APP_SECRET = os.environ["FEISHU_APP_SECRET"]

_loop = asyncio.new_event_loop()
threading.Thread(target=_loop.run_forever, daemon=True).start()


def _on_message(data: P2ImMessageReceiveV1) -> None:
    msg = data.event.message
    if msg.message_type != "text":
        return

    text = json.loads(msg.content).get("text", "").strip()
    if not text:
        return

    asyncio.run_coroutine_threadsafe(
        handle_message(
            chat_id=msg.chat_id,
            sender_id=data.event.sender.sender_id.open_id,
            text=text,
            message_id=msg.message_id,
            client=_api_client,
        ),
        _loop,
    )


def _on_message_read(_: P2ImMessageMessageReadV1) -> None:
    # Feishu may still deliver read receipts for existing long connections.
    return


_api_client = lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).build()

_event_handler = (
    lark.EventDispatcherHandler.builder("", "")
    .register_p2_im_message_message_read_v1(_on_message_read)
    .register_p2_im_message_receive_v1(_on_message)
    .build()
)


def main() -> None:
    print(f"Starting feishu-codex bot (app_id={APP_ID[:8]}…)")
    ws = lark.ws.Client(
        APP_ID,
        APP_SECRET,
        event_handler=_event_handler,
        log_level=lark.LogLevel.INFO,
    )
    ws.start()


if __name__ == "__main__":
    main()
