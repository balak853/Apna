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
        "/level {Uɪᴅ} {Rᴇɢɪᴏɴ}",
        "Sʜᴏᴡs ʟᴇᴠᴇʟ, EXP, ᴘʀᴏɢʀᴇss ᴀɴᴅ ʟɪᴋᴇs. Bᴏᴛʜ UID–Rᴇɢɪᴏɴ ᴏʀ Rᴇɢɪᴏɴ–UID ᴏʀᴅᴇʀs ᴀʀᴇ ᴀᴄᴄᴇᴘᴛᴇᴅ.",
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

ADMIN_COMMANDS = (
    (
        "/onverify",
        "Eɴᴀʙʟᴇs ғᴏʀᴄᴇ-ᴊᴏɪɴ ᴠᴇʀɪғɪᴄᴀᴛɪᴏɴ.",
    ),
    (
        "/offverify",
        "Dɪsᴀʙʟᴇs ғᴏʀᴄᴇ-ᴊᴏɪɴ ᴠᴇʀɪғɪᴄᴀᴛɪᴏɴ.",
    ),
    (
        "/addforce {Cʜᴀᴛ_ɪᴅ}",
        "Aᴅᴅs ᴀ ᴄʜᴀɴɴᴇʟ, ɢʀᴏᴜᴘ ᴏʀ sᴜᴘᴇʀɢʀᴏᴜᴘ ғᴏʀ ғᴏʀᴄᴇ-ᴊᴏɪɴ.",
    ),
    (
        "/disable {Cᴏᴍᴍᴀɴᴅ_Nᴀᴍᴇ}",
        "Dɪsᴀʙʟᴇs /like, /visit ᴏʀ /bancheck ғᴏʀ ᴛʜᴇ ɢʀᴏᴜᴘ.",
    ),
    (
        "/active {Cᴏᴍᴍᴀɴᴅ_Nᴀᴍᴇ}",
        "Rᴇ-ᴀᴄᴛɪᴠᴀᴛᴇs ᴀ ᴅɪsᴀʙʟᴇᴅ /like, /visit ᴏʀ /bancheck.",
    ),
    (
        "/users",
        "Eхᴘᴏʀᴛs ᴛʜᴇ ʙᴏᴛ's ᴜsᴇʀ ᴅᴀᴛᴀʙᴀsᴇ.",
    ),
    (
        "/setapi {Mᴏᴅᴇ}",
        "Sᴇᴛs 1, 2, 3, all, custom_api N ᴏʀ custom_api_N ᴀs ᴛʜᴇ ʟɪᴋᴇ ᴍᴏᴅᴇ.",
    ),
    (
        "/addlikeapi {Hᴛᴛᴘ_Aᴘɪ_URL}",
        "Aᴅᴅs ᴀ ᴠᴀʟɪᴅ HTTP/HTTPS ʟɪᴋᴇ API ᴛᴏ ᴛʜᴇ ʀᴏᴜᴛɪɴɢ ʟɪsᴛ.",
    ),
    (
        "/likeapis | /listlikeapis",
        "Sʜᴏᴡs ᴛʜᴇ ᴄᴏɴғɪɢᴜʀᴇᴅ ʟɪᴋᴇ API ʟɪsᴛ.",
    ),
    (
        "/removealllikeapis | /removeallapi",
        "Oᴘᴇɴs ᴛʜᴇ ᴄᴏɴғɪʀᴍᴀᴛɪᴏɴ ᴛᴏ ʀᴇᴍᴏᴠᴇ ᴀʟʟ ʟɪᴋᴇ APIs.",
    ),
    (
        "/removelikeapi {Nᴜᴍʙᴇʀ} | /removeapi {Nᴜᴍʙᴇʀ}",
        "Oᴘᴇɴs ᴛʜᴇ ᴄᴏɴғɪʀᴍᴀᴛɪᴏɴ ᴛᴏ ʀᴇᴍᴏᴠᴇ ʟɪᴋᴇ API 1, 2 ᴏʀ 3.",
    ),
    (
        "/runnow",
        "Sᴛᴀʀᴛs ᴛʜᴇ ᴀᴅᴍɪɴ ᴀᴜᴛᴏʟɪᴋᴇ ʀᴜɴ ɪᴍᴍᴇᴅɪᴀᴛᴇʟʏ.",
    ),
    VIP_COMMAND,
    (
        "/addvip {Rᴇɢɪᴏɴ} {Uɪᴅ} {Tᴏᴛᴀʟ_Dᴀʏs}",
        "Aᴅᴅs ᴀɴ ᴀᴅᴍɪɴ-ᴏɴʟʏ VIP ᴀᴜᴛᴏʟɪᴋᴇ ʀᴇᴄᴏʀᴅ.",
    ),
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
                "│ 🔐 <b>Aᴅᴍɪɴ Cᴏᴍᴍᴀɴᴅs</b>",
            ]
        )
        for command, _ in ADMIN_COMMANDS:
            lines.append(f"│ {_command_line(command)}")
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
                "│ 🔐 <b>Aᴅᴍɪɴ Cᴏᴍᴍᴀɴᴅs</b>",
                "│",
            ]
        )
        for command, description in ADMIN_COMMANDS:
            lines.extend(
                [
                    f"│ {_command_line(command)}",
                    f"│ <i>{description}</i>",
                    "│",
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