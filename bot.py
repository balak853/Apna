from __future__ import annotations

import asyncio
import html
import io
import json
import logging
import math
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from flask import Flask
from pymongo import ASCENDING, MongoClient, ReturnDocument
from pymongo.collection import Collection
from pymongo.errors import PyMongoError
from pyrogram import Client, filters
from pyrogram.errors import UserNotParticipant
from pyrogram.enums import ChatMemberStatus, ChatMembersFilter, ChatType, ParseMode
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)


BASE_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = BASE_DIR / "Downloads"
CONFIG_PATH = BASE_DIR / "config.json"
IST = timezone(timedelta(hours=5, minutes=30), name="Asia/Kolkata")
LIKE_TIMEOUT = httpx.Timeout(connect=8.0, read=20.0, write=8.0, pool=8.0)
UID_PATTERN = re.compile(r"^[0-9]{1,20}$")
REGION_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")

API_CATALOG: list[dict[str, str]] = [
    {
        "name": "PLAYER INFO API",
        "category": "PLAYER INFO",
        "url": "https://player-info-ob54.vercel.app/player-info?uid={UID}",
    },
    {
        "name": "BAN CHECK API — PRIMARY",
        "category": "BAN CHECK",
        "url": "https://api2.nftoken.info/checkbanned?id={UID}",
    },
    {
        "name": "BAN CHECK API — FALLBACK",
        "category": "BAN CHECK",
        "url": "https://ffban-ashu.vercel.app/checkbanned?id={UID}&key=ashu",
    },
    {
        "name": "LIKE API — PRIMARY",
        "category": "LIKE",
        "url": (
            "https://like-api-src-gamma.vercel.app/"
            "like?uid={UID}&server_name={REGION}&api_key=BALAK"
        ),
    },
    {
        "name": "LIKE API — SECONDARY",
        "category": "LIKE",
        "url": "https://220-likes-vaibhav.vercel.app/like?uid={UID}&server_name={REGION}",
    },
    {
        "name": "LIKE API — THIRD",
        "category": "LIKE",
        "url": "http://187.127.175.208:5002/like?uid={UID}&server_name={REGION}",
    },
    {
        "name": "VISIT API",
        "category": "VISIT",
        "url": "http://2.24.160.65:5000/Bmw",
    },
    {
        "name": "BANNER IMAGE API — PRIMARY",
        "category": "BANNER IMAGE",
        "url": "https://vertex-x-banner.vercel.app/profile?uid={UID}",
    },
    {
        "name": "BANNER IMAGE API — SECONDARY",
        "category": "BANNER IMAGE",
        "url": (
            "https://image.killersharmabot.online/banner-image?"
            "headPic={HEADPIC}&bannerId={BANNERID}&name={NAME}&level={LEVEL}"
            "&guild={GUILD}&pinId={PINID}&celebrity={CELEBRITY}&frame={FRAME}"
        ),
    },
    {
        "name": "OUTFIT IMAGE API",
        "category": "OUTFIT IMAGE",
        "url": "https://vertex-x-outfit.vercel.app/outfit-image?uid={UID}&key=VERTEX",
    },
    {
        "name": "SECONDARY PLAYER INFO API",
        "category": "PLAYER INFO",
        "url": "https://star-info-api.lovable.app/functions/v1/info-api/accinfo?uid={UID}",
    },
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("ff-bot2")


def status_print(message: str) -> None:
    print(message, flush=True)


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as config_file:
        config = json.load(config_file)

    required = (
        "api_id",
        "api_hash",
        "bot_token",
        "admin_id",
        "mongodb_uri",
        "database_name",
    )
    missing = [key for key in required if key not in config]
    if "bot_token" in missing or not str(config.get("bot_token", "")).strip():
        status_print("BOT TOKEN MISSING")
    if missing:
        status_print("CONFIGURATION MISSING")
        raise ValueError(f"Missing configuration fields: {', '.join(missing)}")

    try:
        api_id = int(config["api_id"])
        admin_id = int(config["admin_id"])
    except (TypeError, ValueError) as error:
        raise ValueError("api_id and admin_id must be numeric") from error

    api_hash = str(config["api_hash"]).strip()
    bot_token = str(config["bot_token"]).strip()
    mongodb_uri = str(config["mongodb_uri"]).strip()
    database_name = str(config["database_name"]).strip()
    if api_id <= 0 or not api_hash:
        raise ValueError("A valid api_id and api_hash are required")
    if admin_id <= 0:
        raise ValueError("A valid admin_id is required")
    if not bot_token or bot_token == "YOUR_BOT_TOKEN":
        status_print("BOT TOKEN MISSING")
        raise ValueError("A valid bot token is required in config.json")
    if not mongodb_uri or not database_name:
        raise ValueError("A valid MongoDB URI and database name are required")

    # Keep all runtime credentials sourced from config.json, normalized once,
    # and never included in logs or exception messages.
    config.update(
        {
            "api_id": api_id,
            "api_hash": api_hash,
            "bot_token": bot_token,
            "admin_id": admin_id,
            "mongodb_uri": mongodb_uri,
            "database_name": database_name,
        }
    )
    return config


CONFIG = load_config()
mongo_client = MongoClient(
    CONFIG["mongodb_uri"],
    serverSelectionTimeoutMS=5000,
    connectTimeoutMS=5000,
)
database = mongo_client[CONFIG["database_name"]]
users: Collection = database["users"]
groups: Collection = database["groups"]
apis: Collection = database["apis"]
autolike: Collection = database["autolike"]
force_join: Collection = database["force_join"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ist_now() -> datetime:
    return datetime.now(IST)


def next_daily_reset(now: datetime | None = None) -> datetime:
    local_now = (now or ist_now()).astimezone(IST)
    reset = local_now.replace(hour=4, minute=0, second=0, microsecond=0)
    if local_now >= reset:
        reset += timedelta(days=1)
    return reset.astimezone(timezone.utc)


def initialize_database() -> None:
    try:
        status_print("DATABASE CONNECTION STARTING")
        mongo_client.admin.command("ping")
        users.create_index(
            [("user_id", ASCENDING)],
            unique=True,
            name="users_user_id_unique",
        )
        groups.create_index(
            [("chat_id", ASCENDING)],
            unique=True,
            name="groups_chat_id_unique",
        )
        force_join.create_index(
            [("chat_id", ASCENDING)],
            unique=True,
            name="force_join_chat_id_unique",
        )
        autolike.create_index(
            [("uid", ASCENDING), ("region", ASCENDING)],
            unique=True,
            name="autolike_uid_region_unique",
        )
        autolike.create_index(
            [("delete_after", ASCENDING)],
            expireAfterSeconds=0,
            name="autolike_delete_after_ttl",
        )
        apis.update_one(
            {"_id": "routing"},
            {
                "$setOnInsert": {
                    "like_api": (
                        "https://like-api-src-gamma.vercel.app/"
                        "like?uid=13853252729&server_name=IND&api_key=BALAK"
                    ),
                    "like_api_1": (
                        "https://220-likes-vaibhav.vercel.app/"
                        "like?uid=451012596&server_name=IND"
                    ),
                    "like_api_2": "",
                    "like_api_3": (
                        "http://187.127.175.208:5002/"
                        "like?uid=1589573783&server_name=IND"
                    ),
                    "active_like_api": "all",
                    "updated_at": utc_now(),
                }
            },
            upsert=True,
        )
        apis.update_one(
            {"_id": "routing"},
            {
                "$set": {
                    "api_catalog": API_CATALOG,
                    "updated_at": utc_now(),
                }
            },
            upsert=True,
        )
        status_print("DATABASE SUCCESSFULLY COMPLETED ✅")
        logger.info("MongoDB indexes and API routing configuration are ready")
    except PyMongoError:
        status_print("DATABASE CONNECTION FAILED")
        logger.exception("MongoDB initialization failed; the bot will stay alive")


def user_defaults(user_id: int, username: str | None) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "username": username or "N/A",
        "like_limit": 1,
        "visit_limit": 0,
        "paid_user": "No",
        "today_like_given": 0,
        "daily_successful_requests": 0,
        "pending_like_requests": 0,
        "daily_reset_at": next_daily_reset(),
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }


def is_unlimited_user(
    user_id: int,
    document: dict[str, Any] | None = None,
) -> bool:
    if int(user_id) == int(CONFIG["admin_id"]):
        return True
    if document is None:
        return False
    paid_user = str(document.get("paid_user", "")).strip().lower()
    return paid_user in {"yes", "true", "1", "paid", "vip", "owner", "unlimited"}


def register_user_sync(user: Any) -> tuple[bool, dict[str, Any] | None]:
    if user is None or getattr(user, "is_bot", False):
        return False, None

    user_id = int(user.id)
    username = getattr(user, "username", None) or "N/A"
    defaults = user_defaults(user_id, username)
    defaults.pop("username")
    defaults.pop("updated_at")
    try:
        result = users.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "username": username,
                    "updated_at": utc_now(),
                },
                "$setOnInsert": defaults,
            },
            upsert=True,
        )
        users.update_one(
            {
                "user_id": user_id,
                "daily_successful_requests": {"$exists": False},
            },
            {
                "$set": {
                    "daily_successful_requests": 0,
                    "pending_like_requests": 0,
                    "daily_reset_at": next_daily_reset(),
                }
            },
        )
        document = users.find_one({"user_id": user_id})
        return result.upserted_id is not None, document
    except PyMongoError:
        logger.exception("User registration failed for user id %s", user_id)
        return False, None


def list_users_sync() -> list[dict[str, Any]]:
    return list(users.find({}).sort("user_id", ASCENDING))


async def register_user(user: Any, notify_admin: bool = True) -> dict[str, Any] | None:
    first_registration, document = await asyncio.to_thread(register_user_sync, user)
    if document is None:
        status_print("USER REGISTRATION FAILED")
    if first_registration and notify_admin and document is not None:
        status_print("NEW USER REGISTERED")
        await notify_new_user(user, document)
    return document


async def notify_new_user(user: Any, document: dict[str, Any]) -> None:
    try:
        total_users = await asyncio.to_thread(users.count_documents, {})
        display_name = (
            getattr(user, "first_name", None)
            or getattr(user, "last_name", None)
            or document.get("username")
            or "N/A"
        )
        username = document.get("username") or "N/A"
        username_display = (
            f"@{username}" if username != "N/A" else "N/A"
        )
        notification = (
            "╭━━━ 🎉 Nᴇᴡ Uꜱᴇʀ Jᴏɪɴᴇᴅ ━━━╮\n"
            "│\n"
            f"│ 👤 Nᴀᴍᴇ     : {display_name}\n"
            f"│ 🔗 Uꜱᴇʀɴᴀᴍᴇ : {username_display}\n"
            f"│ 🆔 Uꜱᴇʀ ID  :\n"
            "│\n"
            f"│ `{document['user_id']}`\n"
            "│\n"
            "├───────────────\n"
            f"│ 👥 Tᴏᴛᴀʟ Uꜱᴇʀs : {total_users}\n"
            "╰━━━ 💾 Uꜱᴇʀ Dᴀᴛᴀʙᴀꜱᴇ ━━━╯"
        )
        await bot.send_message(int(CONFIG["admin_id"]), notification)
    except Exception:
        logger.exception("New-user notification failed")


def is_group_message(message: Message) -> bool:
    chat_type = getattr(message.chat, "type", None)
    return chat_type in {ChatType.GROUP, ChatType.SUPERGROUP} or str(
        chat_type
    ).lower() in {"group", "supergroup", "chattype.group", "chattype.supergroup"}


def human_admin_document(member: Any) -> dict[str, Any] | None:
    user = getattr(member, "user", None)
    if user is None or getattr(user, "is_bot", False):
        return None
    return {
        "user_id": int(user.id),
        "username": getattr(user, "username", None) or "N/A",
        "first_name": getattr(user, "first_name", None) or "",
    }


