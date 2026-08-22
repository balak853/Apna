from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import threading
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
from pyrogram.enums import ChatMemberStatus, ChatMembersFilter, ChatType
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
IST = timezone(timedelta(hours=5, minutes=30), name="Asia/Kolkata")
LIKE_TIMEOUT = httpx.Timeout(connect=8.0, read=20.0, write=8.0, pool=8.0)
UID_PATTERN = re.compile(r"^[0-9]{1,20}$")
REGION_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")

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
    if str(config["bot_token"]).strip() == "YOUR_BOT_TOKEN":
        status_print("BOT TOKEN MISSING")
        raise ValueError("Replace YOUR_BOT_TOKEN in config.json before starting")
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
            await message.reply_text("PLEASE MAKE ME ADMIN FOR USE THIS BOT!")
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
            "I could not verify the group permissions right now. Please try again."
        )
        return False


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
    uid = find_value(data, {"uid", "playeruid", "playerid", "userid"})
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
    return [
        (name, str(config[name]))
        for name in names
        if config.get(name)
        and isinstance(config.get(name), str)
        and str(config[name]).startswith(("http://", "https://"))
    ]


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
    return f"{used}/{limit}"


def remaining_vip_days(
    expires_at: datetime,
    now: datetime | None = None,
) -> int:
    current = now or utc_now()
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return max(0, math.ceil((expires_at - current).total_seconds() / 86400))


def success_message(result: dict[str, Any], document: dict[str, Any], uid: str, region: str) -> str:
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
        f"📉Yᴏᴜʀ Lɪᴍɪᴛ: {used_limit_text(document)}"
    )


def already_liked_message(result: dict[str, Any], uid: str) -> str:
    return (
        "⚠️Fᴀɪʟᴇᴅ Tᴏ Sᴇɴᴅ Lɪᴋᴇꜱ\n\n"
        f"👤Pʟᴀʏᴇʀ Nɪᴄᴋɴᴀᴍᴇ: {result.get('nickname') or 'N/A'}\n"
        f"🆔Pʟᴀʏᴇʀ UID: {result.get('uid') or uid}\n"
        "🙅🏻Mᴇꜱꜱᴀɢᴇ: Lɪᴋᴇꜱ_ᴀʟʀᴇᴀᴅʏ_ꜱᴇɴᴅ"
    )


def failure_message(result: dict[str, Any], uid: str) -> str:
    return (
        "⚠️Fᴀɪʟᴇᴅ Tᴏ Sᴇɴᴅ Lɪᴋᴇꜱ\n\n"
        f"🆔Pʟᴀʏᴇʀ UID: {result.get('uid') or uid}\n"
        f"🙅🏻Mᴇꜱꜱᴀɢᴇ: {result.get('message') or 'Like request failed.'}"
    )


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
            if used >= limit:
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
                success_message(combined, refreshed or slot, uid, region),
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


health_app = Flask("ff-bot2-health")


@health_app.get("/")
def health_check() -> str:
    return "OK"


def start_health_server() -> None:
    threading.Thread(
        target=lambda: health_app.run(
            host="0.0.0.0",
            port=8000,
            debug=False,
            use_reloader=False,
        ),
        name="koyeb-health",
        daemon=True,
    ).start()
    status_print("FLASK HEALTH CHECK STARTED ON PORT 8000")


bot = Client(
    "ff-bot2",
    api_id=int(CONFIG["api_id"]),
    api_hash=str(CONFIG["api_hash"]),
    bot_token=str(CONFIG["bot_token"]),
)


@bot.on_message(filters.incoming & ~filters.service, group=-1)
async def register_every_interaction(_: Client, message: Message) -> None:
    try:
        if message.from_user is not None:
            await register_user(message.from_user)
    except Exception:
        logger.exception("Interaction registration handler failed")


@bot.on_message(filters.command("start"))
async def start_command(_: Client, message: Message) -> None:
    try:
        status_print("COMMAND RECEIVED: /start")
        if message.from_user is not None:
            await register_user(message.from_user)
        bot_user = await bot.get_me()
        username = getattr(bot_user, "username", None)
        if not username:
            await message.reply_text("The bot username is not available right now.")
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
            "✨ Welcome to the Free Fire Like Bot ✨\n\n"
            "Send a player UID with /like region uid to request likes.",
            reply_markup=keyboard,
        )
    except Exception:
        logger.exception("Start command failed")
        await message.reply_text("Something went wrong. Please try again.")


@bot.on_message(filters.command("like"))
async def like_command(_: Client, message: Message) -> None:
    processing: Message | None = None
    try:
        status_print("COMMAND RECEIVED: /like")
        if message.from_user is None:
            return
        document = await register_user(message.from_user)
        if document is None:
            await message.reply_text("User registration is temporarily unavailable.")
            return
        if not await sync_group(message):
            return
        arguments = command_arguments(message)
        if not valid_like_arguments(arguments):
            await message.reply_text("Usage: /like region uid\nExample: /like ind 1589573783")
            return
        await asyncio.to_thread(reset_user_if_due_sync, int(message.from_user.id))
        document = await asyncio.to_thread(
            users.find_one,
            {"user_id": int(message.from_user.id)},
        ) or document
        used = int(document.get("daily_successful_requests", 0) or 0)
        pending = int(document.get("pending_like_requests", 0) or 0)
        limit = int(document.get("like_limit", 1) or 1)
        if used + pending >= limit:
            await message.reply_text(daily_limit_message(document))
            return
        processing = await message.reply_text(
            "⏳ Pʀᴏᴄᴇꜱꜱɪɴɢ Yᴏᴜʀ Rᴇǫᴜᴇꜱᴛ..."
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
        await message.reply_text(result_text, reply_markup=reply_markup)
    except Exception:
        logger.exception("Like command failed")
        if processing is not None:
            try:
                await processing.delete()
            except Exception:
                pass
        await message.reply_text("The Like request failed safely. Please try again.")


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
            await message.reply_text("Usage: /setapi 1, /setapi 2, /setapi 3, or /setapi all")
            return
        mode = arguments[0]
        api_config = await get_api_configuration()
        selected = configured_like_apis(api_config, mode)
        if not selected:
            await message.reply_text(f"No Like API is configured for mode {mode}.")
            return
        await asyncio.to_thread(
            apis.update_one,
            {"_id": "routing"},
            {"$set": {"active_like_api": mode, "updated_at": utc_now()}},
            upsert=True,
        )
        await message.reply_text(f"Active Like API mode set to: {mode}")
    except Exception:
        logger.exception("setapi command failed")
        await message.reply_text("Could not update the active Like API mode.")


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
            await message.reply_text("Usage: /addvip region uid Total_Days")
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
            f"VIP AutoLike added for {uid} ({region.upper()}) for {total_days} days.\n"
            f"Remaining days: {current_remaining}"
        )
    except Exception:
        logger.exception("addvip command failed")
        await message.reply_text("Could not create the VIP AutoLike record.")


def main() -> None:
    status_print("BOT INITIALIZATION STARTED")
    initialize_database()
    start_health_server()
    start_daily_reset_worker()
    status_print("BOT SUCCESSFULLY STARTED")
    logger.info("Starting persistent Pyrogram client")
    try:
        bot.run()
    finally:
        status_print("BOT STOPPED")


if __name__ == "__main__":
    main()