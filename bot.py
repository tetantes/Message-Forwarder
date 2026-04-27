import sqlite3
import json
import os
import threading
from datetime import datetime
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# ========== CONFIG ==========
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_IDS = [int(id) for id in os.environ.get("ADMIN_IDS", "6011460052").split(",")]

if not BOT_TOKEN:
    raise Exception("BOT_TOKEN environment variable not set!")

# ========== Flask for cron ping ==========
flask_app = Flask(__name__)

@flask_app.route('/')
def health():
    return "", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host='0.0.0.0', port=port, debug=False)

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
    result = []
    for row in rows:
        result.append({
            'id': row[0],
            'source': row[1],
            'destinations': json.loads(row[2]),
            'mode': row[3],
            'footer': row[4],
            'active': row[5]
        })
    return result

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

# ========== Helper Functions ==========
async def safe_edit(context, chat_id, message_id, text, reply_markup=None):
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return True
    except:
        return False

async def send_or_edit(context, chat_id, message_id, text, reply_markup=None):
    if message_id:
        success = await safe_edit(context, chat_id, message_id, text, reply_markup)
        if success:
            return message_id
    msg = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode='Markdown')
    return msg.message_id

async def copy_message(context, chat_id, msg, footer):
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
    elif msg.voice:
        await context.bot.send_voice(chat_id=chat_id, voice=msg.voice.file_id)
    elif msg.sticker:
        await context.bot.send_sticker(chat_id=chat_id, sticker=msg.sticker.file_id)

# ========== Command Handlers ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_user(user.id, user.username, user.first_name)
    
    keyboard = [[InlineKeyboardButton("➕ New Forwarder", callback_data="new")],
                [InlineKeyboardButton("📋 My Forwarders", callback_data="my")],
                [InlineKeyboardButton("❓ Help", callback_data="help")]]
    
    await update.message.reply_text(
        "🎯 **ForwardBot**\n\nForward messages from one source to multiple destinations.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def new_forwarder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_states[user_id] = {'step': 'source'}
    await update.message.reply_text(
        "🔄 **Step 1: Set SOURCE**\n\n"
        "Forward any message from the source channel/group to me.\n"
        "Send /cancel to abort."
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_states:
        del user_states[user_id]
    await update.message.reply_text("❌ Cancelled.")

async def my_forwarders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    forwarders = get_user_forwarders(user_id)
    
    if not forwarders:
        await update.message.reply_text("📭 No forwarders. Use /new to create one.")
        return
    
    text = "🔁 **Your Forwarders**\n\n"
    for f in forwarders:
        status = "✅" if f['active'] else "⏸"
        text += f"{status} #{f['id']}: {len(f['destinations'])} dest | {f['mode']}\n"
    
    await update.message.reply_text(text, parse_mode='Markdown')

# ========== Callback Handlers ==========
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    chat_id = query.message.chat_id
    msg_id = query.message.message_id
    
    await query.answer()
    
    if data == "new":
        user_states[user_id] = {'step': 'source'}
        await send_or_edit(context, chat_id, msg_id,
            "🔄 **Step 1: Set SOURCE**\n\nForward a message from the source channel/group to me.")
    
    elif data == "my":
        forwarders = get_user_forwarders(user_id)
        if not forwarders:
            await send_or_edit(context, chat_id, msg_id, "📭 No forwarders.")
            return
        
        keyboard = []
        for f in forwarders:
            status = "✅" if f['active'] else "⏸"
            keyboard.append([InlineKeyboardButton(f"{status} Forwarder #{f['id']}", callback_data=f"view_{f['id']}")])
        keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data="menu")])
        
        await send_or_edit(context, chat_id, msg_id, "🔁 **Your Forwarders**", InlineKeyboardMarkup(keyboard))
    
    elif data == "menu":
        keyboard = [[InlineKeyboardButton("➕ New Forwarder", callback_data="new")],
                    [InlineKeyboardButton("📋 My Forwarders", callback_data="my")],
                    [InlineKeyboardButton("❓ Help", callback_data="help")]]
        await send_or_edit(context, chat_id, msg_id, "🎯 **ForwardBot**", InlineKeyboardMarkup(keyboard))
    
    elif data == "help":
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="menu")]]
        await send_or_edit(context, chat_id, msg_id,
            "❓ **Help**\n\n/new - Create forwarder\n/my - List forwarders\n/cancel - Cancel setup",
            InlineKeyboardMarkup(keyboard))
    
    elif data.startswith("view_"):
        forwarder_id = int(data.split("_")[1])
        f = get_forwarder(forwarder_id)
        if not f:
            await send_or_edit(context, chat_id, msg_id, "❌ Not found.")
            return
        
        text = f"🔁 **Forwarder #{forwarder_id}**\n"
        text += f"Source: `{f['source']}`\n"
        text += f"Destinations: {len(f['destinations'])}\n"
        text += f"Mode: {f['mode']}\n"
        text += f"Footer: {f['footer'] or '(none)'}\n"
        text += f"Active: {'✅' if f['active'] else '⏸'}"
        
        keyboard = [
            [InlineKeyboardButton("🔄 Toggle Mode", callback_data=f"mode_{forwarder_id}")],
            [InlineKeyboardButton("⏸ Pause/Resume", callback_data=f"active_{forwarder_id}")],
            [InlineKeyboardButton("🗑 Delete", callback_data=f"del_{forwarder_id}")],
            [InlineKeyboardButton("🔙 Back", callback_data="my")]
        ]
        await send_or_edit(context, chat_id, msg_id, text, InlineKeyboardMarkup(keyboard))
    
    elif data.startswith("mode_"):
        forwarder_id = int(data.split("_")[1])
        f = get_forwarder(forwarder_id)
        if f:
            new_mode = "forward" if f['mode'] == "copy" else "copy"
            update_forwarder(forwarder_id, mode=new_mode)
        await handle_callback(update, context)
    
    elif data.startswith("active_"):
        forwarder_id = int(data.split("_")[1])
        f = get_forwarder(forwarder_id)
        if f:
            update_forwarder(forwarder_id, active=0 if f['active'] else 1)
        await handle_callback(update, context)
    
    elif data.startswith("del_"):
        forwarder_id = int(data.split("_")[1])
        delete_forwarder(forwarder_id)
        await send_or_edit(context, chat_id, msg_id, "✅ Deleted.", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="my")]]))

