import asyncio
import sqlite3
import json
import os
import threading
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from flask import Flask

# ========== CONFIG ==========
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
ADMIN_IDS = [int(id) for id in os.environ.get("ADMIN_IDS", "6011460052").split(",")]

# ========== Flask for cron ping ==========
flask_app = Flask(__name__)

@flask_app.route('/')
def health():
    return "", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host='0.0.0.0', port=port)

threading.Thread(target=run_flask, daemon=True).start()

# ========== Database ==========
DB_PATH = "forward_bot.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        joined_at TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS forwarders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        source_chat_id TEXT,
        destinations TEXT,
        mode TEXT DEFAULT 'copy',
        footer TEXT DEFAULT '',
        active INTEGER DEFAULT 1,
        created_at TIMESTAMP
    )''')
    conn.commit()
    conn.close()

def add_user(user_id, username, first_name):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, username, first_name, joined_at) VALUES (?, ?, ?, ?)",
              (user_id, username, first_name, datetime.now()))
    conn.commit()
    conn.close()

def get_user_forwarders(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, source_chat_id, destinations, mode, footer, active FROM forwarders WHERE user_id = ?", (user_id,))
    rows = c.fetchall()
    conn.close()
    return [{
        'id': row[0],
        'source': row[1],
        'destinations': json.loads(row[2]),
        'mode': row[3],
        'footer': row[4],
        'active': row[5]
    } for row in rows]

def get_forwarder(forwarder_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id, source_chat_id, destinations, mode, footer, active FROM forwarders WHERE id = ?", (forwarder_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {
            'user_id': row[0],
            'source': row[1],
            'destinations': json.loads(row[2]),
            'mode': row[3],
            'footer': row[4],
            'active': row[5]
        }
    return None

def create_forwarder(user_id, source_chat_id, destinations):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO forwarders (user_id, source_chat_id, destinations, mode, footer, active, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
              (user_id, source_chat_id, json.dumps(destinations), 'copy', '', 1, datetime.now()))
    forwarder_id = c.lastrowid
    conn.commit()
    conn.close()
    return forwarder_id

def update_forwarder(forwarder_id, **kwargs):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    for key, value in kwargs.items():
        if key == 'destinations':
            value = json.dumps(value)
        c.execute(f"UPDATE forwarders SET {key} = ? WHERE id = ?", (value, forwarder_id))
    conn.commit()
    conn.close()

def delete_forwarder(forwarder_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM forwarders WHERE id = ?", (forwarder_id,))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users")
    rows = c.fetchall()
    conn.close()
    return [row[0] for row in rows]

# ========== User States ==========
user_states = {}

# ========== Message Helper ==========
async def edit_or_send(context, chat_id, message_id, text, reply_markup=None):
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return message_id
    except:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return msg.message_id

async def format_message(update, context, chat_id, mode, footer):
    """Copy or forward a message to destination"""
    msg = update.effective_message
    if mode == 'forward':
        await context.bot.forward_message(chat_id=chat_id, from_chat_id=update.effective_chat.id, message_id=msg.message_id)
    else:
        if msg.text:
            text = msg.text
            if footer:
                text += f"\n\n{footer}"
            await context.bot.send_message(chat_id=chat_id, text=text, parse_mode='HTML')
        elif msg.photo:
            caption = msg.caption or ""
            if footer:
                caption += f"\n\n{footer}" if caption else footer
            await context.bot.send_photo(chat_id=chat_id, photo=msg.photo[-1].file_id, caption=caption, parse_mode='HTML')
        elif msg.video:
            caption = msg.caption or ""
            if footer:
                caption += f"\n\n{footer}"
            await context.bot.send_video(chat_id=chat_id, video=msg.video.file_id, caption=caption, parse_mode='HTML')
        elif msg.document:
            caption = msg.caption or ""
            if footer:
                caption += f"\n\n{footer}"
            await context.bot.send_document(chat_id=chat_id, document=msg.document.file_id, caption=caption, parse_mode='HTML')
        elif msg.audio:
            caption = msg.caption or ""
            if footer:
                caption += f"\n\n{footer}"
            await context.bot.send_audio(chat_id=chat_id, audio=msg.audio.file_id, caption=caption, parse_mode='HTML')
        elif msg.voice:
            await context.bot.send_voice(chat_id=chat_id, voice=msg.voice.file_id)
        elif msg.sticker:
            await context.bot.send_sticker(chat_id=chat_id, sticker=msg.sticker.file_id)
        elif msg.animation:
            await context.bot.send_animation(chat_id=chat_id, animation=msg.animation.file_id)
        elif msg.video_note:
            await context.bot.send_video_note(chat_id=chat_id, video_note=msg.video_note.file_id)

# ========== Command Handlers ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_user(user.id, user.username, user.first_name)
    
    text = "🎯 **ForwardBot**\n\nForward messages from one source to multiple destinations.\n\nUse the buttons below."
    keyboard = [
        [InlineKeyboardButton("➕ New Forwarder", callback_data="new_forwarder")],
        [InlineKeyboardButton("📋 My Forwarders", callback_data="my_forwarders")],
        [InlineKeyboardButton("❓ Help", callback_data="help")]
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def my_forwarders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_forwarders(update, context, update.effective_chat.id, None, update.effective_user.id)

async def new_forwarder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    msg = update.message
    if msg:
        await msg.reply_text(
            "🔄 **Create New Forwarder**\n\n"
            "Step 1: Set **SOURCE**\n\n"
            "Forward any message from the **source** channel/group to me.\n"
            "Send /cancel to abort."
        )
    user_states[user_id] = {'step': 'awaiting_source', 'destinations': []}

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_states:
        del user_states[user_id]
    await update.message.reply_text("❌ Cancelled.")

# ========== Callback Handlers ==========
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    chat_id = query.message.chat_id
    message_id = query.message.message_id
    
    await query.answer()
    
    if data == "my_forwarders":
        await show_forwarders(update, context, chat_id, message_id, user_id)
    elif data == "new_forwarder":
        user_states[user_id] = {'step': 'awaiting_source', 'destinations': []}
        await edit_or_send(context, chat_id, message_id,
            "🔄 **Create New Forwarder**\n\n"
            "Step 1: Set **SOURCE**\n\n"
            "Forward any message from the **source** channel/group to me.\n"
            "Send /cancel to abort.")
    elif data == "help":
        await edit_or_send(context, chat_id, message_id,
            "❓ **Help**\n\n"
            "1. /start → Main menu\n"
            "2. New Forwarder → Forward source → Forward destinations → Done\n"
            "3. Copy mode = clean, Forward mode = with attribution\n\n"
            "🔙 Click back to return.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="main_menu")]]))
    elif data == "main_menu":
        await main_menu(update, context, chat_id, message_id, user_id)
    elif data.startswith("view_"):
        forwarder_id = int(data.split("_")[1])
        await forwarder_detail(update, context, chat_id, message_id, forwarder_id)
    elif data.startswith("toggle_mode_"):
        forwarder_id = int(data.split("_")[2])
        f = get_forwarder(forwarder_id)
        if f:
            new_mode = "forward" if f['mode'] == "copy" else "copy"
            update_forwarder(forwarder_id, mode=new_mode)
            await forwarder_detail(update, context, chat_id, message_id, forwarder_id)
    elif data.startswith("toggle_active_"):
        forwarder_id = int(data.split("_")[2])
        f = get_forwarder(forwarder_id)
        if f:
            new_active = 0 if f['active'] else 1
            update_forwarder(forwarder_id, active=new_active)
            await forwarder_detail(update, context, chat_id, message_id, forwarder_id)
    elif data.startswith("delete_"):
        forwarder_id = int(data.split("_")[1])
        delete_forwarder(forwarder_id)
        await show_forwarders(update, context, chat_id, message_id, user_id)
    elif data.startswith("footer_"):
        forwarder_id = int(data.split("_")[1])
        user_states[user_id] = {'step': 'awaiting_footer', 'forwarder_id': forwarder_id}
        await edit_or_send(context, chat_id, message_id,
            "📝 **Set Footer**\n\n"
            "Send me the text to append to every message.\n"
            "Supports HTML: `<b>bold</b>`, `<a href='url'>link</a>`\n\n"
            "Send /skip to remove footer.")
    elif data.startswith("add_dest_"):
        forwarder_id = int(data.split("_")[2])
        user_states[user_id] = {'step': 'awaiting_destination', 'forwarder_id': forwarder_id}
        await edit_or_send(context, chat_id, message_id,
            "➕ **Add Destination**\n\n"
            "Forward a message from the **destination** channel/group to me.\n"
            "Click **Done** when finished.",
            InlineKeyboardMarkup([[InlineKeyboardButton("✅ Done", callback_data=f"done_dest_{forwarder_id}")]]))
    elif data.startswith("done_dest_"):
        forwarder_id = int(data.split("_")[2])
        if user_id in user_states and 'source' in user_states[user_id]:
            source = user_states[user_id]['source']
            destinations = user_states[user_id].get('destinations', [])
            if destinations:
                create_forwarder(user_id, source, destinations)
                del user_states[user_id]
                await edit_or_send(context, chat_id, message_id, f"✅ Forwarder created with {len(destinations)} destinations!")
            else:
                await edit_or_send(context, chat_id, message_id, "❌ No destinations added.")
        else:
            await edit_or_send(context, chat_id, message_id, "❌ No source set.")

async def main_menu(update, context, chat_id, message_id, user_id):
    text = "🎯 **ForwardBot**\n\nForward messages from one source to multiple destinations."
    keyboard = [
        [InlineKeyboardButton("➕ New Forwarder", callback_data="new_forwarder")],
        [InlineKeyboardButton("📋 My Forwarders", callback_data="my_forwarders")],
        [InlineKeyboardButton("❓ Help", callback_data="help")]
    ]
    await edit_or_send(context, chat_id, message_id, text, InlineKeyboardMarkup(keyboard))

async def show_forwarders(update, context, chat_id, message_id, user_id):
    forwarders = get_user_forwarders(user_id)
    
    if not forwarders:
        text = "📭 **No forwarders yet.**\n\nClick below to create one."
        keyboard = [[InlineKeyboardButton("➕ New Forwarder", callback_data="new_forwarder")]]
        await edit_or_send(context, chat_id, message_id, text, InlineKeyboardMarkup(keyboard))
        return
    
    text = f"🔁 **Your Forwarders** ({len(forwarders)})\n\n"
    keyboard = []
    for f in forwarders:
        status = "✅" if f['active'] else "⏸"
        text += f"{status} **ID {f['id']}** → {len(f['destinations'])} dest | Mode: {f['mode']}\n"
        keyboard.append([InlineKeyboardButton(f"📌 Forwarder #{f['id']}", callback_data=f"view_{f['id']}")])
    
    keyboard.append([InlineKeyboardButton("➕ New Forwarder", callback_data="new_forwarder")])
    keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")])
    
    await edit_or_send(context, chat_id, message_id, text, InlineKeyboardMarkup(keyboard))

async def forwarder_detail(update, context, chat_id, message_id, forwarder_id):
    f = get_forwarder(forwarder_id)
    if not f:
        await edit_or_send(context, chat_id, message_id, "❌ Forwarder not found.")
        return
    
    status = "✅ Active" if f['active'] else "⏸ Paused"
    text = f"🔁 **Forwarder #{forwarder_id}**\n\n"
    text += f"📤 **Source:** `{f['source']}`\n"
    text += f"📥 **Destinations:** {len(f['destinations'])}\n"
    text += f"🔄 **Mode:** {f['mode'].upper()}\n"
    text += f"📝 **Footer:** {f['footer'] if f['footer'] else '(none)'}\n"
    text += f"⚡ **Status:** {status}\n"
    
    keyboard = [
        [InlineKeyboardButton("🔄 Toggle Mode", callback_data=f"toggle_mode_{forwarder_id}")],
        [InlineKeyboardButton("📝 Set Footer", callback_data=f"footer_{forwarder_id}")],
        [InlineKeyboardButton("⏸ Pause/Resume", callback_data=f"toggle_active_{forwarder_id}")],
        [InlineKeyboardButton("➕ Add Destination", callback_data=f"add_dest_{forwarder_id}")],
        [InlineKeyboardButton("🗑 Delete", callback_data=f"delete_{forwarder_id}")],
        [InlineKeyboardButton("🔙 Back", callback_data="my_forwarders")]
    ]
    
    await edit_or_send(context, chat_id, message_id, text, InlineKeyboardMarkup(keyboard))

# ========== Message Handlers ==========
async def handle_forwarded_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in user_states:
        await update.message.reply_text("❌ No active setup. Use /new to start.")
        return
    
    state = user_states[user_id]
    msg = update.effective_message
    
    if not msg.forward_from_chat:
        await update.message.reply_text("❌ Please forward a message from a **channel or group**.")
        return
    
    chat_id = str(msg.forward_from_chat.id)
    
    if state.get('step') == 'awaiting_source':
        user_states[user_id]['source'] = chat_id
        user_states[user_id]['step'] = 'awaiting_destinations'
        await update.message.reply_text(
            f"✅ **Source registered:** `{chat_id}`\n\n"
            "Now forward messages from **destination** channels/groups.\n"
            "Click **Done** when finished.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Done", callback_data=f"done_dest_{0}")]]),
            parse_mode='Markdown'
        )
    elif state.get('step') == 'awaiting_destination' or state.get('step') == 'awaiting_destinations':
        if chat_id not in state.get('destinations', []):
            state['destinations'].append(chat_id)
            await update.message.reply_text(f"✅ Destination added: `{chat_id}`")
        else:
            await update.message.reply_text(f"⚠️ Already added.")

async def handle_footer_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in user_states or user_states[user_id].get('step') != 'awaiting_footer':
        return
    
    footer = update.message.text
    forwarder_id = user_states[user_id]['forwarder_id']
    
    if footer == "/skip":
        footer = ""
    
    update_forwarder(forwarder_id, footer=footer)
    del user_states[user_id]
    
    await update.message.reply_text(f"✅ Footer set for forwarder #{forwarder_id}.")

async def forward_from_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT destinations, mode, footer FROM forwarders WHERE source_chat_id = ? AND active = 1", (chat_id,))
    rows = c.fetchall()
    conn.close()
    
    for row in rows:
        destinations, mode, footer = row
        for dest in json.loads(destinations):
            try:
                await format_message(update, context, int(dest), mode, footer)
            except:
                pass

# ========== Admin Broadcast ==========
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Admin only.")
        return
    
    msg = " ".join(context.args)
    if not msg:
        await update.message.reply_text("Usage: /broadcast <message>")
        return
    
    users = get_all_users()
    sent = 0
    for uid in users:
        try:
            await context.bot.send_message(uid, f"📢 **Announcement**\n\n{msg}", parse_mode='Markdown')
            sent += 1
        except:
            pass
    
    await update.message.reply_text(f"✅ Sent to {sent}/{len(users)} users.")

# ========== Main ==========
def main():
    init_db()
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("my", my_forwarders))
    app.add_handler(CommandHandler("new", new_forwarder))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("broadcast", broadcast))
    
    # Callbacks
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    # Messages
    app.add_handler(MessageHandler(filters.FORWARDED, handle_forwarded_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_footer_text))
    app.add_handler(MessageHandler(filters.ALL, forward_from_source))
    
    print("🚀 Bot started!")
    app.run_polling()

if __name__ == "__main__":
    main()
