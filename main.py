"""Python/MongoDB conversion of the ff-bot PHP application.

The original ff-bot directory is deliberately never written by this program.
On the first MongoDB connection, its JSON state is imported into collections.
"""

from __future__ import annotations

import copy
import html
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

import requests
from flask import Flask, jsonify, request
from pymongo import ASCENDING, MongoClient, ReturnDocument

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT.parent / "ff-bot"
IST = timezone(timedelta(hours=5, minutes=30))
CONFIG_PATH = ROOT / "config.json"
MONGO_URI = os.environ.get("MONGO_URI", "").strip()
DB_NAME = os.environ.get("MONGO_DATABASE", "ff_bot")
HTTP_TIMEOUT = 20
LIKE_LIMIT = 1
VISIT_LIMIT = 10
RESET_HOUR = 4
AUTO_LIKE_CHAT_ID = -1004360282377
AUTO_LIKE_IMAGE = "https://iili.io/CsuErH7.jpg"
API_URLS = {
    1: "https://like-api-src-gamma.vercel.app/like",
    2: "https://220-likes-vaibhav.vercel.app/like",
    3: "http://187.127.175.208:5002/like",
}
SECONDARY_INFO_API = "https://star-info-api.lovable.app/functions/v1/info-api/accinfo?uid="
BANNER_IMAGE_API = "https://image.killersharmabot.online/banner-image"
BANNER_IMAGE_API_2 = "https://vertex-x-banner.vercel.app/profile?uid="
OUTFIT_IMAGE_API = "https://vertex-x-outfit.vercel.app/outfit-image"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ff-bot")
app = Flask(__name__)
mongo: MongoClient | None = None
db: Any = None
state: MongoState | None = None
db_connected = False
startup_error = ""


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def now_ist() -> datetime:
    return datetime.now(IST)


