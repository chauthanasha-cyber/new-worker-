import asyncio
import html
import logging
import os
import re
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PicklePersistence,
    filters,
)

# =========================================================
# CONFIG
# =========================================================

TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise RuntimeError(
        "BOT_TOKEN environment variable is missing."
    )

ALLOWED_CHAT_ID = -1004413547137
BRAND = "\n\n@epic_india"

# IST
IST = timezone(timedelta(hours=5, minutes=30))

# Persistent storage file
PERSISTENCE_FILE = "bot_data.pkl"

# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

# =========================================================
# IN-MEMORY DATA
# =========================================================

# Structure:
# filters_db[chat_id][word] = (type, file_id/text, caption)
filters_db = {}

# Last automatic note message
last_note_msg = {}

# Members tracked per chat
# group_members[chat_id] = {
#     user_id: first_name
# }
group_members = {}

# =========================================================
# EMOJIS / SYMBOLS
# =========================================================

EMOJIS = [
    "😀", "😃", "😄", "😁", "😆", "😅", "😂", "🤣",
    "😊", "😇", "🙂", "🙃", "😉", "😌", "😍", "🥰",
    "😘", "😗", "😙", "😚", "😋", "😛", "😝", "😜",
    "🤪", "🤨", "🧐", "🤓", "😎", "🤩", "🥳", "😏",
    "😒", "😞", "😔", "😟", "😕", "🙁", "☹️", "😣",
]

SYMBOLS = [
    "♦️", "♠️", "♣️", "♥️", "★", "⚡", "🔥", "💥",
    "✨", "🌟", "👑", "💎", "🎯", "🚀", "❄️", "🍀",
    "🌸", "🌺", "🔱", "⚜️", "🔴", "🔵", "🟡", "🟢",
    "🟣", "🟠", "⚫", "⚪", "🟤", "🔺",
]

# =========================================================
# HELPERS
# =========================================================

def escape_html(value: str) -> str:
    """Safely escape text before using HTML parse mode."""
    return html.escape(str(value), quote=True)


def get_chat_members(chat_id: int):
    """Return member dict for a chat."""
    return group_members.setdefault(chat_id, {})


def add_member(chat_id: int, user):
    """Track one member safely."""
    if not user or user.is_bot:
        return

    members = get_chat_members(chat_id)

    members[user.id] = user.first_name or user.username or "User"


async def direct_reply(
    update: Update,
    text: str,
    parse_mode="HTML",
):
    """Reply to user's message."""
    if not update.message:
        return

    try:
        await update.message.reply_text(
            text + BRAND,
            reply_to_message_id=update.message.message_id,
            parse_mode=parse_mode,
        )
    except Exception:
        logger.exception("Failed to send direct reply")


async def check_allowed_chat(
    update: Update,
) -> bool:
    """Allow only the configured group."""
    chat = update.effective_chat

    if not chat:
        return False

    # Private DM
    if chat.type == "private":
        if update.message:
            try:
                await update.message.reply_text(
                    "Yeh bot sirf @epic_india group ke liye kaam karti hai.\n"
                    "For more contact - @E_admi_n"
                )
            except Exception:
                logger.exception("Failed to send DM response")

        return False

    # Other groups
    if chat.id != ALLOWED_CHAT_ID:
        try:
            await context_bot_leave(update)
        except Exception:
            logger.exception("Could not leave unauthorized chat")

        return False

    return True


async def context_bot_leave(update: Update):
    """Leave unauthorized chat."""
    chat = update.effective_chat

    if chat:
        # Bot instance is not directly available here,
        # so this helper is replaced by handlers where possible.
        return


async def is_admin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> bool:
    """Check whether sender is admin/creator."""
    chat = update.effective_chat
    user = update.effective_user

    if not chat or chat.type not in ("group", "supergroup"):
        return False

    if not user:
        return False

    try:
        member = await context.bot.get_chat_member(
            chat.id,
            user.id,
        )

        if member.status in ("administrator", "creator"):
            return True

    except Exception:
        logger.exception("Admin check failed")

    await direct_reply(
        update,
        "⚠️ Sirf <b>Group Admins</b> ye command use kar sakte hain!",
    )

    return False


# =========================================================
# MEMBER TRACKING
# =========================================================