async def sync_group(message: Message) -> bool:
    if not is_group_message(message):
        return True

    try:
        bot_member = await bot.get_chat_member(message.chat.id, "me")
        status = getattr(bot_member, "status", None)
        if status not in {
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
            "administrator",
            "creator",
        }:
            await message.reply_text(
                "⚠️ 𝗣ʟᴇᴀsᴇ Mᴀᴋᴇ Mᴇ 𝗔ᴅᴍɪɴ Tᴏ Usᴇ Tʜɪs Bᴏᴛ!",
                quote=True,
            )
            return False

        human_admins: list[dict[str, Any]] = []
        async for member in bot.get_chat_members(
            message.chat.id,
            filter=ChatMembersFilter.ADMINISTRATORS,
        ):
            admin = human_admin_document(member)
            if admin is not None and admin["user_id"] not in {
                item["user_id"] for item in human_admins
            }:
                human_admins.append(admin)

        await asyncio.to_thread(
            groups.update_one,
            {"chat_id": int(message.chat.id)},
            {
                "$set": {
                    "group_name": (
                        getattr(message.chat, "title", None)
                        or str(message.chat.id)
                    ),
                    "admins": human_admins,
                    "updated_at": utc_now(),
                },
                "$setOnInsert": {"chat_id": int(message.chat.id)},
            },
            upsert=True,
        )
        return True
    except Exception:
        logger.exception("Group administrator synchronization failed")
        await message.reply_text(
            "⚠️ I Cᴏᴜʟᴅ Nᴏᴛ Vᴇʀɪғʏ Gʀᴏᴜᴘ Pᴇʀᴍɪssɪᴏɴs.\n"
            "Pʟᴇᴀsᴇ Tʀʏ Aɢᴀɪɴ Sʜᴏʀᴛʟʏ.",
            quote=True,
        )
        return False



def is_configured_admin(user_id: int | None) -> bool:
    return user_id is not None and int(user_id) == int(CONFIG["admin_id"])


def is_private_chat(message: Message) -> bool:
    chat_type = getattr(getattr(message, "chat", None), "type", None)
    return chat_type == ChatType.PRIVATE or str(chat_type).lower() in {"private", "chattype.private"}


def force_join_link(document: dict[str, Any]) -> str | None:
    link = str(document.get("invite_link") or "").strip()
    return link if link.startswith(("http://", "https://", "tg://")) else None


def force_join_keyboard(documents: list[dict[str, Any]]) -> InlineKeyboardMarkup | None:
    rows: list[list[InlineKeyboardButton]] = []
    for document in documents:
        link = force_join_link(document)
        if link:
            rows.append([InlineKeyboardButton("🔗 Jᴏɪɴ Nᴏᴡ", url=link)])
    if not rows:
        return None
    rows.append([InlineKeyboardButton("✅ Cʜᴇᴄᴋ", callback_data="force_join_check")])
    return InlineKeyboardMarkup(rows)


def force_join_prompt(documents: list[dict[str, Any]]) -> str:
    lines = [
        "<pre>╭━━━━━━━━━━━━━━━━━━━━━━━╮",
        "│  🔒 Fᴏʀᴄᴇ Tᴏ Jᴏɪɴ",
        "╰━━━━━━━━━━━━━━━━━━━━━━━╯",
        "",
        "Yᴏᴜ Mᴜsᴛ Jᴏɪɴ Oᴜʀ Cʜᴀɴɴᴇʟ/Gʀᴏᴜᴘ Bᴇғᴏʀᴇ Uѕɪɴɢ Tʜɪѕ Cᴏᴍᴍᴀɴᴅ.",
        "",
    ]
    for document in documents:
        title = html.escape(str(document.get("title") or "Rᴇǫᴜɪʀᴇᴅ Cʜᴀᴛ"), quote=True)
        lines.append(f"📢 {title} — Jᴏɪɴ Nᴏᴡ")
    lines.append("</pre>")
    return "\n".join(lines)


def force_member_status_name(status: Any) -> str:
    return str(status).lower().rsplit(".", 1)[-1]


def is_force_bot_admin(status: Any) -> bool:
    return status in {ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR} or force_member_status_name(status) in {
        "owner",
        "creator",
        "administrator",
    }


def is_force_join_member(status: Any) -> bool:
    return status in {
        ChatMemberStatus.OWNER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.MEMBER,
    } or force_member_status_name(status) in {
        "owner",
        "creator",
        "administrator",
        "member",
    }


async def missing_force_join_chats(user_id: int) -> list[dict[str, Any]] | None:
    try:
        documents = await asyncio.to_thread(lambda: list(force_join.find({})))
    except PyMongoError:
        logger.exception("Force-join collection read failed")
        return None

    missing: list[dict[str, Any]] = []
    for document in documents:
        chat_id = document.get("chat_id")
        if chat_id in (None, ""):
            continue
        try:
            bot_member = await bot.get_chat_member(int(chat_id), "me")
            bot_status = getattr(bot_member, "status", None)
            if not is_force_bot_admin(bot_status):
                logger.error(
                    "Force-join bot is not administrator for chat %s (status=%s)",
                    chat_id,
                    bot_status,
                )
                return None
            member = await bot.get_chat_member(int(chat_id), user_id)
            user_status = getattr(member, "status", None)
            if not is_force_join_member(user_status):
                # OWNER/CREATOR, ADMINISTRATOR, and MEMBER pass.
                # LEFT, BANNED, and RESTRICTED remain blocked.
                missing.append(document)
        except Exception:
            logger.exception(
                "Force-join membership check failed for chat %s",
                chat_id,
            )
            return None
    return missing


async def command_access_allowed(message: Message) -> bool:
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if is_configured_admin(user_id):
        return True
    if user_id is None:
        return False
    missing = await missing_force_join_chats(int(user_id))
    if missing is None:
        await message.reply_text(
            "<pre>⚠️ Fᴏʀᴄᴇ Tᴏ Jᴏɪɴ Vᴇʀɪғɪᴄᴀᴛɪᴏɴ Tᴇᴍᴘᴏʀᴀʀɪʟʏ Uɴᴀᴠᴀɪʟᴀʙʟᴇ.\nPʟᴇᴀsᴇ Tʀʏ Aɢᴀɪɴ Sʜᴏʀᴛʟʏ.</pre>",
            parse_mode=ParseMode.HTML, quote=True,
        )
        return False
    if missing:
        await message.reply_text(
            force_join_prompt(missing),
            parse_mode=ParseMode.HTML,
            reply_markup=force_join_keyboard(missing),
            quote=True,
        )
        return False
    if is_private_chat(message):
        await message.reply_text(
            "<pre>🚫 Aᴄᴄᴇss Dᴇɴɪᴇᴅ!\n\nTʜɪs Cᴏᴍᴍᴀɴᴅ Iѕ Oɴʟʏ Aᴠᴀɪʟᴀʙʟᴇ Iɴ Gʀᴏᴜᴘs Fᴏʀ Rᴇɢᴜʟᴀʀ Uѕᴇʀѕ.</pre>",
            parse_mode=ParseMode.HTML, quote=True,
        )
        return False
    return True


async def verify_force_join_callback(callback_query: CallbackQuery) -> None:
    message = callback_query.message
    user = callback_query.from_user
    if message is None or user is None:
        return
    missing = await missing_force_join_chats(int(user.id))
    if missing is None:
        await callback_query.answer("⚠️ Vᴇʀɪғɪᴄᴀᴛɪᴏɴ Tᴇᴍᴘᴏʀᴀʀɪʟʏ Uɴᴀᴠᴀɪʟᴀʙʟᴇ.", show_alert=True)
        return
    if missing:
        await callback_query.answer("⚠️ Vᴇʀɪғɪᴄᴀᴛɪᴏɴ Fᴀɪʟᴇᴅ!", show_alert=True)
        await message.edit_text(force_join_prompt(missing), parse_mode=ParseMode.HTML, reply_markup=force_join_keyboard(missing))
        return
    await callback_query.answer("✅ Vᴇʀɪғɪᴇᴅ!")
    await message.edit_text(
        "<pre>✅ Vᴇʀɪғɪᴄᴀᴛɪᴏɴ Sᴜᴄᴄᴇssғᴜʟ!\n\nYᴏᴜ Cᴀɴ Nᴏᴡ Sᴇɴᴅ Yᴏᴜʀ Cᴏᴍᴍᴀɴᴅs.</pre>",
        parse_mode=ParseMode.HTML,
    )


def force_chat_type(chat: Any) -> str | None:
    chat_type = getattr(chat, "type", None)
    value = str(chat_type).lower()
    if chat_type == ChatType.CHANNEL or value in {"channel", "chattype.channel"}:
        return "CHANNEL"
    if chat_type == ChatType.GROUP or value in {"group", "chattype.group"}:
        return "GROUP"
    if chat_type == ChatType.SUPERGROUP or value in {"supergroup", "chattype.supergroup"}:
        return "SUPERGROUP"
    return None


async def force_chat_invite_link(chat: Any, chat_id: int) -> str | None:
    username = str(getattr(chat, "username", "") or "").strip().lstrip("@")
    if username:
        return f"https://t.me/{username}"
    existing = str(getattr(chat, "invite_link", "") or "").strip()
    if existing:
        return existing
    try:
        return await bot.export_chat_invite_link(chat_id)
    except Exception:
        logger.warning("Could not export invite link for force-join chat %s", chat_id)
        return None


async def make_force_admin_help(callback_query: CallbackQuery) -> None:
    message = callback_query.message
    if message is None:
        return
    try:
        chat_id = int((callback_query.data or "").split(":", 1)[1])
        chat = await bot.get_chat(chat_id)
    except (IndexError, TypeError, ValueError):
        await callback_query.answer("⚠️ Iɴᴠᴀʟɪᴅ Cʜᴀᴛ.", show_alert=True)
        return
    except Exception:
        await callback_query.answer("⚠️ Cʜᴀᴛ Cᴏᴜʟᴅ Nᴏᴛ Bᴇ Fᴏᴜɴᴅ.", show_alert=True)
        return
    buttons = []
    username = str(getattr(chat, "username", "") or "").strip().lstrip("@")
    if username:
        buttons.append([InlineKeyboardButton("📢 Oᴘᴇɴ Cʜᴀᴛ", url=f"https://t.me/{username}")])
    await callback_query.answer()
    await message.edit_text(
        "<pre>👑 Mᴀᴋᴇ Mᴇ Aᴅᴍɪɴ\n\nTelegram Bot API bot ko bina human admin action ke administrator nahi bana sakti.\n\nCʜᴀᴛ Oᴘᴇɴ Kᴀʀᴇɪɴ, Bᴏᴛ Kᴏ Jᴏɪɴ Kᴀʀᴇɪɴ, Pʜɪʀ Bᴏᴛ Kᴏ Aᴅᴍɪɴ Pʀᴏᴍᴏᴛᴇ Kᴀʀᴇɪɴ.\n\nUske baad /addforce command dobara run karein.</pre>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
    )


def command_arguments(message: Message) -> list[str]:
    text = (message.text or message.caption or "").strip()
    parts = text.split()
    return parts[1:] if parts else []


def valid_like_arguments(arguments: list[str]) -> bool:
    return (
        len(arguments) == 2
        and bool(REGION_PATTERN.fullmatch(arguments[0]))
        and bool(UID_PATTERN.fullmatch(arguments[1]))
        and int(arguments[1]) > 0
    )


def valid_vip_arguments(arguments: list[str]) -> bool:
    return (
        len(arguments) == 3
        and bool(REGION_PATTERN.fullmatch(arguments[0]))
        and bool(UID_PATTERN.fullmatch(arguments[1]))
        and arguments[2].isdigit()
        and 0 < int(arguments[2]) <= 3650
    )


def parse_api_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                return value
    return value


def find_value(payload: Any, aliases: set[str]) -> Any:
    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if normalized in aliases and value not in (None, ""):
                return value
        for value in payload.values():
            found = find_value(value, aliases)
            if found not in (None, ""):
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = find_value(value, aliases)
            if found not in (None, ""):
                return found
    return None


def as_number(value: Any) -> int | None:
    if value is None:
        return None
    match = re.search(r"-?\d+", str(value).replace(",", ""))
    return int(match.group()) if match else None


def payload_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        parts = [str(value) for key, value in payload.items() if key.lower() in {
            "message",
            "msg",
            "status",
            "error",
            "detail",
            "result",
        }]
        return " ".join(parts) or json.dumps(payload, ensure_ascii=False)
    return json.dumps(payload, ensure_ascii=False)


