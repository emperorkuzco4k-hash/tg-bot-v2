import os
import json
import time
import re
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
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# =======================
# ENV
# =======================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int((os.getenv("ADMIN_ID", "0").strip() or "0"))
ARCHIVE_CHANNEL_ID = int((os.getenv("ARCHIVE_CHANNEL_ID", "0").strip() or "0"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set.")
if not ADMIN_ID:
    raise RuntimeError("ADMIN_ID is not set.")
if not ARCHIVE_CHANNEL_ID:
    raise RuntimeError("ARCHIVE_CHANNEL_ID is not set (e.g. -100...).")

DB_PATH = "db.json"

# دسته‌ها
CATS_MAIN = ["فیلم", "سریال", "کارتون", "انیمیشن", "فیلم ایرانی", "سریال ایرانی"]
ANIME_SUB = ["انیمیشن", "سریال انیمیشن"]

SINGLE_CATS = {"فیلم", "کارتون", "انیمیشن", "فیلم ایرانی"}
SERIES_CATS = {"سریال", "سریال ایرانی", "سریال انیمیشن"}

# حالت‌ها
MODE_NONE = "none"
MODE_ANIME_MENU = "anime_menu"
MODE_PICK_ITEM = "pick_item"
MODE_PICK_SEASON = "pick_season"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("filmbaz")

# =======================
# DB
# =======================
def load_db() -> Dict[str, Any]:
    if not os.path.exists(DB_PATH):
        return {"categories": {}, "_uploads": []}
    try:
        with open(DB_PATH, "r", encoding="utf-8") as f:
            db = json.load(f)
    except Exception:
        db = {}
    db.setdefault("categories", {})
    db.setdefault("_uploads", [])
    for c in (CATS_MAIN + ["سریال انیمیشن"]):
        db["categories"].setdefault(c, {})
    if len(db["_uploads"]) > 200:
        db["_uploads"] = db["_uploads"][-200:]
    return db

def save_db(db: Dict[str, Any]) -> None:
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def ensure_db():
    save_db(load_db())

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
    rows, buf = [], []
    for s in seasons:
        buf.append(f"فصل {s}")
        if len(buf) == 2:
            rows.append(buf); buf = []
    if buf:
        rows.append(buf)
    rows.append(["⬅️ برگشت"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True)

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
# Helpers: media extract
# =======================
def extract_media(msg) -> Tuple[Optional[str], Optional[str]]:
    # media_type, file_id
    if msg.video:
        return "video", msg.video.file_id
    if msg.document:
        return "document", msg.document.file_id
    if msg.photo:
        return "photo", msg.photo[-1].file_id
    if msg.audio:
        return "audio", msg.audio.file_id
    return None, None

# =======================
# Hashtag parser
# =======================
STRUCT_TAGS = {
    "فیلم","سریال","کارتون","انیمیشن","ایرانی","فیلم_ایرانی","سریال_ایرانی",
    "film","series","cartoon","anime","iran","iranian",
}

def norm_tag(t: str) -> str:
    t = t.strip()
    if t.startswith("#"):
        t = t[1:]
    return t.strip()

def detect_category(tags: List[str]) -> str:
    tl = [x.lower() for x in tags]
    # فارسی
    if "فیلم_ایرانی" in tags or ("فیلم" in tags and "ایرانی" in tags):
        return "فیلم ایرانی"
    if "سریال_ایرانی" in tags or ("سریال" in tags and "ایرانی" in tags):
        return "سریال ایرانی"
    if "سریال" in tags:
        return "سریال"
    if "فیلم" in tags:
        return "فیلم"
    if "کارتون" in tags:
        return "کارتون"
    if "انیمیشن" in tags and "سریال" in tags:
        return "سریال انیمیشن"
    if "سریال_انیمیشن" in tags or "سریالانیمیشن" in tl:
        return "سریال انیمیشن"
    if "انیمیشن" in tags:
        return "انیمیشن"
    # انگلیسی
    if "iranian" in tl and "film" in tl:
        return "فیلم ایرانی"
    if "iranian" in tl and "series" in tl:
        return "سریال ایرانی"
    if "series" in tl:
        return "سریال"
    if "film" in tl:
        return "فیلم"
    if "cartoon" in tl:
        return "کارتون"
    if "anime" in tl and "series" in tl:
        return "سریال انیمیشن"
    if "anime" in tl:
        return "انیمیشن"
    # پیش‌فرض
    return "سریال"

def detect_season_episode(tags: List[str]) -> Tuple[Optional[int], Optional[int]]:
    # S01E02
    for t in tags:
        m = re.match(r"(?i)^s(\d{1,2})e(\d{1,3})$", t)
        if m:
            return int(m.group(1)), int(m.group(2))
    # فصل1 / قسمت2
    season = None
    ep = None
    for t in tags:
        m1 = re.match(r"^فصل(\d{1,2})$", t)
        if m1:
            season = int(m1.group(1))
        m2 = re.match(r"^قسمت(\d{1,3})$", t)
        if m2:
            ep = int(m2.group(1))
    return season, ep

def detect_name(tags: List[str]) -> Optional[str]:
    # اولین تگی که ساختاری نیست، اسم محسوب می‌کنیم
    for t in tags:
        tl = t.lower()
        if tl in STRUCT_TAGS:
            continue
        if re.match(r"(?i)^s\d{1,2}e\d{1,3}$", t):
            continue
        if re.match(r"^فصل\d{1,2}$", t) or re.match(r"^قسمت\d{1,3}$", t):
            continue
        return t
    return None

def parse_caption(caption: str) -> Tuple[str, str, Optional[int], Optional[int]]:
    # returns cat, name, season, ep
    tags = [norm_tag(x) for x in re.findall(r"#\S+", caption or "")]
    cat = detect_category(tags)
    season, ep = detect_season_episode(tags)
    name = detect_name(tags) or "بدون_نام"
    # نام را تمیزتر کنیم (فقط جهت نمایش)
    name = name.replace("#", "").strip()
    return cat, name, season, ep

# =======================
# Send functions
# =======================
async def send_single(chat_id: int, context: ContextTypes.DEFAULT_TYPE, cat: str, name: str):
    db = load_db()
    item = db["categories"].get(cat, {}).get(name)
    if not item:
        await context.bot.send_message(chat_id, "❌ مورد پیدا نشد.")
        return

    media = item.get("media", "video")
    file_id = item["file_id"]
    caption = item.get("caption") or f"{cat} / {name}"

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

    eps = sorted([int(k) for k in season_data.keys() if k.isdigit() and int(k) >= 1])
    ep_data = season_data[str(ep)]
    file_id = ep_data["file_id"]
    media = ep_data.get("media", "video")
    caption = ep_data.get("caption") or f"{name} | فصل {season} | قسمت {ep}"

    kb = ep_nav_kb(cat, name, season, ep, eps)

    if media == "photo":
        await context.bot.send_photo(chat_id, photo=file_id, caption=caption, reply_markup=kb)
    elif media == "document":
        await context.bot.send_document(chat_id, document=file_id, caption=caption, reply_markup=kb)
    else:
        await context.bot.send_video(chat_id, video=file_id, caption=caption, reply_markup=kb)

# =======================
# Channel listener: AUTO REGISTER
# =======================
async def on_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg or msg.chat_id != ARCHIVE_CHANNEL_ID:
        return

    media_type, file_id = extract_media(msg)
    if not file_id:
        return  # فقط مدیاها

    caption = msg.caption or ""
    cat, name, season, ep = parse_caption(caption)

    db = load_db()

    # ثبت در لاگ/آخرین
    context.bot_data["last_channel_file"] = {
        "media_type": media_type,
        "file_id": file_id,
        "caption": caption,
        "cat": cat,
        "name": name,
        "season": season,
        "ep": ep,
        "message_id": msg.message_id,
        "ts": int(time.time()),
    }
    db["_uploads"].append(context.bot_data["last_channel_file"])
    save_db(db)

    # ثبت خودکار در دیتابیس اصلی
    # اگر سریال و فصل/قسمت دارد => سریال
    if season is not None and ep is not None:
        # دسته باید سریالی باشد؛ اگر نیست، خودکار تبدیلش می‌کنیم
        if cat not in SERIES_CATS:
            cat = "سریال"
        db["categories"].setdefault(cat, {})
        if name not in db["categories"][cat]:
            db["categories"][cat][name] = {"type": "series", "seasons": {}}
        db["categories"][cat][name].setdefault("seasons", {})
        db["categories"][cat][name]["seasons"].setdefault(str(season), {})
        db["categories"][cat][name]["seasons"][str(season)][str(ep)] = {
            "media": "video" if media_type in ("video",) else ("photo" if media_type == "photo" else "document"),
            "file_id": file_id,
            "caption": caption,
            "source": "channel",
            "channel_message_id": msg.message_id,
        }
        save_db(db)
        log.info(f"AUTO-REG series: {cat}/{name} S{season}E{ep}")
    else:
        # تک‌فیلم/تک محتوا
        if cat not in SINGLE_CATS:
            # اگر فقط #سریال زده ولی فصل/قسمت نداده، باز تک ذخیره می‌کنیم
            # کاربر می‌تواند بعداً فصل/قسمت استاندارد بزند
            pass
        db["categories"].setdefault(cat, {})
        db["categories"][cat][name] = {
            "type": "single",
            "media": "video" if media_type in ("video",) else ("photo" if media_type == "photo" else "document"),
            "file_id": file_id,
            "caption": caption,
            "source": "channel",
            "channel_message_id": msg.message_id,
        }
        save_db(db)
        log.info(f"AUTO-REG single: {cat}/{name}")

# =======================
# Commands & UI
# =======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_db()
    context.user_data["mode"] = MODE_NONE
    context.user_data.pop("picked_cat", None)
    context.user_data.pop("picked_item", None)
    await update.message.reply_text("سلام 👋\nاز منوی زیر انتخاب کن:", reply_markup=kb_main())

async def last(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.bot_data.get("last_channel_file")
    if not data:
        await update.message.reply_text("هنوز چیزی از کانال ثبت نشده. یه ویدیو با هشتگ داخل کانال بفرست.")
        return
    await update.message.reply_text(
        "آخرین مورد ثبت شده از کانال ✅\n"
        f"دسته: {data.get('cat')}\n"
        f"نام: {data.get('name')}\n"
        f"فصل: {data.get('season')}\n"
        f"قسمت: {data.get('ep')}\n"
        f"message_id: {data.get('message_id')}"
    )

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_db()
    text = (update.message.text or "").strip()

    if text == "⬅️ برگشت":
        context.user_data["mode"] = MODE_NONE
        context.user_data.pop("picked_cat", None)
        context.user_data.pop("picked_item", None)
        await update.message.reply_text("منوی اصلی 👇", reply_markup=kb_main())
        return

    # انیمیشن => زیرمنو
    if text == "انیمیشن":
        context.user_data["mode"] = MODE_ANIME_MENU
        await update.message.reply_text("🎞 یکی رو انتخاب کن:", reply_markup=kb_anime_menu())
        return

    # انتخاب از زیرمنوی انیمیشن
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

    # انتخاب دسته اصلی
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

    # انتخاب آیتم
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

        # تک
        if cat in SINGLE_CATS:
            context.user_data["mode"] = MODE_NONE
            await send_single(update.message.chat_id, context, cat, text)
            return

        # سریال => انتخاب فصل
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

    # انتخاب فصل
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

            # قسمت 1 را بفرست
            await send_episode(update.message.chat_id, context, cat, name, season, 1)

            context.user_data["mode"] = MODE_NONE
            context.user_data.pop("picked_cat", None)
            context.user_data.pop("picked_item", None)
            return

        await update.message.reply_text("از دکمه‌های فصل انتخاب کن.", reply_markup=kb_main())
        return

    await update.message.reply_text("از منو یکی رو انتخاب کن 👇", reply_markup=kb_main())

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = (q.data or "").split("|")
    if not data:
        return

    if data[0] == "ep" and len(data) >= 6:
        cat = data[1]
        name = data[2]
        season = int(data[3])
        ep = int(data[4])
        await send_episode(q.message.chat_id, context, cat, name, season, ep)
        return

    if data[0] == "pickseason" and len(data) >= 3:
        cat = data[1]
        name = data[2]
        db = load_db()
        entry = db["categories"].get(cat, {}).get(name)
        if not entry:
            await context.bot.send_message(q.message.chat_id, "❌ پیدا نشد.")
            return
        seasons = sorted([int(k) for k in entry.get("seasons", {}).keys() if k.isdigit()])
        context.user_data["mode"] = MODE_PICK_SEASON
        context.user_data["picked_cat"] = cat
        context.user_data["picked_item"] = name
        await context.bot.send_message(q.message.chat_id, f"📺 {name}\nفصل را انتخاب کن:", reply_markup=kb_seasons(seasons))
        return

# =======================
# main
# =======================
def main():
    ensure_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("last", last))
    app.add_handler(CommandHandler("add", add))

    # کانال
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, on_channel_post))

    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    log.info("Bot is running...")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
