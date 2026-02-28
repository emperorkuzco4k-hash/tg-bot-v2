import os
import json
import logging
from typing import Dict, Any, List, Optional

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# =======================
# ENV
# =======================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0").strip() or "0")
ARCHIVE_CHANNEL_ID = int(os.getenv("ARCHIVE_CHANNEL_ID", "0").strip() or "0")

DB_PATH = "db.json"

CATS_MAIN = ["فیلم", "سریال", "کارتون", "انیمیشن", "فیلم ایرانی", "سریال ایرانی"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)
log = logging.getLogger("filmbaz")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set.")
if ADMIN_ID == 0:
    raise RuntimeError("ADMIN_ID is not set.")
if ARCHIVE_CHANNEL_ID == 0:
    raise RuntimeError("ARCHIVE_CHANNEL_ID is not set (e.g. -100...).")

# =======================
# DB helpers
# =======================
def load_db() -> Dict[str, Any]:
    if not os.path.exists(DB_PATH):
        return {"items": [], "categories": {c: [] for c in CATS_MAIN}}
    try:
        with open(DB_PATH, "r", encoding="utf-8") as f:
            db = json.load(f)
    except Exception:
        db = {}
    db.setdefault("items", [])
    db.setdefault("categories", {c: [] for c in CATS_MAIN})
    for c in CATS_MAIN:
        db["categories"].setdefault(c, [])
    return db

def save_db(db: Dict[str, Any]) -> None:
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def is_admin(update: Update) -> bool:
    return bool(update.effective_user and update.effective_user.id == ADMIN_ID)

def kb_main() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("فیلم"), KeyboardButton("سریال")],
            [KeyboardButton("کارتون"), KeyboardButton("انیمیشن")],
            [KeyboardButton("فیلم ایرانی"), KeyboardButton("سریال ایرانی")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )

# =======================
# Core send helpers
# =======================
async def send_by_file_id(chat_id: int, context: ContextTypes.DEFAULT_TYPE, item: Dict[str, Any]) -> None:
    media_type = item.get("media_type")
    file_id = item.get("file_id")
    caption = item.get("caption", "")

    if not media_type or not file_id:
        await context.bot.send_message(chat_id, "❌ فایل ناقصه یا ثبت نشده.")
        return

    if media_type == "video":
        await context.bot.send_video(chat_id=chat_id, video=file_id, caption=caption)
    elif media_type == "photo":
        await context.bot.send_photo(chat_id=chat_id, photo=file_id, caption=caption)
    elif media_type == "document":
        await context.bot.send_document(chat_id=chat_id, document=file_id, caption=caption)
    elif media_type == "audio":
        await context.bot.send_audio(chat_id=chat_id, audio=file_id, caption=caption)
    else:
        await context.bot.send_message(chat_id, "❌ نوع فایل پشتیبانی نمی‌شود.")

def media_from_channel_post(msg) -> Optional[Dict[str, str]]:
    # Telegram channel_post can contain: video/photo/document/audio
    if msg.video:
        return {"media_type": "video", "file_id": msg.video.file_id}
    if msg.photo:
        # biggest photo
        return {"media_type": "photo", "file_id": msg.photo[-1].file_id}
    if msg.document:
        return {"media_type": "document", "file_id": msg.document.file_id}
    if msg.audio:
        return {"media_type": "audio", "file_id": msg.audio.file_id}
    return None

# =======================
# Commands
# =======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("سلام 👋\nاز منوی زیر انتخاب کن:", reply_markup=kb_main())

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("pong 🟢")

async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text("این بخش فقط برای ادمین است.")
        return
    await update.message.reply_text(
        "✅ حالت ادمین فعاله.\n"
        "برای اضافه کردن محتوا:\n"
        "1) فایل/ویدیو رو داخل کانال آرشیو پست کن\n"
        "2) بعدش داخل ربات `/last` بزن تا ببینی ثبت شده\n\n"
        "فعلاً ذخیره‌سازی فایل داخل کانال انجام میشه و ربات فقط ثبت می‌کنه.",
    )

async def last_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = load_db()
    items: List[Dict[str, Any]] = db.get("items", [])
    if not items:
        await update.message.reply_text("هنوز چیزی از کانال ثبت نشده. یه فایل داخل کانال بفرست.")
        return

    # show last 10
    items = items[-10:][::-1]
    buttons = []
    for it in items:
        title = it.get("title") or "بدون عنوان"
        item_id = it.get("id")
        buttons.append([InlineKeyboardButton(f"📦 {title}", callback_data=f"get|{item_id}")])

    await update.message.reply_text(
        "آخرین فایل‌های ثبت شده:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = (q.data or "")
    if not data.startswith("get|"):
        return

    item_id = data.split("|", 1)[1]
    db = load_db()
    items: List[Dict[str, Any]] = db.get("items", [])
    item = next((x for x in items if str(x.get("id")) == str(item_id)), None)
    if not item:
        await q.message.reply_text("❌ این آیتم پیدا نشد.")
        return

    await send_by_file_id(q.message.chat_id, context, item)

# =======================
# Channel listener (IMPORTANT)
# =======================
async def on_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg:
        return

    # ensure it's our archive channel
    if msg.chat_id != ARCHIVE_CHANNEL_ID:
        return

    m = media_from_channel_post(msg)
    if not m:
        # ignore non-media posts
        return

    db = load_db()
    new_id = (db["items"][-1]["id"] + 1) if db["items"] else 1

    title = (msg.caption or "").strip()
    if not title:
        # fallback title based on message id
        title = f"Archive #{msg.message_id}"

    item = {
        "id": new_id,
        "title": title[:60],
        "caption": msg.caption or "",
        "channel_message_id": msg.message_id,
        "media_type": m["media_type"],
        "file_id": m["file_id"],
    }

    db["items"].append(item)
    save_db(db)

    log.info(f"Saved from channel: id={new_id} type={m['media_type']} msg_id={msg.message_id}")

# =======================
# Main
# =======================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("add", add_cmd))
    app.add_handler(CommandHandler("last", last_cmd))

    app.add_handler(CallbackQueryHandler(on_callback))

    # This is the key: listen to channel posts
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, on_channel_post))

    log.info("Bot is running...")
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query", "channel_post"]
    )

if __name__ == "__main__":
    main()