def normalize_api_response(
    api_name: str,
    status_code: int,
    payload: Any,
    raw_text: str = "",
) -> dict[str, Any]:
    data = parse_api_value(payload)
    message_value = next(
        (
            find_value(data, {alias})
            for alias in ("message", "msg", "error", "detail", "result")
            if find_value(data, {alias}) not in (None, "")
        ),
        None,
    )
    text = payload_text(data) if data not in (None, "") else raw_text
    normalized_text = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    already_sent = any(
        phrase in normalized_text
        for phrase in (
            "likes_already_sent",
            "like_already_sent",
            "already_sent",
            "already_received",
            "already_liked",
            "likes_already",
        )
    )
    nickname = find_value(
        data,
        {"nickname", "playername", "playernickname", "name", "username"},
    )
    uid = find_value(data, {"uid", "playeruid", "playerid", "userid", "accountid"})
    region = find_value(data, {"region", "servername", "server", "country"})
    level = find_value(data, {"level", "playerlevel"})
    before = find_value(
        data,
        {
            "before",
            "beforelikes",
            "likesbefore",
            "likebefore",
            "likesbeforecommand",
        },
    )
    after = find_value(
        data,
        {
            "after",
            "afterlikes",
            "likesafter",
            "likeafter",
            "likesaftercommand",
        },
    )
    given = find_value(
        data,
        {
            "given",
            "likesgiven",
            "likesgivenbyapi",
            "likesgivenbybot",
            "likesadded",
            "likecount",
        },
    )
    success_field = find_value(
        data,
        {"success", "successful", "ok", "is_success", "issuccess"},
    )
    explicit_failure = find_value(
        data,
        {"failed", "failure", "error"},
    )
    before_number = as_number(before)
    after_number = as_number(after)
    given_number = as_number(given)
    if given_number is None and before_number is not None and after_number is not None:
        given_number = max(0, after_number - before_number)
    provider_status = find_value(data, {"status"})
    unchanged_zero_like_result = (
        str(provider_status) == "2"
        and given_number == 0
        and before_number is not None
        and after_number is not None
        and before_number == after_number
    )
    already_sent = already_sent or unchanged_zero_like_result

    status_success = str(success_field).lower() in {
        "true",
        "1",
        "yes",
        "ok",
        "success",
        "successful",
    }
    status_failure = (
        str(explicit_failure).lower() in {"true", "1", "yes"}
        or status_code >= 400
        or any(word in normalized_text for word in ("failed", "failure", "error"))
    )
    success = not already_sent and not status_failure and (
        status_success
        or (given_number is not None and given_number > 0)
        or (before_number is not None and after_number is not None and after_number > before_number)
        or (
            status_code < 300
            and any(word in normalized_text for word in ("success", "sent", "done", "complete"))
        )
    )
    return {
        "api_name": api_name,
        "success": success,
        "already_sent": already_sent,
        "nickname": str(nickname) if nickname is not None else None,
        "uid": str(uid) if uid is not None else None,
        "region": str(region) if region is not None else None,
        "level": str(level) if level is not None else None,
        "before": before_number if before_number is not None else before,
        "after": after_number if after_number is not None else after,
        "given": given_number or 0,
        "message": (
            str(message_value)[:300]
            if isinstance(message_value, str) and message_value.strip()
            else "The Like API reported a failure."
        ),
        "status_code": status_code,
    }


def build_api_url(template: str, uid: str, region: str) -> str:
    parsed = urlsplit(template)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["uid"] = uid
    query["server_name"] = region.upper()
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(query),
            parsed.fragment,
        )
    )


async def call_like_api(
    api_name: str,
    template: str,
    uid: str,
    region: str,
) -> dict[str, Any]:
    request_url = build_api_url(template, uid, region)
    logger.info("LIKE API REQUEST: %s", api_name)
    try:
        async with httpx.AsyncClient(
            timeout=LIKE_TIMEOUT,
            follow_redirects=True,
        ) as client:
            response = await client.get(request_url)
        try:
            payload = response.json()
            raw_text = ""
        except ValueError:
            payload = None
            raw_text = response.text
        return normalize_api_response(
            api_name,
            response.status_code,
            payload,
            raw_text,
        )
    except httpx.TimeoutException:
        logger.warning("Like API %s timed out", api_name)
        return {
            "api_name": api_name,
            "success": False,
            "already_sent": False,
            "message": "The Like API timed out.",
            "given": 0,
        }
    except httpx.HTTPError:
        logger.warning("Like API %s returned an HTTP client error", api_name)
        return {
            "api_name": api_name,
            "success": False,
            "already_sent": False,
            "message": "The Like API could not be reached.",
            "given": 0,
        }
    except Exception:
        logger.exception("Unexpected Like API error from %s", api_name)
        return {
            "api_name": api_name,
            "success": False,
            "already_sent": False,
            "message": "The Like API returned an unexpected response.",
            "given": 0,
        }


def reset_user_if_due_sync(user_id: int) -> None:
    now = utc_now()
    users.update_one(
        {
            "user_id": user_id,
            "$or": [
                {"daily_reset_at": {"$lte": now}},
                {"daily_reset_at": {"$exists": False}},
            ],
        },
        {
            "$set": {
                "today_like_given": 0,
                "daily_successful_requests": 0,
                "pending_like_requests": 0,
                "daily_reset_at": next_daily_reset(now),
                "updated_at": now,
            }
        },
    )


def reserve_like_slot_sync(user_id: int) -> dict[str, Any] | None:
    try:
        reset_user_if_due_sync(user_id)
        user_document = users.find_one(
            {"user_id": user_id},
            {"paid_user": 1},
        )
        if is_unlimited_user(user_id, user_document):
            return users.find_one_and_update(
                {"user_id": user_id},
                {
                    "$inc": {"pending_like_requests": 1},
                    "$set": {"updated_at": utc_now()},
                },
                return_document=ReturnDocument.AFTER,
            )
        return users.find_one_and_update(
            {
                "user_id": user_id,
                "$expr": {
                    "$lt": [
                        {
                            "$add": [
                                {"$ifNull": ["$daily_successful_requests", 0]},
                                {"$ifNull": ["$pending_like_requests", 0]},
                            ]
                        },
                        {"$ifNull": ["$like_limit", 1]},
                    ]
                },
            },
            {
                "$inc": {"pending_like_requests": 1},
                "$set": {"updated_at": utc_now()},
            },
            return_document=ReturnDocument.AFTER,
        )
    except PyMongoError:
        logger.exception("Atomic daily Like limit reservation failed")
        return None


def release_like_slot_sync(user_id: int) -> None:
    try:
        users.update_one(
            {"user_id": user_id, "pending_like_requests": {"$gt": 0}},
            {
                "$inc": {"pending_like_requests": -1},
                "$set": {"updated_at": utc_now()},
            },
        )
    except PyMongoError:
        logger.exception("Like limit reservation release failed")


def commit_like_slot_sync(user_id: int, given: int) -> bool:
    try:
        result = users.update_one(
            {"user_id": user_id, "pending_like_requests": {"$gt": 0}},
            {
                "$inc": {
                    "pending_like_requests": -1,
                    "daily_successful_requests": 1,
                    "today_like_given": max(0, given),
                },
                "$set": {"updated_at": utc_now()},
            },
        )
        return result.modified_count == 1
    except PyMongoError:
        logger.exception("Successful Like usage commit failed")
        return False


async def get_api_configuration() -> dict[str, Any]:
    try:
        document = await asyncio.to_thread(apis.find_one, {"_id": "routing"})
        return document or {}
    except PyMongoError:
        logger.exception("Could not read Like API configuration")
        return {}


def configured_like_apis(config: dict[str, Any], mode: str) -> list[tuple[str, str]]:
    if mode == "1":
        names = ["like_api_1"]
    elif mode == "2":
        names = ["like_api_2"]
    elif mode == "3":
        names = ["like_api_3"]
    else:
        names = ["like_api", "like_api_1", "like_api_2", "like_api_3"]
    configured: list[tuple[str, str]] = [
        (name, str(config[name]))
        for name in names
        if config.get(name)
        and isinstance(config.get(name), str)
        and str(config[name]).startswith(("http://", "https://"))
    ]
    if mode == "all":
        dynamic_apis = config.get("like_apis", [])
        if isinstance(dynamic_apis, list):
            configured.extend(
                (f"custom_api_{index}", str(api_url))
                for index, api_url in enumerate(dynamic_apis, start=1)
                if isinstance(api_url, str)
                and api_url.startswith(("http://", "https://"))
            )

    unique: list[tuple[str, str]] = []
    seen_urls: set[str] = set()
    for api_name, api_url in configured:
        if api_url not in seen_urls:
            unique.append((api_name, api_url))
            seen_urls.add(api_url)
    return unique


def valid_like_api_url(value: str) -> bool:
    parsed = urlsplit(value)
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
        and not any(character.isspace() for character in value)
        and len(value) <= 2048
    )


def masked_api_url(value: str) -> str:
    parsed = urlsplit(value)
    if not parsed.query:
        return value
    masked_query = []
    for key, query_value in parse_qsl(parsed.query, keep_blank_values=True):
        lowered_key = key.lower()
        if any(
            sensitive_word in lowered_key
            for sensitive_word in ("key", "token", "secret", "password", "auth")
        ):
            query_value = "••••••"
        masked_query.append((key, query_value))
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(masked_query),
            parsed.fragment,
        )
    )


def like_api_list_text(config: dict[str, Any]) -> str:
    lines = [
        "╭━━━ 𝗟ɪᴋᴇ 𝗔ᴘɪs 𝗟ɪsᴛ ━━━╮",
        f"│ ⚙️ 𝗔ᴄᴛɪᴠᴇ Mᴏᴅᴇ: {config.get('active_like_api', 'all')}",
        "│",
    ]
    total = 0
    for api_name in ("like_api", "like_api_1", "like_api_2", "like_api_3"):
        api_url = config.get(api_name)
        if isinstance(api_url, str) and api_url.startswith(("http://", "https://")):
            total += 1
            status = "✅ Aᴄᴛɪᴠᴇ"
            display_url = masked_api_url(api_url)
        else:
            status = "⚪ Nᴏᴛ Cᴏɴғɪɢᴜʀᴇᴅ"
            display_url = "—"
        lines.extend(
            [
                f"│ 🔹 {api_name}: {status}",
                f"│    {display_url}",
            ]
        )

    dynamic_apis = config.get("like_apis", [])
    if isinstance(dynamic_apis, list):
        for index, api_url in enumerate(dynamic_apis, start=1):
            if isinstance(api_url, str) and api_url.startswith(("http://", "https://")):
                total += 1
                lines.extend(
                    [
                        f"│ 🔹 custom_api_{index}: ✅ Aᴄᴛɪᴠᴇ",
                        f"│    {masked_api_url(api_url)}",
                    ]
                )

    lines.extend(
        [
            "│",
            "│ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "│ 🔥 FF-BOT — Aʟʟ 𝗔ᴘɪ Cᴀᴛᴀʟᴏɢ",
            "│ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ]
    )
    for index, api_entry in enumerate(API_CATALOG, start=1):
        lines.extend(
            [
                f"│ {index}️⃣ {api_entry['name']}",
                f"│    {masked_api_url(api_entry['url'])}",
            ]
        )

    lines.extend(
        [
            "│",
            f"│ 📊 Tᴏᴛᴀʟ Cᴏɴғɪɢᴜʀᴇᴅ: {total}",
            f"│ 📚 Cᴀᴛᴀʟᴏɢ Eɴᴛʀɪᴇs: {len(API_CATALOG)}",
            "╰━━━ 𝗔ᴘɪ Cᴏɴғɪɢᴜʀᴀᴛɪᴏɴ ━━━╯",
        ]
    )
    return "\n".join(lines)


def remove_all_like_apis_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Cᴏɴғɪʀᴍ Rᴇᴍᴏᴠᴇ Aʟʟ",
                    callback_data="confirm_remove_all_like_apis",
                ),
                InlineKeyboardButton(
                    "❌ Cᴀɴᴄᴇʟ",
                    callback_data="cancel_remove_like_apis",
                ),
            ]
        ]
    )


def remove_like_api_keyboard(api_number: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Cᴏɴғɪʀᴍ Rᴇᴍᴏᴠᴇ",
                    callback_data=f"confirm_remove_like_api_{api_number}",
                ),
                InlineKeyboardButton(
                    "❌ Cᴀɴᴄᴇʟ",
                    callback_data="cancel_remove_like_apis",
                ),
            ]
        ]
    )