def window_key() -> str:
    current = now_ist()
    if current.hour < RESET_HOUR:
        current -= timedelta(days=1)
    return current.strftime("%Y-%m-%d")


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def int_value(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def first(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return default


class MongoState:
    """Collection-backed replacement for the PHP JSON stores."""

    def __init__(self, database: Any) -> None:
        self.db = database
        self.users = database.users
        self.groups = database.groups
        self.check = database.check
        self.paid = database.paid
        self.processed = database.processed_updates
        self.meta = database.migration_meta

    def migrate_once(self) -> None:
        if self.meta.find_one({"_id": "json_migration_v1"}):
            return
        # Import each source file exactly once. Existing Mongo data wins.
        users = read_json(SOURCE / "users.json", {"users": []})
        groups = read_json(SOURCE / "groups.json", {"groups": []})
        check = read_json(SOURCE / "check.json", {"chat_ids": [], "force_join_enabled": False})
        paid = read_json(SOURCE / "paid.json", {"version": 1, "records": []})
        processed = read_json(SOURCE / ".processed_update_ids.json", {"updates": {}})
        if self.users.count_documents({}) == 0:
            docs = [copy.deepcopy(x) for x in users.get("users", []) if isinstance(x, dict)]
            for doc in docs:
                doc["_id"] = str(doc.get("user_id", uuid.uuid4().hex))
            if docs:
                self.users.insert_many(docs, ordered=False)
        if self.groups.count_documents({}) == 0:
            docs = [copy.deepcopy(x) for x in groups.get("groups", []) if isinstance(x, dict)]
            for doc in docs:
                doc["_id"] = str(doc.get("group_id", uuid.uuid4().hex))
            if docs:
                self.groups.insert_many(docs, ordered=False)
        if self.check.count_documents({}) == 0:
            self.check.insert_one({"_id": "state", **check})
        if self.paid.count_documents({}) == 0:
            for record in paid.get("records", []):
                if isinstance(record, dict):
                    item = copy.deepcopy(record)
                    item["_id"] = str(item.get("id", uuid.uuid4().hex))
                    self.paid.insert_one(item)
        if self.processed.count_documents({}) == 0:
            updates = processed.get("updates", {})
            self.processed.insert_one({"_id": "state", "updates": updates})
            if isinstance(updates, dict):
                for update_id, processed_at in updates.items():
                    self.processed.update_one(
                        {"_id": str(update_id)},
                        {"$set": {"processed_at": int_value(processed_at, int(time.time()))}},
                        upsert=True,
                    )
        self.meta.insert_one({"_id": "json_migration_v1", "completed_at": datetime.now(timezone.utc)})

    def user(self, user_id: int) -> dict[str, Any] | None:
        return self.users.find_one({"user_id": user_id}, {"_id": 0})

    def upsert_user(self, data: dict[str, Any]) -> None:
        uid = int_value(data.get("user_id"))
        if uid:
            self.users.update_one({"user_id": uid}, {"$set": data}, upsert=True)

    def all_users(self) -> list[dict[str, Any]]:
        return list(self.users.find({}, {"_id": 0}))

    def group(self, group_id: int) -> dict[str, Any] | None:
        return self.groups.find_one({"group_id": group_id}, {"_id": 0})

    def check_state(self) -> dict[str, Any]:
        return self.check.find_one({"_id": "state"}, {"_id": 0}) or {"chat_ids": [], "force_join_enabled": False}


def connect_database() -> None:
    global mongo, db, state, db_connected, startup_error
    if not MONGO_URI:
        raise RuntimeError("MONGO_URI environment variable is not set")
    mongo = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
    mongo.admin.command("ping")
    db = mongo[DB_NAME]
    state = MongoState(db)
    state.migrate_once()
    db.users.create_index("user_id", unique=True)
    db.groups.create_index("group_id", unique=True)
    db.paid.create_index("id", unique=True)
    db_connected = True
    startup_error = ""


def tg(token: str, method: str, **params: Any) -> dict[str, Any] | None:
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/{method}",
            data=params,
            timeout=HTTP_TIMEOUT,
        )
        data = response.json()
        return data if isinstance(data, dict) else None
    except (requests.RequestException, ValueError):
        return None


def send(token: str, chat_id: int, text: str, reply_to: int | None = None, **extra: Any) -> int | None:
    params: dict[str, Any] = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", **extra}
    if reply_to:
        params["reply_to_message_id"] = reply_to
    result = tg(token, "sendMessage", **params)
    return int_value(result.get("result", {}).get("message_id")) if result and result.get("ok") else None


def delete(token: str, chat_id: int, message_id: int) -> bool:
    result = tg(token, "deleteMessage", chat_id=chat_id, message_id=message_id)
    return bool(result and result.get("ok"))


def edit(token: str, chat_id: int, message_id: int, text: str, **extra: Any) -> bool:
    result = tg(token, "editMessageText", chat_id=chat_id, message_id=message_id,
                text=text, parse_mode="HTML", **extra)
    return bool(result and result.get("ok"))


def send_photo(token: str, chat_id: int, photo: str, caption: str = "", reply_to: int | None = None) -> bool:
    params: dict[str, Any] = {"chat_id": chat_id, "photo": photo, "parse_mode": "HTML"}
    if caption:
        params["caption"] = caption
    if reply_to:
        params["reply_to_message_id"] = reply_to
    result = tg(token, "sendPhoto", **params)
    return bool(result and result.get("ok"))


def config() -> dict[str, Any] | None:
    value = read_json(CONFIG_PATH)
    if not isinstance(value, dict):
        return None
    if not str(value.get("bot_token", "")).strip() or not str(value.get("info_api", "")).strip():
        return None
    runtime = db.runtime.find_one({"_id": "config"}, {"_id": 0}) if db is not None else {}
    runtime = runtime if isinstance(runtime, dict) else {}
    return {
        "bot_token": str(value["bot_token"]).strip(),
        "owner_user_id": int_value(value.get("owner_user_id")),
        "info_api": str(value["info_api"]).strip(),
        "bot_enabled": runtime.get("bot_enabled", value.get("bot_enabled", True) is not False),
        "api_mode": max(1, min(3, int_value(runtime.get("api_mode", value.get("api_mode")), 1))),
    }


def admin(user_id: int, cfg: dict[str, Any]) -> bool:
    return user_id > 0 and user_id == cfg["owner_user_id"]


def chat_is_group(chat: dict[str, Any]) -> bool:
    return chat.get("type") in ("group", "supergroup")


def force_chats() -> list[dict[str, Any]]:
    value = state.check_state()
    chats = value.get("chat_ids", value.get("chats", value.get("groups", [])))
    return [x for x in chats if isinstance(x, dict)] if isinstance(chats, list) else []


def force_enabled() -> bool:
    return bool(state.check_state().get("force_join_enabled", False))


def membership(token: str, chat_id: int, user_id: int) -> bool:
    result = tg(token, "getChatMember", chat_id=chat_id, user_id=user_id)
    member = result.get("result", {}) if result and result.get("ok") else {}
    status = member.get("status", "")
    return status in ("creator", "administrator", "member") or (status == "restricted" and member.get("is_member") is True)


def missing_required(token: str, user_id: int) -> list[dict[str, Any]]:
    if not force_enabled():
        return []
    return [chat for chat in force_chats() if int_value(chat.get("group_id", chat.get("chat_id"))) and
            not membership(token, int_value(chat.get("group_id", chat.get("chat_id"))), user_id)]


def force_prompt(token: str, chat_id: int, missing: list[dict[str, Any]], reply_to: int | None) -> None:
    keyboard: list[list[dict[str, str]]] = []
    for chat in missing:
        cid = int_value(chat.get("group_id", chat.get("chat_id")))
        title = str(chat.get("title") or chat.get("username") or cid)
        url = str(chat.get("invite_link") or (f"https://t.me/{str(chat['username']).lstrip('@')}" if chat.get("username") else ""))
        keyboard.append([{"text": f"Join {title}", "url": url}] if url else
                        [{"text": f"Join {title}", "callback_data": f"force_join_info:{cid}"}])
    keyboard.append([{"text": "I have joined — check again", "callback_data": "force_join_check"}])
    send(token, chat_id,
         "⚠️ Please join all required channels/groups before using <code>/Get uid</code>.",
         reply_to, reply_markup=json.dumps({"inline_keyboard": keyboard}))


def authorize_group(token: str, chat: dict[str, Any], owner_id: int, reply_to: int) -> bool:
    """Register a group and keep commands locked until owner approval."""
    if not chat_is_group(chat):
        return True
    group_id = int_value(chat.get("id"))
    if not group_id:
        return False
    bot_user = tg(token, "getMe")
    bot_id = int_value((bot_user or {}).get("result", {}).get("id"))
    administrators = tg(token, "getChatAdministrators", chat_id=group_id)
    admin_ids = []
    if administrators and administrators.get("ok"):
        admin_ids = [
            int_value(item.get("user", {}).get("id"))
            for item in administrators.get("result", [])
            if isinstance(item, dict)
        ]
    bot_is_admin = bot_id > 0 and bot_id in admin_ids
    current = state.group(group_id)
    record = {
        "group_id": group_id,
        "title": str(chat.get("title", "")),
        "type": str(chat.get("type", "")),
        "username": str(chat.get("username", "")),
        "approved": bool(current and current.get("approved") is True),
        "bot_is_admin": bot_is_admin,
        "admin_user_ids": admin_ids,
        "disabled_commands": list(current.get("disabled_commands", [])) if current else [],
    }
    state.groups.update_one({"group_id": group_id}, {"$set": record}, upsert=True)
    if not bot_is_admin:
        send(token, group_id,
             "⚠️ Commands are disabled in this group. Please make the bot an administrator.",
             reply_to)
        return False
    if record["approved"] or owner_id <= 0:
        return record["approved"]
    send(token, group_id,
         f"⚠️ Commands are disabled in this group until the bot owner authorizes it with "
         f"<code>/accept {group_id}</code>.", reply_to)
    return False


def ensure_user(message: dict[str, Any]) -> int:
    user = message.get("from") or {}
    uid = int_value(user.get("id"))
    if not uid:
        return 0
    current = state.user(uid) or {"user_id": uid, "like_usage": {}, "visit_usage": {}, "likes": []}
    current.update({k: v for k, v in user.items() if k in ("id", "first_name", "last_name", "username")})
    current["user_id"] = uid
    state.upsert_user(current)
    return uid


def reserve(user_id: int, field: str, limit: int, owner_id: int) -> tuple[bool, int]:
    if user_id == owner_id:
        return True, 0
    user = state.user(user_id)
    if not user:
        return False, 0
    usage = user.get(field) if isinstance(user.get(field), dict) else {}
    used = int_value(usage.get("count")) if usage.get("window") == window_key() else 0
    pending = int_value(usage.get("pending")) if usage.get("window") == window_key() else 0
    if used + pending >= limit:
        return False, used
    usage = {"window": window_key(), "count": used, "pending": pending + 1}
    state.users.update_one({"user_id": user_id}, {"$set": {field: usage}})
    return True, used


def finalize(user_id: int, field: str, success: bool) -> None:
    user = state.user(user_id)
    if not user:
        return
    usage = user.get(field) if isinstance(user.get(field), dict) else {}
    if usage.get("window") != window_key():
        return
    usage["pending"] = max(0, int_value(usage.get("pending")) - 1)
    if success:
        usage["count"] = int_value(usage.get("count")) + 1
    state.users.update_one({"user_id": user_id}, {"$set": {field: usage}})


def api_json(url: str, **kwargs: Any) -> dict[str, Any] | None:
    try:
        r = requests.get(url, timeout=HTTP_TIMEOUT, **kwargs)
        value = r.json()
        return value if isinstance(value, dict) else None
    except (requests.RequestException, ValueError):
        return None


def api_like(region: str, uid: str, mode: int) -> dict[str, Any] | None:
    url = API_URLS.get(mode, API_URLS[1])
    for _ in range(3):
        result = api_json(url, params={"uid": uid, "region": region, "key": "BALAK"})
        if result is not None:
            return result
    return None


def like_command(token: str, message: dict[str, Any], cfg: dict[str, Any], args: str) -> None:
    chat_id = int_value((message.get("chat") or {}).get("id"))
    msg_id = int_value(message.get("message_id"))
    parts = args.split()
    if len(parts) != 2 or not re.fullmatch(r"\d+", parts[1]):
        send(token, chat_id, "❌ <b>Invalid format.</b>\nUse <code>/like region uid</code>.", msg_id)
        return
    user_id = int_value((message.get("from") or {}).get("id"))
    allowed, used = reserve(user_id, "like_usage", LIKE_LIMIT, cfg["owner_user_id"])
    if not allowed:
        send(token, chat_id, f"⚠️ Daily like limit reached. Used: <code>{used}</code>", msg_id)
        return
    loading = send(token, chat_id, "⏳<b>Pʀᴏᴄᴇꜱꜱɪɴɢ Yᴏᴜʀ Rᴇǫᴜᴇsᴛ...</b>", msg_id)
    result = api_like(parts[0].upper(), parts[1], cfg["api_mode"])
    if loading:
        delete(token, chat_id, loading)
    before = int_value(first(result or {}, "LikesbeforeCommand", "likes_before"), -1)
    after = int_value(first(result or {}, "LikesafterCommand", "likes_after"), -1)
    if result is None or before < 0 or after < 0 or after < before:
        finalize(user_id, "like_usage", False)
        send(token, chat_id, "❌<b>Something went wrong!</b>\n\nPlease try again later.", msg_id)
        return
    given = max(0, int_value(first(result, "LikesGivenByAPI", "likes_given"), after - before))
    if given == 0 and after == before:
        finalize(user_id, "like_usage", False)
        send(token, chat_id, f"💬 <b>Like already received</b> for <code>{esc(parts[1])}</code>.", msg_id)
        return
    finalize(user_id, "like_usage", True)
    send(token, chat_id,
         "━━━━━━━━━━━━━━━━━━━━━\n🌟 <b>ʟɪᴋᴇs sᴇɴᴛ sᴜᴄᴄᴇssғᴜʟʟʏ!</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
         f"🆔 <b>UID:</b> <code>{esc(parts[1])}</code>\n🌍 <b>Region:</b> <code>{esc(parts[0].upper())}</code>\n"
         f"👍 <b>Before:</b> <code>{before}</code>\n🎯 <b>Given:</b> <code>{given}</code>\n🚀 <b>After:</b> <code>{after}</code>\n\n"
         "👑 <b>Bot Owner:</b> <b>@BALAK_TRUSTED</b>", msg_id,
         reply_markup=json.dumps({"inline_keyboard": [[{"text": "🧑‍💻 𝐃𝐞𝐯𝐥𝐨𝐩𝐞𝐫", "url": "https://t.me/BALAK_TRUSTED"}]]}))


def visit_command(token: str, message: dict[str, Any], cfg: dict[str, Any], args: str) -> None:
    chat_id, msg_id = int_value((message.get("chat") or {}).get("id")), int_value(message.get("message_id"))
    parts = args.split()
    if len(parts) != 2 or not re.fullmatch(r"\d+", parts[1]):
        send(token, chat_id, "❌ <b>Incorrect format.</b>\nUse <code>/visit region uid</code>.", msg_id)
        return
    uid = int_value((message.get("from") or {}).get("id"))
    allowed, used = reserve(uid, "visit_usage", VISIT_LIMIT, cfg["owner_user_id"])
    if not allowed:
        send(token, chat_id, "⚠️ Daily visit limit reached." if used >= VISIT_LIMIT else "❌ Visit request failed.", msg_id)
        return
    loading = send(token, chat_id, "⏳<b>Pʀᴏᴄᴇꜱꜱɪɴɢ Yᴏᴜʀ Rᴇǫᴜᴇsᴛ...</b>", msg_id)
    result = api_json("http://2.24.160.65:5000/Bmw", params={"region": parts[0].upper(), "uid": parts[1]})
    if loading:
        delete(token, chat_id, loading)
    finalize(uid, "visit_usage", result is not None)
    if result is None:
        send(token, chat_id, "❌<b>Vɪꜱɪᴛ Rᴇǫᴜᴇsᴛ Fᴀɪʟᴇᴅ!</b>\n\nPlease try again later.", msg_id)
        return
    send(token, chat_id, f"📈 <b>Vɪꜱɪᴛ Rᴇsᴜʟᴛ</b>\n├━━━━━━━━━━━━━━━━━━━━\n"
         f"│ ✅ <b>Sᴜᴄᴄᴇss</b> : <code>{esc(result.get('ToTaL', 'N/A'))}</code>\n"
         f"│ ❌ <b>Fᴀɪʟ</b> : <code>{esc(result.get('FaiL', 'N/A'))}</code>\n╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
         "⚡ <i>Thanks for using Visit Command.</i>", msg_id)


def ban_command(token: str, message: dict[str, Any], cfg: dict[str, Any], args: str) -> None:
    chat_id, msg_id = int_value((message.get("chat") or {}).get("id")), int_value(message.get("message_id"))
    uid = args.strip()
    if not re.fullmatch(r"[1-9]\d*", uid):
        send(token, chat_id, "❌ Please use <code>/bancheck UID</code>.", msg_id)
        return
    result = api_json("https://api2.nftoken.info/checkbanned", params={"id": uid})
    if result is None:
        result = api_json("https://ffban-ashu.vercel.app/checkbanned", params={"id": uid, "key": "ashu"})
    if result is None:
        send(token, chat_id, "❌ Ban check request failed. Please try again later.", msg_id)
        return
    raw = str(result.get("status", result.get("is_banned", "UNKNOWN"))).upper()
    state_text = "BANNED" if raw == "BANNED" or result.get("is_banned") is True else "NOT BANNED"
    send(token, chat_id, f"🔎 <b>Ban Check Result</b>\n\n🆔 UID: <code>{esc(uid)}</code>\n"
         f"🚫 Status: <b>{state_text}</b>", msg_id)


def info_command(token: str, message: dict[str, Any], cfg: dict[str, Any], uid: str) -> None:
    chat_id, msg_id = int_value((message.get("chat") or {}).get("id")), int_value(message.get("message_id"))
    if not re.fullmatch(r"\d+", uid):
        send(token, chat_id, "❌ Please use <code>/Get UID</code>.", msg_id)
        return
    endpoint = cfg["info_api"].replace("{uid}", quote(uid)) if "{uid}" in cfg["info_api"] else cfg["info_api"] + quote(uid)
    data = api_json(endpoint) or {}
    secondary = api_json(SECONDARY_INFO_API + quote(uid)) or {}
    combined = dict(data)
    for key, value in secondary.items():
        combined.setdefault(key, value)
    lines = ["✦ COMPLETE PLAYER PROFILE"]
    for key, value in combined.items():
        label = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(key)).replace("_", " ").title()
        lines.append(f"{label}: {json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value}")
    text = "<b>" + esc(lines[0]) + "</b>\n<pre>" + esc("\n".join(lines[1:])) + "</pre>"
    send(token, chat_id, text, msg_id)
    head_pic = first(combined, "headPic", "head_pic", default="")
    banner_id = first(combined, "bannerId", "banner_id", default="")
    nickname = first(combined, "nickname", "nickName", default="")
    level = first(combined, "level", "accountLevel", default="")
    prime = first(combined, "primeLevel", "prime_level", default="")
    pin_id = first(combined, "pinId", "pin_id", default="")
    guild = first(combined, "guild", "guildName", "guild_name", default="")
    banner_one = BANNER_IMAGE_API + "?" + urlencode({
        "headPic": head_pic, "bannerId": banner_id, "name": nickname,
        "level": level, "guild": guild, "pinId": pin_id, "celebrity": "0",
        "primeLevel": prime, "frame": "false",
    })
    banner_sent = False
    for banner in (BANNER_IMAGE_API_2 + quote(uid), banner_one):
        result = tg(token, "sendSticker", chat_id=chat_id, sticker=banner,
                    reply_to_message_id=msg_id or None)
        if result and result.get("ok"):
            banner_sent = True
            break
    if not banner_sent:
        send(token, chat_id, "⚠️ Banner sticker is temporarily unavailable. Player information was sent successfully.", msg_id)
    outfit = f"{OUTFIT_IMAGE_API}?{urlencode({'uid': uid, 'key': 'VERTEX'})}"
    result = tg(token, "sendPhoto", chat_id=chat_id, photo=outfit,
                reply_to_message_id=msg_id or None)
    if not result or not result.get("ok"):
        send(token, chat_id, "⚠️ Outfit image is temporarily unavailable. Player information was sent successfully.", msg_id)


