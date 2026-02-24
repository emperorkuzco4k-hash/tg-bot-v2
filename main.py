import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

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
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

DB_PATH = "db.json"

TTL_SECONDS = 10          # ✅ طبق درخواست: 10 ثانیه
COUNTDOWN_STEP = 2        # هر 2 ثانیه آپدیت شمارش معکوس

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
# Helpers
# =======================
def require_token():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set (Railway Variables).")


def is_admin(update: Update) -> bool:
    return bool(update.effective_user and update.effective_user.id == ADMIN_ID)


def fmt_mmss(sec: int) -> str:
    if sec < 0:
        sec = 0
    return f"{sec//60:02d}:{sec%60:02d}"


# =======================
# DB (فعلاً JSON)
# =======================
def load_db() -> dict:
    if not os.path.exists(DB_PATH):
        return {"categories": {}, "_stats": {"item_requests": {}, "season_requests": {}}}
    try:
        with open(DB_PATH, "r", encoding="utf-8") as f:
            db = json.load(f)
    except Exception:
        db = {"categories": {}, "_stats": {"item_requests": {}, "season_requests": {}}}

    if "categories" not in db or not isinstance(db.get("categories"), dict):
        db["categories"] = {}
    db.setdefault("_stats", {})
    db["_stats"].setdefault("item_requests", {})
    db["_stats"].setdefault("season_requests", {})

    # ensure categories exist
    for c in (CATS_MAIN + ["سریال انیمیشن"]):
        db["categories"].setdefault(c, {})

    return db


def save_db(db: dict) -> None:
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def ensure_db() -> None:
    db = load_db()
    save_db(db)


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
    rows = [[x] for x in items[:50]]
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


def search_kb(results: List[Tuple[str, str]]):
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(f"{name} | {cat}", callback_data=f"search|{cat}|{name}")]
         for cat, name in results]
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


def redownload_kb(payload: str):
    # payload مثل: single|cat|name   یا   ep|cat|name|season|ep
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬇️ دانلود مجدد (10 ثانیه)", callback_data=f"redo|{payload}")]]
    )


# =======================
# Stats (فعلاً ساده)
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
# Jobs: delete + countdown + redownload prompt
# =======================
async def delete_messages_job(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    chat_id = data["chat_id"]
    mids = data["message_ids"]
    for mid in mids:
        try:
            await context.bot.delete_message(chat_id, mid)
        except Exception:
            pass


async def countdown_job(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    chat_id = data["chat_id"]
    msg_id = data["msg_id"]
    end_ts = data["end_ts"]
    label = data.get("label", "")

    remain = int(end_ts - time.time())
    if remain <= 0:
        try:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=f"⏳ {label}\nحذف شد ✅")
        except Exception:
            pass
        try:
            context.job.schedule_removal()
        except Exception:
            pass
        return

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text=f"⏳ {label}\nزمان باقی‌مانده: {fmt_mmss(remain)}",
        )
    except Exception:
        pass


async def send_redownload_prompt_job(context: ContextTypes.DEFAULT_TYPE):
    """
    بعد از حذف فایل‌ها، یک پیام جدا با دکمه "دانلود مجدد" ارسال می‌کنیم
    و خود این پیام هم بعد 10 ثانیه پاک می‌شود.
    """
    data = context.job.data
    chat_id = data["chat_id"]
    payload = data["payload"]   # مثلا single|cat|name  یا ep|cat|name|season|ep

    try:
        m = await context.bot.send_message(
            chat_id,
            "✅ فایل حذف شد.\nاگر دوباره لازم داری، اینجا بزن (10 ثانیه فرصت):",
            reply_markup=redownload_kb(payload),
        )
        # خود پیام دکمه هم بعد TTL حذف شود
        context.job_queue.run_once(
            delete_messages_job,
            when=TTL_SECONDS,
            data={"chat_id": chat_id, "message_ids": [m.message_id]},
            name=f"del_redo_{chat_id}_{int(time.time())}",
        )
    except Exception:
        pass


