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

logging.basicConfig(level=logging.INFO)
log=logging.getLogger("bot")

DELETE_TIME=30

# ========= DATABASE =========

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

# ========= KEYBOARD =========

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

# ========= AUTO DELETE =========

async def auto_delete(context,chat_id,msg_id):
    await asyncio.sleep(DELETE_TIME)
    try:
        await context.bot.delete_message(chat_id,msg_id)
    except:
        pass

# ========= PARSER =========

def parse_caption(text):

    tags=re.findall(r"#\S+",text or "")

    name=None
    season=None
    ep=None

    dub="sub"

    if "دوبله" in text:
        dub="dub"
    if "زیرنویس" in text:
        dub="sub"

    for t in tags:

        t=t.replace("#","")

        m=re.match(r"s(\d+)e(\d+)",t,re.I)

        if m:
            season=int(m.group(1))
            ep=int(m.group(2))

        elif not name:
            name=t

    return "سریال",name,season,ep,dub

# ========= CHANNEL SYNC =========

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

# ========= SEND EPISODE =========

async def send_episode(chat_id,context,cat,name,season,ep,dub="sub"):

    db=load_db()

    entry=db["categories"].get(cat,{}).get(name)

    if not entry:
        return

    season_data=entry["seasons"].get(str(season),{})
    ep_data=season_data.get(str(ep),{})

    file_id=ep_data.get(dub) or ep_data.get("dub") or ep_data.get("sub")

    if not file_id:
        return

    msg=await context.bot.send_video(
        chat_id,
        file_id,
        caption=f"{name} | فصل {season} | قسمت {ep}"
    )

    asyncio.create_task(auto_delete(context,chat_id,msg.message_id))

# ========= START =========

async def start(update:Update, context:ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "👋 خوش آمدی",
        reply_markup=kb_main()
    )

# ========= SEARCH =========

async def search(update:Update, context:ContextTypes.DEFAULT_TYPE):

    text=" ".join(context.args)

    db=load_db()

    result=[]

    for cat in db["categories"]:
        for name in db["categories"][cat]:
            if text.lower() in name.lower():
                result.append(name)

    await update.message.reply_text("\n".join(result[:20]) or "چیزی پیدا نشد")

# ========= ADD COMMAND =========

async def add(update:Update, context:ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != ADMIN_ID:
        return

    await update.message.reply_text(
        "📤 فایل را در کانال آرشیو بفرست\n"
        "#سریال #نام #S01E01 #دوبله یا #زیرنویس"
    )

# ========= MAIN =========

def main():

    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN not set")

    app=Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("add",add))
    app.add_handler(CommandHandler("search",search))

    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST,on_channel_post))

    log.info("Bot Running")

    app.run_polling(drop_pending_updates=True)

if __name__=="__main__":
    main()