def remaining(token: str, message: dict[str, Any], cfg: dict[str, Any]) -> None:
    chat_id, uid = int_value((message.get("chat") or {}).get("id")), int_value((message.get("from") or {}).get("id"))
    user = state.user(uid) or {}
    output = []
    for label, field, limit in (("Like", "like_usage", LIKE_LIMIT), ("Visit", "visit_usage", VISIT_LIMIT)):
        usage = user.get(field) if isinstance(user.get(field), dict) else {}
        count = int_value(usage.get("count")) if usage.get("window") == window_key() else 0
        output.append(f"• <b>{label}:</b> <code>{max(0, limit-count)}</code> remaining")
    send(cfg["bot_token"], chat_id, "📊 <b>Daily Remaining</b>\n\n" + "\n".join(output), int_value(message.get("message_id")))


def admin_command(token: str, message: dict[str, Any], cfg: dict[str, Any], command: str, args: str) -> bool:
    chat_id, uid, mid = int_value((message.get("chat") or {}).get("id")), int_value((message.get("from") or {}).get("id")), int_value(message.get("message_id"))
    if command == "users":
        if not admin(uid, cfg):
            send(token, chat_id, "⚠️ This is an admin-only command.", mid); return True
        send(token, chat_id, f"<b>✦ USER STATISTICS</b>\n\n<code>TOTAL USERS:</code> <b>{state.users.count_documents({})}</b>", mid); return True
    if command == "status":
        if not admin(uid, cfg):
            send(token, chat_id, "⚠️ This is an admin-only command.", mid); return True
        send(token, chat_id, f"<b>✦ BOT STATUS</b>\n\nUsers: <code>{state.users.count_documents({})}</code>\nBot: <code>{'ON' if cfg['bot_enabled'] else 'OFF'}</code>", mid); return True
    if command in ("botoff", "boton"):
        if not admin(uid, cfg):
            send(token, chat_id, "⚠️ This is an admin-only command.", mid); return True
        # config.json is immutable by requirement; runtime state lives in Mongo.
        db.runtime.update_one({"_id": "config"}, {"$set": {"bot_enabled": command == "boton"}}, upsert=True)
        send(token, chat_id, "✅ Bot is now ON." if command == "boton" else "✅ Bot is now OFF.", mid); return True
    if command == "setapi":
        if not admin(uid, cfg):
            send(token, chat_id, "⚠️ This is an admin-only command.", mid); return True
        mode = int_value(args)
        if mode not in API_URLS:
            send(token, chat_id, "❌ Invalid API mode. Use 1, 2 or 3.", mid)
        else:
            db.runtime.update_one({"_id": "config"}, {"$set": {"api_mode": mode}}, upsert=True)
            send(token, chat_id, f"✅ API mode updated to <code>{mode}</code>.", mid)
        return True
    if command == "checkapi":
        if not admin(uid, cfg):
            send(token, chat_id, "⚠️ This is an admin-only command.", mid); return True
        statuses = [f"API {n}: {'UP' if api_json(url, params={'uid': '100000000'}) is not None else 'DOWN'}" for n, url in API_URLS.items()]
        send(token, chat_id, "📡 <b>API STATUS</b>\n\n" + "\n".join(statuses), mid); return True
    if command == "help":
        send(token, chat_id, "<b>✦ USER COMMANDS</b>\n\n"
             "<code>/start</code> — Open the welcome message\n"
             "<code>/get UID</code> — Get a complete Free Fire profile\n"
             "<code>/bancheck UID</code> — Check ban status\n"
             "<code>/like REGION UID</code> — Send likes\n"
             "<code>/visit REGION UID</code> — Send visits\n"
             "<code>/remain</code> — Check remaining limits\n"
             "<code>/requiredchats</code> — Show required chats", mid); return True
    if command == "start":
        send(token, chat_id, "👋 <b>Welcome!</b>\nUse <code>/help</code> to see available commands.", mid); return True
    if command == "requiredchats":
        chats = force_chats()
        if not chats:
            send(token, chat_id, "✅ No required channels or groups are configured.", mid)
        else:
            send(token, chat_id, "🔒 <b>Required Chats</b>\n\n" + "\n".join(
                f"• {esc(c.get('title') or c.get('username') or c.get('group_id', c.get('chat_id')))}"
                for c in chats), mid)
        return True
    if command == "cmds":
        if not admin(uid, cfg):
            send(token, chat_id, "⚠️ This is an admin-only command.", mid); return True
        send(token, chat_id, "<b>✦ ADMIN COMMANDS</b>\n\n"
             "/accept GROUP_ID\n/autolike REGION UID [target]\n/vip UID\n/runnow\n"
             "/cmds\n/botoff\n/boton\n/checkapi\n/setapi 1|2|3\n/addchatid CHAT_ID\n"
             "/removechatid CHAT_ID\n/checkchatid CHAT_ID\n/offverify\n/onverify", mid); return True
    if command == "accept":
        if not admin(uid, cfg) or not re.fullmatch(r"-?\d+", args):
            send(token, chat_id, "⚠️ This is an admin-only command or the group ID is invalid.", mid); return True
        gid = int_value(args)
        group = state.group(gid) or {"group_id": gid, "approved": True, "disabled_commands": []}
        group["approved"] = True
        state.groups.update_one({"group_id": gid}, {"$set": group}, upsert=True)
        send(token, chat_id, f"✅ Group <code>{gid}</code> has been authorized.", mid); return True
    if command in ("offverify", "onverify"):
        if not admin(uid, cfg):
            send(token, chat_id, "⚠️ This is an admin-only command.", mid); return True
        db.check.update_one({"_id": "state"}, {"$set": {"force_join_enabled": command == "onverify"}}, upsert=True)
        send(token, chat_id, "✅ Force-to-Join verification " + ("enabled." if command == "onverify" else "disabled."), mid); return True
    if command == "addchatid":
        if not admin(uid, cfg):
            send(token, chat_id, "⚠️ This is an admin-only command.", mid); return True
        if not re.fullmatch(r"-?\d+", args):
            send(token, chat_id, "❌ Use <code>/addchatid CHAT_ID</code>.", mid); return True
        cid = int_value(args)
        chat_info = tg(token, "getChat", chat_id=cid)
        value = chat_info.get("result", {}) if chat_info and chat_info.get("ok") else {"id": cid}
        record = {"group_id": cid, "title": value.get("title", ""), "type": value.get("type", ""),
                  "username": value.get("username", ""), "invite_link": value.get("invite_link", "")}
        state.check.update_one({"_id": "state"}, {"$addToSet": {"chat_ids": record}}, upsert=True)
        send(token, chat_id, f"✅ Required chat <code>{cid}</code> has been added.", mid); return True
    if command == "removechatid":
        if not admin(uid, cfg):
            send(token, chat_id, "⚠️ This is an admin-only command.", mid); return True
        cid = int_value(args)
        state.check.update_one({"_id": "state"}, {"$pull": {"chat_ids": {"group_id": cid}}})
        send(token, chat_id, f"✅ Required chat <code>{cid}</code> has been removed.", mid); return True
    if command == "checkchatid":
        if not admin(uid, cfg):
            send(token, chat_id, "⚠️ This is an admin-only command.", mid); return True
        cid = args.strip()
        chat_info = tg(token, "getChat", chat_id=cid)
        if not chat_info or not chat_info.get("ok"):
            send(token, chat_id, "❌ I could not find that chat.", mid)
        else:
            found = chat_info["result"]
            send(token, chat_id, f"✅ <b>Chat found</b>\nID: <code>{found.get('id')}</code>\n"
                 f"Title: <b>{esc(found.get('title', found.get('username', '')))}</b>", mid)
        return True
    if command == "vip":
        if not admin(uid, cfg):
            send(token, chat_id, "⚠️ This is an admin-only command.", mid); return True
        if not re.fullmatch(r"\d+", args):
            send(token, chat_id, "❌ Use <code>/vip UID</code>.", mid); return True
        state.users.update_one({"user_id": int_value(args)}, {"$set": {"vip": True}}, upsert=True)
        send(token, chat_id, f"✅ VIP access enabled for <code>{esc(args)}</code>.", mid); return True
    if command == "runnow":
        if not admin(uid, cfg):
            send(token, chat_id, "⚠️ This is an admin-only command.", mid); return True
        process_auto_like()
        send(token, chat_id, "✅ AutoLike run completed.", mid); return True
    return False