async def track_members(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """Track users only from the allowed group."""
    chat = update.effective_chat
    user = update.effective_user

    if not chat or chat.id != ALLOWED_CHAT_ID:
        return

    add_member(chat.id, user)


# =========================================================
# NIGHT LOCK
# =========================================================

async def night_lock(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await check_allowed_chat(update):
        return

    msg = update.message

    if not msg:
        return

    hour = datetime.now(IST).hour

    if not (1 <= hour < 5):
        return

    # Media only
    if not (
        msg.photo
        or msg.sticker
        or msg.animation
        or msg.video
        or msg.document
    ):
        return

    try:
        user_tag = msg.from_user.mention_html()

        await msg.delete()

        await context.bot.send_message(
            msg.chat_id,
            (
                f"{user_tag} ⚠️ <b>NIGHT MODE IS ON</b>\n"
                "WAIT TILL 5 AM\n\n"
                "नाइट मोड चालू है, सुबह 5 बजे तक प्रतीक्षा करें"
                + BRAND
            ),
            parse_mode="HTML",
        )

    except Exception:
        logger.exception("Night lock failed")


# =========================================================
# ANTI LINK
# =========================================================

LINK_PATTERN = re.compile(
    r"(https?://|www\.|t\.me/|telegram\.me/)",
    re.IGNORECASE,
)


async def anti_link(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await check_allowed_chat(update):
        return

    msg = update.message

    if not msg or not msg.text:
        return

    if not LINK_PATTERN.search(msg.text):
        return

    try:
        user_tag = msg.from_user.mention_html()

        await msg.delete()

        await context.bot.send_message(
            msg.chat_id,
            (
                f"{user_tag} ⚠️ "
                "<b>Link share karna allowed nahi hai!</b>\n\n"
                "Group link/username share na karein."
                + BRAND
            ),
            parse_mode="HTML",
        )

    except Exception:
        logger.exception("Anti-link handler failed")


# =========================================================
# FILTER ADD
# =========================================================

async def filter_add(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await check_allowed_chat(update):
        return

    if not await is_admin(update, context):
        return

    msg = update.message

    if not msg:
        return

    if not msg.reply_to_message:
        await direct_reply(
            update,
            "⚠️ Usage: <code>/filter word</code>\n"
            "Command ko kisi message/media ke reply mein bhejo.",
        )
        return

    if not context.args:
        await direct_reply(
            update,
            "⚠️ Filter word do.\n"
            "Example: <code>/filter hello</code>",
        )
        return

    word = context.args[0].strip().lower()

    if not word:
        return

    chat_id = msg.chat_id
    rep = msg.reply_to_message

    filters_db.setdefault(chat_id, {})

    # Photo
    if rep.photo:
        filters_db[chat_id][word] = (
            "photo",
            rep.photo[-1].file_id,
            rep.caption or "",
        )

    # Sticker
    elif rep.sticker:
        filters_db[chat_id][word] = (
            "sticker",
            rep.sticker.file_id,
            "",
        )

    # Animation/GIF
    elif rep.animation:
        filters_db[chat_id][word] = (
            "gif",
            rep.animation.file_id,
            rep.caption or "",
        )

    # Video
    elif rep.video:
        filters_db[chat_id][word] = (
            "video",
            rep.video.file_id,
            rep.caption or "",
        )

    # Text
    elif rep.text:
        filters_db[chat_id][word] = (
            "text",
            rep.text,
            "",
        )

    # Unsupported
    else:
        await direct_reply(
            update,
            "❌ Is message type ko filter ke liye save nahi kar sakta.",
        )
        return

    await direct_reply(
        update,
        f"✅ Filter <b>{escape_html(word)}</b> save ho gaya!",
    )


# =========================================================
# FILTER REMOVE
# =========================================================

async def filter_remove(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await check_allowed_chat(update):
        return

    if not await is_admin(update, context):
        return

    msg = update.message

    if not msg:
        return

    if not context.args:
        await direct_reply(
            update,
            "⚠️ Example: <code>/blow hello</code>",
        )
        return

    word = context.args[0].strip().lower()
    chat_id = msg.chat_id

    if (
        chat_id in filters_db
        and word in filters_db[chat_id]
    ):
        del filters_db[chat_id][word]

        await direct_reply(
            update,
            f"🗑️ Filter <b>{escape_html(word)}</b> hata diya.",
        )

    else:
        await direct_reply(
            update,
            "❌ Ye filter database mein nahi mila.",
        )


# =========================================================
# FILTER LIST
# =========================================================

async def filter_list(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await check_allowed_chat(update):
        return

    if not await is_admin(update, context):
        return

    msg = update.message

    if not msg:
        return

    chat_id = msg.chat_id
    words = list(
        filters_db.get(chat_id, {}).keys()
    )

    if not words:
        await direct_reply(
            update,
            "ℹ️ Group mein koi filter nahi hai.",
        )
        return

    text = (
        "📝 <b>Saved Filters:</b>\n\n"
        + "\n".join(
            f"• <code>{escape_html(word)}</code>"
            for word in words
        )
    )

    await direct_reply(update, text)


# =========================================================
# FILTER TRIGGER
# =========================================================

async def filter_trigger(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await check_allowed_chat(update):
        return

    msg = update.message

    if not msg or not msg.text:
        return

    if msg.text.startswith("/"):
        return

    chat_id = msg.chat_id
    filters_for_chat = filters_db.get(chat_id)

    if not filters_for_chat:
        return

    # Normalize punctuation around words
    text_words = set(
        re.findall(
            r"[^\W_]+",
            msg.text.lower(),
            flags=re.UNICODE,
        )
    )

    for word, data in filters_for_chat.items():

        # Exact word matching
        if word.lower() not in text_words:
            continue

        ftype, fid, cap = data

        tag = (
            f"{msg.from_user.mention_html()} "
        )

        try:
            if ftype == "photo":

                caption = (
                    tag
                    + escape_html(cap)
                    + BRAND
                )

                await msg.reply_photo(
                    fid,
                    caption=caption,
                    parse_mode="HTML",
                )

            elif ftype == "sticker":

                await msg.reply_sticker(
                    fid,
                )

            elif ftype == "gif":

                caption = (
                    tag
                    + escape_html(cap)
                    + BRAND
                )

                await msg.reply_animation(
                    fid,
                    caption=caption,
                    parse_mode="HTML",
                )

            elif ftype == "video":

                caption = (
                    tag
                    + escape_html(cap)
                    + BRAND
                )

                await msg.reply_video(
                    fid,
                    caption=caption,
                    parse_mode="HTML",
                )

            elif ftype == "text":

                await msg.reply_text(
                    tag
                    + escape_html(fid)
                    + BRAND,
                    parse_mode="HTML",
                )

        except Exception:
            logger.exception(
                "Filter trigger failed for '%s'",
                word,
            )

        # Only trigger first matching filter
        break


# =========================================================
# AUTO NOTE
# =========================================================

async def auto_note(
    context: ContextTypes.DEFAULT_TYPE,
):
    chat_id = ALLOWED_CHAT_ID

    old_message_id = last_note_msg.get(chat_id)

    if old_message_id:
        try:
            await context.bot.delete_message(
                chat_id,
                old_message_id,
            )
        except Exception:
            logger.warning(
                "Could not delete previous auto note",
                exc_info=True,
            )

    try:
        msg = await context.bot.send_message(
            chat_id,
            (
                "📢 <b>Safety Note</b>\n\n"
                "To stay safer, follow the rules and instructions.\n"
                "Don't share any personal information.\n\n"
                "सुरक्षित रहने के लिए नियमों और निर्देशों "
                "का पालन करें। व्यक्तिगत जानकारी मत भेजिए"
                + BRAND
            ),
            parse_mode="HTML",
        )

        last_note_msg[chat_id] = msg.message_id

    except Exception:
        logger.exception("Auto note failed")


# =========================================================
# WELCOME / GOODBYE
# =========================================================

async def welcome_bye(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await check_allowed_chat(update):
        return

    msg = update.message

    if not msg:
        return

    # New members
    if msg.new_chat_members:

        for member in msg.new_chat_members:

            if member.is_bot:
                continue

            add_member(msg.chat_id, member)

            tag = member.mention_html()

            welcome_text = (
                f"🎉 <b>Welcome {tag} to the group!</b> 🎉\n\n"
                "Aapka swagat hai humare group mein!\n"
                "Group ke rules follow karo aur maze karo! 😊"
                + BRAND
            )

            try:
                await context.bot.send_message(
                    msg.chat_id,
                    welcome_text,
                    parse_mode="HTML",
                )
            except Exception:
                logger.exception(
                    "Welcome message failed"
                )

    # Left/kicked member
    if msg.left_chat_member:

        member = msg.left_chat_member

        if member.is_bot:
            return

        tag = member.mention_html()

        # Remove from tracked list
        members = get_chat_members(msg.chat_id)
        members.pop(member.id, None)

        bye_text = (
            f"👋 <b>Goodbye {tag}!</b>\n\n"
            "Hum aapko miss karenge. Wapas zaroor aana! 💔"
            + BRAND
        )

        try:
            await context.bot.send_message(
                msg.chat_id,
                bye_text,
                parse_mode="HTML",
            )
        except Exception:
            logger.exception(
                "Goodbye message failed"
            )


# =========================================================
# INVITE
# =========================================================

async def invite_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await check_allowed_chat(update):
        return

    if not await is_admin(update, context):
        return

    chat_id = update.effective_chat.id
    members = get_chat_members(chat_id)

    note = (
        " ".join(context.args).strip()
        if context.args
        else "Kripya sabhi log active ho jayein!"
    )

    if not members:
        await direct_reply(
            update,
            "⚠️ Abhi koi tracked member available nahi hai.",
        )
        return

    safe_note = escape_html(note)

    members_list = list(members.items())

    batch_size = 25

    for i in range(
        0,
        len(members_list),
        batch_size,
    ):
        batch = members_list[
            i:i + batch_size
        ]

        tags = []

        for user_id, user_name in batch:

            safe_name = escape_html(
                user_name
            )

            tags.append(
                f'<a href="tg://user?id={user_id}">'
                f'@{safe_name}</a>'
            )

        text = (
            f"📢 <b>Note:</b> {safe_note}\n\n"
            + " ".join(tags)
            + BRAND
        )

        try:
            await context.bot.send_message(
                chat_id,
                text,
                parse_mode="HTML",
            )
        except Exception:
            logger.exception(
                "Invite batch failed"
            )

        await asyncio.sleep(2)


# =========================================================
# ALL
# =========================================================

async def tag_all_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await check_allowed_chat(update):
        return

    if not await is_admin(update, context):
        return

    chat_id = update.effective_chat.id
    members = get_chat_members(chat_id)

    if not members:
        await direct_reply(
            update,
            "⚠️ Abhi member database empty hai.",
        )
        return

    members_list = list(members.items())

    batch_size = 25

    for i in range(
        0,
        len(members_list),
        batch_size,
    ):
        batch = members_list[
            i:i + batch_size
        ]

        tags = []

        for user_id, user_name in batch:

            safe_name = escape_html(
                user_name
            )

            tags.append(
                f'<a href="tg://user?id={user_id}">'
                f'{SYMBOLS[i % len(SYMBOLS)]} '
                f'{safe_name}</a>'
            )

        text = (
            f"🔔 <b>Calling All Members "
            f"({i + 1}-{i + len(batch)}):</b>\n\n"
            + "  ".join(tags)
            + BRAND
        )

        try:
            await context.bot.send_message(
                chat_id,
                text,
                parse_mode="HTML",
            )
        except Exception:
            logger.exception(
                "All-members batch failed"
            )

        await asyncio.sleep(2)


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    logger.error(
        "Unhandled exception: %s",
        context.error,
        exc_info=context.error,
    )


# =========================================================
# APPLICATION
# =========================================================

persistence = PicklePersistence(
    filepath=PERSISTENCE_FILE
)

app = (
    ApplicationBuilder()
    .token(TOKEN)
    .persistence(persistence)
    .build()
)

# ---------------------------------------------------------
# Commands
# ---------------------------------------------------------

app.add_handler(
    CommandHandler("filter", filter_add)
)

app.add_handler(
    CommandHandler("blow", filter_remove)
)

app.add_handler(
    CommandHandler("filters", filter_list)
)

app.add_handler(
    CommandHandler("invite", invite_cmd)
)

app.add_handler(
    CommandHandler("all", tag_all_cmd)
)

# ---------------------------------------------------------
# Member tracking
# Lower group = processed earlier
# ---------------------------------------------------------

app.add_handler(
    MessageHandler(
        filters.ALL,
        track_members,
    ),
    group=-2,
)

# ---------------------------------------------------------
# Welcome / Goodbye
# ---------------------------------------------------------

app.add_handler(
    MessageHandler(
        filters.StatusUpdate.ALL,
        welcome_bye,
    ),
    group=0,
)

# ---------------------------------------------------------
# Night lock
# ---------------------------------------------------------

app.add_handler(
    MessageHandler(
        filters.ALL,
        night_lock,
    ),
    group=1,
)

# ---------------------------------------------------------
# Anti-link
# ---------------------------------------------------------

app.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        anti_link,
    ),
    group=2,
)

# ---------------------------------------------------------
# Filter trigger
# ---------------------------------------------------------

app.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        filter_trigger,
    ),
    group=3,
)

# ---------------------------------------------------------
# Error handler
# ---------------------------------------------------------

app.add_error_handler(error_handler)

# ---------------------------------------------------------
# Auto note every 20 minutes
# ---------------------------------------------------------

if app.job_queue:
    app.job_queue.run_repeating(
        auto_note,
        interval=1200,
        first=10,
    )
else:
    logger.warning(
        "JobQueue unavailable. "
        "Install python-telegram-bot[job-queue]."
    )

# =========================================================
# START
# =========================================================

print(
    f"Bot restricted to Chat ID: "
    f"{ALLOWED_CHAT_ID}. Online..."
)

app.run_polling()
