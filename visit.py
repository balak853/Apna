from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

import httpx
from pymongo.collection import Collection
from pymongo.errors import PyMongoError
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message


VISIT_LIMIT = 3
VISIT_API_URL = "https://visit.bhuwanhex.bond/visit"
VISIT_TIMEOUT = httpx.Timeout(connect=8.0, read=25.0, write=8.0, pool=8.0)


async def _delete_processing(processing: Message | None) -> None:
    if processing is None:
        return
    try:
        await processing.delete()
    except Exception:
        pass


def _usage_day(ist_now: Callable[[], datetime]) -> str:
    now = ist_now()
    reset_boundary = now.replace(hour=4, minute=0, second=0, microsecond=0)
    if now < reset_boundary:
        now -= timedelta(days=1)
    return now.date().isoformat()


def _reserve(collection: Collection, kind: str, key: str, day: str) -> bool:
    result = collection.update_one(
        {"kind": kind, "key": key, "day": day, "$expr": {"$lt": [{"$add": ["$successful", "$pending"]}, VISIT_LIMIT]}},
        {
            "$setOnInsert": {"kind": kind, "key": key, "day": day, "successful": 0, "pending": 0},
            "$inc": {"pending": 1},
        },
        upsert=True,
    )
    return result.modified_count == 1 or result.upserted_id is not None


def _release(collection: Collection, kind: str, key: str, day: str) -> None:
    collection.update_one(
        {"kind": kind, "key": key, "day": day, "pending": {"$gt": 0}},
        {"$inc": {"pending": -1}},
    )


def _complete(collection: Collection, kind: str, key: str, day: str) -> None:
    collection.update_one(
        {"kind": kind, "key": key, "day": day, "pending": {"$gt": 0}},
        {"$inc": {"pending": -1, "successful": 1}},
    )


def _ensure_indexes(collection: Collection) -> None:
    collection.create_index(
        [("kind", 1), ("key", 1), ("day", 1)],
        unique=True,
        name="visit_kind_key_day_unique",
    )
    collection.update_many(
        {"successful": {"$exists": False}},
        {"$set": {"successful": 0}},
    )
    collection.update_many(
        {"pending": {"$exists": False}},
        {"$set": {"pending": 0}},
    )


def _format_success(payload: dict[str, Any], region: str, used: int) -> str:
    return (
        "✦━━━ **Vɪꜱɪᴛs Sᴇɴᴛ Sᴜᴄᴄᴇssғᴜʟʟʏ 🥳** ━━━✦\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"👤 **Pʟᴀʏᴇʀ:** {payload['PlayerNickname']}\n"
        f"🆔 **Uɪᴅ:** {payload['UID']}\n"
        f"🌍 **Rᴇɢɪᴏɴ:** {region}\n"
        "📌 **Sᴛᴀᴛᴜs:** Cᴏᴍᴘʟᴇᴛᴇᴅ\n\n"
        f"👁️ **Sᴜᴄᴄᴇssғᴜʟ Vɪsɪᴛs:** {payload['SuccessfulVisits']}\n"
        f"❌ **Fᴀɪʟᴇᴅ Vɪsɪᴛs:** {payload['FailedVisits']}\n"
        f"📊 **Tᴏᴛᴀʟ Vɪsɪᴛs:** {payload['TotalVisits']}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📉 **Yᴏᴜʀ Lɪᴍɪᴛ:** {used}/{VISIT_LIMIT}\n"
        f"🔋 **Rᴇᴍᴀɪɴɪɴɢ:** {max(VISIT_LIMIT - used, 0)}/{VISIT_LIMIT}"
    )


def developer_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Dᴇᴠᴇʟᴏᴘᴇʀ", url="https://t.me/BALAK_TRUSTED")]]
    )


def _limit_message() -> str:
    return (
        "╭━━━ 🚫 **Lɪᴍɪᴛ Rᴇᴀᴄʜᴇᴅ** ━━━╮\n│\n"
        "│ ⚡ **Yᴏᴜʀ Dᴀɪʟʏ Vɪsɪᴛ Lɪᴍɪᴛ**\n│\n"
        "│ 📊 **Uꜱᴇᴅ:** 3/3\n│ 🎯 **Lɪᴍɪᴛ:** 3 Vɪsɪᴛꜱ\n│\n"
        "│ ⏳ **Cᴏᴍᴇ Bᴀᴄᴋ Tᴏᴍᴏʀʀᴏᴡ**\n│\n"
        "╰━━━ 🔒 Lɪᴍɪᴛ Eɴᴅ ━━━╯"
    )


def _uid_limit_message(uid: str) -> str:
    return (
        "╭━━━ 🚫 **Uɪᴅ Vɪsɪᴛ Lɪᴍɪᴛ** ━━━╮\n│\n"
        f"│ 🆔 **Uɪᴅ:** {uid}\n│\n"
        "│ ⚠️ **Tʜɪs Uɪᴅ Hᴀs Rᴇᴀᴄʜᴇᴅ**\n"
        "│    **Tʜᴇ Mᴀx Vɪsɪᴛ Lɪᴍɪᴛ Fᴏʀ Tᴏᴅᴀʏ.**\n│\n"
        "│ 🔒 **Mᴀx:** 3/3\n│ ⏳ **Tʀʏ Aɢᴀɪɴ Tᴏᴍᴏʀʀᴏᴡ.**\n│\n"
        "╰━━━ ⚡ Vɪsɪᴛ Lɪᴍɪᴛ ━━━╯"
    )