def autolike_command(token: str, message: dict[str, Any], cfg: dict[str, Any], args: str) -> None:
    chat_id, uid = int_value((message.get("chat") or {}).get("id")), int_value((message.get("from") or {}).get("id"))
    if not admin(uid, cfg):
        send(token, chat_id, "⚠️ This is an admin-only command.", int_value(message.get("message_id"))); return
    parts = args.split()
    if len(parts) < 2 or not re.fullmatch(r"[A-Z]+", parts[0]) or not re.fullmatch(r"\d+", parts[1]):
        send(token, chat_id, "⚠️ Usage: <code>/autolike REGION UID [target] [date]</code>", int_value(message.get("message_id"))); return
    record = {"id": uuid.uuid4().hex, "region": parts[0], "uid": parts[1], "target": int_value(parts[2], 100) if len(parts) > 2 else 100,
              "status": "active", "created_at": now_ist().isoformat(), "runs": []}
    state.paid.insert_one(record)
    send(token, chat_id, "✅ AutoLike plan created.\n\n" + esc(json.dumps(record, ensure_ascii=False)), int_value(message.get("message_id")))


def handle_update(update: dict[str, Any], cfg: dict[str, Any]) -> None:
    token = cfg["bot_token"]
    message = update.get("message") if isinstance(update.get("message"), dict) else {}
    if not message:
        callback = update.get("callback_query") or {}
        data = str(callback.get("data", ""))
        callback_user = int_value((callback.get("from") or {}).get("id"))
        callback_chat = int_value(((callback.get("message") or {}).get("chat") or {}).get("id"))
        if data == "force_join_check":
            if not missing_required(token, callback_user):
                tg(token, "answerCallbackQuery", callback_query_id=callback.get("id"),
                   text="Membership confirmed. You can use /Get uid now.")
                send(token, callback_chat, "✅ Membership confirmed. You can use <code>/Get uid</code> now.")
            else:
                tg(token, "answerCallbackQuery", callback_query_id=callback.get("id"),
                   text="Please join every required chat first.")
                force_prompt(token, callback_chat, missing_required(token, callback_user), None)
        elif data.startswith("force_join_info:"):
            tg(token, "answerCallbackQuery", callback_query_id=callback.get("id"),
               text="Please join the required chat and try again.", show_alert=True)
        elif data == "like_check_join":
            missing = missing_required(token, callback_user)
            if not missing:
                tg(token, "answerCallbackQuery", callback_query_id=callback.get("id"),
                   text="Membership verified. Send /like again.")
                send(token, callback_chat,
                     "✅ <b>Mᴇᴍʙᴇʀꜱʜɪᴘ Vᴇʀɪꜰɪᴇᴅ</b>\n\n"
                     "Yᴏᴜ Cᴀɴ Nᴏᴡ Sᴇɴᴅ <code>/like {Rᴇɢɪᴏɴ} {Uɪᴅ}</code>.")
            else:
                tg(token, "answerCallbackQuery", callback_query_id=callback.get("id"),
                   text="You are not a member yet. Join the group first.", show_alert=True)
        elif data.startswith("addchatid_admin:"):
            target = data.split(":", 1)[1]
            tg(token, "answerCallbackQuery", callback_query_id=callback.get("id"),
               text="Add the bot as an administrator, then run /addchatid again.", show_alert=True)
        return
    chat = message.get("chat") or {}
    chat_id, uid = int_value(chat.get("id")), ensure_user(message)
    text = str(message.get("text", "")).strip()
    if not text.startswith("/"):
        return
    match = re.match(r"^/([A-Za-z0-9_]+)(?:@\w+)?(?:\s+(.*))?$", text, re.S)
    if not match:
        return
    command, args = match.group(1).lower(), (match.group(2) or "").strip()
    group = state.group(chat_id)
    if chat_is_group(chat) and command not in ("accept", "disable", "active") and not authorize_group(
        token, chat, cfg["owner_user_id"], int_value(message.get("message_id"))
    ):
        return
    if group and command not in ("disable", "active") and command in {
        str(x).lower() for x in group.get("disabled_commands", []) if isinstance(x, str)
    }:
        return
    if not cfg["bot_enabled"] and uid != cfg["owner_user_id"]:
        send(token, chat_id, "⚠️ The bot is currently disabled. Please try again later.",
             int_value(message.get("message_id")))
        return
    if command in ("get", "info", "accinfo") and missing_required(token, uid):
        force_prompt(token, chat_id, missing_required(token, uid), int_value(message.get("message_id"))); return
    if admin_command(token, message, cfg, command, args):
        return
    if command in ("get", "info", "accinfo"):
        value = args or str((message.get("reply_to_message") or {}).get("text", "")).strip()
        info_command(token, message, cfg, value); return
    if command == "like":
        like_command(token, message, cfg, args); return
    if command == "visit":
        visit_command(token, message, cfg, args); return
    if command == "remain":
        remaining(token, message, cfg); return
    if command == "bancheck":
        ban_command(token, message, cfg, args); return
    if command == "autolike":
        autolike_command(token, message, cfg, args); return
    if command in ("disable", "active") and chat_is_group(chat):
        group = state.group(chat_id) or {}
        admins = [int_value(x) for x in group.get("admin_user_ids", [])]
        if uid in admins:
            disabled = set(group.get("disabled_commands", []))
            name = args.lower().lstrip("/")
            disabled.discard(name) if command == "active" else disabled.add(name)
            state.groups.update_one({"group_id": chat_id}, {"$set": {"disabled_commands": list(disabled)}}, upsert=True)
            send(token, chat_id, f"✅ Command <code>/{name}</code> {'enabled' if command == 'active' else 'disabled'}.", int_value(message.get("message_id")))
        return


