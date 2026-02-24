import os
import json
import logging
from typing import Dict, Any, List, Tuple, Optional

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
# تنظیمات
# =======================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# 👇 فقط اینو عوض کن (آیدی تلگرام خودت)
ADMIN_ID = 1016313273

DB_PATH = "db.json"

CATS_MAIN = ["فیلم", "سریال", "کارتون", "انیمیشن", "فیلم ایرانی", "سریال ایرانی"]
ANIME_SUB = ["انیمیشن", "سریال انیمیشن"]

SINGLE_CATS = {"فیلم", "کارتون", "انیمیشن", "فیلم ایرانی"}
SERIES_CATS = {"سریال", "سریال ایرانی", "سریال انیمیشن"}

MODE_NONE = "none"
MODE_ANIME_MENU = "anime_menu"
MODE_PICK_ITEM = "pick_item"
MODE_PICK_SEASON = "pick_season"

# =======================
# لاگ
# =======================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)
logger = logging.getLogger("bot")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in environment variables (Railway Variables).")

# =======================
# DB
# =======================
def load_db() -> Dict[str, Any]:
    if not os.path.exists(DB_PATH):
        return {"categories": {}, "_stats": {"requests": {}}}

    try:
        with open(DB_PATH, "r", encoding="utf-8") as f:
            db = json.load(f)
    except Exception:
        db = {}

    if "categories" not in db or not isinstance(db.get("categories"), dict):
        db["categories"] = {}

    db.setdefault("_stats", {})
    db["_stats"].setdefault("requests", {})

    # ensure categories exist
    for c in (CATS_MAIN + ["سریال انیمیشن"]):
        db["categories"].setdefault(c, {})

    return db


def save_db(db: Dict[str, Any]) -> None:
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def ensure_db() -> None:
    db = load_db()
    save_db(db)


def bump_stat(key: str) -> None:
    db = load_db()
    db["_stats"]["requests"][key] = int(db["_stats"]["requests"].get(key, 0)) + 1
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
    rows = [[x] for x in items[:40]]
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
        [
            [InlineKeyboardButton(f"{name} | {cat}", callback_data=f"search|{cat}|{name}")]
            for cat, name in results
        ]
    )


def ep_nav_kb(cat: str, name: str, season: int, ep: int, eps: List[int]):
    row = []
    if ep > eps[0]:
        row.append(InlineKeyboardButton("⬅ قسمت قبلی", callback_data=f"ep|{cat}|{name}|{season}|{ep-1}"))
    if ep < eps[-1]:
        row.append(InlineKeyboardButton("➡ قسمت بعدی", callback_data=f"ep|{cat}|{name}|{season}|{ep+1}"))

    buttons = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("📺 انتخاب فصل", callback_data=f"pickseason|{cat}|{name}")])
    return InlineKeyboardMarkup(buttons)

# =======================
# Send media helpers
# =======================
async def send_single(chat_id: int, context: ContextTypes.DEFAULT_TYPE, cat: str, name: str):
    db = load_db()
    item = db["categories"].get(cat, {}).get(name)
    if not item:
        await context.bot.send_message(chat_id, "❌ مورد پیدا نشد.")
        return

    bump_stat(f"{cat}|{name}")
    file_id = item["file_id"]
    media = item.get("media", "video")
    title = item.get("title") or ""

    caption = f"🎬 {cat}\n📌 {name}"
    if title:
        caption += f"\n📝 {title}"

    if media == "photo":
        await context.bot.send_photo(chat_id, photo=file_id, caption=caption)
    elif media == "document":
        await context.bot.send_document(chat_id, document=file_id, caption=caption)
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

    eps = sorted([int(k) for k in season_data.keys() if k.isdigit()])
    if not eps:
        await context.bot.send_message(chat_id, "❌ برای این فصل قسمتی ثبت نشده.")
        return

    bump_stat(f"{cat}|{name}|S{season}|E{ep}")

    ep_data = season_data[str(ep)]
    file_id = ep_data["file_id"]
    media = ep_data.get("media", "video")
    title = ep_data.get("title") or f"S{season:02d}E{ep:02d}"

    caption = f"🎬 {cat}\n{name}\nفصل {season} - قسمت {ep}\n{title}"

    kb = ep_nav_kb(cat, name, season, ep, eps)

    if media == "photo":
        await context.bot.send_photo(chat_id, photo=file_id, caption=caption, reply_markup=kb)
    elif media == "document":
        await context.bot.send_document(chat_id, document=file_id, caption=caption, reply_markup=kb)
    else:
        await context.bot.send_video(chat_id, video=file_id, caption=caption, reply_markup=kb)