def register_visit_handler(
    bot: Client,
    database: Any,
    require_bot_group_admin: Callable[[Message], Awaitable[bool]],
    command_access_allowed: Callable[[Message], Awaitable[bool]],
    is_command_disabled: Callable[[Message, str], Awaitable[bool]],
    ist_now: Callable[[], datetime],
    logger: logging.Logger,
) -> None:
    collection: Collection = database["visits"]
    try:
        _ensure_indexes(collection)
    except PyMongoError:
        logger.exception("Visit database initialization failed")

    @bot.on_message(filters.command("visit", case_sensitive=False))
    async def visit_command(_: Client, message: Message) -> None:
        processing: Message | None = None
        user = getattr(message, "from_user", None)
        user_id = getattr(user, "id", None)
        try:
            if not await require_bot_group_admin(message):
                return
            if not await command_access_allowed(message):
                return
            if await is_command_disabled(message, "visit"):
                return

            processing = await message.reply_text(
                "**⏳ Pʀᴏᴄᴇꜱꜱɪɴɢ Yᴏᴜʀ Rᴇǫᴜᴇsᴛ...**",
                parse_mode=ParseMode.MARKDOWN,
                quote=True,
            )
            parts = (message.text or message.caption or "").strip().split()
            if len(parts) != 3 or not parts[1] or not parts[2].isdigit() or int(parts[2]) <= 0:
                await _delete_processing(processing)
                processing = None
                await message.reply_text(
                    "**❌ Iɴᴠᴀʟɪᴅ Uѕᴀɢᴇ**\n\n**Uѕᴇ:** \`/visit Rᴇɢɪᴏɴ Uɪᴅ\`\n\n**Eхᴀᴍᴘʟᴇ:** \`/visit ind 1589573783\`",
                    parse_mode=ParseMode.MARKDOWN,
                    quote=True,
                )
                return

            if user_id is None:
                await _delete_processing(processing)
                processing = None
                await message.reply_text("**❌ Uѕᴇʀ Iᴅᴇɴᴛɪғɪᴄᴀᴛɪᴏɴ Fᴀɪʟᴇᴅ**\n\nPʟᴇᴀsᴇ Tʀʏ Aɢᴀɪɴ.", parse_mode=ParseMode.MARKDOWN, quote=True)
                return

            region = parts[1]
            uid = parts[2]
            day = _usage_day(ist_now)
            user_key = str(int(user_id))
            user_reserved = await asyncio.to_thread(_reserve, collection, "user", user_key, day)
            if not user_reserved:
                await _delete_processing(processing)
                processing = None
                await message.reply_text(_limit_message(), parse_mode=ParseMode.MARKDOWN, quote=True)
                return

            uid_reserved = await asyncio.to_thread(_reserve, collection, "uid", uid, day)
            if not uid_reserved:
                await asyncio.to_thread(_release, collection, "user", user_key, day)
                await _delete_processing(processing)
                processing = None
                await message.reply_text(_uid_limit_message(uid), parse_mode=ParseMode.MARKDOWN, quote=True)
                return

            try:
                async with httpx.AsyncClient(timeout=VISIT_TIMEOUT) as client:
                    response = await client.get(VISIT_API_URL, params={"uid": uid, "region": region})
                if response.status_code < 200 or response.status_code >= 300:
                    raise ValueError("visit API returned a non-success response")
                payload = response.json()
                required = {"PlayerNickname", "UID", "SuccessfulVisits", "FailedVisits", "TotalVisits"}
                if not isinstance(payload, dict) or not required.issubset(payload):
                    raise ValueError("visit API returned an invalid response")
            except Exception:
                await asyncio.gather(
                    asyncio.to_thread(_release, collection, "user", user_key, day),
                    asyncio.to_thread(_release, collection, "uid", uid, day),
                )
                await _delete_processing(processing)
                processing = None
                await message.reply_text(
                    "╭━━━ ⚠️ **Vɪsɪᴛ Fᴀɪʟᴇᴅ** ━━━╮\n│\n│ **Cᴏᴜʟᴅ Nᴏᴛ Cᴏᴍᴘʟᴇᴛᴇ Yᴏᴜʀ Vɪsɪᴛ Rᴇǫᴜᴇsᴛ.**\n│\n│ ⏳ **Pʟᴇᴀsᴇ Tʀʏ Aɢᴀɪɴ Lᴀᴛᴇʀ.**\n│\n╰━━━ ⚡ **Tʀʏ Aɢᴀɪɴ** ━━━╯",
                    parse_mode=ParseMode.MARKDOWN,
                    quote=True,
                )
                return

            await asyncio.gather(
                asyncio.to_thread(_complete, collection, "user", user_key, day),
                asyncio.to_thread(_complete, collection, "uid", uid, day),
            )
            used = await asyncio.to_thread(
                lambda: collection.find_one({"kind": "user", "key": user_key, "day": day}, {"successful": 1}).get("successful", 0)
            )
            await _delete_processing(processing)
            processing = None
            await message.reply_text(
                _format_success(payload, region, int(used)),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=developer_keyboard(),
                quote=True,
            )
        except PyMongoError:
            logger.exception("Visit database operation failed")
            await _delete_processing(processing)
            await message.reply_text("⚠️ **Vɪsɪᴛ Rᴇǫᴜᴇsᴛ Cᴏᴜʟᴅ Nᴏᴛ Bᴇ Cᴏᴍᴘʟᴇᴛᴇᴅ.**", parse_mode=ParseMode.MARKDOWN, quote=True)
        except Exception:
            logger.exception("Visit command failed")
            await _delete_processing(processing)
            await message.reply_text("⚠️ **Vɪsɪᴛ Rᴇǫᴜᴇsᴛ Cᴏᴜʟᴅ Nᴏᴛ Bᴇ Cᴏᴍᴘʟᴇᴛᴇᴅ.**", quote=True)