def combine_api_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [result for result in results if result.get("success")]
    already = [result for result in results if result.get("already_sent")]
    chosen = successful[0] if successful else (already[0] if already else results[0])
    combined = dict(chosen)
    combined["given"] = sum(int(result.get("given") or 0) for result in successful)
    if successful:
        for field in ("nickname", "uid", "region", "level", "before", "after"):
            if not combined.get(field):
                combined[field] = next(
                    (result.get(field) for result in successful if result.get(field)),
                    None,
                )
        combined["success"] = True
        combined["already_sent"] = False
        combined["message"] = next(
            (
                result.get("message")
                for result in successful
                if result.get("message")
            ),
            "Likes sent successfully.",
        )
    return combined


def used_limit_text(document: dict[str, Any]) -> str:
    used = int(document.get("daily_successful_requests", 0) or 0)
    limit = int(document.get("like_limit", 1) or 1)
    remaining = max(0, limit - used)
    return f"{used}/{limit} • {remaining} Rᴇᴍᴀɪɴɪɴɢ"


def limit_display_text(document: dict[str, Any], user_id: int) -> str:
    if is_unlimited_user(user_id, document):
        return "♾️ Yᴏᴜʀ Lɪᴍɪᴛ: Uɴʟɪᴍɪᴛᴇᴅ (Oᴡɴᴇʀ/VIP)"
    return used_limit_text(document)


def remaining_vip_days(
    expires_at: datetime,
    now: datetime | None = None,
) -> int:
    current = now or utc_now()
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return max(0, math.ceil((expires_at - current).total_seconds() / 86400))


def success_message(
    result: dict[str, Any],
    document: dict[str, Any],
    uid: str,
    region: str,
    user_id: int,
) -> str:
    return (
        "✅Lɪᴋᴇs Sᴇɴᴛ Sᴜᴄᴄᴇꜱꜰᴜʟʟʏ 🥳\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"👤Pʟᴀʏᴇʀ Nɪᴄᴋɴᴀᴍᴇ: {result.get('nickname') or 'N/A'}\n"
        f"🆔Pʟᴀʏᴇʀ UID: {result.get('uid') or uid}\n"
        f"🌍Pʟᴀʏᴇʀ Rᴇɢɪᴏɴ: {result.get('region') or region.upper()}\n"
        f"📊Pʟᴀʏᴇʀ Lᴇᴠᴇʟ: {result.get('level') or 'N/A'}\n"
        f"❤️Bᴇꜰᴏʀᴇ Lɪᴋᴇꜱ: {result.get('before') or 'N/A'}\n"
        f"💝Aꜰᴛᴇʀ Lɪᴋᴇꜱ: {result.get('after') or 'N/A'}\n"
        f"🤖Lɪᴋᴇs Gɪᴠᴇɴ Bʏ Bᴏᴛ: {result.get('given') or 0}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📊Dᴀɪʟʏ Lɪᴋᴇ Uꜱᴀɢᴇ: {limit_display_text(document, user_id)}"
    )


def already_liked_message(result: dict[str, Any], uid: str) -> str:
    return (
        "⚠️Fᴀɪʟᴇᴅ Tᴏ Sᴇɴᴅ Lɪᴋᴇꜱ\n\n"
        f"👤Pʟᴀʏᴇʀ Nɪᴄᴋɴᴀᴍᴇ: {result.get('nickname') or 'N/A'}\n"
        f"🆔Pʟᴀʏᴇʀ UID: {result.get('uid') or uid}\n"
        "🙅🏻Mᴇꜱꜱᴀɢᴇ: Lɪᴋᴇꜱ_ᴀʟʀᴇᴀᴅʏ_ꜱᴇɴᴅ"
    )


def failure_message(result: dict[str, Any], uid: str) -> str:
    return "🔌 Fᴀɪʟᴇᴅ Tᴏ Cᴏɴɴᴇᴄᴛ Tᴏ Tʜᴇ API. Pʟᴇᴀꜱᴇ Tʀʏ Aɢᴀɪɴ Lᴀᴛᴇʀ."


async def process_like(user_id: int, uid: str, region: str) -> tuple[str, dict[str, Any] | None]:
    slot = await asyncio.to_thread(reserve_like_slot_sync, user_id)
    if slot is None:
        try:
            current = await asyncio.to_thread(users.find_one, {"user_id": user_id})
        except PyMongoError:
            current = None
        if current is not None:
            used = int(current.get("daily_successful_requests", 0) or 0)
            limit = int(current.get("like_limit", 1) or 1)
            if not is_unlimited_user(user_id, current) and used >= limit:
                return daily_limit_message(current), None
            return "Another Like request is still processing. Please try again shortly.", None
        return "The database is temporarily unavailable. Please try again.", None

    try:
        api_config = await get_api_configuration()
        mode = str(api_config.get("active_like_api", "all"))
        selected_apis = configured_like_apis(api_config, mode)
        if not selected_apis:
            await asyncio.to_thread(release_like_slot_sync, user_id)
            return "No Like API is configured for the active mode.", None

        results = await asyncio.gather(
            *(
                call_like_api(api_name, template, uid, region)
                for api_name, template in selected_apis
            ),
            return_exceptions=True,
        )
        safe_results: list[dict[str, Any]] = []
        for item in results:
            if isinstance(item, Exception):
                logger.error("Independent Like API task failed: %s", type(item).__name__)
                safe_results.append(
                    {
                        "success": False,
                        "already_sent": False,
                        "message": "One Like API failed.",
                        "given": 0,
                    }
                )
            else:
                safe_results.append(item)

        combined = combine_api_results(safe_results)
        if combined.get("success"):
            given = int(combined.get("given") or 0)
            committed = await asyncio.to_thread(
                commit_like_slot_sync,
                user_id,
                given,
            )
            if not committed:
                await asyncio.to_thread(release_like_slot_sync, user_id)
                return "The Like result was received, but usage could not be saved.", None
            refreshed = await asyncio.to_thread(users.find_one, {"user_id": user_id})
            return (
                success_message(
                    combined,
                    refreshed or slot,
                    uid,
                    region,
                    user_id,
                ),
                refreshed or slot,
            )

        await asyncio.to_thread(release_like_slot_sync, user_id)
        if combined.get("already_sent"):
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🧑‍💻 DEVLOPER",
                            url="https://t.me/BALAK_TRUSTED",
                        )
                    ]
                ]
            )
            return already_liked_message(combined, uid), keyboard
        return failure_message(combined, uid), None
    except Exception:
        await asyncio.to_thread(release_like_slot_sync, user_id)
        logger.exception("Like processing failed after reserving a slot")
        return "The Like request failed safely. Please try again.", None


def daily_limit_message(document: dict[str, Any]) -> str:
    return (
        "🚫Dᴀɪʟʏ Lɪᴍɪᴛ Rᴇᴀᴄʜᴇᴅ!\n\n"
        f"Yᴏᴜ Hᴀᴠᴇ Uꜱᴇᴅ: {used_limit_text(document)} Sᴜᴄᴄᴇꜱꜰᴜʟ Lɪᴋᴇꜱ Tᴏᴅᴀʏ.\n"
        "Pʟᴇᴀꜱᴇ Tʀʏ Aɢᴀɪɴ Tᴏᴍᴏʀʀᴏᴡ Aꜰᴛᴇʀ 4:00 AM IST."
    )


async def daily_reset_loop() -> None:
    status_print("DAILY RESET WORKER STARTED: 4:00 AM IST")
    while True:
        now = utc_now()
        next_reset = next_daily_reset(now)
        delay = max(0.1, (next_reset - now).total_seconds())
        await asyncio.sleep(delay)
        try:
            reset_at = next_daily_reset(ist_now())
            await asyncio.to_thread(
                users.update_many,
                {},
                {
                    "$set": {
                        "today_like_given": 0,
                        "daily_successful_requests": 0,
                        "pending_like_requests": 0,
                        "daily_reset_at": reset_at,
                        "updated_at": utc_now(),
                    }
                },
            )
            status_print("DAILY LIKE LIMIT RESET COMPLETED")
            logger.info("Daily Like usage reset at 4:00 AM IST")
        except PyMongoError:
            status_print("DAILY LIKE LIMIT RESET FAILED")
            logger.exception("Daily Like usage reset failed")


def start_daily_reset_worker() -> None:
    def runner() -> None:
        asyncio.run(daily_reset_loop())

    threading.Thread(
        target=runner,
        name="daily-reset-4am-ist",
        daemon=True,
    ).start()


PLAYER_INFO_API_URL = (
    "https://player-info-ob54.vercel.app/player-info?uid={uid}"
)
SECONDARY_PLAYER_INFO_API_URL = (
    "https://star-info-api.lovable.app/functions/v1/info-api/accinfo?uid={uid}"
)
PRIMARY_BANNER_API_URL = "https://vertex-x-banner.vercel.app/profile?uid={uid}"
SECONDARY_BANNER_API_URL = (
    "https://image.killersharmabot.online/banner-image"
    "?headPic={headPic}&bannerId={bannerId}&name={name}&level={level}"
    "&guild={guild}&pinId={pinId}&celebrity={celebrity}&frame={frame}"
)
OUTFIT_API_URL = (
    "https://vertex-x-outfit.vercel.app/outfit-image?uid={uid}&key=VERTEX"
)


def profile_value(payloads: list[Any], aliases: set[str]) -> Any:
    for payload in payloads:
        value = find_value(payload, aliases)
        if value not in (None, ""):
            return value
    return None


def profile_text(value: Any) -> str:
    if value in (None, ""):
        return "Not Available"
    if isinstance(value, (dict, list)):
        if not value:
            return "Not Available"
        return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
    return str(value)


def html_profile_text(value: Any) -> str:
    return html.escape(profile_text(value), quote=True)


def profile_response_is_valid(status_code: int, payload: Any) -> bool:
    if status_code >= 400 or payload in (None, ""):
        return False
    uid = find_value(payload, {"uid", "playeruid", "playerid", "userid"})
    nickname = find_value(
        payload,
        {"nickname", "playername", "playernickname", "username"},
    )
    response_text = payload_text(payload).lower()
    not_found = any(
        phrase in response_text
        for phrase in (
            "not found",
            "invalid uid",
            "player not exist",
            "does not exist",
        )
    )
    error = find_value(payload, {"error", "failed", "failure"})
    has_profile_data = uid not in (None, "") or nickname not in (None, "")
    if not has_profile_data:
        has_profile_data = any(
            profile_value([payload], {field}) not in (None, "")
            for field in (
                "level",
                "region",
                "likes",
                "guildname",
                "brrank",
                "csrank",
            )
        )
    if not has_profile_data:
        return False
    if error not in (None, "") and uid in (None, "") and nickname in (None, ""):
        return False
    return not not_found or uid not in (None, "") or nickname not in (None, "")


async def fetch_player_info(url: str) -> Any | None:
    try:
        async with httpx.AsyncClient(
            timeout=LIKE_TIMEOUT,
            follow_redirects=True,
        ) as client:
            response = await client.get(url)
        try:
            payload = response.json()
        except ValueError:
            return None
        if profile_response_is_valid(response.status_code, payload):
            return payload
    except httpx.HTTPError:
        return None
    except Exception:
        logger.exception("Player info API request failed")
    return None


async def fetch_image_bytes(url: str) -> bytes | None:
    try:
        async with httpx.AsyncClient(
            timeout=LIKE_TIMEOUT,
            follow_redirects=True,
        ) as client:
            response = await client.get(url)
        content_type = response.headers.get("content-type", "").lower()
        if response.status_code >= 400 or not response.content or not content_type.startswith("image/"):
            return None
        try:
            from PIL import Image

            with Image.open(io.BytesIO(response.content)) as image:
                image.verify()
        except Exception:
            logger.warning("Image API returned invalid image data", exc_info=True)
            return None
        return response.content
    except httpx.HTTPError:
        return None
    except Exception:
        logger.exception("Profile image request failed")
    return None


def create_media_temp_file(content: bytes, suffix: str) -> str:
    DOWNLOADS_DIR.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb",
        suffix=suffix,
        prefix="profile_",
        dir=DOWNLOADS_DIR,
        delete=False,
    ) as temporary_file:
        temporary_file.write(content)
        return temporary_file.name