# =======================
# /start + /ping + /myid
# =======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_db()
    context.user_data["mode"] = MODE_NONE
    context.user_data.pop("picked_cat", None)
    context.user_data.pop("picked_item", None)
    await update.message.reply_text("سلام 👋\nاز منوی زیر انتخاب کن:", reply_markup=kb_main())


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("pong 🟢")


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id if update.effective_user else None
    await update.message.reply_text(f"Your ID: {uid}")

# =======================
# Browse text handler
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

    # open anime menu
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

    # pick a main category (except anime which already handled)
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

    # smart search (if typed)
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

        # series -> seasons list
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

        context.user_data["mode"] = MODE_NONE
        await update.message.reply_text("این بخش تنظیم نشده.", reply_markup=kb_main())
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
                context.user_data["mode"] = MODE_NONE
                await update.message.reply_text("فصل نامعتبر.", reply_markup=kb_main())
                return

            # send episode 1 by default
            context.user_data["mode"] = MODE_NONE
            context.user_data.pop("picked_cat", None)
            context.user_data.pop("picked_item", None)
            await send_episode(update.message.chat_id, context, cat, name, season, 1)
            return

        await update.message.reply_text("از دکمه‌های فصل انتخاب کن.", reply_markup=kb_main())
        return

    # default
    await update.message.reply_text("از منو یکی رو انتخاب کن 👇", reply_markup=kb_main())

# =======================
# Inline callbacks
# =======================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_db()
    q = update.callback_query
    await q.answer()

    parts = (q.data or "").split("|")
    if not parts:
        return

    if parts[0] == "search" and len(parts) >= 3:
        cat = parts[1]
        name = "|".join(parts[2:])  # just in case
        if cat in SINGLE_CATS:
            await send_single(q.message.chat_id, context, cat, name)
        elif cat in SERIES_CATS:
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
            await context.bot.send_message(
                q.message.chat_id, f"📺 {name}\nفصل را انتخاب کن:", reply_markup=kb_seasons(seasons)
            )
        return

    if parts[0] == "ep" and len(parts) >= 5:
        cat = parts[1]
        name = parts[2]
        season = int(parts[3])
        ep = int(parts[4])
        await send_episode(q.message.chat_id, context, cat, name, season, ep)
        return

    if parts[0] == "pickseason" and len(parts) >= 3:
        cat = parts[1]
        name = parts[2]
        db = load_db()
        entry = db["categories"].get(cat, {}).get(name)
        if not entry:
            await context.bot.send_message(q.message.chat_id, "❌ پیدا نشد.")
            return
        seasons = sorted([int(k) for k in entry.get("seasons", {}).keys() if k.isdigit()])
        context.user_data["mode"] = MODE_PICK_SEASON
        context.user_data["picked_cat"] = cat
        context.user_data["picked_item"] = name
        await context.bot.send_message(
            q.message.chat_id, f"📺 {name}\nفصل را انتخاب کن:", reply_markup=kb_seasons(seasons)
        )
        return

# =======================
# Admin /add conversation
# آپلود = کاربر (ادمین) فایل رو می‌فرسته و ما file_id رو ذخیره می‌کنیم
# =======================
ASK_CAT, ASK_NAME, ASK_SEASON, ASK_EP, ASK_TITLE, ASK_FILE = range(6)

def kb_add_cat():
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

async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_db()
    if not is_admin(update):
        await update.message.reply_text("این بخش فقط برای ادمین است.")
        return ConversationHandler.END

    context.user_data["add"] = {}
    await update.message.reply_text("کدوم دسته؟", reply_markup=kb_add_cat())
    return ASK_CAT

async def add_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cat = (update.message.text or "").strip()
    if cat == "⬅️ برگشت":
        await update.message.reply_text("کنسل شد.", reply_markup=kb_main())
        return ConversationHandler.END

    if cat not in (CATS_MAIN + ["سریال انیمیشن"]):
        await update.message.reply_text("از دکمه‌ها انتخاب کن.", reply_markup=kb_add_cat())
        return ASK_CAT

    context.user_data["add"]["cat"] = cat
    await update.message.reply_text("اسم آیتم چیه؟ (مثلاً Breaking Bad)")
    return ASK_NAME