# =======================
# Search
# =======================
def search_items(q: str):
    db = load_db()
    ql = q.lower().strip()
    out = []
    for cat, items in db["categories"].items():
        for name in items.keys():
            if ql in name.lower():
                out.append((cat, name))
    return out[:10]


# =======================
# Send media
# =======================
async def send_media(chat_id: int, context: ContextTypes.DEFAULT_TYPE, file_id: str, media: str, caption: str, reply_markup=None):
    if media == "photo":
        return await context.bot.send_photo(chat_id, photo=file_id, caption=caption, reply_markup=reply_markup)
    if media == "document":
        return await context.bot.send_document(chat_id, document=file_id, caption=caption, reply_markup=reply_markup)
    # default video
    return await context.bot.send_video(chat_id, video=file_id, caption=caption, reply_markup=reply_markup)


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

    mids = []
    caption = f"🎬 {cat}\n📌 {name}\n📝 {title}\n⏳ تا {TTL_SECONDS} ثانیه دیگه حذف می‌شود."

    m = await send_media(chat_id, context, file_id, media, caption)
    mids.append(m.message_id)

    end_ts = time.time() + TTL_SECONDS
    cd = await context.bot.send_message(chat_id, f"⏳ {cat} / {name}\nزمان باقی‌مانده: {fmt_mmss(TTL_SECONDS)}")
    mids.append(cd.message_id)

    # countdown
    context.job_queue.run_repeating(
        countdown_job,
        interval=COUNTDOWN_STEP,
        first=COUNTDOWN_STEP,
        data={"chat_id": chat_id, "msg_id": cd.message_id, "end_ts": end_ts, "label": f"{cat} / {name}"},
        name=f"cd_{chat_id}_{cat}_{int(end_ts)}",
    )

    # delete
    context.job_queue.run_once(
        delete_messages_job,
        when=TTL_SECONDS,
        data={"chat_id": chat_id, "message_ids": mids},
        name=f"del_{chat_id}_{cat}_{int(end_ts)}",
    )

    # ✅ بعد از حذف: دکمه دانلود مجدد
    payload = f"single|{cat}|{name}"
    context.job_queue.run_once(
        send_redownload_prompt_job,
        when=TTL_SECONDS + 1,
        data={"chat_id": chat_id, "payload": payload},
        name=f"redo_prompt_{chat_id}_{int(end_ts)}",
    )


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
    if not eps:
        await context.bot.send_message(chat_id, "❌ قسمتی برای این فصل ثبت نشده.")
        return

    bump_item_stat(cat, name)
    bump_season_stat(cat, name, season)

    ep_data = season_data[str(ep)]
    file_id = ep_data["file_id"]
    title = ep_data.get("title") or f"S{season:02d}E{ep:02d}"
    media = ep_data.get("media", "video")

    kb = ep_nav_kb(cat, name, season, ep, eps)
    caption = f"🎬 {cat}\n{name}\nفصل {season} - قسمت {ep}\n{title}\n⏳ تا {TTL_SECONDS} ثانیه دیگه حذف می‌شود."

    mids = []
    m = await send_media(chat_id, context, file_id, media, caption, reply_markup=kb)
    mids.append(m.message_id)

    end_ts = time.time() + TTL_SECONDS
    cd = await context.bot.send_message(chat_id, f"⏳ {name} / فصل {season} / قسمت {ep}\nزمان باقی‌مانده: {fmt_mmss(TTL_SECONDS)}")
    mids.append(cd.message_id)

    context.job_queue.run_repeating(
        countdown_job,
        interval=COUNTDOWN_STEP,
        first=COUNTDOWN_STEP,
        data={"chat_id": chat_id, "msg_id": cd.message_id, "end_ts": end_ts, "label": f"{name} S{season}E{ep}"},
        name=f"cd_{chat_id}_{name}_{season}_{ep}_{int(end_ts)}",
    )
    context.job_queue.run_once(
        delete_messages_job,
        when=TTL_SECONDS,
        data={"chat_id": chat_id, "message_ids": mids},
        name=f"del_{chat_id}_{name}_{season}_{ep}_{int(end_ts)}",
    )

    # ✅ بعد از حذف: دکمه دانلود مجدد
    payload = f"ep|{cat}|{name}|{season}|{ep}"
    context.job_queue.run_once(
        send_redownload_prompt_job,
        when=TTL_SECONDS + 1,
        data={"chat_id": chat_id, "payload": payload},
        name=f"redo_prompt_{chat_id}_{name}_{season}_{ep}_{int(end_ts)}",
    )


