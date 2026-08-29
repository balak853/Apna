from __future__ import annotations

import html
import logging
import re
from typing import Any, Awaitable, Callable

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message


# Cumulative Free Fire EXP thresholds: the value is the total EXP required
# to reach the corresponding level. The table is intentionally kept local and
# configurable so it can be reviewed or updated without changing the handler.
#
# Source checked against the published Level Table Up to Level 100:
# https://freefirejornal.com/en/find-out-how-much-xp-you-need-for-level-100-in-free-fire-using-your-id
LEVEL_EXP_THRESHOLDS: dict[int, int] = {
    1: 0,
    2: 48,
    3: 202,
    4: 544,
    5: 1_012,
    6: 1_844,
    7: 2_792,
    8: 3_800,
    9: 4_870,
    10: 6_004,
    11: 7_192,
    12: 8_448,
    13: 9_760,
    14: 11_140,
    15: 12_566,
    16: 14_060,
    17: 15_610,
    18: 17_224,
    19: 18_902,
    20: 20_632,
    21: 22_424,
    22: 24_278,
    23: 26_192,
    24: 28_166,
    25: 30_200,
    26: 32_294,
    27: 34_448,
    28: 37_804,
    29: 41_274,
    30: 44_870,
    31: 48_582,
    32: 53_394,
    33: 58_566,
    34: 64_096,
    35: 69_994,
    36: 76_260,
    37: 83_506,
    38: 91_128,
    39: 99_322,
    40: 108_092,
    41: 120_144,
    42: 133_266,
    43: 147_472,
    44: 162_760,
    45: 179_126,
    46: 196_572,
    47: 215_368,
    48: 235_316,
    49: 257_010,
    50: 279_860,
    51: 304_056,
    52: 348_318,
    53: 394_982,
    54: 444_044,
    55: 495_508,
    56: 549_364,
    57: 633_756,
    58: 721_744,
    59: 813_336,
    60: 908_522,
    61: 1_041_438,
    62: 1_180_352,
    63: 1_325_266,
    64: 1_476_184,
    65: 1_634_300,
    66: 1_840_946,
    67: 2_056_594,
    68: 2_281_242,
    69: 2_514_880,
    70: 2_757_530,
    71: 3_059_506,
    72: 3_372_284,
    73: 3_699_456,
    74: 4_041_030,
    75: 4_397_002,
    76: 4_829_104,
    77: 5_282_204,
    78: 5_756_304,
    79: 6_251_404,
    80: 6_767_502,
    81: 7_381_324,
    82: 8_043_154,
    83: 8_752_982,
    84: 9_510_808,
    85: 10_316_638,
    86: 11_277_190,
    87: 12_291_748,
    88: 13_360_304,
    89: 14_482_858,
    90: 15_659_418,
    91: 17_026_708,
    92: 18_453_990,
    93: 19_941_280,
    94: 21_488_570,
    95: 23_095_858,
    96: 24_763_138,
    97: 26_490_428,
    98: 28_277_708,
    99: 30_124_996,
    100: 32_032_284,
}

UID_PATTERN = re.compile(r"^[0-9]{1,20}$")
LEVEL_MESSAGE_PATTERN = re.compile(r"^\s*/?level(?:\s+.*)?\s*$", re.IGNORECASE)
NOT_AVAILABLE = "N/A"

