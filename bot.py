import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from flask import Flask
import threading
import os

from config import BOT_TOKEN, ADMIN_IDS
from database import init_db, add_user, get_user_forwarders, get_forwarder, create_forwarder, update_forwarder, delete_forwarder, get_all_users

# ---------- Flask for cron ping ----------
flask_app = Flask(__name__)

@flask_app.route('/')
def health():
    return "", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host='0.0.0.0', port=port)

threading.Thread(target=run_flask, daemon=True).start()

# ---------- User States ----------
user_states = {}  # user_id -> {'step': 'awaiting_source', 'forwarder_id': id, 'destinations': []}

# ---------- Message Helper ----------
async def edit_or_send(context, chat_id, message_id, text, reply_markup=None):
    """Try to edit, if fails (message too old), send new message"""
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

# ---------- Main Menu ----------
async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id=None, message_id=None):
    text = "🎯 **ForwardBot**\n\nForward messages from one source to multiple destinations.\n\nUse the buttons below to manage your forwarders."
    
    keyboard = [
        [InlineKeyboardButton("➕ New Forwarder", callback_data="new_forwarder")],
        [InlineKeyboardButton("📋 My Forwarders", callback_data="my_forwarders")],
        [InlineKeyboardButton("❓ Help", callback_data="help")]
    ]
    
    if chat_id and message_id:
        await edit_or_send(context, chat_id, message_id, text, InlineKeyboardMarkup(keyboard))
    else:
        msg = await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return msg.message_id

# ---------- Forwarders List ----------
async def show_forwarders(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id, message_id, user_id):
    forwarders = get_user_forwarders(user_id)
    
    if not forwarders:
        text = "📭 **No forwarders yet.**\n\nClick below to create your first forwarder."
        keyboard = [[InlineKeyboardButton("➕ New Forwarder", callback_data="new_forwarder")]]
        await edit_or_send(context, chat_id, message_id, text, InlineKeyboardMarkup(keyboard))
        return
    
    text = f"🔁 **Your Forwarders** ({len(forwarders)})\n\n"
    keyboard = []
    for f in forwarders:
        status = "✅" if f['active'] else "⏸"
        text += f"{status} **ID {f['id']}** → Source: `{f['source'][:20]}...` | {len(f['destinations'])} dest | Mode: {f['mode']}\n"
        keyboard.append([InlineKeyboardButton(f"📌 Forwarder #{f['id']}", callback_data=f"view_{f['id']}")])
    
    keyboard.append([InlineKeyboardButton("➕ New Forwarder", callback_data="new_forwarder")])
    keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")])
    
    await edit_or_send(context, chat_id, message_id, text, InlineKeyboardMarkup(keyboard))

# ---------- Forwarder Detail ----------
async def forwarder_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id, message_id, forwarder_id):
    forwarder = get_forwarder(forwarder_id)
    if not forwarder:
        await edit_or_send(context, chat_id, message_id, "❌ Forwarder not found.", None)
        return
    
    status = "✅ Active" if forwarder['active'] else "⏸ Paused"
    text = f"🔁 **Forwarder #{forwarder_id}**\n\n"
    text += f"📤 **Source:** `{forwarder['source']}`\n"
    text += f"📥 **Destinations:** ({len(forwarder['destinations'])})\n"
    for dest in forwarder['destinations'][:5]:
        text += f"  • `{dest}`\n"
    if len(forwarder['destinations']) > 5:
        text += f"  • ... and {len(forwarder['destinations'])-5} more\n"
    text += f"🔄 **Mode:** {forwarder['mode'].upper()}\n"
    text += f"📝 **Footer:** {forwarder['footer'] if forwarder['footer'] else '(none)'}\n"
    text += f"⚡ **Status:** {status}\n"
    
    keyboard = [
        [InlineKeyboardButton("🔄 Toggle Mode", callback_data=f"toggle_mode_{forwarder_id}")],
        [InlineKeyboardButton("📝 Set Footer", callback_data=f"footer_{forwarder_id}")],
        [InlineKeyboardButton("⏸ Pause/Resume", callback_data=f"toggle_active_{forwarder_id}")],
        [InlineKeyboardButton("➕ Add Destination", callback_data=f"add_dest_{forwarder_id}")],
        [InlineKeyboardButton("🗑 Delete Forwarder", callback_data=f"delete_{forwarder_id}")],
        [InlineKeyboardButton("🔙 Back to List", callback_data="my_forwarders")]
    ]
    
    await edit_or_send(context, chat_id, message_id, text, InlineKeyboardMarkup(keyboard))