async def send_season_poster_if_exists(chat_id: int, context: ContextTypes.DEFAULT_TYPE, cat: str, name: str, season: int):
    db = load_db()
    entry = db["categories"].get(cat, {}).get(name)
    if not entry or entry.get("type") != "series":
        return
    season_data = entry.get("seasons", {}).get(str(season), {})
    if "0" not in season_data:
        return

    poster = season_data["0"]
    file_id = poster["file_id"]
    title = poster.get("title") or "پوستر فصل"

    mids = []
    m = await send_media(
        chat_id,
        context,
        file_id=file_id,
        media="photo",
        caption=f"📌 {name}\nپوستر فصل {season}\n{title}\n⏳ تا {TTL_SECONDS} ثانیه دیگه حذف می‌شود.",
    )
    mids.append(m.message_id)

    end_ts = time.time() + TTL_SECONDS
    cd = await context.bot.send_message(chat_id, f"⏳ {name} / پوستر فصل {season}\nزمان باقی‌مانده: {fmt_mmss(TTL_SECONDS)}")
    mids.append(cd.message_id)

    context.job_queue.run_repeating(
        countdown_job,
        interval=COUNTDOWN_STEP,
        first=COUNTDOWN_STEP,
        data={"chat_id": chat_id, "msg_id": cd.message_id, "end_ts": end_ts, "label": f"{name} poster S{season}"},
        name=f"cd_{chat_id}_{name}_poster_{season}_{int(end_ts)}",
    )
    context.job_queue.run_once(
        delete_messages_job,
        when=TTL_SECONDS,
        data={"chat_id": chat_id, "message_ids": mids},
        name=f"del_{chat_id}_{name}_poster_{season}_{int(end_ts)}",
    )

    payload = f"poster|{cat}|{name}|{season}"
    context.job_queue.run_once(
        send_redownload_prompt_job,
        when=TTL_SECONDS + 1,
        data={"chat_id": chat_id, "payload": payload},
        name=f"redo_prompt_{chat_id}_{name}_poster_{season}_{int(end_ts)}",
    )


# =======================
# /start
# =======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_db()
    context.user_data["mode"] = MODE_NONE
    context.user_data.pop("picked_cat", None)
    context.user_data.pop("picked_item", None)
    await update.message.reply_text("سلام 👋\nاز منوی زیر انتخاب کن:", reply_markup=kb_main())


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

    # anime menu open (منوی دو مرحله‌ای)
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

    # smart search
    if len(text) >= 3 and text not in CATS_MAIN and text not in ANIME_SUB and text != "⬅️ برگشت":
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
                await update.message.reply_text("فصل نامعتبر.", reply_markup=kb_main())
                context.user_data["mode"] = MODE_NONE
                return

            # poster then episode 1
            await send_season_poster_if_exists(update.message.chat_id, context, cat, name, season)
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

    parts = (q.data or "").split("|")
    if not parts:
        return

    # search|cat|name...
    if parts[0] == "search" and len(parts) >= 3:
        cat = parts[1]
        name = "|".join(parts[2:])
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
            await context.bot.send_message(q.message.chat_id, f"📺 {name}\nفصل را انتخاب کن:", reply_markup=kb_seasons(seasons))
        return

    # ep|cat|name|season|ep
    if parts[0] == "ep" and len(parts) >= 6:
        cat = parts[1]
        name = parts[2]
        season = int(parts[3])
        ep = int(parts[4])
        await send_episode(q.message.chat_id, context, cat, name, season, ep)
        return

    # pickseason|cat|name
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
        await context.bot.send_message(q.message.chat_id, f"📺 {name}\nفصل را انتخاب کن:", reply_markup=kb_seasons(seasons))
        return

    # redo|payload...
    if parts[0] == "redo" and len(parts) >= 2:
        payload = "|".join(parts[1:])
        p = payload.split("|")
        if not p:
            return

        # single|cat|name
        if p[0] == "single" and len(p) >= 3:
            cat = p[1]
            name = "|".join(p[2:])
            await send_single(q.message.chat_id, context, cat, name)
            return

        # ep|cat|name|season|ep
        if p[0] == "ep" and len(p) >= 5:
            cat = p[1]
            name = p[2]
            season = int(p[3])
            ep = int(p[4])
            await send_episode(q.message.chat_id, context, cat, name, season, ep)
            return

        # poster|cat|name|season
        if p[0] == "poster" and len(p) >= 4:
            cat = p[1]
            name = p[2]
            season = int(p[3])
            await send_season_poster_if_exists(q.message.chat_id, context, cat, name, season)
            return