def make_sticker_file(image_bytes: bytes) -> str:
    # Telegram stickers must be sent as a sticker-compatible WEBP file.
    from PIL import Image

    with Image.open(io.BytesIO(image_bytes)) as image:
        sticker = image.convert("RGBA")
        sticker.thumbnail((512, 512), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        sticker.save(output, format="WEBP", lossless=True, method=6)
    return create_media_temp_file(output.getvalue(), ".webp")


def build_profile_output(uid: str, payloads: list[Any]) -> str:
    missing = "Nᴏᴛ Aᴠᴀɪʟᴀʙʟᴇ"

    def value(*aliases: str) -> str:
        found = profile_value(payloads, set(aliases))
        if found in (None, ""):
            return missing
        return html_profile_text(found)

    def nested_value(
        container_aliases: set[str], field_aliases: set[str]
    ) -> Any:
        for payload in payloads:
            container = find_value(payload, container_aliases)
            if isinstance(container, dict):
                found = find_value(container, field_aliases)
                if found not in (None, ""):
                    return found
        return None

    def readable_timestamp(*aliases: str) -> str:
        found = profile_value(payloads, set(aliases))
        if found in (None, ""):
            return missing
        try:
            timestamp = int(str(found).replace(",", ""))
            return html_profile_text(
                datetime.fromtimestamp(timestamp, tz=IST).strftime(
                    "%d %b %Y, %I:%M %p"
                )
            )
        except (TypeError, ValueError, OverflowError, OSError):
            return missing

    def clean_tag(*aliases: str) -> str:
        found = profile_value(payloads, set(aliases))
        if found in (None, "", [], {}):
            return missing

        def readable_item(item: Any) -> str:
            if isinstance(item, dict):
                label = find_value(item, {"tag", "name", "label", "title", "value"})
                return str(label).strip() if label not in (None, "") else ""
            return str(item).strip()

        if isinstance(found, (list, tuple, set)):
            parts = [readable_item(item) for item in found]
            return html_profile_text(", ".join(part for part in parts if part)) or missing

        cleaned = html.unescape(str(found)).strip()
        try:
            parsed = json.loads(cleaned)
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = None
        if isinstance(parsed, (dict, list)):
            return clean_tag_value(parsed)
        cleaned = cleaned.strip("[]{}\"'")
        return html_profile_text(cleaned) if cleaned else missing

    def clean_tag_value(found: Any) -> str:
        if isinstance(found, dict):
            parts = [str(value).strip() for key, value in found.items()
                     if str(key).lower() not in {"id", "tagid"} and value not in (None, "")]
            return html_profile_text(", ".join(parts)) if parts else missing
        if isinstance(found, (list, tuple, set)):
            parts = [str(item).strip() for item in found if item not in (None, "")]
            return html_profile_text(", ".join(parts)) if parts else missing
        return html_profile_text(found) if found not in (None, "") else missing

    def readable_badges() -> str:
        found = profile_value(payloads, {"badges", "badge", "badgecount"})
        if found in (None, "", [], {}):
            return missing
        if isinstance(found, (list, tuple, set)):
            names = []
            for item in found:
                if isinstance(item, dict):
                    name = find_value(item, {"name", "badgename", "title", "label"})
                    if name not in (None, ""):
                        names.append(str(name))
                elif not str(item).strip().isdigit():
                    names.append(str(item))
            return html_profile_text(", ".join(names)) if names else missing
        if isinstance(found, dict):
            name = find_value(found, {"name", "badgename", "title", "label"})
            return html_profile_text(name) if name not in (None, "") else missing
        return missing if str(found).strip().isdigit() else html_profile_text(found)

    def safe_identity(*aliases: str) -> str:
        found = profile_value(payloads, set(aliases))
        if found in (None, "") or str(found).strip().isdigit():
            return missing
        return html_profile_text(found)

    def count_value(*aliases: str) -> str:
        found = profile_value(payloads, set(aliases))
        if isinstance(found, (list, tuple, set)):
            return str(len(found))
        if found in (None, ""):
            return missing
        return html_profile_text(found)

    def readable_gender() -> str:
        found = profile_value(payloads, {"gender", "sex"})
        if found in (None, ""):
            return missing
        normalized = str(found).upper()
        if normalized.endswith("MALE"):
            return "Mᴀʟᴇ"
        if normalized.endswith("FEMALE"):
            return "Fᴇᴍᴀʟᴇ"
        return html_profile_text(found)

    def readable_language() -> str:
        found = profile_value(payloads, {"language", "lang"})
        if found in (None, ""):
            return missing
        normalized = str(found)
        return html_profile_text(
            normalized.rsplit("_", 1)[-1] if "_" in normalized else normalized
        )

    resolved_uid = profile_value(
        payloads,
        {"uid", "playeruid", "playerid", "userid", "accountid"},
    )
    pet_id = nested_value({"petinfo"}, {"id"})
    pet_selected = nested_value({"petinfo"}, {"isselected"})
    skills = profile_value(payloads, {"equipedskills", "equippedskills", "skills"})
    clothes = profile_value(payloads, {"clothes", "outfit", "outfits"})
    weapons = profile_value(payloads, {"weaponskinshows", "weaponskins", "guns skins"})
    pet_display = "Eǫᴜɪᴘᴘᴇᴅ" if pet_id not in (None, "") else missing
    pet_status = (
        "Eǫᴜɪᴘᴘᴇᴅ" if pet_selected is True else
        "Nᴏᴛ Eǫᴜɪᴘᴘᴇᴅ" if pet_selected is False else missing
    )
    skill_count = str(len(skills)) if isinstance(skills, (list, tuple, set)) else missing
    weapon_count = str(len(weapons)) if isinstance(weapons, (list, tuple, set)) else missing

    return (
        "<pre>"
        "╭━━━━━━━━━━━━━━━━━━━━━━━╮\n"
        "│   🔥 Fʀᴇᴇ Fɪʀᴇ Pʀᴏғɪʟᴇ\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
        "┌─ 👤 Pʟᴀʏᴇʀ Iɴғᴏʀᴍᴀᴛɪᴏɴ\n"
        f"├─ 🎮 Nɪᴄᴋɴᴀᴍᴇ  : {value('nickname', 'playername', 'playernickname', 'name')}\n"
        f"├─ 🆔 Uɪᴅ       : {html_profile_text(resolved_uid or uid)}\n"
        f"├─ 🌍 Rᴇɢɪᴏɴ    : {value('region', 'servername', 'server', 'country')}\n"
        f"├─ ⭐ Lᴇᴠᴇʟ     : {value('level', 'playerlevel')}\n"
        f"├─ ✨ Eхᴘ       : {value('exp', 'experience', 'playerexp')}\n"
        f"├─ ❤️ Lɪᴋᴇs     : {value('likes', 'like', 'likecount', 'totallikes', 'liked')}\n"
        f"├─ 🎖️ Bᴀᴅɢᴇs   : {readable_badges()}\n"
        f"├─ 💎 Pʀɪᴍᴇ     : {value('prime', 'primelevel', 'prime_level')}\n"
        f"└─ 🎮 Vᴇʀsɪᴏɴ   : {value('version', 'gameversion')}\n\n"
        "┌─ 📅 Aᴄᴄᴏᴜɴᴛ Iɴғᴏʀᴍᴀᴛɪᴏɴ\n"
        f"├─ 🗓️ Cʀᴇᴀᴛᴇᴅ   : {readable_timestamp('created', 'createdat', 'createat', 'accountcreated', 'createddate')}\n"
        f"├─ 🕐 Lᴀsᴛ Lᴏɢɪɴ : {readable_timestamp('lastlogin', 'lastloginat', 'lastlogindate', 'lastseen')}\n"
        f"└─ 🎟️ Eʟɪᴛᴇ Pᴀss : {value('elitepass', 'elite_pass', 'elitepasslevel')}\n\n"
        "┌─ 🏆 Rᴀɴᴋ Iɴғᴏʀᴍᴀᴛɪᴏɴ\n"
        f"├─ 🔥 Bʀ Rᴀɴᴋ      : {value('brrank', 'br_rank', 'battleroyalerank', 'rank')}\n"
        f"├─ 📊 Bʀ Pᴏɪɴᴛs    : {value('brpoints', 'br_points', 'battleroyalepts', 'rankingpoints')}\n"
        f"├─ 🏆 Bʀ Mᴀx Rᴀɴᴋ  : {value('brmaxrank', 'br_max_rank', 'maxrank')}\n"
        f"├─ ⚔️ Cꜱ Rᴀɴᴋ      : {value('csrank', 'cs_rank', 'clashsquadrank')}\n"
        f"├─ 📊 Cꜱ Pᴏɪɴᴛs    : {value('cspoints', 'cs_points', 'clashsquadpts')}\n"
        f"└─ 🏆 Cꜱ Mᴀx Rᴀɴᴋ  : {value('csmaxrank', 'cs_max_rank')}\n\n"
        "┌─ 🏰 Gᴜɪʟᴅ Iɴғᴏʀᴍᴀᴛɪᴏɴ\n"
        f"├─ 🏰 Gᴜɪʟᴅ Nᴀᴍᴇ  : {value('guildname', 'guild_name', 'clanname')}\n"
        f"├─ 🆔 Gᴜɪʟᴅ Iᴅ    : {safe_identity('guildid', 'guild_id', 'clanid')}\n"
        f"├─ ⭐ Gᴜɪʟᴅ Lᴇᴠᴇʟ : {value('guildlevel', 'guild_level', 'clanlevel')}\n"
        f"├─ 👥 Mᴇᴍʙᴇʀs     : {value('members', 'guildmembers', 'membercount', 'membernum')}\n"
        f"└─ 👑 Cᴀᴘᴛᴀɪɴ     : {value('captain', 'guildcaptain', 'leader', 'captainid')}\n\n"
        "┌─ 🐾 Pᴇᴛ Iɴғᴏʀᴍᴀᴛɪᴏɴ\n"
        f"├─ 🐾 Pᴇᴛ         : {pet_display}\n"
        f"├─ ⭐ Lᴇᴠᴇʟ       : {html_profile_text(nested_value({"petinfo"}, {"level"}) or missing)}\n"
        f"├─ ✨ Eхᴘ         : {html_profile_text(nested_value({"petinfo"}, {"exp"}) or missing)}\n"
        f"└─ ⚡ Sᴛᴀᴛᴜs      : {pet_status}\n\n"
        "┌─ ⚡ Lᴏᴀᴅᴏᴜᴛ\n"
        f"├─ ⚡ Sᴋɪʟʟs      : {skill_count} Eǫᴜɪᴘᴘᴇᴅ\n"
        f"├─ 👕 Oᴜᴛғɪᴛ     : {'Eǫᴜɪᴘᴘᴇᴅ' if clothes else missing}\n"
        f"└─ 🔫 Wᴇᴀᴘᴏɴ Sᴋɪɴs : {weapon_count} Dɪsᴘʟᴀʏᴇᴅ\n\n"
        "┌─ 📱 Sᴏᴄɪᴀʟ Iɴғᴏʀᴍᴀᴛɪᴏɴ\n"
        f"├─ 🚻 Gᴇɴᴅᴇʀ      : {readable_gender()}\n"
        f"├─ 🌐 Lᴀɴɢᴜᴀɢᴇ    : {readable_language()}\n"
        f"├─ 🏷️ Bᴀᴛᴛʟᴇ Tᴀɢ : {clean_tag('battletag', 'battle_tag')}\n"
        f"├─ 🎯 Sᴏᴄɪᴀʟ Tᴀɢ : {clean_tag('socialtag', 'social_tag')}\n"
        f"└─ ✍️ Sɪɢɴᴀᴛᴜʀᴇ : {value('signature', 'bio', 'about')}\n\n"
        "┌─ 🛡️ Cʀᴇᴅɪᴛ Sᴄᴏʀᴇ\n"
        f"└─ ⭐ Sᴄᴏʀᴇ       : {value('creditscore', 'credit_score', 'credits')}\n\n"
        "╭━━━━━━━━━━━━━━━━━━━━━━━╮\n"
        "│    ✅ Pʀᴏғɪʟᴇ Fᴏᴜɴᴅ\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━━╯"
        "</pre>"
    )


async def send_profile_media(
    message: Message, uid: str, payloads: list[Any]
) -> list[str]:
    async def send_banner_from_url(url: str, source: str) -> bool:
        banner = await fetch_image_bytes(url)
        if banner is None:
            logger.info("%s banner download failed", source)
            return False

        sticker_file = None
        try:
            sticker_file = make_sticker_file(banner)
            await message.reply_sticker(sticker_file, quote=True)
            return True
        except Exception:
            logger.exception("%s banner conversion or Telegram send failed", source)
            return False
        finally:
            if sticker_file is not None:
                try:
                    Path(sticker_file).unlink(missing_ok=True)
                except OSError:
                    logger.warning("Could not remove temporary %s banner file", source)

    primary_banner_sent = await send_banner_from_url(
        PRIMARY_BANNER_API_URL.format(uid=uid),
        "Primary",
    )
    if not primary_banner_sent:
        secondary_values = {
            "headPic": profile_value(payloads, {"headpic"}),
            "bannerId": profile_value(payloads, {"bannerid"}),
            "name": profile_value(payloads, {
                "name", "nickname", "playername", "playernickname",
            }),
            "level": profile_value(payloads, {"level", "playerlevel"}),
            "guild": profile_value(payloads, {
                "guild", "guildname", "clan", "clanname",
            }),
            "pinId": profile_value(payloads, {"pinid"}),
            "celebrity": profile_value(payloads, {"celebrity"}),
            "frame": profile_value(payloads, {"frame"}),
        }
        secondary_url = SECONDARY_BANNER_API_URL.format(
            **{
                key: "" if value in (None, "") else str(value)
                for key, value in secondary_values.items()
            }
        )
        logger.info("Primary banner failed, using secondary banner API")
        banner_sent = await send_banner_from_url(secondary_url, "Secondary")
        if not banner_sent:
            return ["⚠️ Bᴀɴɴᴇʀ Gᴇɴᴇʀᴀᴛɪᴏɴ Fᴀɪʟᴇᴅ"]

    outfit = await fetch_image_bytes(OUTFIT_API_URL.format(uid=uid))
    if outfit is not None:
        outfit_file = None
        try:
            outfit_file = create_media_temp_file(outfit, ".png")
            await message.reply_photo(outfit_file, quote=True)
        except Exception:
            logger.exception("Could not send profile outfit photo")
        finally:
            if outfit_file is not None:
                try:
                    Path(outfit_file).unlink(missing_ok=True)
                except OSError:
                    logger.warning("Could not remove temporary outfit file")
    else:
        logger.info("Outfit image download failed")
    return []

health_app = Flask("ff-bot2-health")


@health_app.get("/")
def health_check() -> str:
    return "OK"


def start_health_server() -> None:
    threading.Thread(
        target=lambda: health_app.run(
            host="0.0.0.0",
            port=int(os.environ.get("PORT", "8000")),
            debug=False,
            use_reloader=False,
        ),
        name="koyeb-health",
        daemon=True,
    ).start()
    status_print("FLASK HEALTH CHECK STARTED ON PORT 8000")


bot = Client(
    "ff-bot2",
    workdir=str(BASE_DIR),
    api_id=CONFIG["api_id"],
    api_hash=CONFIG["api_hash"],
    bot_token=CONFIG["bot_token"],
)


@bot.on_message(filters.incoming & ~filters.service, group=-1)
async def register_every_interaction(_: Client, message: Message) -> None:
    try:
        if message.from_user is not None:
            await register_user(message.from_user)
    except Exception:
        logger.exception("Interaction registration handler failed")


@bot.on_message(filters.command("addforce", case_sensitive=False))
async def add_force_command(_: Client, message: Message) -> None:
    try:
        status_print("COMMAND RECEIVED: /addforce")
        if not is_configured_admin(getattr(message.from_user, "id", None)):
            return
        arguments = command_arguments(message)
        if len(arguments) != 1:
            await message.reply_text("<pre>▸ Uѕᴀɢᴇ: /addforce &lt;chat_id&gt;\n\nEхᴀᴍᴘʟᴇ: /addforce -1001234567890</pre>", parse_mode=ParseMode.HTML, quote=True)
            return
        try:
            chat_id = int(arguments[0])
        except ValueError:
            await message.reply_text("<pre>⚠️ Iɴᴠᴀʟɪᴅ Cʜᴀᴛ Iᴅ.</pre>", parse_mode=ParseMode.HTML, quote=True)
            return
        try:
            chat = await bot.get_chat(chat_id)
        except Exception:
            await message.reply_text("<pre>⚠️ Cʜᴀᴛ Cᴏᴜʟᴅ Nᴏᴛ Bᴇ Fᴏᴜɴᴅ Oʀ Iѕ Nᴏᴛ Aᴄᴄᴇssɪʙʟᴇ Bʏ Tʜᴇ Bᴏᴛ.</pre>", parse_mode=ParseMode.HTML, quote=True)
            return
        chat_type = force_chat_type(chat)
        if chat_type is None:
            await message.reply_text("<pre>⚠️ Oɴʟʏ CHANNEL, GROUP, ᴏʀ SUPERGROUP Cʜᴀᴛs Aʀᴇ Sᴜᴘᴘᴏʀᴛᴇᴅ.</pre>", parse_mode=ParseMode.HTML, quote=True)
            return
        try:
            bot_member = await bot.get_chat_member(chat_id, "me")
            status = getattr(bot_member, "status", None)
            bot_is_admin = is_force_bot_admin(status)
        except Exception:
            logger.exception("Force-join bot-admin check failed for chat %s", chat_id)
            bot_is_admin = False
        title = str(getattr(chat, "title", None) or getattr(chat, "username", None) or chat_id)
        if not bot_is_admin:
            username = str(getattr(chat, "username", "") or "").strip().lstrip("@")
            public_link = f"https://t.me/{username}" if username else None
            stored_link = str(getattr(chat, "invite_link", "") or "").strip() or public_link or ""
            document = {
                "chat_id": chat_id,
                "chat_type": chat_type,
                "title": title,
                "invite_link": stored_link,
                "added_by": int(message.from_user.id),
                "bot_is_admin": False,
                "created_at": utc_now(),
            }
            try:
                await asyncio.to_thread(force_join.update_one, {"chat_id": chat_id}, {"$set": document}, upsert=True)
            except PyMongoError:
                logger.exception("Force-join invalid-admin record save failed for chat %s", chat_id)
            buttons = [[InlineKeyboardButton("👑 Mᴀᴋᴇ Mᴇ Aᴅᴍɪɴ", callback_data=f"force_admin:{chat_id}")]]
            if public_link:
                buttons.insert(0, [InlineKeyboardButton("📢 Oᴘᴇɴ Cʜᴀᴛ", url=public_link)])
            await message.reply_text(
                "<pre>╭━━━━━━━━━━━━━━━━━━━━━━━╮\n│  ⚠️ Fᴏʀᴄᴇ Tᴏ Jᴏɪɴ Nᴏᴛ Aᴅᴅᴇᴅ\n╰━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
                f"├─ 📢 Tʏᴘᴇ : {html.escape(chat_type)}\n├─ 🆔 Cʜᴀᴛ Iᴅ : {chat_id}\n├─ 👑 Aᴅᴍɪɴ : Nᴏ\n└─ 🔗 Lɪɴᴋ : {html.escape(stored_link, quote=True) if stored_link else 'Nᴏᴛ Aᴠᴀɪʟᴀʙʟᴇ'}</pre>",
                parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons), quote=True,
            )
            return
        invite_link = await force_chat_invite_link(chat, chat_id)
        if not invite_link:
            await message.reply_text("<pre>⚠️ Bᴏᴛ Aᴅᴍɪɴ Hᴀɪ, Lᴇᴋɪɴ Vᴀʟɪᴅ Iɴᴠɪᴛᴇ Lɪɴᴋ Nᴀʜɪ Mɪʟ Sᴀᴋᴀ.</pre>", parse_mode=ParseMode.HTML, quote=True)
            return
        document = {"chat_id": chat_id, "chat_type": chat_type, "title": title, "invite_link": invite_link, "added_by": int(message.from_user.id), "bot_is_admin": True, "created_at": utc_now()}
        await asyncio.to_thread(force_join.update_one, {"chat_id": chat_id}, {"$set": document}, upsert=True)
        await message.reply_text(
            "<pre>╭━━━━━━━━━━━━━━━━━━━━━━━╮\n│  ✅ Fᴏʀᴄᴇ Tᴏ Jᴏɪɴ Aᴅᴅᴇᴅ\n╰━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
            f"├─ 📢 Tʏᴘᴇ : {html.escape(chat_type)}\n├─ 🆔 Cʜᴀᴛ Iᴅ : {chat_id}\n├─ 👑 Aᴅᴍɪɴ : Yᴇs\n└─ 🔗 Lɪɴᴋ : {html.escape(invite_link, quote=True)}</pre>",
            parse_mode=ParseMode.HTML, quote=True,
        )
    except PyMongoError:
        logger.exception("Force-join database update failed")
        await message.reply_text("<pre>⚠️ Fᴏʀᴄᴇ Tᴏ Jᴏɪɴ Sᴀᴠᴇ Fᴀɪʟᴇᴅ.</pre>", parse_mode=ParseMode.HTML, quote=True)
    except Exception:
        logger.exception("addforce command failed")
        await message.reply_text("<pre>⚠️ Fᴏʀᴄᴇ Tᴏ Jᴏɪɴ Rᴇǫᴜᴇsᴛ Fᴀɪʟᴇᴅ.</pre>", parse_mode=ParseMode.HTML, quote=True)