def process_auto_like() -> None:
    if db is None or not db_connected or state is None:
        return
    cfg = config()
    if not cfg:
        return
    for record in state.paid.find({"status": "active"}):
        if record.get("last_run_window") == window_key():
            continue
        result = api_like(str(record.get("region", "")), str(record.get("uid", "")), cfg["api_mode"])
        status = "success" if result else "failed"
        state.paid.update_one({"_id": record["_id"]}, {"$set": {"last_run_window": window_key(), "last_status": status, "last_run_at": now_ist().isoformat()}})
        send(cfg["bot_token"], AUTO_LIKE_CHAT_ID, f"AutoLike {status}: <code>{esc(record.get('uid'))}</code>")


def scheduler() -> None:
    while True:
        try:
            process_auto_like()
        except Exception:
            log.exception("auto-like scheduler tick failed")
        time.sleep(30)


def database_retry_loop() -> None:
    global startup_error
    while not db_connected:
        time.sleep(30)
        try:
            connect_database()
            print("DATABASE SUCCESSFULLY CONNECTED", flush=True)
        except Exception as exc:
            startup_error = str(exc)
            log.error("DATABASE CONNECTION FAILED: %s", exc)


def process_webhook_update(update: dict[str, Any], cfg: dict[str, Any]) -> None:
    """Process Telegram work outside the webhook request."""
    try:
        handle_update(update, cfg)
    except Exception:
        log.exception("webhook update processing failed")