# =======================
# /add (admin) - کامل
# =======================
ASK_CAT, ASK_NAME, ASK_SEASON, ASK_EP, ASK_MEDIA, ASK_TITLE = range(6)


def add_cat_kb():
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

    context.user_data["add_anime_pick"] = False
    context.user_data["add_cat"] = None
    context.user_data["add_name"] = None
    context.user_data["add_season"] = None
    context.user_data["add_ep"] = None
    context.user_data["add_media_type"] = None
    context.user_data["add_file_id"] = None

    await update.message.reply_text("کدوم دسته؟", reply_markup=add_cat_kb())
    return ASK_CAT


async def add_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cat = (update.message.text or "").strip()

    if cat == "⬅️ برگشت":
        await update.message.reply_text("کنسل شد.", reply_markup=kb_main())
        return ConversationHandler.END

    # اگر "انیمیشن" انتخاب شد، زیرمنو می‌خوایم
    if cat == "انیمیشن" and not context.user_data.get("add_anime_pick"):
        context.user_data["add_anime_pick"] = True
        await update.message.reply_text("برای افزودن یکی رو انتخاب کن:", reply_markup=kb_anime_menu())
        return ASK_CAT

    # انتخاب واقعی دسته
    if cat in CATS_MAIN or cat == "سریال انیمیشن":
        context.user_data["add_cat"] = cat
        await update.message.reply_text("اسم مورد چیه؟ (مثلاً: Breaking Bad)")
        return ASK_NAME

    await update.message.reply_text("از دکمه‌ها انتخاب کن.", reply_markup=add_cat_kb())
    return ASK_CAT


async def add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = (update.message.text or "").strip()
    if not name or name == "⬅️ برگشت":
        await update.message.reply_text("کنسل شد.", reply_markup=kb_main())
        return ConversationHandler.END

    context.user_data["add_name"] = name
    cat = context.user_data["add_cat"]

    if cat in SERIES_CATS:
        await update.message.reply_text("شماره فصل؟ (مثلاً 1)")
        return ASK_SEASON

    # single
    await update.message.reply_text("حالا فایل رو بفرست (ویدیو/عکس/فایل).")
    return ASK_MEDIA


