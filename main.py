import os
import json
import time
import logging
from typing import Dict, Any, List, Optional, Tuple

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# =======================
# تنظیمات اصلی (فقط اینا رو عوض کن)
# =======================
ADMIN_ID = 1016313273         # ✅ آیدی عددی خودت
CHANNEL_ID = --1003740405524   # ✅ آیدی کانال پرایوت (با -100 شروع میشه)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in Railway Variables (Environment).")

DB_PATH = "db.json"

# اگر خواستی پیام‌های ویدیو بعد از مدت حذف بشن:
# 0 یعنی حذف نکن
TTL_SECONDS = 0

# دسته‌ها
CATS_MAIN = ["فیلم", "سریال", "کارتون", "انیمیشن", "فیلم ایرانی", "سریال ایرانی"]
ANIME_SUB = ["انیمیشن", "سریال انیمیشن"]

SINGLE_CATS = {"فیلم", "کارتون", "انیمیشن", "فیلم ایرانی"}
SERIES_CATS = {"سریال", "سریال ایرانی", "سریال انیمیشن"}

# Browse modes
MODE_NONE = "none"
MODE_ANIME_MENU = "anime_menu"
MODE_PICK_ITEM = "pick_item"
MODE_PICK_SEASON = "pick_season"

# =======================
# Logging
# =======================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)
log = logging.getLogger("bot")

# =======================
# DB
# =======================
def load_db() -> Dict[str, Any]:
    if not os.path.exists(DB_PATH):
        return {
            "categories": {},
            "_stats": {"item_requests": {}, "season_requests": {}},
            "_uploads": []  # فایل‌های اخیر کانال (برای /add)
        }
    try:
        with open(DB_PATH, "r", encoding="utf-8") as f:
            db = json.load(f)
    except Exception:
        db = {}

    if "categories" not in db or not isinstance(db.get("categories"), dict):
        db["categories"] = {}
    db.setdefault("_stats", {})
    db["_stats"].setdefault("item_requests", {})
    db["_stats"].setdefault("season_requests", {})
    db.setdefault("_uploads", [])

    # دسته‌ها را بساز
    for c in (CATS_MAIN + ["سریال انیمیشن"]):
        db["categories"].setdefault(c, {})

    # محدود کردن لیست آپلودها
    if isinstance(db.get("_uploads"), list) and len(db["_uploads"]) > 200:
        db["_uploads"] = db["_uploads"][-200:]

    return db

def save_db(db: Dict[str, Any]) -> None:
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def ensure_db() -> None:
    db = load_db()
    save_db(db)

def is_admin(update: Update) -> bool:
    return bool(update.effective_user and update.effective_user.id == ADMIN_ID)