@bot.on_callback_query(filters.regex(r"^force_join_check$"))
async def force_join_check_callback(_: Client, callback_query: CallbackQuery) -> None:
    try:
        await verify_force_join_callback(callback_query)
    except Exception:
        logger.exception("Force-join verification callback failed")
        await callback_query.answer("⚠️ Vᴇʀɪғɪᴄᴀᴛɪᴏɴ Fᴀɪʟᴇᴅ!", show_alert=True)


@bot.on_callback_query(filters.regex(r"^force_admin:-?[0-9]+$"))
async def force_admin_callback(_: Client, callback_query: CallbackQuery) -> None:
    try:
        await make_force_admin_help(callback_query)
    except Exception:
        logger.exception("Force-join admin help callback failed")
        await callback_query.answer("⚠️ Rᴇǫᴜᴇsᴛ Fᴀɪʟᴇᴅ.", show_alert=True)


@bot.on_message(filters.command("get", case_sensitive=False))
async def get_uid_command(_: Client, message: Message) -> None:
    processing: Message | None = None
    try:
        status_print("COMMAND RECEIVED: /get")
        if not await command_access_allowed(message):
            return
        arguments = command_arguments(message)
        if len(arguments) != 1 or not UID_PATTERN.fullmatch(arguments[0]):
            await message.reply_text(
                "<pre>❌ Invalid Usage\n\n"
                "Use:\n"
                "/get UID\n\n"
                "Example:\n"
                "/get 1589573783</pre>",
                parse_mode=ParseMode.HTML,
                quote=True,
            )
            return

        uid = arguments[0]
        processing = await message.reply_text(
            "<b>⏳ Pʀᴏᴄᴇꜱꜱɪɴɢ Yᴏᴜʀ Rᴇǫᴜᴇꜱᴛ...</b>",
            parse_mode=ParseMode.HTML,
            quote=True,
        )
        primary_payload, fallback_payload = await asyncio.gather(
            fetch_player_info(PLAYER_INFO_API_URL.format(uid=uid)),
            fetch_player_info(SECONDARY_PLAYER_INFO_API_URL.format(uid=uid)),
        )
        fallback_warnings: list[str] = []
        if primary_payload is None and fallback_payload is not None:
            fallback_warnings.append(
                "⚠️ Primary player-info API unavailable — fallback player-info API used."
            )
        elif primary_payload is None and fallback_payload is None:
            fallback_warnings.append(
                "⚠️ Primary and fallback player-info APIs are unavailable."
            )
        elif fallback_payload is None:
            fallback_warnings.append(
                "⚠️ Fallback player-info API unavailable — primary API data used."
            )
        payloads = [payload for payload in (primary_payload, fallback_payload) if payload is not None]
        valid_payloads = [payload for payload in payloads if payload is not None]

        if processing is not None:
            try:
                await processing.delete()
            except Exception:
                logger.warning("Could not delete UID processing message")
            processing = None

        if not valid_payloads:
            if fallback_warnings:
                await message.reply_text(
                    "\n".join(fallback_warnings),
                    quote=True,
                )
            await message.reply_text(
                "<pre>❌ Pʀᴏғɪʟᴇ Nᴏᴛ Fᴏᴜɴᴅ\n\n"
                "Please check the UID and try again.</pre>",
                parse_mode=ParseMode.HTML,
                quote=True,
            )
            return

        await message.reply_text(
            build_profile_output(uid, valid_payloads),
            parse_mode=ParseMode.HTML,
            quote=True,
        )
        media_warnings = await send_profile_media(message, uid, valid_payloads)
        all_warnings = fallback_warnings + media_warnings
        if all_warnings:
            await message.reply_text("\n".join(all_warnings), quote=True)
    except Exception:
        logger.exception("Get UID command failed")
        if processing is not None:
            try:
                await processing.delete()
            except Exception:
                pass
        await message.reply_text(
            "<pre>❌ Pʀᴏғɪʟᴇ Nᴏᴛ Fᴏᴜɴᴅ\n\n"
            "Please check the UID and try again.</pre>",
            parse_mode=ParseMode.HTML,
            quote=True,
        )