async def add_season(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = (update.message.text or "").strip()
    if t == "⬅️ برگشت":
        await update.message.reply_text("کنسل شد.", reply_markup=kb_main())
        return ConversationHandler.END

    try:
        season = int(t)
        if season < 1:
            raise ValueError()
    except Exception:
        await update.message.reply_text("فصل نامعتبر. یک عدد مثل 1 بفرست.")
        return ASK_SEASON

    context.user_data["add_season"] = season
    await update.message.reply_text("شماره قسمت؟ (مثلاً 1)\nبرای پوستر فصل عدد 0 بفرست.")
    return ASK_EP


async def add_ep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = (update.message.text or "").strip()
    if t == "⬅️ برگشت":
        await update.message.reply_text("کنسل شد.", reply_markup=kb_main())
        return ConversationHandler.END

    try:
        ep = int(t)
        if ep < 0:
            raise ValueError()
    except Exception:
        await update.message.reply_text("قسمت نامعتبر. عدد 0 یا 1 به بالا بفرست.")
        return ASK_EP

    context.user_data["add_ep"] = ep

    if ep == 0:
        await update.message.reply_text("حالا پوستر فصل رو بفرست (فقط عکس).")
    else:
        await update.message.reply_text("حالا فایل قسمت رو بفرست (ویدیو/فایل).")
    return ASK_MEDIA


async def add_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message

    media_type = None
    file_id = None

    # photo
    if msg.photo:
        media_type = "photo"
        file_id = msg.photo[-1].file_id

    # video
    elif msg.video:
        media_type = "video"
        file_id = msg.video.file_id

    # document (برای فایل‌های حجیم)
    elif msg.document:
        media_type = "document"
        file_id = msg.document.file_id

    if not file_id:
        await update.message.reply_text("فایل معتبر نبود. لطفاً ویدیو/عکس/فایل بفرست.")
        return ASK_MEDIA

    # اگر پوستر فصل هست، فقط عکس قبول کن
    ep = context.user_data.get("add_ep")
    if ep == 0 and media_type != "photo":
        await update.message.reply_text("برای پوستر فصل فقط عکس بفرست.")
        return ASK_MEDIA

    context.user_data["add_media_type"] = media_type
    context.user_data["add_file_id"] = file_id

    await update.message.reply_text("عنوان/توضیح؟ (اختیاری)\nاگر نمی‌خوای، فقط - بفرست.")
    return ASK_TITLE


async def add_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = (update.message.text or "").strip()
    if title == "-":
        title = ""

    cat = context.user_data["add_cat"]
    name = context.user_data["add_name"]
    media = context.user_data["add_media_type"]
    file_id = context.user_data["add_file_id"]

    db = load_db()

    # سریال
    if cat in SERIES_CATS:
        season = int(context.user_data["add_season"])
        ep = int(context.user_data["add_ep"])

        db["categories"].setdefault(cat, {})
        if name not in db["categories"][cat]:
            db["categories"][cat][name] = {"type": "series", "seasons": {}}

        db["categories"][cat][name].setdefault("type", "series")
        db["categories"][cat][name].setdefault("seasons", {})
        db["categories"][cat][name]["seasons"].setdefault(str(season), {})
        db["categories"][cat][name]["seasons"][str(season)][str(ep)] = {
            "file_id": file_id,
            "media": media,
            "title": title,
        }

        save_db(db)

        if ep == 0:
            await update.message.reply_text(f"✅ پوستر فصل {season} برای «{name}» ذخیره شد.", reply_markup=kb_main())
        else:
            await update.message.reply_text(f"✅ فصل {season} - قسمت {ep} برای «{name}» ذخیره شد.", reply_markup=kb_main())
        return ConversationHandler.END

    # تک‌قسمتی
    db["categories"].setdefault(cat, {})
    db["categories"][cat][name] = {
        "type": "single",
        "file_id": file_id,
        "media": media,
        "title": title,
    }
    save_db(db)
    await update.message.reply_text(f"✅ «{name}» در دسته {cat} ذخیره شد.", reply_markup=kb_main())
    return ConversationHandler.END


async def add_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("کنسل شد.", reply_markup=kb_main())
    return ConversationHandler.END


# =======================
# main
# =======================
def build_app() -> Application:
    require_token()
    app = Application.builder().token(BOT_TOKEN).build()

    # commands
    app.add_handler(CommandHandler("start", start))

    # /add conversation
    add_conv = ConversationHandler(
        entry_points=[CommandHandler("add", add_start)],
        states={
            ASK_CAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_cat)],
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_name)],
            ASK_SEASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_season)],
            ASK_EP: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_ep)],
            ASK_MEDIA: [MessageHandler((filters.VIDEO | filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND, add_media)],
            ASK_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_title)],
        },
        fallbacks=[CommandHandler("cancel", add_cancel), MessageHandler(filters.Regex("^⬅️ برگشت$"), add_cancel)],
        name="add_conv",
        persistent=False,
    )
    app.add_handler(add_conv)

    # callbacks
    app.add_handler(CallbackQueryHandler(on_callback))

    # text browsing
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    return app


def main():
    ensure_db()
    app = build_app()
    print("Bot is running...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