# ========== Message Handlers ==========
async def handle_forwarded(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    msg = update.effective_message
    
    if not msg.forward_from_chat:
        await update.message.reply_text("❌ Forward a message from a channel/group.")
        return
    
    chat_id = str(msg.forward_from_chat.id)
    
    if user_id not in user_states:
        await update.message.reply_text("❌ No active setup. Use /new")
        return
    
    state = user_states[user_id]
    
    if state.get('step') == 'source':
        state['source'] = chat_id
        state['step'] = 'destinations'
        state['destinations'] = []
        await update.message.reply_text(f"✅ Source set.\n\nNow forward messages from destination channels.\nSend /done when finished.")
    
    elif state.get('step') == 'destinations':
        if chat_id not in state['destinations']:
            state['destinations'].append(chat_id)
            await update.message.reply_text(f"✅ Destination added: `{chat_id}`")
        else:
            await update.message.reply_text(f"⚠️ Already added.")

async def done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in user_states:
        await update.message.reply_text("❌ No active setup.")
        return
    
    state = user_states[user_id]
    
    if 'source' not in state or not state.get('destinations'):
        await update.message.reply_text("❌ Need source and at least one destination.")
        return
    
    forwarder_id = create_forwarder(user_id, state['source'], state['destinations'])
    del user_states[user_id]
    
    await update.message.reply_text(f"✅ Forwarder #{forwarder_id} created!\nUse /my to manage.")

async def forward_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Forward new messages from source to destinations"""
    chat_id = str(update.effective_chat.id)
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT destinations, mode, footer FROM forwarders WHERE source_chat_id = ? AND active = 1", (chat_id,))
    rows = c.fetchall()
    conn.close()
    
    for row in rows:
        destinations = json.loads(row[0])
        mode = row[1]
        footer = row[2]
        
        for dest in destinations:
            try:
                if mode == 'forward':
                    await context.bot.forward_message(chat_id=int(dest), from_chat_id=chat_id, message_id=update.effective_message.message_id)
                else:
                    await copy_message(context, int(dest), update.effective_message, footer)
            except:
                pass

# ========== Admin ==========
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
            await context.bot.send_message(uid, f"📢 {msg}")
            sent += 1
        except:
            pass
    
    await update.message.reply_text(f"✅ Sent to {sent}/{len(users)}")

# ========== Main ==========
def main():
    init_db()
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("new", new_forwarder))
    app.add_handler(CommandHandler("my", my_forwarders))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("done", done))
    app.add_handler(CommandHandler("broadcast", broadcast))
    
    # Callbacks
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    # Messages
    app.add_handler(MessageHandler(filters.FORWARDED, handle_forwarded))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, forward_messages))
    
    print("🤖 Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
