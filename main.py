import os
import json
import re
import time
import math
import asyncio
import logging
from uuid import uuid4

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
ARCHIVE_CHANNEL_ID = int(os.getenv("ARCHIVE_CHANNEL_ID", "0"))

REQUIRED_CHANNEL_ID = int(os.getenv("REQUIRED_CHANNEL_ID", "0") or "0")
REQUIRED_CHANNEL_USERNAME = os.getenv("REQUIRED_CHANNEL_USERNAME", "").strip()

DB_PATH = "db.json"
DELETE_TIME = 30
PAGE_SIZE = 8

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("bot")

CATEGORIES = [
    "فیلم",
    "سریال",
    "کارتون",
    "انیمیشن",
    "فیلم ایرانی",
    "سریال ایرانی",
    "کالکشن ها",
    "بتمن ها",
]

SEARCH_BTN = "🔎 جستجو"
BACK_BTN = "⬅️ بازگشت"
HOME_BTN = "🏠 خانه"

(
    ADD_KIND,
    ADD_CATEGORY,
    ADD_TITLE,
    ADD_POSTER,
    ADD_MOVIE_FILE,
    ADD_SERIES_SEASON_COUNT,
    ADD_SERIES_EPISODE_COUNT,
    ADD_SERIES_EPISODE_FILE,
    EDIT_WAIT_TITLE,
    EDIT_WAIT_POSTER,
    EDIT_WAIT_MOVIE_FILE,
    EDIT_WAIT_SEASON_SELECT,
    EDIT_WAIT_SERIES_EPISODE_COUNT,
    EDIT_WAIT_SERIES_EPISODE_FILE,
) = range(14)

# ================= DATABASE =================

def default_db():
    return {
        "items": {},
        "latest_item_id": None
    }

def load_db():
    if not os.path.exists(DB_PATH):
        return default_db()
    try:
        with open(DB_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if "items" not in data:
                data["items"] = {}
            if "latest_item_id" not in data:
                data["latest_item_id"] = None
            return data
    except Exception:
        return default_db()

def save_db(db):
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def make_item_id(title: str):
    base = re.sub(r"[^\w\u0600-\u06FF]+", "_", title.strip()).strip("_")
    if not base:
        base = "item"
    return f"{base[:30]}_{uuid4().hex[:6]}"

def sort_numeric_keys(d: dict):
    return sorted(d.keys(), key=lambda x: int(x) if str(x).isdigit() else 999999)

def is_admin_user(user_id: int):
    return user_id == ADMIN_ID

def is_admin_update(update: Update):
    user = update.effective_user
    return bool(user and user.id == ADMIN_ID)

# ================= KEYBOARDS =================

def kb_main():
    return ReplyKeyboardMarkup(
        [
            ["فیلم", "سریال"],
            ["کارتون", "انیمیشن"],
            ["فیلم ایرانی", "سریال ایرانی"],
            ["کالکشن ها", "بتمن ها"],
            [SEARCH_BTN],
        ],
        resize_keyboard=True
    )

def kb_cancel():
    return ReplyKeyboardMarkup(
        [[BACK_BTN], ["/cancel"]],
        resize_keyboard=True
    )

def admin_kind_keyboard():
    return ReplyKeyboardMarkup(
        [["فیلم", "سریال"], [BACK_BTN], ["/cancel"]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def admin_category_keyboard():
    rows = [[c] for c in CATEGORIES]
    rows.append([BACK_BTN])
    rows.append(["/cancel"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True)

def contact_admin_button():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📞 تماس با ادمین", url=f"tg://user?id={ADMIN_ID}")]
    ])

# ================= MEMBERSHIP =================

async def is_joined_required_channel(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    if not REQUIRED_CHANNEL_ID:
        return True
    try:
        member = await context.bot.get_chat_member(REQUIRED_CHANNEL_ID, user_id)
        return member.status in {
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        }
    except Exception:
        return False

async def ensure_joined(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return False

    joined = await is_joined_required_channel(user.id, context)
    if joined:
        return True

    text = "🔒 برای استفاده از ربات باید اول عضو کانال شوید."
    buttons = []

    if REQUIRED_CHANNEL_USERNAME:
        join_link = f"https://t.me/{REQUIRED_CHANNEL_USERNAME.replace('@', '')}"
        buttons.append([InlineKeyboardButton("📢 عضویت در کانال", url=join_link)])

    buttons.append([InlineKeyboardButton("✅ بررسی عضویت", callback_data="check_join")])

    markup = InlineKeyboardMarkup(buttons)

    if update.message:
        await update.message.reply_text(text, reply_markup=markup)
    elif update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=markup)
    return False

# ================= HELPERS =================

def paginate_list(items, page, page_size=PAGE_SIZE):
    total = len(items)
    pages = max(1, math.ceil(total / page_size))
    page = max(0, min(page, pages - 1))
    start = page * page_size
    end = start + page_size
    return items[start:end], page, pages

def get_archive_message_id_from_message(message):
    if message.text and message.text.strip().isdigit():
        return int(message.text.strip())

    try:
        if getattr(message, "forward_from_chat", None) and getattr(message, "forward_from_message_id", None):
            if message.forward_from_chat.id == ARCHIVE_CHANNEL_ID:
                return int(message.forward_from_message_id)
    except Exception:
        pass

    try:
        origin = getattr(message, "forward_origin", None)
        if origin and hasattr(origin, "chat") and hasattr(origin, "message_id"):
            if origin.chat.id == ARCHIVE_CHANNEL_ID:
                return int(origin.message_id)
    except Exception:
        pass

    return None

async def auto_delete_file_and_keep_redownload(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    sent_message_id: int,
    item_id: str,
    season_num: str = None,
    episode_num: str = None,
):
    await asyncio.sleep(DELETE_TIME)

    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=sent_message_id)
    except Exception:
        pass

    try:
        if season_num and episode_num:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "🔁 دانلود مجدد",
                    callback_data=f"redownload_episode:{item_id}:{season_num}:{episode_num}"
                )],
                [InlineKeyboardButton("🏠 خانه", callback_data="go_home")]
            ])
            await context.bot.send_message(
                chat_id=chat_id,
                text="⏱ فایل حذف شد. برای دریافت دوباره روی دکمه زیر بزن.",
                reply_markup=keyboard
            )
        else:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "🔁 دانلود مجدد",
                    callback_data=f"redownload_movie:{item_id}"
                )],
                [InlineKeyboardButton("🏠 خانه", callback_data="go_home")]
            ])
            await context.bot.send_message(
                chat_id=chat_id,
                text="⏱ فایل حذف شد. برای دریافت دوباره روی دکمه زیر بزن.",
                reply_markup=keyboard
            )
    except Exception:
        pass