# ---------- Callback Handlers ----------
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    chat_id = query.message.chat_id
    message_id = query.message.message_id
    
    await query.answer()
    
    # Main navigation
    if data == "main_menu":
        await main_menu(update, context, chat_id, message_id)
    elif data == "my_forwarders":
        await show_forwarders(update, context, chat_id, message_id, user_id)
    elif data == "new_forwarder":
        user_states[user_id] = {'step': 'awaiting_source', 'destinations': []}
        await edit_or_send(context, chat_id, message_id, 
            "🔄 **Create New Forwarder**\n\n"
            "Step 1/3: Set **SOURCE**\n\n"
            "Forward any message from the **source** channel/group to me.\n"
            "(Use the 'Forward' button in Telegram)\n\n"
            "❌ Click /cancel to abort.")
    elif data == "help":
        await edit_or_send(context, chat_id, message_id, 
            "❓ **Help**\n\n"
            "1. /start → Main menu\n"
            "2. New Forwarder → Forward source message → Forward dest messages → Done\n"
            "3. Each forwarder forwards from source to all destinations\n"
            "4. Copy mode = clean message, Forward mode = with attribution\n\n"
            "🔙 Click back to return.", 
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="main_menu")]]))
    
    # View specific forwarder
    elif data.startswith("view_"):
        forwarder_id = int(data.split("_")[1])
        await forwarder_detail(update, context, chat_id, message_id, forwarder_id)
    
    # Toggle mode
    elif data.startswith("toggle_mode_"):
        forwarder_id = int(data.split("_")[2])
        f = get_forwarder(forwarder_id)
        if f:
            new_mode = "forward" if f['mode'] == "copy" else "copy"
            update_forwarder(forwarder_id, mode=new_mode)
            await forwarder_detail(update, context, chat_id, message_id, forwarder_id)
    
    # Toggle active/pause
    elif data.startswith("toggle_active_"):
        forwarder_id = int(data.split("_")[2])
        f = get_forwarder(forwarder_id)
        if f:
            new_active = 0 if f['active'] else 1
            update_forwarder(forwarder_id, active=new_active)
            await forwarder_detail(update, context, chat_id, message_id, forwarder_id)
    
    # Delete forwarder
    elif data.startswith("delete_"):
        forwarder_id = int(data.split("_")[1])
        delete_forwarder(forwarder_id)
        await show_forwarders(update, context, chat_id, message_id, user_id)
    
    # Set footer
    elif data.startswith("footer_"):
        forwarder_id = int(data.split("_")[1])
        user_states[user_id] = {'step': 'awaiting_footer', 'forwarder_id': forwarder_id}
        await edit_or_send(context, chat_id, message_id,
            "📝 **Set Footer**\n\n"
            "Send me the text you want appended to every message.\n"
            "Supports HTML: `<b>bold</b>`, `<a href='url'>link</a>`\n\n"
            "Send /skip to remove footer.")
    
    # Add destination
    elif data.startswith("add_dest_"):
        forwarder_id = int(data.split("_")[2])
        user_states[user_id] = {'step': 'awaiting_destination', 'forwarder_id': forwarder_id}
        await edit_or_send(context, chat_id, message_id,
            "➕ **Add Destination**\n\n"
            "Forward any message from the **destination** channel/group to me.\n"
            "You can add multiple destinations.\n\n"
            "Click **Done** when finished.",
            InlineKeyboardMarkup([[InlineKeyboardButton("✅ Done", callback_data=f"done_dest_{forwarder_id}")]]))

# ---------- Message Handlers ----------
async def handle_forwarded_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in user_states:
        await update.message.reply_text("❌ No active setup. Use /new to start.")
        return
    
    state = user_states[user_id]
    
    # Step 1: Awaiting source
    if state['step'] == 'awaiting_source':
        if not update.effective_message.forward_from_chat:
            await update.message.reply_text("❌ Please forward a message from a **channel or group**, not a user.")
            return
        
        source_chat_id = str(update.effective_message.forward_from_chat.id)
        user_states[user_id]['source'] = source_chat_id
        user_states[user_id]['step'] = 'awaiting_destinations'
        
        await update.message.reply_text(
            f"✅ **Source registered:** `{source_chat_id}`\n\n"
            "Step 2/3: Add **DESTINATIONS**\n\n"
            "Forward messages from each destination channel/group to me.\n"
            "You can add multiple.\n\n"
            "Click **Done** when finished.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Done", callback_data="finish_forwarder")]]),
            parse_mode='Markdown'
        )
    
    # Step 2: Awaiting destinations
    elif state['step'] == 'awaiting_destinations':
        if not update.effective_message.forward_from_chat:
            await update.message.reply_text("❌ Please forward a message from a **channel or group**.")
            return
        
        dest_chat_id = str(update.effective_message.forward_from_chat.id)
        if dest_chat_id not in state.get('destinations', []):
            state['destinations'].append(dest_chat_id)
            await update.message.reply_text(f"✅ Destination added: `{dest_chat_id}`")
        else:
            await update.message.reply_text(f"⚠️ Destination `{dest_chat_id}` already added.")