@app.route("/health", methods=["GET"])
@app.route("/healthz", methods=["GET"])
def health() -> Any:
    # Koyeb should be able to distinguish a live web process from database
    # readiness without restarting the service during a transient outage.
    return jsonify({
        "status": "ok",
        "service": "ff-bot2",
        "database": "connected" if db_connected else "disconnected",
    }), 200


@app.route("/", methods=["GET", "POST"])
@app.route("/webhook", methods=["GET", "POST"])
def webhook() -> Any:
    if request.method == "GET":
        return jsonify({"status": "ok", "service": "ff-bot2", "database": db_connected})
    if not db_connected or state is None:
        return jsonify({"ok": False, "error": "Database is not connected yet"}), 503
    cfg = config()
    if cfg:
        update = request.get_json(silent=True)
        if isinstance(update, dict):
            update_id = int_value(update.get("update_id"), -1)
            if update_id >= 0 and state.processed.find_one({"_id": str(update_id)}):
                return jsonify({"ok": True})
            if update_id >= 0:
                state.processed.update_one({"_id": str(update_id)}, {"$set": {"processed_at": time.time()}}, upsert=True)
            # Telegram expects a fast 2xx response. Commands can call several
            # third-party APIs, so process them after acknowledging the update.
            threading.Thread(
                target=process_webhook_update,
                args=(update, cfg),
                daemon=True,
                name=f"telegram-update-{update_id}",
            ).start()
    return jsonify({"ok": True})


def startup() -> None:
    global db, db_connected, startup_error
    print("BOT SUCCESSFULLY START", flush=True)
    try:
        connect_database()
        print("DATABASE SUCCESSFULLY CONNECTED", flush=True)
    except Exception as exc:
        startup_error = str(exc)
        db_connected = False
        print(f"DATABASE CONNECTION FAILED: {exc}", flush=True)
        threading.Thread(target=database_retry_loop, daemon=True, name="database-retry").start()
        return
    threading.Thread(target=scheduler, daemon=True, name="autolike-scheduler").start()


startup()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