async def copy_archive_message_and_schedule_delete(
    chat_id: int,
    archive_message_id: int,
    item_id: str,
    context: ContextTypes.DEFAULT_TYPE,
    season_num: str = None,
    episode_num: str = None,
):
    sent = await context.bot.copy_message(
        chat_id=chat_id,
        from_chat_id=ARCHIVE_CHANNEL_ID,
        message_id=archive_message_id,
    )

    asyncio.create_task(
        auto_delete_file_and_keep_redownload(
            context=context,
            chat_id=chat_id,
            sent_message_id=sent.message_id,
            item_id=item_id,
            season_num=season_num,
            episode_num=episode_num,
        )
    )

# ================= RENDERING =================

async def send_item_overview(
    chat_id: int,
    item: dict,
    context: ContextTypes.DEFAULT_TYPE,
    category_page: int = 0
):
    title = item["title"]
    category = item["category"]
    kind = item["kind"]

    text = f"🎬 {title}\n📂 دسته‌بندی: {category}"

    common_rows = [
        [InlineKeyboardButton("📞 تماس با ادمین", url=f"tg://user?id={ADMIN_ID}")],
        [
            InlineKeyboardButton("⬅️ بازگشت", callback_data=f"back_category:{category}:{category_page}"),
            InlineKeyboardButton("🏠 خانه", callback_data="go_home"),
        ]
    ]

    if kind == "movie":
        text += "\n\nبرای دریافت فایل روی دکمه زیر بزن."
        rows = [
            [InlineKeyboardButton("📥 دریافت فایل", callback_data=f"getmovie:{item['id']}")]
        ] + common_rows
        keyboard = InlineKeyboardMarkup(rows)
    else:
        text += "\n\nفصل موردنظر را انتخاب کن:"
        seasons = item.get("seasons", {})
        rows = []
        for season_num in sort_numeric_keys(seasons):
            rows.append([
                InlineKeyboardButton(
                    f"فصل {season_num}",
                    callback_data=f"season:{item['id']}:{season_num}:{category_page}"
                )
            ])
        rows += common_rows
        keyboard = InlineKeyboardMarkup(rows)

    poster = item.get("poster_file_id")
    if poster:
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=poster,
            caption=text,
            reply_markup=keyboard
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard
        )

