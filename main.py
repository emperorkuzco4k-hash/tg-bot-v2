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
            ["🔎 جستجو"]
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

# ================= PARSER =================

def parse_caption(text):

    tags=re.findall(r"#\S+",text or "")

    name=None
    season=None
    ep=None
    dub="sub"

    if "دوبله" in text:
        dub="dub"

    for t in tags:

        t=t.replace("#","")

        m=re.match(r"s(\d+)e(\d+)",t,re.I)

        if m:
            season=int(m.group(1))
            ep=int(m.group(2))

        elif not name:
            name=t

    return "سریال",name,season,ep,dub

# ================= CHANNEL SYNC =================

async def on_channel_post(update:Update, context:ContextTypes.DEFAULT_TYPE):

    msg=update.channel_post

    if msg.chat_id != ARCHIVE_CHANNEL_ID:
        return

    if not msg.video:
        return

    cat,name,season,ep,dub=parse_caption(msg.caption or "")

    db=load_db()

    db["categories"].setdefault(cat,{})

    db["categories"][cat].setdefault(name,{
        "type":"series",
        "seasons":{}
    })

    if season and ep:

        db["categories"][cat][name]["seasons"].setdefault(str(season),{})
        db["categories"][cat][name]["seasons"][str(season)].setdefault(str(ep),{})

        db["categories"][cat][name]["seasons"][str(season)][str(ep)][dub]=msg.video.file_id

    save_db(db)

# ================= START =================

async def start(update:Update, context:ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "👋 خوش آمدی",
        reply_markup=kb_main()
    )

# ================= TEXT MENU FIX ⭐⭐⭐ =================

async def on_text(update:Update, context:ContextTypes.DEFAULT_TYPE):

    text = update.message.text
    db = load_db()

    # منوی اصلی
    if text in ["فیلم","سریال","کارتون","انیمیشن","فیلم ایرانی","سریال ایرانی"]:

        cat=text

        items=list(db["categories"].get(cat,{}).keys())

        if not items:
            await update.message.reply_text("فعلاً چیزی اضافه نشده")
            return

        rows=[[i] for i in items]

        await update.message.reply_text(
            f"📺 {cat}",
            reply_markup=ReplyKeyboardMarkup(rows,resize_keyboard=True)
        )
        return

    # انتخاب محتوا
    for cat in db["categories"]:

        if text in db["categories"][cat]:

            entry=db["categories"][cat][text]

            if entry["type"]=="single":

                msg=await context.bot.send_video(
                    update.message.chat_id,
                    entry["file_id"],
                    caption=text
                )

                asyncio.create_task(
                    auto_delete(context,update.message.chat_id,msg.message_id)
                )

            return

# ================= ADD =================

async def add(update:Update, context:ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != ADMIN_ID:
        return

    await update.message.reply_text(
        "📤 فایل را در کانال آرشیو بفرست\n"
        "#سریال #نام #S01E01 #دوبله یا #زیرنویس"
    )

# ================= MAIN =================

def main():

    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN not set")

    app=Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("add",add))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,on_text))

    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST,on_channel_post))

    log.info("BOT RUNNING")

    app.run_polling(drop_pending_updates=True)

if __name__=="__main__":
    main()