# ---------- Finish Forwarder ----------
async def finish_forwarder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    
    if user_id not in user_states or 'source' not in user_states[user_id]:
        await query.edit_message_text("❌ No active forwarder setup. Use /new.")
        return
    
    state = user_states[user_id]
    source = state['source']
    destinations = state.get('destinations', [])
    
    if not destinations:
        await query.edit_message_text("❌ You need at least one destination.\n\nForward messages from destination channels/groups.")
        return
    
    forwarder_id = create_forwarder(user_id, source, destinations)
    
    # Ask for mode and footer
    user_states[user_id] = {'step': 'awaiting_mode', 'forwarder_id': forwarder_id}
    
    await query.edit_message_text(
        f"✅ **Forwarder #{forwarder_id} created!**\n\n"
        "Step 3/3: Choose settings\n\n"
        "**Mode:**\n"
        "• Copy → Clean message, no forward tag\n"
        "• Forward → Shows original attribution\n\n"
        "**Footer:** Will be appended to every message (optional)\n\n"
        "Select mode:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Copy Mode (Clean)", callback_data=f"mode_copy_{forwarder_id}")],
            [InlineKeyboardButton("🔄 Forward Mode (With Tag)", callback_data=f"mode_forward_{forwarder_id}")],
            [InlineKeyboardButton("⏩ Set Footer Later", callback_data=f"footer_later_{forwarder_id}")]
        ])
    )

async def set_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    await query.answer()
    
    forwarder_id = int(data.split("_")[2])
    mode = "copy" if "copy" in data else "forward"
    
    update_forwarder(forwarder_id, mode=mode)
    
    # Ask for footer
    user_states[user_id] = {'step': 'awaiting_footer', 'forwarder_id': forwarder_id}
    
    await query.edit_message_text(
        f"✅ Mode set to: **{mode.upper()}**\n\n"
        "📝 **Add Footer?** (Optional)\n\n"
        "Send me the text you want appended to every message.\n"
        "Supports HTML: `<b>bold</b>`, `<a href='url'>link</a>`\n\n"
        "Send /skip to finish without footer.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏩ Skip", callback_data=f"skip_footer_{forwarder_id}")]])
    )

async def skip_footer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    await query.answer()
    
    forwarder_id = int(data.split("_")[2])
    
    # Clear state
    if user_id in user_states:
        del user_states[user_id]
    
    await query.edit_message_text(
        f"✅ **Forwarder #{forwarder_id} is active!**\n\n"
        "It will forward every new message from source to all destinations.\n\n"
        "Use /my to manage your forwarders."
    )

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
    
    await update.message.reply_text(
        f"✅ **Forwarder #{forwarder_id} is active!**\n\n"
        f"Footer: {footer if footer else '(none)'}\n\n"
        "Use /my to manage your forwarders."
    )

# ---------- Cancel Command ----------
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_states:
        del user_states[user_id]
    await update.message.reply_text("❌ Operation cancelled.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]]))

# ---------- Admin Broadcast ----------
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
    
    await update.message.reply_text(f"✅ Broadcast sent to {sent}/{len(users)} users.")

# ---------- Register Message Forwarder ----------
async def forward_from_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Called when a message is posted in a channel. Forwards to destinations."""
    chat_id = str(update.effective_chat.id)
    
    # Find all forwarders with this source
    conn = sqlite3.connect("forward_bot.db")
    c = conn.cursor()
    c.execute("SELECT id, destinations, mode, footer FROM forwarders WHERE source_chat_id = ? AND active = 1", (chat_id,))
    forwarders = c.fetchall()
    conn.close()
    
    for f in forwarders:
        forwarder_id, destinations_json, mode, footer = f
        destinations = json.loads(destinations_json)
        
        for dest in destinations:
            try:
                await format_message(update, context, int(dest), mode, footer)
            except:
                pass

# ---------- Main ----------
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
    app.add_handler(CallbackQueryHandler(handle_callback, pattern="^(?!mode_).*"))
    app.add_handler(CallbackQueryHandler(set_mode, pattern="^mode_"))
    app.add_handler(CallbackQueryHandler(skip_footer, pattern="^skip_footer_"))
    app.add_handler(CallbackQueryHandler(finish_forwarder, pattern="^finish_forwarder$"))
    app.add_handler(CallbackQueryHandler(lambda u,c: None, pattern="^done_dest_"))
    
    # Messages
    app.add_handler(MessageHandler(filters.FORWARDED, handle_forwarded_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_footer_text))
    app.add_handler(MessageHandler(filters.ALL, forward_from_source))
    
    print("🚀 Bot started!")
    app.run_polling()

if __name__ == "__main__":
    main()