INVALID_UID_MESSAGE = (
    "<b>❌ Iɴᴠᴀʟɪᴅ Uɪᴅ</b>\n\n"
    "Pʟᴇᴀsᴇ Pʀᴏᴠɪᴅᴇ A Vᴀʟɪᴅ Fʀᴇᴇ Fɪʀᴇ Uɪᴅ."
)
API_UNAVAILABLE_MESSAGE = (
    "<b>⚠️ Pʟᴀʏᴇʀ Iɴғᴏ Uɴᴀᴠᴀɪʟᴀʙʟᴇ</b>\n\n"
    "Pʟᴇᴀsᴇ Tʀʏ Aɢᴀɪɴ Sʜᴏʀᴛʟʏ."
)
REGION_REQUIRED_MESSAGE = (
    "<b>⚠️ Rᴇɢɪᴏɴ Rᴇǫᴜɪʀᴇᴅ</b>\n\n"
    "Pʟᴇᴀsᴇ Pʀᴏᴠɪᴅᴇ Tʜᴇ Pʟᴀʏᴇʀ Rᴇɢɪᴏɴ.\n"
    "Uѕᴇ: <code>/Level UID REGION</code>\n"
    "E.xᴀᴍᴘʟᴇ: <code>/Level 1589573783 ind</code>"
)
LEVEL_PLAYER_INFO_API_URL = (
    "https://vertex-x-ff.vercel.app/get?uid={uid}&region={region}"
)


