import telebot
from telebot import types
import sqlite3
import json
import os
import threading
from datetime import datetime
from flask import Flask

# ========== CONFIG ==========
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_IDS = [int(id) for id in os.environ.get("ADMIN_IDS", "6011460052").split(",")]

if not BOT_TOKEN:
    raise Exception("BOT_TOKEN environment variable not set!")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML')

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
def main_menu_keyboard():
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        types.InlineKeyboardButton("➕ New Forwarder", callback_data="new"),
        types.InlineKeyboardButton("📋 My Forwarders", callback_data="my"),
        types.InlineKeyboardButton("❓ Help", callback_data="help")
    )
    return keyboard

def forwarder_list_keyboard(user_id):
    forwarders = get_user_forwarders(user_id)
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for f in forwarders:
        status = "✅" if f['active'] else "⏸"
        keyboard.add(types.InlineKeyboardButton(f"{status} Forwarder #{f['id']}", callback_data=f"view_{f['id']}"))
    keyboard.add(types.InlineKeyboardButton("🔙 Main Menu", callback_data="menu"))
    return keyboard

def forwarder_detail_keyboard(forwarder_id):
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("🔄 Toggle Mode", callback_data=f"mode_{forwarder_id}"),
        types.InlineKeyboardButton("📝 Set Footer", callback_data=f"footer_{forwarder_id}")
    )
    keyboard.add(
        types.InlineKeyboardButton("⏸ Pause/Resume", callback_data=f"active_{forwarder_id}"),
        types.InlineKeyboardButton("➕ Add Destination", callback_data=f"add_dest_{forwarder_id}")
    )
    keyboard.add(types.InlineKeyboardButton("🗑 Delete", callback_data=f"del_{forwarder_id}"))
    keyboard.add(types.InlineKeyboardButton("🔙 Back", callback_data="my"))
    return keyboard

# ========== Command Handlers ==========
@bot.message_handler(commands=['start'])
def start(message):
    user = message.from_user
    add_user(user.id, user.username, user.first_name)
    bot.send_message(
        message.chat.id,
        "🎯 <b>ForwardBot</b>\n\nForward messages from one source to multiple destinations.",
        reply_markup=main_menu_keyboard()
    )

@bot.message_handler(commands=['new'])
def new_forwarder(message):
    user_id = message.from_user.id
    user_states[user_id] = {'step': 'source'}
    bot.send_message(
        message.chat.id,
        "🔄 <b>Step 1: Set SOURCE</b>\n\n"
        "Forward any message from the source channel/group to me.\n"
        "Send /cancel to abort."
    )

@bot.message_handler(commands=['my'])
def my_forwarders(message):
    user_id = message.from_user.id
    forwarders = get_user_forwarders(user_id)
    
    if not forwarders:
        bot.send_message(message.chat.id, "📭 No forwarders. Use /new to create one.")
        return
    
    text = "🔁 <b>Your Forwarders</b>\n\n"
    for f in forwarders:
        status = "✅" if f['active'] else "⏸"
        text += f"{status} #{f['id']}: {len(f['destinations'])} dest | {f['mode']}\n"
    
    bot.send_message(message.chat.id, text, reply_markup=forwarder_list_keyboard(user_id))

@bot.message_handler(commands=['cancel'])
def cancel(message):
    user_id = message.from_user.id
    if user_id in user_states:
        del user_states[user_id]
    bot.send_message(message.chat.id, "❌ Cancelled.")

@bot.message_handler(commands=['done'])
def done(message):
    user_id = message.from_user.id
    
    if user_id not in user_states:
        bot.send_message(message.chat.id, "❌ No active setup.")
        return
    
    state = user_states[user_id]
    
    if 'source' not in state or not state.get('destinations'):
        bot.send_message(message.chat.id, "❌ Need source and at least one destination.")
        return
    
    forwarder_id = create_forwarder(user_id, state['source'], state['destinations'])
    del user_states[user_id]
    
    bot.send_message(message.chat.id, f"✅ Forwarder #{forwarder_id} created!\nUse /my to manage.")

