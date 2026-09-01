from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import Message


BOT_STATUS_ID = "global"
BOT_OFF_MESSAGE = (
    "<b>⛔ Bᴏᴛ Cᴜʀʀᴇɴᴛʟʏ Oғғ</b>\n\n"
    "Pʟᴇᴀsᴇ Tʀʏ Aɢᴀɪɴ Lᴀᴛᴇʀ."
)
BOT_USAGE_MESSAGE = (
    "<b>Uѕᴀɢᴇ:</b> <code>/bot on</code> ᴏʀ <code>/bot off</code>"
)


def _message_command_parts(message: Message) -> list[str]:
    text = (getattr(message, "text", None) or getattr(message, "caption", None) or "").strip()
    if not text:
        return []
    parts = text.split()
    if not parts or not parts[0].startswith("/"):
        return []
    command = parts[0][1:].split("@", 1)[0].lower()
    return [command, *parts[1:]]


def register_system_handlers(
    *,
    bot: Client,
    database: Any,
    is_configured_admin: Callable[[int | None], bool],
    logger: logging.Logger,
) -> None:
    """Register the admin bot switch and the global command guard."""

    bot_status = database["bot_status"]

    async def bot_is_enabled() -> bool:
        try:
            document = await asyncio.to_thread(
                bot_status.find_one,
                {"_id": BOT_STATUS_ID},
            )
            return document is None or bool(document.get("enabled", True))
        except Exception:
            logger.exception("Bot status lookup failed; keeping bot enabled")
            return True

    async def set_bot_enabled(enabled: bool) -> None:
        await asyncio.to_thread(
            bot_status.update_one,
            {"_id": BOT_STATUS_ID},
            {"$set": {"enabled": enabled}},
            upsert=True,
        )

    @bot.on_message(filters.incoming & ~filters.service, group=-2)
    async def bot_status_guard(_: Client, message: Message) -> None:
        parts = _message_command_parts(message)
        if not parts:
            return

        user = getattr(message, "from_user", None)
        user_is_admin = is_configured_admin(getattr(user, "id", None))
        is_bot_command = parts[0] == "bot"

        if is_bot_command and user_is_admin:
            return

        if await bot_is_enabled():
            return

        try:
            await message.reply_text(
                BOT_OFF_MESSAGE,
                parse_mode=ParseMode.HTML,
                quote=True,
            )
        except Exception:
            logger.exception("Bot-off response failed")
        message.stop_propagation()

    @bot.on_message(
        filters.incoming & ~filters.service & filters.command("bot", case_sensitive=False),
        group=-1,
    )
    async def bot_status_command(_: Client, message: Message) -> None:
        user = getattr(message, "from_user", None)
        if not is_configured_admin(getattr(user, "id", None)):
            return

        parts = _message_command_parts(message)
        if len(parts) != 2 or parts[1].lower() not in {"on", "off"}:
            await message.reply_text(
                BOT_USAGE_MESSAGE,
                parse_mode=ParseMode.HTML,
                quote=True,
            )
            return

        enabled = parts[1].lower() == "on"
        try:
            await set_bot_enabled(enabled)
            if enabled:
                response = (
                    "<b>✅ Bᴏᴛ Sᴛᴀᴛᴜs: Oɴ</b>\n\n"
                    "Aʟʟ ᴄᴏᴍᴍᴀɴᴅs ᴀʀᴇ ᴀᴠᴀɪʟᴀʙʟᴇ ɴᴏᴡ."
                )
            else:
                response = (
                    "<b>🛑 Bᴏᴛ Sᴛᴀᴛᴜs: Oғғ</b>\n\n"
                    "Nᴏɴ-ᴀᴅᴍɪɴ ᴄᴏᴍᴍᴀɴᴅs ᴀʀᴇ ɴᴏᴡ ᴅɪsᴀʙʟᴇᴅ."
                )
            await message.reply_text(
                response,
                parse_mode=ParseMode.HTML,
                quote=True,
            )
        except Exception:
            logger.exception("Bot status update failed")
            await message.reply_text(
                "<b>⚠️ Cᴏᴜʟᴅ Nᴏᴛ Uᴘᴅᴀᴛᴇ Bᴏᴛ Sᴛᴀᴛᴜs.</b>",
                parse_mode=ParseMode.HTML,
                quote=True,
            )