def _integer_value(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip().replace(",", "")
    if not re.fullmatch(r"[0-9]+", text):
        return None
    return int(text)


def _has_value(value: Any) -> bool:
    return value not in (None, "")


def _basic_info(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    for field_name in (
        "captainBasicInfo",
        "basicInfo",
        "basic_info",
        "AccountInfo",
        "accountInfo",
    ):
        basic_info = payload.get(field_name)
        if isinstance(basic_info, dict):
            return basic_info
    return None


def resolve_basic_info(payloads: list[Any]) -> dict[str, Any] | None:
    """Read the Level API's player fields without combining API responses."""
    field_aliases = {
        "account_id": ("account_id", "accountId", "uid", "playerId"),
        "nickname": ("nickname", "accountName", "name"),
        "region": ("region", "accountRegion"),
        "level": ("level", "accountLevel"),
        "exp": ("exp", "accountEXP", "accountExp", "experience"),
        "liked": ("liked", "accountLikes", "likes"),
    }
    resolved: dict[str, Any] = {}
    for payload in payloads:
        basic_info = _basic_info(payload)
        if basic_info is None:
            continue
        for output_field, aliases in field_aliases.items():
            if output_field in resolved:
                continue
            for alias in aliases:
                value = basic_info.get(alias)
                if _has_value(value):
                    resolved[output_field] = value
                    break
    return resolved or None


def calculate_level_progress(
    level_value: Any,
    exp_value: Any,
    thresholds: dict[int, int] | None = None,
) -> dict[str, int | float | None]:
    """Calculate progress only after validating API level/EXP boundaries."""
    table = thresholds if thresholds is not None else LEVEL_EXP_THRESHOLDS
    level = _integer_value(level_value)
    exp = _integer_value(exp_value)
    invalid = {
        "next_level": None,
        "exp_needed": None,
        "level_100_exp": None,
        "remaining_exp": None,
        "progress_percent": None,
    }
    if level is None or exp is None or not 1 <= level <= 100 or exp < 0:
        return invalid

    current_threshold = table.get(level)
    total_threshold = table.get(100)
    if current_threshold is None or total_threshold is None:
        return invalid

    next_level = level + 1 if level < 100 else None
    next_threshold = table.get(next_level) if next_level is not None else None
    if exp < current_threshold:
        return invalid
    if next_level is not None and next_threshold is None:
        return invalid
    if next_threshold is not None and exp >= next_threshold:
        return invalid
    if level == 100 and exp < total_threshold:
        return invalid

    return {
        "next_level": next_level,
        "exp_needed": max(0, next_threshold - exp) if next_threshold is not None else None,
        "level_100_exp": total_threshold,
        "remaining_exp": max(0, total_threshold - exp),
        "progress_percent": min(100.0, max(0.0, exp / total_threshold * 100)),
    }


def _escaped(value: Any) -> str:
    if not _has_value(value):
        return NOT_AVAILABLE
    return html.escape(str(value), quote=True)


def _number_text(value: Any) -> str:
    number = _integer_value(value)
    return f"{number:,}" if number is not None else NOT_AVAILABLE


def _level_text(value: Any) -> str:
    number = _integer_value(value)
    return str(number) if number is not None else NOT_AVAILABLE


def _progress_bar(progress: float | None, segments: int = 10) -> str:
    if progress is None:
        return "░" * segments
    filled = min(segments, max(0, round(progress / 100 * segments)))
    if progress > 0 and filled == 0:
        filled = 1
    return "▰" * filled + "▱" * (segments - filled)


def build_level_info_output(
    requested_uid: str,
    basic_info: dict[str, Any],
    thresholds: dict[int, int] | None = None,
) -> str:
    level = _integer_value(basic_info.get("level"))
    exp = _integer_value(basic_info.get("exp"))
    calculations = calculate_level_progress(level, exp, thresholds)
    progress = calculations["progress_percent"]
    progress_text = f"{progress:.2f}%" if progress is not None else NOT_AVAILABLE
    progress_bar = _progress_bar(progress)

    # requested_uid is intentionally not used as a fallback for account_id:
    # the displayed UID must come from basic_info.account_id as requested.
    return (
        "<b>╭━━━ 📊 Lᴇᴠᴇʟ Iɴғᴏʀᴍᴀᴛɪᴏɴ ━━━╮</b>\n"
        "<b>│</b>\n"
        "<b>│ ᴘʟᴀʏᴇʀ ᴘʀᴏғɪʟᴇ</b>\n"
        "<b>╰────────────────────╯</b>\n\n"
        "<blockquote>"
        f"🆔 <b>UID</b>  <code>{_escaped(basic_info.get('account_id'))}</code>\n"
        f"👤 <b>Nᴀᴍᴇ</b>  <code>{_escaped(basic_info.get('nickname'))}</code>\n"
        f"🌍 <b>Rᴇɢɪᴏɴ</b>  <code>{_escaped(basic_info.get('region'))}</code>\n"
        f"⭐ <b>Lᴇᴠᴇʟ</b>  <code>{_level_text(level)}</code>\n\n"
        "<b>✨ EXP Jᴏᴜʀɴᴇʏ</b>\n"
        f"✨ Cᴜʀʀᴇɴᴛ EXP  <code>{_number_text(exp)}</code>\n"
        f"📈 Nᴇxᴛ Lᴇᴠᴇʟ  <code>{calculations['next_level'] or NOT_AVAILABLE}</code>\n"
        f"🎯 EXP Nᴇᴇᴅᴇᴅ  <code>{_number_text(calculations['exp_needed'])}</code>\n\n"
        f"🏆 Tᴏ Lᴇᴠᴇʟ 100  <code>{progress_text}</code>\n"
        f"<code>{progress_bar}</code>\n"
        f"🚀 Rᴇᴍᴀɪɴɪɴɢ EXP  <code>{_number_text(calculations['remaining_exp'])}</code>\n"
        f"📦 Tᴏᴛᴀʟ Rᴇǫᴜɪʀᴇᴅ  <code>{_number_text(calculations['level_100_exp'])}</code>\n\n"
        f"❤️ Lɪᴋᴇs  <code>{_number_text(basic_info.get('liked'))}</code>"
        "</blockquote>\n"
        "<i>⚡ Kᴇᴇᴘ Pʟᴀʏɪɴɢ • Kᴇᴇᴘ Lᴇᴠᴇʟɪɴɢ</i>"
    )


async def _reply_api_unavailable(
    message: Message,
    processing: Message | None,
) -> None:
    if processing is not None:
        try:
            await processing.delete()
        except Exception:
            pass
    await message.reply_text(
        API_UNAVAILABLE_MESSAGE,
        parse_mode=ParseMode.HTML,
        quote=True,
    )


def developer_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(
                "✦ 𝑫ᴇᴠʟᴏᴘᴇʀ ✦",
                url="https://t.me/BALAK_TRUSTED",
            )
        ]]
    )