@bot.message_handler(commands=['broadcast'])
def broadcast(message):
    if message.from_user.id not in ADMIN_IDS:
        bot.send_message(message.chat.id, "❌ Admin only.")
        return
    
    msg = message.text.replace("/broadcast", "").strip()
    if not msg:
        bot.send_message(message.chat.id, "Usage: /broadcast <message>")
        return
    
    users = get_all_users()
    sent = 0
    for uid in users:
        try:
            bot.send_message(uid, f"📢 {msg}")
            sent += 1
        except:
            pass
    
    bot.send_message(message.chat.id, f"✅ Sent to {sent}/{len(users)} users")

# ========== Callback Handlers ==========
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    user_id = call.from_user.id
    data = call.data
    
    if data == "new":
        user_states[user_id] = {'step': 'source'}
        bot.edit_message_text(
            "🔄 <b>Step 1: Set SOURCE</b>\n\nForward a message from the source channel/group to me.",
            call.message.chat.id,
            call.message.message_id
        )
    
    elif data == "my":
        forwarders = get_user_forwarders(user_id)
        if not forwarders:
            bot.edit_message_text("📭 No forwarders.", call.message.chat.id, call.message.message_id)
            return
        
        text = "🔁 <b>Your Forwarders</b>\n\n"
        for f in forwarders:
            status = "✅" if f['active'] else "⏸"
            text += f"{status} #{f['id']}: {len(f['destinations'])} dest | {f['mode']}\n"
        
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=forwarder_list_keyboard(user_id))
    
    elif data == "menu":
        bot.edit_message_text(
            "🎯 <b>ForwardBot</b>\n\nForward messages from one source to multiple destinations.",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=main_menu_keyboard()
        )
    
    elif data == "help":
        bot.edit_message_text(
            "❓ <b>Help</b>\n\n/new - Create forwarder\n/my - List forwarders\n/cancel - Cancel setup\n/done - Finish setup",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔙 Back", callback_data="menu"))
        )
    
    elif data.startswith("view_"):
        forwarder_id = int(data.split("_")[1])
        f = get_forwarder(forwarder_id)
        if not f:
            bot.edit_message_text("❌ Not found.", call.message.chat.id, call.message.message_id)
            return
        
        text = f"🔁 <b>Forwarder #{forwarder_id}</b>\n"
        text += f"Source: <code>{f['source']}</code>\n"
        text += f"Destinations: {len(f['destinations'])}\n"
        text += f"Mode: {f['mode']}\n"
        text += f"Footer: {f['footer'] or '(none)'}\n"
        text += f"Active: {'✅' if f['active'] else '⏸'}"
        
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=forwarder_detail_keyboard(forwarder_id))
    
    elif data.startswith("mode_"):
        forwarder_id = int(data.split("_")[1])
        f = get_forwarder(forwarder_id)
        if f:
            new_mode = "forward" if f['mode'] == "copy" else "copy"
            update_forwarder(forwarder_id, mode=new_mode)
            bot.answer_callback_query(call.id, f"Mode changed to {new_mode}")
        handle_callback(call)
    
    elif data.startswith("active_"):
        forwarder_id = int(data.split("_")[1])
        f = get_forwarder(forwarder_id)
        if f:
            new_active = 0 if f['active'] else 1
            update_forwarder(forwarder_id, active=new_active)
            bot.answer_callback_query(call.id, "Status toggled")
        handle_callback(call)
    
    elif data.startswith("del_"):
        forwarder_id = int(data.split("_")[1])
        delete_forwarder(forwarder_id)
        bot.answer_callback_query(call.id, "Deleted")
        bot.edit_message_text("✅ Deleted.", call.message.chat.id, call.message.message_id, reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔙 Back", callback_data="my")))
    
    elif data.startswith("footer_"):
        forwarder_id = int(data.split("_")[1])
        user_states[user_id] = {'step': 'footer', 'forwarder_id': forwarder_id}
        bot.send_message(call.message.chat.id, "📝 Send me the footer text to append to every message.\nSend /skip to remove.")

    elif data.startswith("add_dest_"):
        forwarder_id = int(data.split("_")[2])
        user_states[user_id] = {'step': 'add_dest', 'forwarder_id': forwarder_id}
        bot.send_message(call.message.chat.id, "➕ Forward a message from the destination channel/group to me.\nSend /done when finished.")

# ========== Message Handlers ==========
@bot.message_handler(func=lambda message: message.forward_from_chat is not None)
def handle_forwarded(message):
    user_id = message.from_user.id
    chat_id = str(message.forward_from_chat.id)
    
    if user_id not in user_states:
        bot.reply_to(message, "❌ No active setup. Use /new")
        return
    
    state = user_states[user_id]
    
    if state.get('step') == 'source':
        state['source'] = chat_id
        state['step'] = 'destinations'
        state['destinations'] = []
        bot.reply_to(message, f"✅ Source set.\n\nNow forward messages from destination channels.\nSend /done when finished.")
    
    elif state.get('step') == 'destinations':
        if chat_id not in state['destinations']:
            state['destinations'].append(chat_id)
            bot.reply_to(message, f"✅ Destination added: <code>{chat_id}</code>")
        else:
            bot.reply_to(message, f"⚠️ Already added.")
    
    elif state.get('step') == 'add_dest':
        forwarder_id = state.get('forwarder_id')
        f = get_forwarder(forwarder_id)
        if f and chat_id not in f['destinations']:
            destinations = f['destinations']
            destinations.append(chat_id)
            update_forwarder(forwarder_id, destinations=destinations)
            bot.reply_to(message, f"✅ Destination added to forwarder #{forwarder_id}")
        else:
            bot.reply_to(message, f"⚠️ Already added or forwarder not found.")

@bot.message_handler(func=lambda message: message.text and message.text.startswith('/skip'))
def skip_footer(message):
    user_id = message.from_user.id
    if user_id in user_states and user_states[user_id].get('step') == 'footer':
        forwarder_id = user_states[user_id]['forwarder_id']
        update_forwarder(forwarder_id, footer='')
        del user_states[user_id]
        bot.reply_to(message, f"✅ Footer removed for forwarder #{forwarder_id}")

@bot.message_handler(func=lambda message: message.text and not message.text.startswith('/'))
def handle_footer(message):
    user_id = message.from_user.id
    if user_id in user_states and user_states[user_id].get('step') == 'footer':
        forwarder_id = user_states[user_id]['forwarder_id']
        footer = message.text
        update_forwarder(forwarder_id, footer=footer)
        del user_states[user_id]
        bot.reply_to(message, f"✅ Footer set for forwarder #{forwarder_id}")

@bot.message_handler(func=lambda message: True, content_types=['text', 'photo', 'video', 'document', 'audio', 'voice', 'sticker', 'animation'])
def forward_from_source(message):
    """Forward messages from source channels to destinations"""
    chat_id = str(message.chat.id)
    
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
                    bot.forward_message(int(dest), chat_id, message.message_id)
                else:
                    if message.text:
                        text = message.text
                        if footer:
                            text += f"\n\n{footer}"
                        bot.send_message(int(dest), text)
                    elif message.photo:
                        caption = message.caption or ""
                        if footer:
                            caption += f"\n\n{footer}" if caption else footer
                        bot.send_photo(int(dest), message.photo[-1].file_id, caption=caption)
                    elif message.video:
                        caption = message.caption or ""
                        if footer:
                            caption += f"\n\n{footer}"
                        bot.send_video(int(dest), message.video.file_id, caption=caption)
                    elif message.document:
                        caption = message.caption or ""
                        if footer:
                            caption += f"\n\n{footer}"
                        bot.send_document(int(dest), message.document.file_id, caption=caption)
                    elif message.voice:
                        bot.send_voice(int(dest), message.voice.file_id)
                    elif message.sticker:
                        bot.send_sticker(int(dest), message.sticker.file_id)
            except:
                pass

# ========== Main ==========
def main():
    init_db()
    print("🤖 ForwardBot started!")
    bot.infinity_polling(skip_pending=True)

if __name__ == "__main__":
    main()
