import os
import json
import re
import asyncio
import logging

from telegram import *
from telegram.ext import *

BOT_TOKEN = os.getenv("BOT_TOKEN","").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID","0"))
ARCHIVE_CHANNEL_ID = int(os.getenv("ARCHIVE_CHANNEL_ID","0"))

DB_PATH="db.json"
DELETE_TIME=30

logging.basicConfig(level=logging.INFO)
log=logging.getLogger("bot")

# ================= DATABASE =================

def load_db():

    if not os.path.exists(DB_PATH):
        return {"categories":{}}

    try:
        with open(DB_PATH,"r",encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"categories":{}}

def save_db(db):

    with open(DB_PATH,"w",encoding="utf-8") as f:
        json.dump(db,f,ensure_ascii=False,indent=2)

# ================= KEYBOARD =================

def kb_main():

    return ReplyKeyboardMarkup(
        [
            ["فیلم","سریال"],
            ["کارتون","انیمیشن"],
            ["فیلم ایرانی","سریال ایرانی"],
            ["🔎 جستجو"],
            ["/last"]
        ],
        resize_keyboard=True
    )

# ================= AUTO DELETE =================

async def auto_delete(context,chat_id,msg_id):

    await asyncio.sleep(DELETE_TIME)

    try:
        await context.bot.delete_message(chat_id,msg_id)
    except:
        pass

# ================= START =================

async def start(update:Update, context:ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "👋 خوش آمدی",
        reply_markup=kb_main()
    )

# ================= LAST COMMAND (FIXED ⭐) =================

async def last(update:Update, context:ContextTypes.DEFAULT_TYPE):

    async for msg in context.bot.get_chat_history(
        chat_id=ARCHIVE_CHANNEL_ID,
        limit=1
    ):

        if msg.video:

            await update.message.reply_video(
                msg.video.file_id,
                caption=msg.caption or "آخرین فایل کانال"
            )
            return

    await update.message.reply_text("❌ چیزی پیدا نشد")

# ================= TEXT MENU =================

async def on_text(update:Update, context:ContextTypes.DEFAULT_TYPE):

    text = update.message.text
    db = load_db()

    if text in ["فیلم","سریال","کارتون","انیمیشن","فیلم ایرانی","سریال ایرانی"]:

        cat=text

        results=set()

        async for msg in context.bot.get_chat_history(
            chat_id=ARCHIVE_CHANNEL_ID,
            limit=200
        ):

            if msg.video and msg.caption and cat in msg.caption:

                name=re.findall(r"#\S+",msg.caption)

                if name:
                    results.add(name[0].replace("#",""))

        if not results:
            await update.message.reply_text("فعلاً چیزی اضافه نشده")
            return

        rows=[[r] for r in list(results)]

        await update.message.reply_text(
            cat,
            reply_markup=ReplyKeyboardMarkup(rows,resize_keyboard=True)
        )
        return

# ================= CHANNEL SYNC =================

async def on_channel_post(update:Update, context:ContextTypes.DEFAULT_TYPE):

    msg=update.channel_post

    if msg.chat_id != ARCHIVE_CHANNEL_ID:
        return

    if not msg.video:
        return

    caption=msg.caption or ""

    tags=re.findall(r"#\S+",caption)

    name=None
    cat="سریال"

    for t in tags:
        if not name:
            name=t.replace("#","")

    db=load_db()

    db["categories"].setdefault(cat,{})

    db["categories"][cat].setdefault(name,{
        "type":"single",
        "file_id":msg.video.file_id
    })

    save_db(db)

# ================= MAIN =================

def main():

    app=Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("last",last))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,on_text))
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST,on_channel_post))

    log.info("BOT RUNNING")

    app.run_polling(drop_pending_updates=True)

if __name__=="__main__":
    main()