async def fetch_level_basic_info(
    *,
    uid: str,
    region: str,
    fetch_player_info: Callable[[str], Awaitable[Any | None]],
    logger: logging.Logger,
) -> dict[str, Any] | None:
    """Fetch /Level data only from the configured Vertex-X endpoint."""
    api_url = LEVEL_PLAYER_INFO_API_URL.format(uid=uid, region=region)
    try:
        payload = await fetch_player_info(api_url)
    except Exception as error:
        logger.warning("Level player-info source failed: %s", type(error).__name__)
        return None

    basic_info = resolve_basic_info([payload])
    if basic_info is None:
        logger.warning("Level player-info source returned no usable basic_info")
    return basic_info


def register_level_info_handler(
    *,
    bot: Client,
    fetch_player_info: Callable[[str], Awaitable[Any | None]],
    require_bot_group_admin: Callable[[Message], Awaitable[bool]],
    command_access_allowed: Callable[[Message], Awaitable[bool]],
    logger: logging.Logger,
) -> None:
    """Register Level/level using only the Vertex-X player-info endpoint."""

    @bot.on_message(filters.incoming & ~filters.service & filters.regex(LEVEL_MESSAGE_PATTERN))
    async def level_info_command(_: Client, message: Message) -> None:
        processing: Message | None = None
        try:
            if not await require_bot_group_admin(message):
                return
            if not await command_access_allowed(message):
                return

            raw_text = (message.text or message.caption or "").strip()
            parts = raw_text.split()
            command = parts[0] if parts else ""
            command = command[1:] if command.startswith("/") else command
            if command.lower() != "level" or len(parts) not in {2, 3}:
                await message.reply_text(
                    INVALID_UID_MESSAGE,
                    parse_mode=ParseMode.HTML,
                    quote=True,
                )
                return

            if len(parts) == 2:
                uid = parts[1]
                region = None
            elif UID_PATTERN.fullmatch(parts[1]):
                uid = parts[1]
                region = parts[2].lower()
            elif UID_PATTERN.fullmatch(parts[2]):
                region = parts[1].lower()
                uid = parts[2]
            else:
                uid = parts[1]
                region = parts[2].lower()

            if not UID_PATTERN.fullmatch(uid):
                await message.reply_text(
                    INVALID_UID_MESSAGE,
                    parse_mode=ParseMode.HTML,
                    quote=True,
                )
                return
            if int(uid) <= 0:
                await message.reply_text(
                    INVALID_UID_MESSAGE,
                    parse_mode=ParseMode.HTML,
                    quote=True,
                )
                return

            if region is None or not re.fullmatch(r"[A-Za-z0-9_-]+", region):
                await message.reply_text(
                    REGION_REQUIRED_MESSAGE,
                    parse_mode=ParseMode.HTML,
                    quote=True,
                )
                return

            processing = await message.reply_text(
                "⏳ Lᴇᴠᴇʟ Iɴғᴏʀᴍᴀᴛɪᴏɴ Lᴏᴀᴅɪɴɢ...",
                quote=True,
            )
            basic_info = await fetch_level_basic_info(
                uid=uid,
                region=region,
                fetch_player_info=fetch_player_info,
                logger=logger,
            )
            if basic_info is None:
                await _reply_api_unavailable(message, processing)
                processing = None
                return

            output = build_level_info_output(uid, basic_info)
            if processing is not None:
                try:
                    await processing.delete()
                except Exception:
                    pass
                processing = None
            await message.reply_text(
                output,
                parse_mode=ParseMode.HTML,
                reply_markup=developer_keyboard(),
                quote=True,
            )
        except Exception:
            logger.exception("Level information command failed")
            await _reply_api_unavailable(message, processing)
            processing = None
        finally:
            if processing is not None:
                try:
                    await processing.delete()
                except Exception:
                    pass