async def send_category_items(chat_id: int, category: str, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    db = load_db()
    items = [
        item for item in db["items"].values()
        if item.get("category") == category
    ]

    if not items:
        await context.bot.send_message(chat_id=chat_id, text="فعلاً چیزی اضافه نشده", reply_markup=contact_admin_button())
        return

    items = sorted(items, key=lambda x: x.get("created_at", 0), reverse=True)
    page_items, page, pages = paginate_list(items, page)

    rows = []
    for item in page_items:
        rows.append([
            InlineKeyboardButton(item["title"], callback_data=f"item:{item['id']}:{category}:{page}")
        ])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ قبلی", callback_data=f"catpage:{category}:{page-1}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("بعدی ▶️", callback_data=f"catpage:{category}:{page+1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("🏠 خانه", callback_data="go_home")])

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"📂 {category}\nصفحه {page+1} از {pages}\nیکی را انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(rows)
    )

async def send_search_results(chat_id: int, query_text: str, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    db = load_db()
    q = query_text.strip().lower()

    results = []
    for item in db["items"].values():
        title = item.get("title", "").lower()
        category = item.get("category", "").lower()
        if q in title or q in category:
            results.append(item)

    if not results:
        await context.bot.send_message(chat_id=chat_id, text="❌ نتیجه‌ای پیدا نشد", reply_markup=contact_admin_button())
        return

    results = sorted(results, key=lambda x: x.get("created_at", 0), reverse=True)
    page_items, page, pages = paginate_list(results, page)

    rows = []
    for item in page_items:
        rows.append([
            InlineKeyboardButton(
                f"{item['title']} | {item['category']}",
                callback_data=f"searchitem:{item['id']}:{page}"
            )
        ])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ قبلی", callback_data=f"searchpage:{query_text}:{page-1}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("بعدی ▶️", callback_data=f"searchpage:{query_text}:{page+1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("🏠 خانه", callback_data="go_home")])

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🔎 نتایج جستجو برای: {query_text}\nصفحه {page+1} از {pages}",
        reply_markup=InlineKeyboardMarkup(rows)
    )

async def send_delete_page(chat_id: int, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    db = load_db()
    items = sorted(db["items"].values(), key=lambda x: x.get("created_at", 0), reverse=True)

    if not items:
        await context.bot.send_message(chat_id=chat_id, text="❌ چیزی برای حذف وجود ندارد")
        return

    page_items, page, pages = paginate_list(items, page)

    rows = []
    for item in page_items:
        rows.append([
            InlineKeyboardButton(
                f"🗑 {item['title']}",
                callback_data=f"delete_item:{item['id']}:{page}"
            )
        ])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ قبلی", callback_data=f"admin_del_page:{page-1}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("بعدی ▶️", callback_data=f"admin_del_page:{page+1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("🏠 خانه", callback_data="go_home")])

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"حذف آیتم - صفحه {page+1} از {pages}",
        reply_markup=InlineKeyboardMarkup(rows)
    )

async def send_edit_page(chat_id: int, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    db = load_db()
    items = sorted(db["items"].values(), key=lambda x: x.get("created_at", 0), reverse=True)

    if not items:
        await context.bot.send_message(chat_id=chat_id, text="❌ چیزی برای ویرایش وجود ندارد")
        return

    page_items, page, pages = paginate_list(items, page)

    rows = []
    for item in page_items:
        rows.append([
            InlineKeyboardButton(
                f"✏️ {item['title']}",
                callback_data=f"edit_item:{item['id']}:{page}"
            )
        ])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ قبلی", callback_data=f"admin_edit_page:{page-1}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("بعدی ▶️", callback_data=f"admin_edit_page:{page+1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("🏠 خانه", callback_data="go_home")])

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"ویرایش آیتم - صفحه {page+1} از {pages}",
        reply_markup=InlineKeyboardMarkup(rows)
    )

async def send_edit_fields(chat_id: int, item: dict, page: int, context: ContextTypes.DEFAULT_TYPE):
    rows = [
        [InlineKeyboardButton("✏️ ویرایش عنوان", callback_data=f"edit_field:title:{item['id']}:{page}")],
        [InlineKeyboardButton("🖼 ویرایش پوستر", callback_data=f"edit_field:poster:{item['id']}:{page}")],
    ]

    if item["kind"] == "movie":
        rows.append([InlineKeyboardButton("🎞 ویرایش فایل فیلم", callback_data=f"edit_field:moviefile:{item['id']}:{page}")])
    else:
        rows.append([InlineKeyboardButton("📺 ویرایش فصل/قسمت سریال", callback_data=f"edit_field:seriesfile:{item['id']}:{page}")])

    rows.append([InlineKeyboardButton("⬅️ بازگشت", callback_data=f"admin_edit_page:{page}")])
    rows.append([InlineKeyboardButton("🏠 خانه", callback_data="go_home")])

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"آیتم: {item['title']}\nفیلد موردنظر برای ویرایش را انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(rows)
    )

# ================= START / CANCEL =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_joined(update, context):
        return
    context.user_data["mode"] = None
    await update.message.reply_text("👋 خوش آمدی", reply_markup=kb_main())

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("add_data", None)
    context.user_data.pop("edit_data", None)
    context.user_data["mode"] = None
    await update.message.reply_text("❌ عملیات لغو شد", reply_markup=kb_main())
    return ConversationHandler.END

# ================= LAST =================

async def last(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_joined(update, context):
        return

    db = load_db()
    latest_id = db.get("latest_item_id")

    if not latest_id or latest_id not in db["items"]:
        await update.message.reply_text("❌ هنوز چیزی ثبت نشده")
        return

    item = db["items"][latest_id]
    await send_item_overview(update.effective_chat.id, item, context)

# ================= SEARCH =================

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_joined(update, context):
        return
    context.user_data["mode"] = "search"
    await update.message.reply_text(
        "عبارت موردنظر را بفرست:",
        reply_markup=ReplyKeyboardMarkup([[BACK_BTN], [HOME_BTN]], resize_keyboard=True)
    )

# ================= USER TEXT =================

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_joined(update, context):
        return

    text = update.message.text.strip()

    if text in [BACK_BTN, HOME_BTN]:
        context.user_data["mode"] = None
        await update.message.reply_text("🏠 منوی اصلی", reply_markup=kb_main())
        return

    if text == SEARCH_BTN:
        return await search_command(update, context)

    if context.user_data.get("mode") == "search":
        context.user_data["mode"] = None
        await send_search_results(update.effective_chat.id, text, context, page=0)
        await update.message.reply_text("🏠 منوی اصلی", reply_markup=kb_main())
        return

    if text not in CATEGORIES:
        return

    await send_category_items(update.effective_chat.id, text, context, page=0)

# ================= CALLBACKS =================

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = query.data
    db = load_db()

    if data == "check_join":
        if await is_joined_required_channel(user_id, context):
            await query.message.reply_text("✅ عضویت شما تایید شد", reply_markup=kb_main())
        else:
            await query.message.reply_text("❌ هنوز عضو کانال نشده‌اید")
        return

    if data == "go_home":
        await query.message.reply_text("🏠 منوی اصلی", reply_markup=kb_main())
        return

    if not await is_joined_required_channel(user_id, context):
        await ensure_joined(update, context)
        return

    try:
        if data.startswith("catpage:"):
            _, category, page = data.split(":", 2)
            await send_category_items(query.message.chat_id, category, context, int(page))
            return

        if data.startswith("item:"):
            _, item_id, category, page = data.split(":", 3)
            item = db["items"].get(item_id)
            if not item:
                await query.message.reply_text("❌ آیتم پیدا نشد")
                return
            await send_item_overview(query.message.chat_id, item, context, category_page=int(page))
            return

        if data.startswith("back_category:"):
            _, category, page = data.split(":", 2)
            await send_category_items(query.message.chat_id, category, context, int(page))
            return

        if data.startswith("season:"):
            _, item_id, season_num, category_page = data.split(":", 3)
            item = db["items"].get(item_id)
            if not item:
                await query.message.reply_text("❌ سریال پیدا نشد")
                return

            seasons = item.get("seasons", {})
            if season_num not in seasons:
                await query.message.reply_text("❌ این فصل پیدا نشد")
                return

            episodes = seasons[season_num]
            rows = []
            for ep_num in sort_numeric_keys(episodes):
                rows.append([
                    InlineKeyboardButton(
                        f"قسمت {ep_num}",
                        callback_data=f"episode:{item_id}:{season_num}:{ep_num}"
                    )
                ])

            rows.append([
                InlineKeyboardButton("⬅️ بازگشت", callback_data=f"item:{item_id}:{item['category']}:{category_page}")
            ])
            rows.append([InlineKeyboardButton("🏠 خانه", callback_data="go_home")])

            await query.message.reply_text(
                f"📺 {item['title']}\nفصل {season_num}\nقسمت موردنظر را انتخاب کن:",
                reply_markup=InlineKeyboardMarkup(rows)
            )
            return

        if data.startswith("episode:"):
            _, item_id, season_num, ep_num = data.split(":", 3)
            item = db["items"].get(item_id)
            if not item:
                await query.message.reply_text("❌ آیتم پیدا نشد")
                return

            msg_id = item.get("seasons", {}).get(season_num, {}).get(ep_num)
            if not msg_id:
                await query.message.reply_text("❌ فایل این قسمت ثبت نشده")
                return

            await copy_archive_message_and_schedule_delete(
                chat_id=query.message.chat_id,
                archive_message_id=msg_id,
                item_id=item_id,
                season_num=season_num,
                episode_num=ep_num,
                context=context,
            )
            return

        if data.startswith("getmovie:"):
            item_id = data.split(":", 1)[1]
            item = db["items"].get(item_id)
            if not item:
                await query.message.reply_text("❌ فیلم پیدا نشد")
                return

            await copy_archive_message_and_schedule_delete(
                chat_id=query.message.chat_id,
                archive_message_id=item["archive_message_id"],
                item_id=item_id,
                context=context,
            )
            return

        if data.startswith("redownload_movie:"):
            item_id = data.split(":", 1)[1]
            item = db["items"].get(item_id)
            if not item:
                await query.message.reply_text("❌ فیلم پیدا نشد")
                return

            await copy_archive_message_and_schedule_delete(
                chat_id=query.message.chat_id,
                archive_message_id=item["archive_message_id"],
                item_id=item_id,
                context=context,
            )
            return

        if data.startswith("redownload_episode:"):
            _, item_id, season_num, ep_num = data.split(":", 3)
            item = db["items"].get(item_id)
            if not item:
                await query.message.reply_text("❌ آیتم پیدا نشد")
                return

            msg_id = item.get("seasons", {}).get(season_num, {}).get(ep_num)
            if not msg_id:
                await query.message.reply_text("❌ فایل این قسمت ثبت نشده")
                return

            await copy_archive_message_and_schedule_delete(
                chat_id=query.message.chat_id,
                archive_message_id=msg_id,
                item_id=item_id,
                season_num=season_num,
                episode_num=ep_num,
                context=context,
            )
            return

        if data.startswith("searchpage:"):
            _, query_text, page = data.split(":", 2)
            await send_search_results(query.message.chat_id, query_text, context, int(page))
            return

        if data.startswith("searchitem:"):
            _, item_id, page = data.split(":", 2)
            item = db["items"].get(item_id)
            if not item:
                await query.message.reply_text("❌ آیتم پیدا نشد")
                return
            await send_item_overview(query.message.chat_id, item, context, category_page=0)
            return

        if data.startswith("admin_del_page:"):
            if not is_admin_user(user_id):
                await query.message.reply_text("⛔ فقط ادمین")
                return
            _, page = data.split(":", 1)
            await send_delete_page(query.message.chat_id, context, int(page))
            return

        if data.startswith("delete_item:"):
            if not is_admin_user(user_id):
                await query.message.reply_text("⛔ فقط ادمین")
                return

            _, item_id, page = data.split(":", 2)
            if item_id not in db["items"]:
                await query.message.reply_text("❌ آیتم پیدا نشد")
                return

            title = db["items"][item_id]["title"]
            del db["items"][item_id]

            if db.get("latest_item_id") == item_id:
                db["latest_item_id"] = next(iter(db["items"]), None)

            save_db(db)
            await query.message.reply_text(f"✅ حذف شد: {title}")
            await send_delete_page(query.message.chat_id, context, int(page))
            return

    except Exception as e:
        log.exception(e)
        await query.message.reply_text("❌ خطا در پردازش درخواست")

# ================= ADMIN ADD =================

async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_update(update):
        await update.message.reply_text("⛔ فقط ادمین می‌تواند استفاده کند")
        return ConversationHandler.END

    context.user_data["add_data"] = {}
    await update.message.reply_text(
        "نوع محتوا را انتخاب کن:",
        reply_markup=admin_kind_keyboard()
    )
    return ADD_KIND

async def add_kind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text == BACK_BTN:
        await update.message.reply_text("🏠 منوی اصلی", reply_markup=kb_main())
        return ConversationHandler.END

    if text not in ["فیلم", "سریال"]:
        await update.message.reply_text("فقط «فیلم» یا «سریال» را بفرست")
        return ADD_KIND

    context.user_data["add_data"]["kind"] = "movie" if text == "فیلم" else "series"

    await update.message.reply_text(
        "دسته‌بندی را انتخاب کن:",
        reply_markup=admin_category_keyboard()
    )
    return ADD_CATEGORY

async def add_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text == BACK_BTN:
        await update.message.reply_text(
            "نوع محتوا را انتخاب کن:",
            reply_markup=admin_kind_keyboard()
        )
        return ADD_KIND

    if text not in CATEGORIES:
        await update.message.reply_text("یکی از دسته‌بندی‌های موجود را انتخاب کن")
        return ADD_CATEGORY

    context.user_data["add_data"]["category"] = text

    await update.message.reply_text(
        "اسم فیلم یا سریال را بفرست:",
        reply_markup=kb_cancel()
    )
    return ADD_TITLE

async def add_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = update.message.text.strip()

    if title == BACK_BTN:
        await update.message.reply_text(
            "دسته‌بندی را انتخاب کن:",
            reply_markup=admin_category_keyboard()
        )
        return ADD_CATEGORY

    if not title:
        await update.message.reply_text("اسم معتبر بفرست")
        return ADD_TITLE

    context.user_data["add_data"]["title"] = title

    await update.message.reply_text(
        "پوستر را بفرست.\nاگر پوستر نداری بنویس: /skip",
        reply_markup=kb_cancel()
    )
    return ADD_POSTER

async def add_skip_poster(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["add_data"]["poster_file_id"] = None
    add_data = context.user_data["add_data"]

    if add_data["kind"] == "movie":
        await update.message.reply_text(
            "فایل فیلم را از کانال آرشیو به ربات فوروارد کن یا message_id آن را بفرست.",
            reply_markup=kb_cancel()
        )
        return ADD_MOVIE_FILE
    else:
        await update.message.reply_text(
            "تعداد فصل‌ها را بفرست.\nمثال: 3",
            reply_markup=kb_cancel()
        )
        return ADD_SERIES_SEASON_COUNT

async def add_poster(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("لطفاً عکس پوستر را بفرست یا /skip بزن")
        return ADD_POSTER

    photo = update.message.photo[-1]
    context.user_data["add_data"]["poster_file_id"] = photo.file_id

    add_data = context.user_data["add_data"]

    if add_data["kind"] == "movie":
        await update.message.reply_text(
            "فایل فیلم را از کانال آرشیو به ربات فوروارد کن یا message_id آن را بفرست.",
            reply_markup=kb_cancel()
        )
        return ADD_MOVIE_FILE
    else:
        await update.message.reply_text(
            "تعداد فصل‌ها را بفرست.\nمثال: 3",
            reply_markup=kb_cancel()
        )
        return ADD_SERIES_SEASON_COUNT

async def add_movie_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    if text == BACK_BTN:
        await update.message.reply_text(
            "پوستر را بفرست.\nاگر پوستر نداری بنویس: /skip",
            reply_markup=kb_cancel()
        )
        return ADD_POSTER

    archive_message_id = get_archive_message_id_from_message(update.message)
    if not archive_message_id:
        await update.message.reply_text(
            "❌ یا باید message_id عددی بفرستی، یا خود فایل را از کانال آرشیو به ربات فوروارد کنی."
        )
        return ADD_MOVIE_FILE

    add_data = context.user_data["add_data"]
    add_data["archive_message_id"] = archive_message_id

    item_id = make_item_id(add_data["title"])
    item = {
        "id": item_id,
        "title": add_data["title"],
        "category": add_data["category"],
        "kind": "movie",
        "poster_file_id": add_data.get("poster_file_id"),
        "archive_message_id": add_data["archive_message_id"],
        "created_at": int(time.time()),
    }

    db = load_db()
    db["items"][item_id] = item
    db["latest_item_id"] = item_id
    save_db(db)

    context.user_data.pop("add_data", None)

    await update.message.reply_text(
        "✅ فیلم با موفقیت ثبت شد",
        reply_markup=kb_main()
    )
    return ConversationHandler.END

async def add_series_season_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text == BACK_BTN:
        await update.message.reply_text(
            "پوستر را بفرست.\nاگر پوستر نداری بنویس: /skip",
            reply_markup=kb_cancel()
        )
        return ADD_POSTER

    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("تعداد فصل باید عدد مثبت باشد")
        return ADD_SERIES_SEASON_COUNT

    add_data = context.user_data["add_data"]
    add_data["season_count"] = int(text)
    add_data["current_season"] = 1
    add_data["seasons"] = {}

    await update.message.reply_text(
        "برای فصل 1 تعداد قسمت‌ها را بفرست.\nمثال: 12",
        reply_markup=kb_cancel()
    )
    return ADD_SERIES_EPISODE_COUNT

async def add_series_episode_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text == BACK_BTN:
        await update.message.reply_text(
            "تعداد فصل‌ها را بفرست.",
            reply_markup=kb_cancel()
        )
        return ADD_SERIES_SEASON_COUNT

    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("تعداد قسمت باید عدد مثبت باشد")
        return ADD_SERIES_EPISODE_COUNT

    add_data = context.user_data["add_data"]
    add_data["current_episode_count"] = int(text)
    add_data["current_episode"] = 1
    add_data["seasons"][str(add_data["current_season"])] = {}

    season_num = add_data["current_season"]
    episode_num = add_data["current_episode"]

    await update.message.reply_text(
        f"فایل قسمت {episode_num} از فصل {season_num} را از کانال آرشیو فوروارد کن یا message_id آن را بفرست.",
        reply_markup=kb_cancel()
    )
    return ADD_SERIES_EPISODE_FILE

async def add_series_episode_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    add_data = context.user_data["add_data"]

    if text == BACK_BTN:
        season_num = add_data["current_season"]
        await update.message.reply_text(
            f"برای فصل {season_num} تعداد قسمت‌ها را بفرست.",
            reply_markup=kb_cancel()
        )
        return ADD_SERIES_EPISODE_COUNT

    archive_message_id = get_archive_message_id_from_message(update.message)
    if not archive_message_id:
        await update.message.reply_text(
            "❌ یا باید message_id عددی بفرستی، یا فایل قسمت را از کانال آرشیو فوروارد کنی."
        )
        return ADD_SERIES_EPISODE_FILE

    season_num = str(add_data["current_season"])
    episode_num = str(add_data["current_episode"])
    add_data["seasons"][season_num][episode_num] = archive_message_id

    if add_data["current_episode"] < add_data["current_episode_count"]:
        add_data["current_episode"] += 1
        await update.message.reply_text(
            f"فایل قسمت {add_data['current_episode']} از فصل {season_num} را بفرست."
        )
        return ADD_SERIES_EPISODE_FILE

    if add_data["current_season"] < add_data["season_count"]:
        add_data["current_season"] += 1
        await update.message.reply_text(
            f"برای فصل {add_data['current_season']} تعداد قسمت‌ها را بفرست.",
            reply_markup=kb_cancel()
        )
        return ADD_SERIES_EPISODE_COUNT

    item_id = make_item_id(add_data["title"])
    item = {
        "id": item_id,
        "title": add_data["title"],
        "category": add_data["category"],
        "kind": "series",
        "poster_file_id": add_data.get("poster_file_id"),
        "seasons": add_data["seasons"],
        "created_at": int(time.time()),
    }

    db = load_db()
    db["items"][item_id] = item
    db["latest_item_id"] = item_id
    save_db(db)

    context.user_data.pop("add_data", None)

    await update.message.reply_text(
        "✅ سریال با موفقیت ثبت شد",
        reply_markup=kb_main()
    )
    return ConversationHandler.END

# ================= ADMIN DELETE =================

async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_update(update):
        await update.message.reply_text("⛔ فقط ادمین")
        return
    await send_delete_page(update.effective_chat.id, context, page=0)

# ================= ADMIN EDIT =================

async def edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_update(update):
        await update.message.reply_text("⛔ فقط ادمین")
        return
    await send_edit_page(update.effective_chat.id, context, page=0)

async def edit_callback_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_admin_user(query.from_user.id):
        await query.message.reply_text("⛔ فقط ادمین")
        return ConversationHandler.END

    data = query.data
    db = load_db()

    if data.startswith("admin_edit_page:"):
        _, page = data.split(":", 1)
        await send_edit_page(query.message.chat_id, context, int(page))
        return EDIT_WAIT_TITLE

    if data.startswith("edit_item:"):
        _, item_id, page = data.split(":", 2)
        item = db["items"].get(item_id)
        if not item:
            await query.message.reply_text("❌ آیتم پیدا نشد")
            return EDIT_WAIT_TITLE

        context.user_data["edit_data"] = {"item_id": item_id, "page": int(page)}
        await send_edit_fields(query.message.chat_id, item, int(page), context)
        return EDIT_WAIT_TITLE

    if data.startswith("edit_field:title:"):
        _, _, _, item_id, page = data.split(":", 4)
        context.user_data["edit_data"] = {"item_id": item_id, "page": int(page)}
        await query.message.reply_text("عنوان جدید را بفرست:", reply_markup=kb_cancel())
        return EDIT_WAIT_TITLE

    if data.startswith("edit_field:poster:"):
        _, _, _, item_id, page = data.split(":", 4)
        context.user_data["edit_data"] = {"item_id": item_id, "page": int(page)}
        await query.message.reply_text("پوستر جدید را بفرست یا /skip برای حذف پوستر:", reply_markup=kb_cancel())
        return EDIT_WAIT_POSTER

    if data.startswith("edit_field:moviefile:"):
        _, _, _, item_id, page = data.split(":", 4)
        context.user_data["edit_data"] = {"item_id": item_id, "page": int(page)}
        await query.message.reply_text(
            "فایل جدید فیلم را از کانال آرشیو فوروارد کن یا message_id آن را بفرست:",
            reply_markup=kb_cancel()
        )
        return EDIT_WAIT_MOVIE_FILE

    if data.startswith("edit_field:seriesfile:"):
        _, _, _, item_id, page = data.split(":", 4)
        item = db["items"].get(item_id)
        if not item:
            await query.message.reply_text("❌ سریال پیدا نشد")
            return ConversationHandler.END

        context.user_data["edit_data"] = {"item_id": item_id, "page": int(page)}

        rows = []
        for season_num in sort_numeric_keys(item.get("seasons", {})):
            rows.append([InlineKeyboardButton(f"فصل {season_num}", callback_data=f"edit_series_season:{item_id}:{season_num}:{page}")])
        rows.append([InlineKeyboardButton("⬅️ بازگشت", callback_data=f"edit_item:{item_id}:{page}")])

        await query.message.reply_text(
            "فصل موردنظر برای ویرایش را انتخاب کن:",
            reply_markup=InlineKeyboardMarkup(rows)
        )
        return EDIT_WAIT_SEASON_SELECT

    if data.startswith("edit_series_season:"):
        _, item_id, season_num, page = data.split(":", 3)
        context.user_data["edit_data"] = {
            "item_id": item_id,
            "page": int(page),
            "season_num": season_num
        }
        await query.message.reply_text(
            f"تعداد قسمت‌های جدید برای فصل {season_num} را بفرست:",
            reply_markup=kb_cancel()
        )
        return EDIT_WAIT_SERIES_EPISODE_COUNT

    return EDIT_WAIT_TITLE

async def edit_title_wait(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == BACK_BTN:
        context.user_data.pop("edit_data", None)
        await update.message.reply_text("🏠 منوی اصلی", reply_markup=kb_main())
        return ConversationHandler.END

    edit_data = context.user_data.get("edit_data", {})
    item_id = edit_data.get("item_id")
    if not item_id:
        await update.message.reply_text("❌ آیتم مشخص نیست", reply_markup=kb_main())
        return ConversationHandler.END

    db = load_db()
    item = db["items"].get(item_id)
    if not item:
        await update.message.reply_text("❌ آیتم پیدا نشد", reply_markup=kb_main())
        return ConversationHandler.END

    item["title"] = text
    save_db(db)
    await update.message.reply_text("✅ عنوان ویرایش شد", reply_markup=kb_main())
    context.user_data.pop("edit_data", None)
    return ConversationHandler.END

async def edit_poster_wait(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if text == BACK_BTN:
        context.user_data.pop("edit_data", None)
        await update.message.reply_text("🏠 منوی اصلی", reply_markup=kb_main())
        return ConversationHandler.END

    edit_data = context.user_data.get("edit_data", {})
    item_id = edit_data.get("item_id")
    if not item_id:
        await update.message.reply_text("❌ آیتم مشخص نیست", reply_markup=kb_main())
        return ConversationHandler.END

    db = load_db()
    item = db["items"].get(item_id)
    if not item:
        await update.message.reply_text("❌ آیتم پیدا نشد", reply_markup=kb_main())
        return ConversationHandler.END

    if update.message.photo:
        item["poster_file_id"] = update.message.photo[-1].file_id
    else:
        await update.message.reply_text("فقط عکس بفرست یا /skip بزن")
        return EDIT_WAIT_POSTER

    save_db(db)
    await update.message.reply_text("✅ پوستر ویرایش شد", reply_markup=kb_main())
    context.user_data.pop("edit_data", None)
    return ConversationHandler.END

async def edit_skip_poster(update: Update, context: ContextTypes.DEFAULT_TYPE):
    edit_data = context.user_data.get("edit_data", {})
    item_id = edit_data.get("item_id")
    db = load_db()
    item = db["items"].get(item_id)
    if not item:
        await update.message.reply_text("❌ آیتم پیدا نشد", reply_markup=kb_main())
        return ConversationHandler.END

    item["poster_file_id"] = None
    save_db(db)
    await update.message.reply_text("✅ پوستر حذف شد", reply_markup=kb_main())
    context.user_data.pop("edit_data", None)
    return ConversationHandler.END

async def edit_movie_file_wait(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if text == BACK_BTN:
        context.user_data.pop("edit_data", None)
        await update.message.reply_text("🏠 منوی اصلی", reply_markup=kb_main())
        return ConversationHandler.END

    archive_message_id = get_archive_message_id_from_message(update.message)
    if not archive_message_id:
        await update.message.reply_text("❌ یا عدد بفرست یا فایل را از کانال آرشیو فوروارد کن")
        return EDIT_WAIT_MOVIE_FILE

    edit_data = context.user_data.get("edit_data", {})
    item_id = edit_data.get("item_id")

    db = load_db()
    item = db["items"].get(item_id)
    if not item:
        await update.message.reply_text("❌ آیتم پیدا نشد", reply_markup=kb_main())
        return ConversationHandler.END

    item["archive_message_id"] = archive_message_id
    save_db(db)

    await update.message.reply_text("✅ فایل فیلم ویرایش شد", reply_markup=kb_main())
    context.user_data.pop("edit_data", None)
    return ConversationHandler.END

async def edit_series_episode_count_wait(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text == BACK_BTN:
        context.user_data.pop("edit_data", None)
        await update.message.reply_text("🏠 منوی اصلی", reply_markup=kb_main())
        return ConversationHandler.END

    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("تعداد قسمت باید عدد مثبت باشد")
        return EDIT_WAIT_SERIES_EPISODE_COUNT

    edit_data = context.user_data.get("edit_data", {})
    edit_data["episode_count"] = int(text)
    edit_data["current_episode"] = 1
    edit_data["new_episode_map"] = {}

    season_num = edit_data["season_num"]
    await update.message.reply_text(
        f"فایل قسمت 1 از فصل {season_num} را از کانال آرشیو فوروارد کن یا message_id آن را بفرست:",
        reply_markup=kb_cancel()
    )
    return EDIT_WAIT_SERIES_EPISODE_FILE

async def edit_series_episode_file_wait(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    if text == BACK_BTN:
        context.user_data.pop("edit_data", None)
        await update.message.reply_text("🏠 منوی اصلی", reply_markup=kb_main())
        return ConversationHandler.END

    archive_message_id = get_archive_message_id_from_message(update.message)
    if not archive_message_id:
        await update.message.reply_text("❌ یا عدد بفرست یا فایل را از کانال آرشیو فوروارد کن")
        return EDIT_WAIT_SERIES_EPISODE_FILE

    edit_data = context.user_data.get("edit_data", {})
    item_id = edit_data.get("item_id")
    season_num = edit_data.get("season_num")
    current_episode = edit_data.get("current_episode")
    episode_count = edit_data.get("episode_count")

    edit_data["new_episode_map"][str(current_episode)] = archive_message_id

    if current_episode < episode_count:
        edit_data["current_episode"] += 1
        await update.message.reply_text(
            f"فایل قسمت {edit_data['current_episode']} از فصل {season_num} را بفرست:"
        )
        return EDIT_WAIT_SERIES_EPISODE_FILE

    db = load_db()
    item = db["items"].get(item_id)
    if not item:
        await update.message.reply_text("❌ سریال پیدا نشد", reply_markup=kb_main())
        return ConversationHandler.END

    item.setdefault("seasons", {})
    item["seasons"][str(season_num)] = edit_data["new_episode_map"]
    save_db(db)

    await update.message.reply_text("✅ فصل سریال ویرایش شد", reply_markup=kb_main())
    context.user_data.pop("edit_data", None)
    return ConversationHandler.END

# ================= CHANNEL POST =================

async def on_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg:
        return
    if msg.chat_id != ARCHIVE_CHANNEL_ID:
        return
    if msg.video or msg.document:
        log.info("Archive post received: message_id=%s", msg.message_id)

# ================= MAIN =================

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN تنظیم نشده")
    if not ADMIN_ID:
        raise RuntimeError("ADMIN_ID تنظیم نشده")
    if not ARCHIVE_CHANNEL_ID:
        raise RuntimeError("ARCHIVE_CHANNEL_ID تنظیم نشده")

    app = Application.builder().token(BOT_TOKEN).build()

    add_conv = ConversationHandler(
        entry_points=[CommandHandler("add", add_start)],
        states={
            ADD_KIND: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_kind)],
            ADD_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_category)],
            ADD_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_title)],
            ADD_POSTER: [
                CommandHandler("skip", add_skip_poster),
                MessageHandler(filters.PHOTO, add_poster),
            ],
            ADD_MOVIE_FILE: [
                MessageHandler(filters.ALL & ~filters.StatusUpdate.ALL, add_movie_file)
            ],
            ADD_SERIES_SEASON_COUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_series_season_count)
            ],
            ADD_SERIES_EPISODE_COUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_series_episode_count)
            ],
            ADD_SERIES_EPISODE_FILE: [
                MessageHandler(filters.ALL & ~filters.StatusUpdate.ALL, add_series_episode_file)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_chat=True,
        per_user=True,
        per_message=False,
    )

    edit_conv = ConversationHandler(
        entry_points=[CommandHandler("edit", edit_command)],
        states={
            EDIT_WAIT_TITLE: [
                CallbackQueryHandler(
                    edit_callback_entry,
                    pattern=r"^(admin_edit_page:|edit_item:|edit_field:title:|edit_field:poster:|edit_field:moviefile:|edit_field:seriesfile:|edit_series_season:)"
                ),
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_title_wait),
            ],
            EDIT_WAIT_POSTER: [
                CommandHandler("skip", edit_skip_poster),
                MessageHandler(filters.PHOTO, edit_poster_wait),
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_poster_wait),
            ],
            EDIT_WAIT_MOVIE_FILE: [
                MessageHandler(filters.ALL & ~filters.StatusUpdate.ALL, edit_movie_file_wait)
            ],
            EDIT_WAIT_SEASON_SELECT: [
                CallbackQueryHandler(edit_callback_entry, pattern=r"^(edit_series_season:|edit_item:)")
            ],
            EDIT_WAIT_SERIES_EPISODE_COUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_series_episode_count_wait)
            ],
            EDIT_WAIT_SERIES_EPISODE_FILE: [
                MessageHandler(filters.ALL & ~filters.StatusUpdate.ALL, edit_series_episode_file_wait)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_chat=True,
        per_user=True,
        per_message=False,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("last", last))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("delete", delete_command))

    app.add_handler(add_conv)
    app.add_handler(edit_conv)

    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, on_channel_post))

    log.info("BOT RUNNING")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