async def add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = (update.message.text or "").strip()
    if not name:
        await update.message.reply_text("اسم معتبر بده.")
        return ASK_NAME

    cat = context.user_data["add"]["cat"]
    context.user_data["add"]["name"] = name

    if cat in SERIES_CATS:
        await update.message.reply_text("شماره فصل؟ (مثلاً 1)")
        return ASK_SEASON
    else:
        # single
        await update.message.reply_text("عنوان/توضیح کوتاه (اختیاری). اگر نداری یه نقطه بفرست.")
        return ASK_TITLE

async def add_season(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()
    try:
        season = int(txt)
        if season < 1:
            raise ValueError
    except ValueError:
        await update.message.reply_text("شماره فصل باید عدد مثبت باشه. دوباره بفرست.")
        return ASK_SEASON

    context.user_data["add"]["season"] = season
    await update.message.reply_text("شماره قسمت؟ (مثلاً 1)")
    return ASK_EP

async def add_ep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()
    try:
        ep = int(txt)
        if ep < 1:
            raise ValueError
    except ValueError:
        await update.message.reply_text("شماره قسمت باید عدد مثبت باشه. دوباره بفرست.")
        return ASK_EP

    context.user_data["add"]["ep"] = ep
    await update.message.reply_text("عنوان/توضیح کوتاه (اختیاری). اگر نداری یه نقطه بفرست.")
    return ASK_TITLE

async def add_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = (update.message.text or "").strip()
    if title == ".":
        title = ""
    context.user_data["add"]["title"] = title

    await update.message.reply_text(
        "حالا فایل رو بفرست (ویدیو / داکیومنت / عکس).\n"
        "✅ همینجا فایل رو Send کن."
    )
    return ASK_FILE

def extract_file_id_and_media(update: Update) -> Tuple[Optional[str], Optional[str]]:
    m = update.message
    if not m:
        return None, None

    if m.video:
        return m.video.file_id, "video"
    if m.document:
        return m.document.file_id, "document"
    if m.photo and len(m.photo) > 0:
        return m.photo[-1].file_id, "photo"

    return None, None

async def add_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file_id, media = extract_file_id_and_media(update)
    if not file_id:
        await update.message.reply_text("فایل معتبر نیست. لطفاً ویدیو/فایل/عکس بفرست.")
        return ASK_FILE

    data = context.user_data["add"]
    cat = data["cat"]
    name = data["name"]
    title = data.get("title", "")

    db = load_db()

    # series
    if cat in SERIES_CATS:
        season = int(data["season"])
        ep = int(data["ep"])

        db["categories"].setdefault(cat, {})
        db["categories"][cat].setdefault(name, {"type": "series", "seasons": {}})

        entry = db["categories"][cat][name]
        entry["type"] = "series"
        entry.setdefault("seasons", {})
        entry["seasons"].setdefault(str(season), {})
        entry["seasons"][str(season)][str(ep)] = {
            "file_id": file_id,
            "media": media,
            "title": title,
        }

        save_db(db)
        await update.message.reply_text(
            f"✅ ذخیره شد:\n{cat} / {name}\nفصل {season} - قسمت {ep}",
            reply_markup=kb_main(),
        )
    else:
        # single
        db["categories"].setdefault(cat, {})
        db["categories"][cat][name] = {
            "type": "single",
            "file_id": file_id,
            "media": media,
            "title": title,
        }

        save_db(db)
        await update.message.reply_text(
            f"✅ ذخیره شد:\n{cat} / {name}",
            reply_markup=kb_main(),
        )

    context.user_data.pop("add", None)
    return ConversationHandler.END

async def cancel_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("add", None)
    await update.message.reply_text("کنسل شد.", reply_markup=kb_main())
    return ConversationHandler.END

# =======================
# Main
# =======================
def main():
    ensure_db()

    app = Application.builder().token(BOT_TOKEN).build()

    # commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("myid", myid))

    # /add conversation
    add_conv = ConversationHandler(
        entry_points=[CommandHandler("add", add_start)],
        states={
            ASK_CAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_cat)],
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_name)],
            ASK_SEASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_season)],
            ASK_EP: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_ep)],
            ASK_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_title)],
            ASK_FILE: [MessageHandler(~filters.COMMAND, add_file)],
        },
        fallbacks=[CommandHandler("cancel", cancel_add)],
        allow_reentry=True,
    )
    app.add_handler(add_conv)

    # callbacks
    app.add_handler(CallbackQueryHandler(on_callback))

    # text browsing
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    logger.info("Bot is running...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