@bot.on_message(filters.command("start"))
async def start_command(_: Client, message: Message) -> None:
    try:
        status_print("COMMAND RECEIVED: /start")
        if message.from_user is not None:
            await register_user(message.from_user)
        bot_user = await bot.get_me()
        username = getattr(bot_user, "username", None)
        if not username:
            await message.reply_text(
                "⚠️ Bᴏᴛ Usᴇʀɴᴀᴍᴇ Is Nᴏᴛ Aᴠᴀɪʟᴀʙʟᴇ Rɪɢʜᴛ Nᴏᴡ.",
                quote=True,
            )
            return
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "➕ ADD ME",
                        url=f"https://t.me/{username}?startgroup=true",
                    )
                ]
            ]
        )
        await message.reply_text(
            "✦ 𝗪ᴇʟᴄᴏᴍᴇ Tᴏ 𝐅ʀᴇᴇ Fɪʀᴇ Lɪᴋᴇ Bᴏᴛ ✦\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "👋 Hᴇʟʟᴏ, 𝗙ʀɪᴇɴᴅ!\n\n"
            "Gᴇᴛ ʏᴏᴜʀ ᴘʟᴀʏᴇʀ ʟɪᴋᴇs ғᴀsᴛ ᴀɴᴅ sᴍᴏᴏᴛʜʟʏ.\n\n"
            "▸ 𝐇ᴏᴡ Tᴏ Rᴇǫᴜᴇsᴛ Lɪᴋᴇs\n"
            "Sᴇɴᴅ: 𝗞 /like ɪɴᴅ 1589573783\n\n"
            "⚡ 𝗢ɴᴇ ʀᴇǫᴜᴇsᴛ ᴀ ᴅᴀʏ ғᴏʀ ғʀᴇᴇ ᴜsᴇʀs\n"
            "━━━━━━━━━━━━━━━━━━",
            reply_markup=keyboard,
            quote=True,
        )
    except Exception:
        logger.exception("Start command failed")
        await message.reply_text(
            "⚠️ Sᴏᴍᴇᴛʜɪɴɢ Wᴇɴᴛ Wʀᴏɴɢ.\nPʟᴇᴀsᴇ Tʀʏ Aɢᴀɪɴ.",
            quote=True,
        )