# =======================
# Keyboards
# =======================
def kb_main():
    return ReplyKeyboardMarkup(
        [
            ["فیلم", "سریال"],
            ["کارتون", "انیمیشن"],
            ["فیلم ایرانی", "سریال ایرانی"],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )

def kb_anime_menu():
    return ReplyKeyboardMarkup(
        [
            ["انیمیشن", "سریال انیمیشن"],
            ["⬅️ برگشت"],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )

def kb_list(items: List[str]):
    rows = [[x] for x in items[:30]]
    rows.append(["⬅️ برگشت"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True)

def kb_seasons(seasons: List[int]):
    rows = []
    buf = []
    for s in seasons:
        buf.append(f"فصل {s}")
        if len(buf) == 2:
            rows.append(buf)
            buf = []
    if buf:
        rows.append(buf)
    rows.append(["⬅️ برگشت"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True)

def kb_add_cats():
    return ReplyKeyboardMarkup(
        [
            ["فیلم", "سریال"],
            ["کارتون", "انیمیشن"],
            ["سریال انیمیشن"],
            ["فیلم ایرانی", "سریال ایرانی"],
            ["⬅️ برگشت"],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )

def kb_yes_no_last():
    return ReplyKeyboardMarkup(
        [["✅ ثبت آخرین فایل کانال"], ["❌ کنسل"]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )

# =======================
# Stats
# =======================
def bump_item_stat(cat: str, name: str):
    db = load_db()
    key = f"{cat}|{name}"
    db["_stats"]["item_requests"][key] = int(db["_stats"]["item_requests"].get(key, 0)) + 1
    save_db(db)

def bump_season_stat(cat: str, name: str, season: int):
    db = load_db()
    key = f"{cat}|{name}|{season}"
    db["_stats"]["season_requests"][key] = int(db["_stats"]["season_requests"].get(key, 0)) + 1
    save_db(db)

# =======================
# Search
# =======================
def search_items(q: str) -> List[Tuple[str, str]]:
    db = load_db()
    ql = q.lower().strip()
    out = []
    for cat, items in db["categories"].items():
        for name in items.keys():
            if ql in name.lower():
                out.append((cat, name))
    return out[:10]

def search_kb(results: List[Tuple[str, str]]):
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(f"{name} | {cat}", callback_data=f"search|{cat}|{name}")]
         for cat, name in results]
    )

# =======================
# ارسال محتوا
# =======================
async def send_single(chat_id: int, context: ContextTypes.DEFAULT_TYPE, cat: str, name: str):
    db = load_db()
    item = db["categories"].get(cat, {}).get(name)
    if not item:
        await context.bot.send_message(chat_id, "❌ مورد پیدا نشد.")
        return

    bump_item_stat(cat, name)

    file_id = item["file_id"]
    media = item.get("media", "video")
    title = item.get("title") or name
    caption = f"🎬 {cat}\n📌 {name}\n📝 {title}"

    if media == "photo":
        await context.bot.send_photo(chat_id, photo=file_id, caption=caption)
    else:
        await context.bot.send_video(chat_id, video=file_id, caption=caption)

async def send_episode(chat_id: int, context: ContextTypes.DEFAULT_TYPE, cat: str, name: str, season: int, ep: int):
    db = load_db()
    entry = db["categories"].get(cat, {}).get(name)
    if not entry or entry.get("type") != "series":
        await context.bot.send_message(chat_id, "❌ سریال پیدا نشد.")
        return

    season_data = entry.get("seasons", {}).get(str(season))
    if not season_data or str(ep) not in season_data:
        await context.bot.send_message(chat_id, "❌ این قسمت موجود نیست.")
        return

    bump_item_stat(cat, name)
    bump_season_stat(cat, name, season)

    ep_data = season_data[str(ep)]
    file_id = ep_data["file_id"]
    title = ep_data.get("title") or f"S{season:02d}E{ep:02d}"
    media = ep_data.get("media", "video")

    caption = f"🎬 {cat}\n{name}\nفصل {season} - قسمت {ep}\n{title}"

    if media == "photo":
        await context.bot.send_photo(chat_id, photo=file_id, caption=caption)
    else:
        await context.bot.send_video(chat_id, video=file_id, caption=caption)

# =======================
# کانال: دریافت file_id از پست‌های کانال
# =======================
def extract_file_from_message(msg) -> Tuple[Optional[str], Optional[str]]:
    # kind, file_id
    if msg.video:
        return "video", msg.video.file_id
    if msg.document:
        return "document", msg.document.file_id
    if msg.photo:
        return "photo", msg.photo[-1].file_id
    if msg.audio:
        return "audio", msg.audio.file_id
    return None, None

async def on_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg:
        return

    # فقط کانال خودمون
    if msg.chat_id != CHANNEL_ID:
        return

    kind, file_id = extract_file_from_message(msg)
    if not file_id:
        return

    db = load_db()
    db["_uploads"].append({
        "ts": int(time.time()),
        "chat_id": msg.chat_id,
        "message_id": msg.message_id,
        "kind": kind,
        "file_id": file_id,
        "caption": (msg.caption or "")[:200],
    })
    save_db(db)

    # برای /add راحت‌تر: آخرین فایل رو تو bot_data نگه دار
    context.bot_data["last_channel_file"] = {
        "kind": kind,
        "file_id": file_id,
        "message_id": msg.message_id,
        "ts": int(time.time()),
    }

    log.info(f"[CHANNEL] saved kind={kind} file_id={file_id} message_id={msg.message_id}")

# =======================
# /start و منو
# =======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_db()
    context.user_data["mode"] = MODE_NONE
    context.user_data.pop("picked_cat", None)
    context.user_data.pop("picked_item", None)
    await update.message.reply_text("سلام 👋\nاز منوی زیر انتخاب کن:", reply_markup=kb_main())

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("pong 🟢")

async def last(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.bot_data.get("last_channel_file")
    if not data:
        await update.message.reply_text("هنوز چیزی از کانال ثبت نشده. یه فایل داخل کانال بفرست.")
        return
    await update.message.reply_text(
        f"آخرین فایل کانال ثبت شده ✅\n"
        f"نوع: {data.get('kind')}\n"
        f"message_id: {data.get('message_id')}\n"
        f"ts: {data.get('ts')}\n"
        f"file_id:\n{data.get('file_id')}"
    )

async def setlast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /setlast <file_id>
    if not is_admin(update):
        await update.message.reply_text("این بخش فقط برای ادمین است.")
        return
    parts = (update.message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await update.message.reply_text("فرمت درست:\n/setlast <file_id>")
        return
    file_id = parts[1].strip()
    context.bot_data["last_channel_file"] = {
        "kind": "video",
        "file_id": file_id,
        "message_id": None,
        "ts": int(time.time()),
    }
    await update.message.reply_text("✅ آخرین فایل (last) ست شد.")

# =======================
# متن‌ها (Browse)
# =======================
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_db()
    text = (update.message.text or "").strip()

    # back
    if text == "⬅️ برگشت":
        context.user_data["mode"] = MODE_NONE
        context.user_data.pop("picked_cat", None)
        context.user_data.pop("picked_item", None)
        await update.message.reply_text("منوی اصلی 👇", reply_markup=kb_main())
        return

    # anime menu open
    if text == "انیمیشن":
        context.user_data["mode"] = MODE_ANIME_MENU
        await update.message.reply_text("🎞 یکی رو انتخاب کن:", reply_markup=kb_anime_menu())
        return

    # anime submenu select
    if context.user_data.get("mode") == MODE_ANIME_MENU and text in ANIME_SUB:
        cat = text
        db = load_db()
        items = sorted(db["categories"][cat].keys(), key=lambda x: x.lower())
        if not items:
            await update.message.reply_text("فعلاً چیزی اضافه نشده.", reply_markup=kb_main())
            context.user_data["mode"] = MODE_NONE
            return
        context.user_data["mode"] = MODE_PICK_ITEM
        context.user_data["picked_cat"] = cat
        await update.message.reply_text(f"📌 {cat} رو انتخاب کن:", reply_markup=kb_list(items))
        return

    # pick main category (except "انیمیشن" already handled)
    if text in CATS_MAIN and text != "انیمیشن":
        cat = text
        db = load_db()
        items = sorted(db["categories"][cat].keys(), key=lambda x: x.lower())
        if not items:
            await update.message.reply_text("فعلاً چیزی اضافه نشده.", reply_markup=kb_main())
            context.user_data["mode"] = MODE_NONE
            return
        context.user_data["mode"] = MODE_PICK_ITEM
        context.user_data["picked_cat"] = cat
        await update.message.reply_text(f"📌 {cat} رو انتخاب کن:", reply_markup=kb_list(items))
        return

    # smart search (free text)
    if len(text) >= 3 and text not in CATS_MAIN and text not in ANIME_SUB:
        results = search_items(text)
        if results:
            await update.message.reply_text("🔎 نتیجه جستجو:", reply_markup=search_kb(results))
            return

    # pick item
    if context.user_data.get("mode") == MODE_PICK_ITEM:
        cat = context.user_data.get("picked_cat")
        if not cat:
            context.user_data["mode"] = MODE_NONE
            await update.message.reply_text("از منو شروع کن.", reply_markup=kb_main())
            return

        db = load_db()
        if text not in db["categories"][cat]:
            items = sorted(db["categories"][cat].keys(), key=lambda x: x.lower())
            await update.message.reply_text("از دکمه‌ها انتخاب کن.", reply_markup=kb_list(items))
            return

        # single
        if cat in SINGLE_CATS:
            context.user_data["mode"] = MODE_NONE
            await send_single(update.message.chat_id, context, cat, text)
            return

        # series -> pick season
        if cat in SERIES_CATS:
            entry = db["categories"][cat][text]
            seasons = sorted([int(k) for k in entry.get("seasons", {}).keys() if k.isdigit()])
            if not seasons:
                context.user_data["mode"] = MODE_NONE
                await update.message.reply_text("برای این سریال فصلی ثبت نشده.", reply_markup=kb_main())
                return
            context.user_data["mode"] = MODE_PICK_SEASON
            context.user_data["picked_item"] = text
            await update.message.reply_text("فصل رو انتخاب کن:", reply_markup=kb_seasons(seasons))
            return

    # pick season
    if context.user_data.get("mode") == MODE_PICK_SEASON:
        cat = context.user_data.get("picked_cat")
        name = context.user_data.get("picked_item")
        if not cat or not name:
            context.user_data["mode"] = MODE_NONE
            await update.message.reply_text("از منو شروع کن.", reply_markup=kb_main())
            return

        if text.startswith("فصل"):
            try:
                season = int(text.replace("فصل", "").strip())
            except ValueError:
                await update.message.reply_text("فصل نامعتبر.", reply_markup=kb_main())
                context.user_data["mode"] = MODE_NONE
                return

            # فعلاً قسمت 1 رو می‌فرستیم
            await send_episode(update.message.chat_id, context, cat, name, season, 1)

            context.user_data["mode"] = MODE_NONE
            context.user_data.pop("picked_cat", None)
            context.user_data.pop("picked_item", None)
            return

        await update.message.reply_text("از دکمه‌های فصل انتخاب کن.", reply_markup=kb_main())
        return

    await update.message.reply_text("از منو یکی رو انتخاب کن 👇", reply_markup=kb_main())

# =======================
# Inline callbacks
# =======================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_db()
    q = update.callback_query
    await q.answer()

    data = (q.data or "").split("|")
    if not data:
        return

    if data[0] == "search" and len(data) >= 3:
        cat = data[1]
        name = "|".join(data[2:])  # safe
        if cat in SINGLE_CATS:
            await send_single(q.message.chat_id, context, cat, name)
            return
        if cat in SERIES_CATS:
            db = load_db()
            entry = db["categories"].get(cat, {}).get(name)
            if not entry:
                await context.bot.send_message(q.message.chat_id, "❌ پیدا نشد.")
                return
            seasons = sorted([int(k) for k in entry.get("seasons", {}).keys() if k.isdigit()])
            if not seasons:
                await context.bot.send_message(q.message.chat_id, "❌ فصل ندارد.")
                return
            context.user_data["mode"] = MODE_PICK_SEASON
            context.user_data["picked_cat"] = cat
            context.user_data["picked_item"] = name
            await context.bot.send_message(q.message.chat_id, f"📺 {name}\nفصل را انتخاب کن:", reply_markup=kb_seasons(seasons))
            return

# =======================
# /add (admin) - فقط ثبت اطلاعات (فعلاً فایل از کانال می‌گیریم)
# =======================
ASK_CAT, ASK_NAME, ASK_TYPE, ASK_SEASON, ASK_EP, ASK_TITLE, ASK_USE_LAST = range(7)

async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_db()
    if not is_admin(update):
        await update.message.reply_text("این بخش فقط برای ادمین است.")
        return ConversationHandler.END
    context.user_data["add"] = {}
    await update.message.reply_text("کدوم دسته؟", reply_markup=kb_add_cats())
    return ASK_CAT

async def add_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cat = (update.message.text or "").strip()
    if cat == "⬅️ برگشت":
        await update.message.reply_text("کنسل شد.", reply_markup=kb_main())
        return ConversationHandler.END

    if cat not in (CATS_MAIN + ["سریال انیمیشن"]):
        await update.message.reply_text("از دکمه‌ها انتخاب کن.", reply_markup=kb_add_cats())
        return ASK_CAT

    context.user_data["add"]["cat"] = cat
    await update.message.reply_text("اسم آیتم چیه؟ (مثلاً Breaking Bad)")
    return ASK_NAME

async def add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = (update.message.text or "").strip()
    if not name:
        await update.message.reply_text("اسم خالی نباشه.")
        return ASK_NAME

    cat = context.user_data["add"]["cat"]
    context.user_data["add"]["name"] = name

    # تشخیص سریال یا تک
    if cat in SERIES_CATS:
        context.user_data["add"]["type"] = "series"
        await update.message.reply_text("شماره فصل؟ (مثلاً 1)")
        return ASK_SEASON
    else:
        context.user_data["add"]["type"] = "single"
        await update.message.reply_text("عنوان/توضیح (اختیاری). اگر نمیخوای، همین یه نقطه بفرست: .")
        return ASK_TITLE

async def add_season(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = (update.message.text or "").strip()
    try:
        season = int(t)
        if season < 1:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("فصل باید عدد مثبت باشه. دوباره بفرست.")
        return ASK_SEASON

    context.user_data["add"]["season"] = season
    await update.message.reply_text("شماره قسمت؟ (مثلاً 1)")
    return ASK_EP

async def add_ep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = (update.message.text or "").strip()
    try:
        ep = int(t)
        if ep < 0:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("قسمت باید عدد باشه (0 هم می‌تونه پوستر فصل باشه). دوباره بفرست.")
        return ASK_EP

    context.user_data["add"]["ep"] = ep
    await update.message.reply_text("عنوان/توضیح (اختیاری). اگر نمیخوای، همین یه نقطه بفرست: .")
    return ASK_TITLE

async def add_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = (update.message.text or "").strip()
    if title == ".":
        title = ""

    context.user_data["add"]["title"] = title

    # حالا از آخرین فایل کانال استفاده کنیم
    last_data = context.bot_data.get("last_channel_file")
    if not last_data:
        await update.message.reply_text(
            "هنوز هیچ فایلی از کانال ثبت نشده.\n"
            "اول داخل کانال یه ویدیو/فایل بفرست، بعد دوباره /add رو بزن."
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "میخوای همین «آخرین فایل کانال» رو برای این آیتم ثبت کنم؟",
        reply_markup=kb_yes_no_last()
    )
    return ASK_USE_LAST

async def add_use_last(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = (update.message.text or "").strip()
    if t == "❌ کنسل":
        await update.message.reply_text("کنسل شد.", reply_markup=kb_main())
        return ConversationHandler.END

    if t != "✅ ثبت آخرین فایل کانال":
        await update.message.reply_text("از دکمه‌ها انتخاب کن.", reply_markup=kb_yes_no_last())
        return ASK_USE_LAST

    last_data = context.bot_data.get("last_channel_file")
    if not last_data:
        await update.message.reply_text("آخرین فایل پیدا نشد. دوباره فایل داخل کانال بفرست.", reply_markup=kb_main())
        return ConversationHandler.END

    file_id = last_data.get("file_id")
    kind = last_data.get("kind") or "video"
    media = "video" if kind in ("video", "document") else "photo"

    db = load_db()
    cat = context.user_data["add"]["cat"]
    name = context.user_data["add"]["name"]
    title = context.user_data["add"].get("title", "")

    if context.user_data["add"]["type"] == "single":
        db["categories"].setdefault(cat, {})
        db["categories"][cat][name] = {
            "type": "single",
            "media": media,
            "file_id": file_id,
            "title": title,
            "source": "channel",
        }
        save_db(db)
        await update.message.reply_text(f"✅ ثبت شد: {cat} / {name}", reply_markup=kb_main())
        return ConversationHandler.END

    # series
    season = int(context.user_data["add"]["season"])
    ep = int(context.user_data["add"]["ep"])
    db["categories"].setdefault(cat, {})
    if name not in db["categories"][cat]:
        db["categories"][cat][name] = {"type": "series", "seasons": {}}

    db["categories"][cat][name].setdefault("seasons", {})
    db["categories"][cat][name]["seasons"].setdefault(str(season), {})
    db["categories"][cat][name]["seasons"][str(season)][str(ep)] = {
        "media": media,
        "file_id": file_id,
        "title": title,
        "source": "channel",
    }
    save_db(db)

    await update.message.reply_text(f"✅ ثبت شد: {cat} / {name} / فصل {season} / قسمت {ep}", reply_markup=kb_main())
    return ConversationHandler.END

async def add_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("کنسل شد.", reply_markup=kb_main())
    return ConversationHandler.END

# =======================
# main
# =======================
def main():
    ensure_db()
    app = Application.builder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("last", last))
    app.add_handler(CommandHandler("setlast", setlast))

    # Channel posts (برای گرفتن file_id)
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, on_channel_post))

    # /add admin
    add_conv = ConversationHandler(
        entry_points=[CommandHandler("add", add_start)],
        states={
            ASK_CAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_cat)],
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_name)],
            ASK_SEASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_season)],
            ASK_EP: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_ep)],
            ASK_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_title)],
            ASK_USE_LAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_use_last)],
        },
        fallbacks=[CommandHandler("cancel", add_cancel)],
        allow_reentry=True,
    )
    app.add_handler(add_conv)

    # Callbacks + Text
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    log.info("Bot is running...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
