from __future__ import annotations

import logging
from typing import Awaitable, Callable

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import Message


PUBLIC_COMMANDS = (
    (
        "/like {Rᴇɢɪᴏɴ} {Uɪᴅ}",
        "Sᴇɴᴅs ʟɪᴋᴇs ᴛᴏ ᴀ ᴘʟᴀʏᴇʀ ᴡɪᴛʜ ᴛʜᴇ ᴅᴀɪʟʏ ʟɪᴍɪᴛ.",
    ),
    (
        "/visit {Rᴇɢɪᴏɴ} {Uɪᴅ}",
        "Sᴇɴᴅs ᴠɪsɪᴛs ᴛᴏ ᴛʜᴇ ᴘʟᴀʏᴇʀ ᴘʀᴏғɪʟᴇ.",
    ),
    (
        "/get {Uɪᴅ}",
        "Fᴇᴛᴄʜᴇs ᴅᴇᴛᴀɪʟᴇᴅ ᴘʟᴀʏᴇʀ ᴘʀᴏғɪʟᴇ ɪɴғᴏʀᴍᴀᴛɪᴏɴ.",
    ),
    (
        "/level {Rᴇɢɪᴏɴ} {Uɪᴅ}",
        "Sʜᴏᴡs ʟᴇᴠᴇʟ, EXP, ᴘʀᴏɢʀᴇss ᴀɴᴅ ʟɪᴋᴇs.",
    ),
    (
        "/bancheck {Uɪᴅ}",
        "Cʜᴇᴄᴋs ᴡʜᴇᴛʜᴇʀ ᴀ ᴘʟᴀʏᴇʀ ᴀᴄᴄᴏᴜɴᴛ ɪs ʙᴀɴɴᴇᴅ.",
    ),
)

VIP_COMMAND = (
    "/vip {Uɪᴅ}",
    "Aᴅᴍɪɴ-ᴏɴʟʏ ᴘʀᴇᴍɪᴜᴍ ᴀᴜᴛᴏʟɪᴋᴇ ʀᴇǫᴜᴇsᴛ.",
)


def _command_line(command: str, description: str | None = None) -> str:
    if description is None:
        return f"<code>{command}</code>"
    return f"<code>{command}</code>\n<i>{description}</i>"


def build_command_menu(include_vip: bool = False) -> str:
    lines = [
        "<blockquote>╭━━━ <b>𝗖ᴏᴍᴍᴀɴᴅ Mᴇɴᴜ</b> ━━━╮",
        "│",
    ]
    for command, _ in PUBLIC_COMMANDS:
        lines.append(f"│ {_command_line(command)}")
    if include_vip:
        lines.extend(
            [
                "│",
                f"│ 🔐 {_command_line(VIP_COMMAND[0])}",
            ]
        )
    lines.extend(
        [
            "│",
            "╰━━━ <i>Tʏᴘᴇ /help Fᴏʀ Dᴇᴛᴀɪʟs</i> ━━━╯</blockquote>",
        ]
    )
    return "\n".join(lines)


def build_detailed_help(include_vip: bool = False) -> str:
    lines = [
        "<blockquote>╭━━━ <b>𝗣ʀᴇᴍɪᴜᴍ Hᴇʟᴘ Cᴇɴᴛᴇʀ</b> ━━━╮",
        "│",
        "│ 🎮 <b>Pʟᴀʏᴇʀ Tᴏᴏʟs</b>",
        "│",
    ]
    for command, description in PUBLIC_COMMANDS:
        lines.extend(
            [
                f"│ {_command_line(command)}",
                f"│ <i>{description}</i>",
                "│",
            ]
        )

    lines.extend(
        [
            "│ ⚙️ <b>Uѕᴀɢᴇ Rᴜʟᴇs</b>",
            "│",
            "│ • Rᴇɢɪᴏɴ ᴋᴏ ᴜsᴇ ᴋᴀʀᴇɪɴ, ᴊᴀɪsᴇ <code>ind</code>.",
            "│ • Uɪᴅ sɪʀғ ᴘᴏsɪᴛɪᴠᴇ ɴᴜᴍʙᴇʀ ʜᴏɴᴀ ᴄʜᴀʜɪᴇ.",
            "│ • Gʀᴏᴜᴘ ᴍᴇɪɴ ʙᴏᴛ ᴋᴏ ᴀᴅᴍɪɴ ᴘᴇʀᴍɪssɪᴏɴ ᴢᴀʀᴜʀɪ ʜᴀɪ.",
        ]
    )
    if include_vip:
        lines.extend(
            [
                "│",
                "│ 🔐 <b>Aᴅᴍɪɴ Pʀᴇᴍɪᴜᴍ</b>",
                "│",
                f"│ {_command_line(VIP_COMMAND[0])}",
                f"│ <i>{VIP_COMMAND[1]}</i>",
            ]
        )
    lines.extend(
        [
            "│",
            "│ 💎 <i>Fᴀsᴛ • Sᴍᴏᴏᴛʜ • Pʀᴇᴍɪᴜᴍ</i>",
            "╰━━━━━━━━━━━━━━━━━━━━━━━╯</blockquote>",
        ]
    )
    return "\n".join(lines)


def _is_configured_admin(
    message: Message,
    is_configured_admin: Callable[[int | None], bool],
) -> bool:
    user = getattr(message, "from_user", None)
    return is_configured_admin(getattr(user, "id", None))


def register_help_handlers(
    *,
    bot: Client,
    require_bot_group_admin: Callable[[Message], Awaitable[bool]],
    command_access_allowed: Callable[[Message], Awaitable[bool]],
    is_configured_admin: Callable[[int | None], bool],
    logger: logging.Logger,
) -> None:
    """Register the public command menu and detailed help handlers."""

    async def can_show_help(message: Message) -> bool:
        if not await require_bot_group_admin(message):
            return False
        return await command_access_allowed(message)

    @bot.on_message(filters.incoming & ~filters.service & filters.regex(r"^/\s*$"))
    async def command_menu_handler(_: Client, message: Message) -> None:
        try:
            if not await can_show_help(message):
                return
            await message.reply_text(
                build_command_menu(_is_configured_admin(message, is_configured_admin)),
                parse_mode=ParseMode.HTML,
                quote=True,
            )
        except Exception:
            logger.exception("Command menu handler failed")
            await message.reply_text(
                "<pre>⚠️ Cᴏᴍᴍᴀɴᴅ Mᴇɴᴜ Tᴇᴍᴘᴏʀᴀʀɪʟʏ Uɴᴀᴠᴀɪʟᴀʙʟᴇ.</pre>",
                parse_mode=ParseMode.HTML,
                quote=True,
            )

    @bot.on_message(filters.incoming & ~filters.service & filters.command("help", case_sensitive=False))
    async def help_command_handler(_: Client, message: Message) -> None:
        try:
            if not await can_show_help(message):
                return
            await message.reply_text(
                build_detailed_help(_is_configured_admin(message, is_configured_admin)),
                parse_mode=ParseMode.HTML,
                quote=True,
            )
        except Exception:
            logger.exception("Help command handler failed")
            await message.reply_text(
                "<pre>⚠️ Hᴇʟᴘ Mᴇɴᴜ Tᴇᴍᴘᴏʀᴀʀɪʟʏ Uɴᴀᴠᴀɪʟᴀʙʟᴇ.</pre>",
                parse_mode=ParseMode.HTML,
                quote=True,
            )