@bot.on_message(filters.command("users", case_sensitive=False))
async def users_command(_: Client, message: Message) -> None:
    export_path: Path | None = None
    processing: Message | None = None
    try:
        status_print("COMMAND RECEIVED: /users")
        if message.from_user is None or int(message.from_user.id) != int(
            CONFIG["admin_id"]
        ):
            return

        processing = await message.reply_text(
            "⏳ Processing users database...\n"
            "🔎 Fetching all user documents...",
            quote=True,
        )
        documents = await asyncio.to_thread(list_users_sync)
        try:
            await processing.edit_text(
                "⏳ Processing users database...\n"
                f"✅ Fetched {len(documents)} documents.\n"
                "📄 Formatting export file...",
            )
        except Exception:
            logger.warning("Could not update users export processing message")

        downloads_dir = BASE_DIR / "Downloads"
        downloads_dir.mkdir(exist_ok=True)
        export_path = downloads_dir / f"users_{int(time.time())}.json"
        export_path.write_text(
            json.dumps(documents, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

        try:
            await processing.edit_text(
                "⏳ Processing users database...\n"
                f"✅ Fetched {len(documents)} documents.\n"
                "✅ Export formatted.\n"
                "📤 Sending document...",
            )
        except Exception:
            logger.warning("Could not update users export sending status")

        await message.reply_document(
            str(export_path),
            caption=(
                "✅ 𝗨sᴇʀs Dᴀᴛᴀʙᴀsᴇ Eᴜxᴘᴏʀᴛ\n"
                f"📄 Tᴏᴛᴀʟ Dᴏᴄᴜᴍᴇɴᴛs: {len(documents)}\n"
                "🗂️ Fɪʟᴇ Fᴏʀᴍᴀᴛ: JSON"
            ),
            quote=True,
        )
    except PyMongoError:
        logger.exception("Users export database query failed")
        await message.reply_text(
            "⚠️ Cᴏᴜʟᴅ Nᴏᴛ Rᴇᴀᴅ Tʜᴇ Uꜱᴇʀs Dᴀᴛᴀʙᴀsᴇ.\n"
            "Pʟᴇᴀsᴇ Tʀʏ Aɢᴀɪɴ Lᴀᴛᴇʀ.",
            quote=True,
        )
    except Exception:
        logger.exception("Users export command failed")
        await message.reply_text(
            "⚠️ Uꜱᴇʀs Eᴜxᴘᴏʀᴛ Fᴀɪʟᴇᴅ.\n"
            "Pʟᴇᴀsᴇ Tʀʏ Aɢᴀɪɴ Lᴀᴛᴇʀ.",
            quote=True,
        )
    finally:
        if processing is not None:
            try:
                await processing.delete()
            except Exception:
                logger.warning("Could not delete users export processing message")
        if export_path is not None:
            try:
                export_path.unlink(missing_ok=True)
            except Exception:
                logger.warning("Could not remove temporary users export file")


@bot.on_message(filters.command("like"))
async def like_command(_: Client, message: Message) -> None:
    processing: Message | None = None
    try:
        status_print("COMMAND RECEIVED: /like")
        if not await command_access_allowed(message):
            return
        if message.from_user is None:
            return
        document = await register_user(message.from_user)
        if document is None:
            await message.reply_text(
                "⚠️ Usᴇʀ Rᴇɢɪsᴛʀᴀᴛɪᴏɴ Is Tᴇᴍᴘᴏʀᴀʀɪʟʏ Uɴᴀᴠᴀɪʟᴀʙʟᴇ.",
                quote=True,
            )
            return
        if not await sync_group(message):
            return
        arguments = command_arguments(message)
        if not valid_like_arguments(arguments):
            await message.reply_text(
                "▸ 𝗨sᴀɢᴇ: /like ʀᴇɢɪᴏɴ ᴜɪᴅ\n"
                "E.xᴀᴍᴘʟᴇ: /like ɪɴᴅ 1589573783",
                quote=True,
            )
            return
        await asyncio.to_thread(reset_user_if_due_sync, int(message.from_user.id))
        document = await asyncio.to_thread(
            users.find_one,
            {"user_id": int(message.from_user.id)},
        ) or document
        used = int(document.get("daily_successful_requests", 0) or 0)
        pending = int(document.get("pending_like_requests", 0) or 0)
        limit = int(document.get("like_limit", 1) or 1)
        if not is_unlimited_user(int(message.from_user.id), document) and used + pending >= limit:
            await message.reply_text(daily_limit_message(document), quote=True)
            return
        processing = await message.reply_text(
            "⏳ 𝗣ʀᴏᴄᴇssɪɴɢ Yᴏᴜʀ Rᴇǫᴜᴇsᴛ...",
            quote=True,
        )
        result_text, result_data = await process_like(
            int(message.from_user.id),
            arguments[1],
            arguments[0],
        )
        reply_markup = result_data if isinstance(result_data, InlineKeyboardMarkup) else None
        if processing is not None:
            try:
                await processing.delete()
            except Exception:
                logger.warning("Could not delete Like processing message")
        await message.reply_text(
            result_text,
            reply_markup=reply_markup,
            quote=True,
        )
    except Exception:
        logger.exception("Like command failed")
        if processing is not None:
            try:
                await processing.delete()
            except Exception:
                pass
        await message.reply_text(
            "⚠️ Tʜᴇ Lɪᴋᴇ Rᴇǫᴜᴇsᴛ Fᴀɪʟᴇᴅ Sᴀғᴇʟʏ.\nPʟᴇᴀsᴇ Tʀʏ Aɢᴀɪɴ.",
            quote=True,
        )


@bot.on_message(filters.command("setapi"))
async def set_api_command(_: Client, message: Message) -> None:
    try:
        status_print("COMMAND RECEIVED: /setapi")
        if message.from_user is None or int(message.from_user.id) != int(CONFIG["admin_id"]):
            return
        if not await sync_group(message):
            return
        arguments = command_arguments(message)
        if len(arguments) != 1 or arguments[0] not in {"1", "2", "3", "all"}:
            await message.reply_text(
                "▸ 𝗨sᴀɢᴇ: /setapi 1, /setapi 2, /setapi 3, ᴏʀ /setapi all",
                quote=True,
            )
            return
        mode = arguments[0]
        api_config = await get_api_configuration()
        selected = configured_like_apis(api_config, mode)
        if not selected:
            await message.reply_text(
                f"⚠️ Nᴏ Lɪᴋᴇ 𝗔ᴘɪ Is Cᴏɴғɪɢᴜʀᴇᴅ Fᴏʀ Mᴏᴅᴇ {mode}.",
                quote=True,
            )
            return
        await asyncio.to_thread(
            apis.update_one,
            {"_id": "routing"},
            {"$set": {"active_like_api": mode, "updated_at": utc_now()}},
            upsert=True,
        )
        await message.reply_text(
            f"✅ 𝗔ᴄᴛɪᴠᴇ Lɪᴋᴇ 𝗔ᴘɪ Mᴏᴅᴇ: {mode}",
            quote=True,
        )
    except Exception:
        logger.exception("setapi command failed")
        await message.reply_text(
            "⚠️ Cᴏᴜʟᴅ Nᴏᴛ Uᴘᴅᴀᴛᴇ Tʜᴇ Lɪᴋᴇ 𝗔ᴘɪ Mᴏᴅᴇ.",
            quote=True,
        )


@bot.on_message(filters.command("addlikeapi"))
async def add_like_api_command(_: Client, message: Message) -> None:
    try:
        status_print("COMMAND RECEIVED: /addlikeapi")
        if message.from_user is None or int(message.from_user.id) != int(CONFIG["admin_id"]):
            return
        if not await sync_group(message):
            return

        arguments = command_arguments(message)
        if len(arguments) != 1 or not valid_like_api_url(arguments[0]):
            await message.reply_text(
                "▸ 𝗨sᴀɢᴇ: /addlikeapi https://example.com/like\n"
                "✅ Oɴʟʏ Vᴀʟɪᴅ HTTP/HTTPS API URLs Aʀᴇ Aᴄᴄᴇᴘᴛᴇᴅ.",
                quote=True,
            )
            return

        api_url = arguments[0]
        api_config = await get_api_configuration()
        existing_apis = api_config.get("like_apis", [])
        if not isinstance(existing_apis, list):
            existing_apis = []
        existing_urls = {
            str(existing_url)
            for existing_url in existing_apis
            if isinstance(existing_url, str)
        }
        legacy_urls = {
            str(api_config.get(name))
            for name in ("like_api", "like_api_1", "like_api_2", "like_api_3")
            if isinstance(api_config.get(name), str)
        }
        if api_url in existing_urls or api_url in legacy_urls:
            await message.reply_text(
                "⚠️ Tʜɪs Lɪᴋᴇ 𝗔ᴘɪ Is Aʟʀᴇᴀᴅʏ Aᴅᴅᴇᴅ.",
                quote=True,
            )
            return

        await asyncio.to_thread(
            apis.update_one,
            {"_id": "routing"},
            {
                "$addToSet": {"like_apis": api_url},
                "$set": {"updated_at": utc_now()},
            },
            upsert=True,
        )
        api_name = f"custom_api_{len(existing_apis) + 1}"
        total_apis = len(existing_urls | legacy_urls) + 1
        await message.reply_text(
            "✅ 𝗟ɪᴋᴇ 𝗔ᴘɪ Aᴅᴅᴇᴅ Sᴜᴄᴄᴇꜱꜰᴜʟʟʏ\n"
            f"🆔 𝗔ᴘɪ Nᴀᴍᴇ: {api_name}\n"
            f"📊 Tᴏᴛᴀʟ Cᴏɴғɪɢᴜʀᴇᴅ 𝗔ᴘɪs: {total_apis}\n"
            "💡 Uꜱᴇ /setapi all Tᴏ Aᴄᴛɪᴠᴀᴛᴇ Aʟʟ 𝗔ᴘɪs.",
            quote=True,
        )
    except Exception:
        logger.exception("addlikeapi command failed")
        await message.reply_text(
            "⚠️ Cᴏᴜʟᴅ Nᴏᴛ Aᴅᴅ Tʜᴇ Lɪᴋᴇ 𝗔ᴘɪ.\n"
            "Pʟᴇᴀꜱᴇ Vᴇʀɪꜰʏ Tʜᴇ URL Aɴᴅ Tʀʏ Aɢᴀɪɴ.",
            quote=True,
        )


@bot.on_message(filters.command(["likeapis", "listlikeapis"]))
async def list_like_apis_command(_: Client, message: Message) -> None:
    try:
        status_print("COMMAND RECEIVED: /likeapis")
        if message.from_user is None or int(message.from_user.id) != int(CONFIG["admin_id"]):
            return
        if not await sync_group(message):
            return
        api_config = await get_api_configuration()
        output = like_api_list_text(api_config)
        for start in range(0, len(output), 3900):
            await message.reply_text(output[start : start + 3900], quote=True)
    except Exception:
        logger.exception("likeapis command failed")
        await message.reply_text(
            "⚠️ Cᴏᴜʟᴅ Nᴏᴛ Lᴏᴀᴅ Tʜᴇ Lɪᴋᴇ 𝗔ᴘɪ Lɪsᴛ.",
            quote=True,
        )


@bot.on_message(filters.command(["removealllikeapis", "removeallapi"]))
async def remove_all_like_apis_command(_: Client, message: Message) -> None:
    try:
        status_print("COMMAND RECEIVED: /removealllikeapis")
        if message.from_user is None or int(message.from_user.id) != int(CONFIG["admin_id"]):
            return
        if not await sync_group(message):
            return
        await message.reply_text(
            "⚠️ 𝗖ᴏɴғɪʀᴍ Aᴄᴛɪᴏɴ\n\n"
            "Yᴏᴜ Aʀᴇ Aʙᴏᴜᴛ Tᴏ Rᴇᴍᴏᴠᴇ Aʟʟ Lɪᴋᴇ 𝗔ᴘɪs.\n"
            "Tʜɪs Wɪʟʟ Cʟᴇᴀʀ Lᴇɢᴀᴄʏ 𝗔ᴘɪs Aɴᴅ Cᴜsᴛᴏᴍ 𝗔ᴘɪs.\n\n"
            "Tʜɪs Aᴄᴛɪᴏɴ Cᴀɴɴᴏᴛ Bᴇ Uɴᴅᴏɴᴇ.",
            reply_markup=remove_all_like_apis_keyboard(),
            quote=True,
        )
    except Exception:
        logger.exception("removealllikeapis command failed")
        await message.reply_text(
            "⚠️ Cᴏᴜʟᴅ Nᴏᴛ Sᴛᴀʀᴛ Tʜᴇ Rᴇᴍᴏᴠᴀʟ Cᴏɴғɪʀᴍᴀᴛɪᴏɴ.",
            quote=True,
        )


@bot.on_message(filters.command(["removelikeapi", "removeapi"]))
async def remove_like_api_command(_: Client, message: Message) -> None:
    try:
        status_print("COMMAND RECEIVED: /removelikeapi")
        if message.from_user is None or int(message.from_user.id) != int(CONFIG["admin_id"]):
            return
        if not await sync_group(message):
            return
        arguments = command_arguments(message)
        if len(arguments) != 1 or arguments[0] not in {"1", "2", "3"}:
            await message.reply_text(
                "▸ 𝗨sᴀɢᴇ: /removelikeapi 1, /removelikeapi 2, ᴏʀ /removelikeapi 3",
                quote=True,
            )
            return
        api_number = arguments[0]
        await message.reply_text(
            "⚠️ 𝗖ᴏɴғɪʀᴍ Aᴄᴛɪᴏɴ\n\n"
            f"Rᴇᴍᴏᴠᴇ Lɪᴋᴇ 𝗔ᴘɪ {api_number} Fʀᴏᴍ Tʜᴇ Rᴏᴜᴛɪɴɢ Cᴏɴғɪɢᴜʀᴀᴛɪᴏɴ?\n\n"
            "Tʜɪs Aᴄᴛɪᴏɴ Cᴀɴɴᴏᴛ Bᴇ Uɴᴅᴏɴᴇ.",
            reply_markup=remove_like_api_keyboard(api_number),
            quote=True,
        )
    except Exception:
        logger.exception("removelikeapi command failed")
        await message.reply_text(
            "⚠️ Cᴏᴜʟᴅ Nᴏᴛ Sᴛᴀʀᴛ Tʜᴇ Rᴇᴍᴏᴠᴀʟ Cᴏɴғɪʀᴍᴀᴛɪᴏɴ.",
            quote=True,
        )


@bot.on_callback_query(
    filters.regex(r"^(confirm_remove_all_like_apis|confirm_remove_like_api_[123]|cancel_remove_like_apis)$")
)
async def like_api_confirmation_callback(
    _: Client,
    callback_query: CallbackQuery,
) -> None:
    try:
        if callback_query.from_user is None or int(callback_query.from_user.id) != int(CONFIG["admin_id"]):
            await callback_query.answer("⛔ Oɴʟʏ Tʜᴇ Aᴅᴍɪɴ Cᴀɴ Cᴏɴғɪʀᴍ Tʜɪs.", show_alert=True)
            return

        data = callback_query.data or ""
        if data == "cancel_remove_like_apis":
            await callback_query.answer("Rᴇᴍᴏᴠᴀʟ Cᴀɴᴄᴇʟʟᴇᴅ.")
            if callback_query.message is not None:
                await callback_query.message.edit_text(
                    "❌ Rᴇᴍᴏᴠᴀʟ Cᴀɴᴄᴇʟʟᴇᴅ.\nNᴏ Lɪᴋᴇ 𝗔ᴘɪs Wᴇʀᴇ Rᴇᴍᴏᴠᴇᴅ."
                )
            return

        if data == "confirm_remove_all_like_apis":
            await asyncio.to_thread(
                apis.update_one,
                {"_id": "routing"},
                {
                    "$set": {
                        "like_api": "",
                        "like_api_1": "",
                        "like_api_2": "",
                        "like_api_3": "",
                        "like_apis": [],
                        "active_like_api": "all",
                        "updated_at": utc_now(),
                    }
                },
                upsert=True,
            )
            await callback_query.answer("Aʟʟ Lɪᴋᴇ 𝗔ᴘɪs Rᴇᴍᴏᴠᴇᴅ.")
            result_text = (
                "✅ Aʟʟ Lɪᴋᴇ 𝗔ᴘɪs Rᴇᴍᴏᴠᴇᴅ Sᴜᴄᴄᴇꜱꜰᴜʟʟʏ\n"
                "📊 Tᴏᴛᴀʟ Cᴏɴғɪɢᴜʀᴇᴅ 𝗔ᴘɪs: 0\n"
                "⚙️ 𝗔ᴄᴛɪᴠᴇ Mᴏᴅᴇ: all"
            )
        else:
            api_number = data.rsplit("_", 1)[-1]
            api_field = f"like_api_{api_number}"
            api_config = await get_api_configuration()
            api_value = api_config.get(api_field)
            await asyncio.to_thread(
                apis.update_one,
                {"_id": "routing"},
                {
                    "$set": {
                        api_field: "",
                        "updated_at": utc_now(),
                        **(
                            {"active_like_api": "all"}
                            if str(api_config.get("active_like_api")) == api_number
                            else {}
                        ),
                    }
                },
                upsert=True,
            )
            await callback_query.answer(f"Lɪᴋᴇ 𝗔ᴘɪ {api_number} Rᴇᴍᴏᴠᴇᴅ.")
            result_text = (
                f"✅ Lɪᴋᴇ 𝗔ᴘɪ {api_number} Rᴇᴍᴏᴠᴇᴅ Sᴜᴄᴄᴇꜱꜰᴜʟʟʏ\n"
                f"🔗 Sᴛᴀᴛᴜs: {'Cʟᴇᴀʀᴇᴅ' if api_value else 'Aʟʀᴇᴀᴅʏ Nᴏᴛ Cᴏɴғɪɢᴜʀᴇᴅ'}"
            )

        if callback_query.message is not None:
            await callback_query.message.edit_text(result_text)
    except Exception:
        logger.exception("Like API confirmation callback failed")
        await callback_query.answer("⚠️ Rᴇǫᴜᴇsᴛ Fᴀɪʟᴇᴅ.", show_alert=True)


@bot.on_message(filters.command("addvip"))
async def add_vip_command(_: Client, message: Message) -> None:
    try:
        status_print("COMMAND RECEIVED: /addvip")
        if message.from_user is None or int(message.from_user.id) != int(CONFIG["admin_id"]):
            return
        if not await sync_group(message):
            return
        arguments = command_arguments(message)
        if not valid_vip_arguments(arguments):
            await message.reply_text(
                "▸ 𝗨sᴀɢᴇ: /addvip ʀᴇɢɪᴏɴ ᴜɪᴅ ᴛᴏᴛᴀʟ_ᴅᴀʏs",
                quote=True,
            )
            return
        region, uid, total_days_text = arguments
        total_days = int(total_days_text)
        added_at = utc_now()
        expires_at = added_at + timedelta(days=total_days)
        delete_after = expires_at + timedelta(hours=24)
        await asyncio.to_thread(
            autolike.update_one,
            {"uid": uid, "region": region.lower()},
            {
                "$set": {
                    "uid": uid,
                    "region": region.lower(),
                    "total_days": total_days,
                    "total_remaining_days": total_days,
                    "added_at": added_at,
                    "expires_at": expires_at,
                    "delete_after": delete_after,
                }
            },
            upsert=True,
        )
        current_remaining = remaining_vip_days(expires_at)
        await message.reply_text(
            f"✅ 𝗩ɪᴘ AᴜᴛᴏLɪᴋᴇ 𝗔ᴅᴅᴇᴅ\n"
            f"🆔 Uɪᴅ: {uid}\n"
            f"🌍 Rᴇɢɪᴏɴ: {region.upper()}\n"
            f"⏳ Dᴜʀᴀᴛɪᴏɴ: {total_days} Dᴀʏs\n"
            f"📉 Rᴇᴍᴀɪɴɪɴɢ: {current_remaining} Dᴀʏs",
            quote=True,
        )
    except Exception:
        logger.exception("addvip command failed")
        await message.reply_text(
            "⚠️ Cᴏᴜʟᴅ Nᴏᴛ Cʀᴇᴀᴛᴇ Tʜᴇ 𝗩ɪᴘ AᴜᴛᴏLɪᴋᴇ Rᴇᴄᴏʀᴅ.",
            quote=True,
        )


def main() -> None:
    status_print("BOT INITIALIZATION STARTED")
    initialize_database()
    start_health_server()
    start_daily_reset_worker()
    status_print("BOT SUCCESSFULLY STARTED")
    try:
        run_persistent_bot()
    finally:
        status_print("BOT STOPPED")


def run_persistent_bot() -> None:
    """Keep the long-polling Pyrogram client alive after transient failures."""
    reconnect_delay = 5
    while True:
        try:
            status_print("PYROGRAM CONNECTION STARTING")
            bot.run()
            status_print("PYROGRAM CONNECTION ENDED")
            return
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as error:
            # Log only the exception type so Telegram credentials can never
            # accidentally appear in a traceback or error string.
            logger.warning(
                "Pyrogram connection ended with %s; reconnecting in %s seconds",
                type(error).__name__,
                reconnect_delay,
            )
            status_print("TELEGRAM CONNECTION LOST; RETRYING")
            try:
                bot.stop()
            except Exception as stop_error:
                logger.warning(
                    "Pyrogram cleanup returned %s",
                    type(stop_error).__name__,
                )
            time.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 60)


if __name__ == "__main__":
    main()
