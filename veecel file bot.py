import logging
import sqlite3
import telebot
import time
import threading
import json
import requests
import os
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, BotCommand
from flask import Flask, request, jsonify

# Simple logging for Termux
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Your credentials
BOT_TOKEN = "8508424494:AAFyHD_NaRH1xrQKMntvdoZz3wpKDOt4tlM"
ADMINS = [8033743774]

# Initialize bot with protect content capability
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
logger.info("🤖 Bot initialized!")

# ==================== 🛡 GLOBAL FORWARD + SCREENSHOT PROTECTION SYSTEM ====================

# Subclass existing bot for auto-protection
class SecureTeleBot(telebot.TeleBot):
    def send_message(self, chat_id, text, **kwargs):
        if get_protect_mode():
            kwargs["protect_content"] = True
        return super().send_message(chat_id, text, **kwargs)

    def send_photo(self, chat_id, photo, **kwargs):
        if get_protect_mode():
            kwargs["protect_content"] = True
        return super().send_photo(chat_id, photo, **kwargs)

    def send_video(self, chat_id, video, **kwargs):
        if get_protect_mode():
            kwargs["protect_content"] = True
        return super().send_video(chat_id, video, **kwargs)

    def send_document(self, chat_id, document, **kwargs):
        if get_protect_mode():
            kwargs["protect_content"] = True
        return super().send_document(chat_id, document, **kwargs)

    def send_audio(self, chat_id, audio, **kwargs):
        if get_protect_mode():
            kwargs["protect_content"] = True
        return super().send_audio(chat_id, audio, **kwargs)

# 🔁 Upgrade existing bot safely (reuses same token)
bot = SecureTeleBot(bot.token, parse_mode="HTML")

# COMPLETE Database class for Termux
class Database:
    def __init__(self):
        db_path = '/tmp/file_bot.db' if os.environ.get('VERCEL') else 'file_bot.db'
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.create_tables()
    
    def create_tables(self):
        cursor = self.conn.cursor()
        
        # Files table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS files (
                file_id TEXT PRIMARY KEY,
                file_type TEXT,
                original_content TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_by INTEGER,
                views INTEGER DEFAULT 0
            )
        ''')
        
        # Bulk collections table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bulk_collections (
                collection_id TEXT PRIMARY KEY,
                file_ids TEXT,
                created_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # User sessions table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_sessions (
                user_id INTEGER PRIMARY KEY,
                mode TEXT DEFAULT 'idle',
                bulk_files TEXT DEFAULT '[]'
            )
        ''')
        
        # Settings table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        
        # Force join table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS force_join (
                channel_id TEXT PRIMARY KEY,
                channel_username TEXT,
                channel_title TEXT
            )
        ''')
        
        # Users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Banned users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS banned_users (
                user_id INTEGER PRIMARY KEY,
                banned_by INTEGER,
                banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reason TEXT
            )
        ''')
        
        # Start message table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS start_message (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_text TEXT,
                media_file_id TEXT,
                media_type TEXT
            )
        ''')
        
        # Initialize default settings
        cursor.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', ('forward_lock', 'Disabled'))
        cursor.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', ('auto_delete', 'off'))
        cursor.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', ('auto_delete_minutes', '3'))
        cursor.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', ('protect_mode', 'off'))
        
        self.conn.commit()
        logger.info("✅ All database tables created!")
    
    def save_file(self, file_id: str, file_type: str, original_content: str, user_id: int):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO files 
            (file_id, file_type, original_content, created_by)
            VALUES (?, ?, ?, ?)
        ''', (file_id, file_type, original_content, user_id))
        self.conn.commit()
    
    def get_file(self, file_id: str):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM files WHERE file_id = ?', (file_id,))
        return cursor.fetchone()
    
    # Bulk collection methods
    def create_bulk_collection(self, collection_id: str, file_ids: list, user_id: int):
        cursor = self.conn.cursor()
        file_ids_json = json.dumps(file_ids)
        cursor.execute('''
            INSERT INTO bulk_collections (collection_id, file_ids, created_by)
            VALUES (?, ?, ?)
        ''', (collection_id, file_ids_json, user_id))
        self.conn.commit()
    
    def get_bulk_collection(self, collection_id: str):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM bulk_collections WHERE collection_id = ?', (collection_id,))
        return cursor.fetchone()
    
    # User session methods
    def set_user_mode(self, user_id: int, mode: str):
        """Set user mode: 'idle', 'single', 'bulk'"""
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO user_sessions (user_id, mode, bulk_files)
            VALUES (?, ?, '[]')
        ''', (user_id, mode))
        self.conn.commit()
        logger.info(f"🔄 User {user_id} mode: {mode}")
    
    def get_user_mode(self, user_id: int):
        """Get user mode"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT mode FROM user_sessions WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        return result[0] if result else 'idle'
    
    def add_file_to_bulk(self, user_id: int, file_id: str):
        """Add file to user's bulk collection"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT bulk_files FROM user_sessions WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        
        if result:
            bulk_files = json.loads(result[0] or '[]')
            bulk_files.append(file_id)
            cursor.execute('UPDATE user_sessions SET bulk_files = ? WHERE user_id = ?', 
                         (json.dumps(bulk_files), user_id))
            self.conn.commit()
            return len(bulk_files)
        return 0
    
    def get_bulk_files(self, user_id: int):
        """Get user's bulk files"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT bulk_files FROM user_sessions WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        if result and result[0]:
            return json.loads(result[0])
        return []
    
    def clear_user_session(self, user_id: int):
        """Clear user session"""
        cursor = self.conn.cursor()
        cursor.execute('DELETE FROM user_sessions WHERE user_id = ?', (user_id,))
        self.conn.commit()
        logger.info(f"🔄 User {user_id} session cleared")
    
    # Settings methods
    def set_setting(self, key: str, value: str):
        cursor = self.conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, value))
        self.conn.commit()
    
    def get_setting(self, key: str, default: str = None):
        cursor = self.conn.cursor()
        cursor.execute('SELECT value FROM settings WHERE key = ?', (key,))
        result = cursor.fetchone()
        return result[0] if result else default
    
    # Force join methods
    def add_force_join_channel(self, channel_id: str, channel_username: str = None, channel_title: str = None):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO force_join 
            (channel_id, channel_username, channel_title)
            VALUES (?, ?, ?)
        ''', (channel_id, channel_username, channel_title))
        self.conn.commit()
        logger.info(f"✅ Channel added: {channel_title} (ID: {channel_id})")
    
    def get_force_join_channels(self):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM force_join')
        return cursor.fetchall()
    
    def delete_force_join_channel(self, channel_id: str):
        cursor = self.conn.cursor()
        cursor.execute('DELETE FROM force_join WHERE channel_id = ?', (channel_id,))
        self.conn.commit()
    
    # User management
    def add_user(self, user_id: int, username: str = None, first_name: str = None, last_name: str = None):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO users (user_id, username, first_name, last_name)
            VALUES (?, ?, ?, ?)
        ''', (user_id, username, first_name, last_name))
        self.conn.commit()
    
    def get_all_users(self):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM users')
        return cursor.fetchall()
    
    def get_user_count(self):
        cursor = self.conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM users')
        return cursor.fetchone()[0]
    
    # Banned users methods
    def ban_user(self, user_id: int, banned_by: int, reason: str = "No reason provided"):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO banned_users (user_id, banned_by, reason)
            VALUES (?, ?, ?)
        ''', (user_id, banned_by, reason))
        self.conn.commit()
        logger.info(f"✅ User {user_id} banned by {banned_by}")
    
    def unban_user(self, user_id: int):
        cursor = self.conn.cursor()
        cursor.execute('DELETE FROM banned_users WHERE user_id = ?', (user_id,))
        self.conn.commit()
        logger.info(f"✅ User {user_id} unbanned")
    
    def is_banned(self, user_id: int):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM banned_users WHERE user_id = ?', (user_id,))
        return cursor.fetchone() is not None
    
    def get_banned_users(self):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM banned_users')
        return cursor.fetchall()
    
    # Start message methods
    def set_start_message(self, message_text: str, media_file_id: str = None, media_type: str = None):
        cursor = self.conn.cursor()
        cursor.execute('DELETE FROM start_message')
        cursor.execute('''
            INSERT INTO start_message (message_text, media_file_id, media_type)
            VALUES (?, ?, ?)
        ''', (message_text, media_file_id, media_type))
        self.conn.commit()

    def get_start_message(self):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM start_message LIMIT 1')
        return cursor.fetchone()

db = Database()

def is_admin(user_id: int) -> bool:
    return user_id in ADMINS

def get_protect_mode():
    """Get protect mode from database"""
    return db.get_setting('protect_mode', 'off') == 'on'

def set_protect_mode(status: bool):
    """Set protect mode in database"""
    db.set_setting('protect_mode', 'on' if status else 'off')

# Save user to database
def save_user(user_id: int, username: str = None, first_name: str = None, last_name: str = None):
    try:
        db.add_user(user_id, username, first_name, last_name)
    except Exception as e:
        logger.error(f"Error saving user: {e}")

# Force join check function
def check_force_join(user_id: int) -> bool:
    try:
        channels = db.get_force_join_channels()
        if not channels:
            return True
        
        for channel in channels:
            channel_id = channel[0]
            try:
                member = bot.get_chat_member(channel_id, user_id)
                if member.status in ['left', 'kicked']:
                    return False
            except Exception as e:
                if "CHAT_ADMIN_REQUIRED" in str(e):
                    logger.warning(f"Bot not admin in channel {channel_id}, skipping force join check")
                    continue
                logger.error(f"Force join check error for channel {channel_id}: {e}")
                continue
        
        return True
    except Exception as e:
        logger.error(f"Force join check error: {e}")
        return True

# Send force join message
def send_force_join_message(chat_id: int, user_id: int):
    try:
        channels = db.get_force_join_channels()
        if not channels:
            return True
        
        not_joined_channels = []
        
        for channel in channels:
            channel_id = channel[0]
            try:
                member = bot.get_chat_member(channel_id, user_id)
                if member.status in ['left', 'kicked']:
                    not_joined_channels.append(channel)
            except Exception as e:
                if "CHAT_ADMIN_REQUIRED" in str(e):
                    continue
                not_joined_channels.append(channel)
        
        if not not_joined_channels:
            return True
        
        keyboard = InlineKeyboardMarkup()
        
        for channel in not_joined_channels:
            channel_id = channel[0]
            channel_username = channel[1]
            channel_title = channel[2] or "Our Channel"
            
            if channel_username:
                keyboard.add(InlineKeyboardButton(
                    f"Join {channel_title}",
                    url=f"https://t.me/{channel_username.lstrip('@')}"
                ))
            else:
                try:
                    invite_link = bot.create_chat_invite_link(channel_id, member_limit=1)
                    keyboard.add(InlineKeyboardButton(
                        f"Join {channel_title}",
                        url=invite_link.invite_link
                    ))
                except Exception as e:
                    logger.error(f"Invite link error: {e}")
                    continue
        
        keyboard.add(InlineKeyboardButton("✅ I've Joined All", callback_data="check_join"))
        
        welcome_message = "📢 Please join all channels to use this bot!"
        
        bot.send_message(chat_id, welcome_message, reply_markup=keyboard)
        return False
    except Exception as e:
        logger.error(f"Welcome message error: {e}")
        return True

# Auto delete function
def schedule_auto_delete(chat_id: int, message_ids: list, file_id: str):
    auto_delete_status = db.get_setting('auto_delete', 'off')
    
    if auto_delete_status == 'off':
        return
    
    delete_time = int(db.get_setting('auto_delete_minutes', '3')) * 60
    
    def delete_messages():
        try:
            time.sleep(delete_time)
            
            # Delete all sent messages
            for msg_id in message_ids:
                try:
                    bot.delete_message(chat_id, msg_id)
                except Exception as e:
                    logger.error(f"Error deleting message {msg_id}: {e}")
            
            # Send get back button after deletion
            if db.get_setting('auto_delete', 'off') == 'on':
                bot_username = bot.get_me().username
                share_link = f"https://t.me/{bot_username}?start={file_id}"
                
                keyboard = InlineKeyboardMarkup()
                keyboard.add(InlineKeyboardButton(
                    "🔄 GET BACK",
                    url=share_link
                ))
                
                bot.send_message(
                    chat_id,
                    "❌ Files were automatically deleted for security.\n\n"
                    "🔗 Click the button below to get them back anytime:",
                    reply_markup=keyboard
                )
                
                logger.info(f"✅ GET BACK button sent for file {file_id}")
            
        except Exception as e:
            logger.error(f"Auto delete error: {e}")
    
    # Start deletion thread
    thread = threading.Thread(target=delete_messages)
    thread.daemon = True
    thread.start()

# Set bot commands for menu
def set_bot_commands():
    commands = [
        BotCommand("start", "Start the bot"),
        BotCommand("genlink", "Generate single file link"),
        BotCommand("bulkgen", "Generate multiple file links"),
        BotCommand("c", "Complete bulk generation"),
        BotCommand("admin", "Admin panel"),
        BotCommand("id", "Get user ID"),
        BotCommand("ban", "Ban a user (admin only)"),
        BotCommand("unban", "Unban a user (admin only)"),
        BotCommand("protect_on", "Enable protect mode (admin only)"),
        BotCommand("protect_off", "Disable protect mode (admin only)")
    ]
    try:
        bot.set_my_commands(commands)
        logger.info("✅ Bot commands set!")
    except Exception as e:
        logger.error(f"Error setting commands: {e}")

# Check if chat is private (not group/channel)
def is_private_chat(chat_id: int) -> bool:
    return chat_id > 0

# PROTECT MODE COMMANDS
@bot.message_handler(commands=['protect_on'])
def protect_on(message):
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        bot.reply_to(message, "❌ Not authorized!")
        return
    
    set_protect_mode(True)
    bot.reply_to(message, "🔒 Protect mode ON — Forwarding restricted for all users.")

@bot.message_handler(commands=['protect_off'])
def protect_off(message):
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        bot.reply_to(message, "❌ Not authorized!")
        return
    
    set_protect_mode(False)
    bot.reply_to(message, "🔓 Protect mode OFF — Forwarding allowed.")

# BAN COMMAND - Updated to work with both replied messages and chat IDs
@bot.message_handler(commands=['ban'])
def ban_command(message: Message):
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        bot.reply_to(message, "❌ Not authorized!")
        return
    
    try:
        target_user_id = None
        reason = ' '.join(message.text.split()[1:]) or "No reason provided"
        
        # Check if replying to a message
        if message.reply_to_message:
            target_user_id = message.reply_to_message.from_user.id
        else:
            # Check if user ID is provided in command
            parts = message.text.split()
            if len(parts) > 1:
                try:
                    target_user_id = int(parts[1])
                except ValueError:
                    bot.reply_to(message, "❌ Please provide a valid user ID or reply to a user's message.")
                    return
            else:
                bot.reply_to(message, "❌ Please reply to a user's message or provide a user ID.\nUsage: `/ban [user_id] [reason]`", parse_mode='Markdown')
                return
        
        db.ban_user(target_user_id, user_id, reason)
        bot.reply_to(message, f"✅ User {target_user_id} has been banned.\nReason: {reason}")
        
    except Exception as e:
        logger.error(f"Ban command error: {e}")
        bot.reply_to(message, f"❌ Error: {str(e)}")

# UNBAN COMMAND - Updated to work with both replied messages and chat IDs
@bot.message_handler(commands=['unban'])
def unban_command(message: Message):
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        bot.reply_to(message, "❌ Not authorized!")
        return
    
    try:
        target_user_id = None
        
        # Check if replying to a message
        if message.reply_to_message:
            target_user_id = message.reply_to_message.from_user.id
        else:
            # Check if user ID is provided in command
            parts = message.text.split()
            if len(parts) > 1:
                try:
                    target_user_id = int(parts[1])
                except ValueError:
                    bot.reply_to(message, "❌ Please provide a valid user ID or reply to a user's message.")
                    return
            else:
                bot.reply_to(message, "❌ Please reply to a user's message or provide a user ID.\nUsage: `/unban [user_id]`", parse_mode='Markdown')
                return
        
        db.unban_user(target_user_id)
        bot.reply_to(message, f"✅ User {target_user_id} has been unbanned.")
        
    except Exception as e:
        logger.error(f"Unban command error: {e}")
        bot.reply_to(message, f"❌ Error: {str(e)}")

# START COMMAND
@bot.message_handler(commands=['start'])
def start_command(message: Message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    # Block in group chats
    if not is_private_chat(chat_id):
        return
    
    logger.info(f"🚀 Start from user: {user_id}")
    
    save_user(
        user_id=user_id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name
    )
    
    if db.is_banned(user_id):
        bot.reply_to(message, "❌ You are banned from using this bot.")
        return
    
    if len(message.text.split()) > 1:
        file_id = message.text.split()[1]
        if file_id.startswith('file_') or file_id.startswith('bulk_'):
            logger.info(f"📁 File access request: {file_id}")
            if not check_force_join(user_id):
                send_force_join_message(chat_id, user_id)
                return
            handle_file_access(message)
            return
    
    if not check_force_join(user_id):
        send_force_join_message(chat_id, user_id)
        return
    
    # Clear any previous session
    db.clear_user_session(user_id)
    
    start_message_data = db.get_start_message()
    
    if start_message_data:
        message_text = start_message_data[1] or "🤖 Welcome to File Link Bot!"
        media_file_id = start_message_data[2]
        media_type = start_message_data[3]
        
        try:
            if media_file_id and media_type:
                if media_type == 'photo':
                    bot.send_photo(chat_id, media_file_id, caption=message_text)
                elif media_type == 'video':
                    bot.send_video(chat_id, media_file_id, caption=message_text)
                else:
                    bot.send_message(chat_id, message_text)
            else:
                bot.send_message(chat_id, message_text)
        except Exception as e:
            bot.send_message(chat_id, message_text)
    else:
        # Create menu keyboard
        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            InlineKeyboardButton("📁 Generate Link", callback_data="menu_genlink"),
            InlineKeyboardButton("📦 Bulk Generate", callback_data="menu_bulkgen"),
            InlineKeyboardButton("👤 Get ID", callback_data="menu_id"),
            InlineKeyboardButton("🆘 Help", callback_data="menu_help")
        )
        
        if is_admin(user_id):
            keyboard.add(InlineKeyboardButton("👑 Admin Panel", callback_data="menu_admin"))
        
        bot.send_message(chat_id, 
            "🎉 Welcome to File Link Bot!\n\n"
            "Choose an option below:",
            reply_markup=keyboard
        )

# BULK GENERATION COMMAND
@bot.message_handler(commands=['bulkgen'])
def bulkgen_command(message: Message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    # Block in group chats
    if not is_private_chat(chat_id):
        return
    
    if db.is_banned(user_id):
        bot.reply_to(message, "❌ You are banned from using this bot.")
        return
    
    if not check_force_join(user_id):
        send_force_join_message(chat_id, user_id)
        return
    
    # Set user to bulk mode
    db.set_user_mode(user_id, 'bulk')
    
    auto_delete_status = db.get_setting('auto_delete', 'off')
    auto_delete_minutes = db.get_setting('auto_delete_minutes', '3')
    
    message_text = (
        "📦 *BULK LINK GENERATION STARTED!*\n\n"
        "🚀 Now send me multiple files one by one:\n"
        "• Photos 🖼️\n• Videos 🎥\n• Documents 📄\n"
        "• Audio 🎵\n• Text 📝\n• Voice messages 🎤\n"
        "• Stickers 🤡\n\n"
        "✅ Each file will be added to your collection\n"
        "🔢 Send as many files as you want\n"
        "⏹️ When done, send /c to generate your bulk link\n\n"
        "📊 Currently collected: *0 files*"
    )
    
    if auto_delete_status == 'on':
        message_text += f"\n⏰ Auto delete: {auto_delete_minutes} minutes"
    else:
        message_text += "\n⏰ Auto delete: OFF"
    
    bot.reply_to(message, message_text, parse_mode='Markdown')
    logger.info(f"✅ User {user_id} started BULK mode")

# COMPLETE BULK COMMAND
@bot.message_handler(commands=['c'])
def complete_bulk_command(message: Message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    # Block in group chats
    if not is_private_chat(chat_id):
        return
    
    if db.is_banned(user_id):
        bot.reply_to(message, "❌ You are banned from using this bot.")
        return
    
    if db.get_user_mode(user_id) != 'bulk':
        bot.reply_to(message, "❌ No active bulk session! Use /bulkgen first.")
        return
    
    bulk_files = db.get_bulk_files(user_id)
    
    if not bulk_files:
        bot.reply_to(message, "❌ No files collected! Send files first then use /c")
        db.clear_user_session(user_id)
        return
    
    try:
        collection_id = f"bulk_{int(time.time())}_{user_id}"
        db.create_bulk_collection(collection_id, bulk_files, user_id)
        
        bot_username = bot.get_me().username
        share_link = f"https://t.me/{bot_username}?start={collection_id}"
        
        auto_delete_status = db.get_setting('auto_delete', 'off')
        auto_delete_minutes = db.get_setting('auto_delete_minutes', '3')
        
        message_text = (
            f"🎉 *BULK LINK GENERATED SUCCESSFULLY!*\n\n"
            f"📦 Total Files: *{len(bulk_files)}*\n"
            f"🔗 Your Share Link:\n`{share_link}`\n\n"
            f"📤 Anyone can access all {len(bulk_files)} files with this single link!"
        )
        
        if auto_delete_status == 'on':
            message_text += f"\n⏰ Files will auto delete after {auto_delete_minutes} minutes"
        else:
            message_text += f"\n⏰ Auto delete is OFF"
        
        # Create share buttons
        keyboard = InlineKeyboardMarkup()
        keyboard.add(
            InlineKeyboardButton("📤 Share Link", url=f"https://t.me/share/url?url={share_link}"),
            InlineKeyboardButton("🔄 Create New", callback_data="menu_bulkgen")
        )
        
        bot.reply_to(message, message_text, parse_mode='Markdown', reply_markup=keyboard)
        
        # Clear the session
        db.clear_user_session(user_id)
        
        logger.info(f"✅ Bulk collection created: {collection_id} with {len(bulk_files)} files")
        
    except Exception as e:
        logger.error(f"Bulk completion error: {e}")
        bot.reply_to(message, f"❌ Error creating bulk link: {str(e)}")
        db.clear_user_session(user_id)

# GENLINK COMMAND
@bot.message_handler(commands=['genlink'])
def genlink_command(message: Message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    # Block in group chats
    if not is_private_chat(chat_id):
        return
    
    if db.is_banned(user_id):
        bot.reply_to(message, "❌ You are banned from using this bot.")
        return
    
    if not check_force_join(user_id):
        send_force_join_message(chat_id, user_id)
        return
    
    # Set user to single mode
    db.set_user_mode(user_id, 'single')
    
    auto_delete_status = db.get_setting('auto_delete', 'off')
    auto_delete_minutes = db.get_setting('auto_delete_minutes', '3')
    
    message_text = (
        "📁 *SINGLE FILE LINK GENERATION*\n\n"
        "📤 Send me any single file:\n"
        "• Photo 🖼️\n• Video 🎥\n• Document 📄\n"
        "• Audio 🎵\n• Text 📝\n• Voice message 🎤\n"
        "• Sticker 🤡\n\n"
        "✅ I'll generate a shareable link instantly!"
    )
    
    if auto_delete_status == 'on':
        message_text += f"\n⏰ Auto delete: {auto_delete_minutes} minutes"
    else:
        message_text += "\n⏰ Auto delete: OFF"
    
    bot.reply_to(message, message_text, parse_mode='Markdown')
    logger.info(f"✅ User {user_id} started SINGLE mode")

# GET ID COMMAND - Works with both /id and !id, handles replies in groups
@bot.message_handler(commands=['id', 'ID'])
@bot.message_handler(func=lambda message: message.text and message.text.startswith('!id'))
def id_command(message: Message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    chat_type = message.chat.type
    
    try:
        # Check if it's a reply to another user's message
        if message.reply_to_message:
            replied_user = message.reply_to_message.from_user
            replied_user_id = replied_user.id
            replied_username = replied_user.username or replied_user.first_name
            
            response = (
                f"👤 Replied User: {replied_username}\n"
                f"🆔 User ID: `{replied_user_id}`\n"
                f"💬 Chat ID: `{chat_id}`\n"
                f"📝 Chat Type: {chat_type}"
            )
        else:
            # Regular command without reply
            response = (
                f"👤 Your User ID: `{user_id}`\n"
                f"💬 Chat ID: `{chat_id}`\n"
                f"📝 Chat Type: {chat_type}"
            )
        
        bot.reply_to(message, response, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"ID command error: {e}")
        bot.reply_to(message, "❌ Error retrieving IDs")

# ADMIN COMMAND
@bot.message_handler(commands=['admin'])
def admin_command(message: Message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    # Block in group chats
    if not is_private_chat(chat_id):
        return
    
    if not is_admin(user_id):
        bot.reply_to(message, "❌ Not authorized!")
        return
    
    try:
        forward_lock_status = db.get_setting('forward_lock', 'Disabled')
        auto_delete_status = db.get_setting('auto_delete', 'off')
        auto_delete_minutes = db.get_setting('auto_delete_minutes', '3')
        protect_mode_status = "ON" if get_protect_mode() else "OFF"
        channels = db.get_force_join_channels()
        banned_users = db.get_banned_users()
        total_users = db.get_user_count()
        
        keyboard = InlineKeyboardMarkup(row_width=1)
        keyboard.add(
            InlineKeyboardButton("📊 Stats", callback_data="admin_stats"),
            InlineKeyboardButton(f"🔒 Forward Lock: {forward_lock_status}", callback_data="admin_forward_lock"),
            InlineKeyboardButton(f"⏰ Auto Delete: {auto_delete_status.upper()}", callback_data="admin_auto_delete_menu"),
            InlineKeyboardButton(f"🛡️ Protect Mode: {protect_mode_status}", callback_data="admin_protect_menu"),
            InlineKeyboardButton("🔗 Add Channel", callback_data="admin_add_force_join"),
            InlineKeyboardButton("📋 View Channels", callback_data="admin_view_force_join"),
            InlineKeyboardButton("👋 Set Start Msg", callback_data="admin_set_start"),
            InlineKeyboardButton(f"🚫 Banned Users: {len(banned_users)}", callback_data="admin_banned_users"),
            InlineKeyboardButton(f"📢 Broadcast ({total_users} users)", callback_data="admin_broadcast")
        )
        
        status_text = (
            f"👑 ADMIN PANEL\n\n"
            f"🔒 Forward Lock: {forward_lock_status}\n"
            f"⏰ Auto Delete: {auto_delete_status.upper()}"
        )
        if auto_delete_status == 'on':
            status_text += f" ({auto_delete_minutes}min)"
        
        status_text += f"\n🛡️ Protect Mode: {protect_mode_status}"
        status_text += f"\n📢 Channels: {len(channels)}\n👥 Total Users: {total_users}\n🚫 Banned Users: {len(banned_users)}"
        
        bot.reply_to(message, status_text, reply_markup=keyboard)
        
    except Exception as e:
        logger.error(f"Admin command error: {e}")
        bot.reply_to(message, f"❌ Error: {str(e)}")

# Handle ALL file uploads
@bot.message_handler(content_types=['text', 'photo', 'video', 'audio', 'document', 'voice', 'sticker'])
def handle_file_upload(message: Message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    # Block in group chats
    if not is_private_chat(chat_id):
        return
    
    if db.is_banned(user_id):
        bot.reply_to(message, "❌ You are banned from using this bot.")
        return
    
    # Ignore command messages
    if message.text and message.text.startswith('/'):
        return
    
    if not check_force_join(user_id):
        send_force_join_message(chat_id, user_id)
        return
    
    user_mode = db.get_user_mode(user_id)
    logger.info(f"🔍 User {user_id} mode: {user_mode}")
    
    if user_mode == 'idle':
        # Guide user to use commands first
        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            InlineKeyboardButton("📁 Single File", callback_data="menu_genlink"),
            InlineKeyboardButton("📦 Multiple Files", callback_data="menu_bulkgen")
        )
        
        bot.reply_to(message, 
            "🤔 What do you want to do?\n\n"
            "📁 Use /genlink for single file\n"
            "📦 Use /bulkgen for multiple files\n"
            "👤 Use /id to get your user ID",
            reply_markup=keyboard
        )
        return
    
    file_id = f"file_{int(time.time())}_{user_id}"
    file_type = "text"
    original_content = ""
    
    try:
        # Determine file type and content
        if message.text:
            file_type = "text"
            original_content = message.text
        elif message.photo:
            file_type = "photo"
            original_content = message.photo[-1].file_id
        elif message.video:
            file_type = "video"
            original_content = message.video.file_id
        elif message.audio:
            file_type = "audio"
            original_content = message.audio.file_id
        elif message.document:
            file_type = "document"
            original_content = message.document.file_id
        elif message.voice:
            file_type = "voice"
            original_content = message.voice.file_id
        elif message.sticker:
            file_type = "sticker"
            original_content = message.sticker.file_id
        else:
            bot.reply_to(message, "❌ Unsupported file type!")
            return
        
        # Save file to database
        db.save_file(file_id, file_type, original_content, user_id)
        logger.info(f"✅ File saved: {file_id} (type: {file_type})")
        
        if user_mode == 'bulk':
            # BULK MODE - Add to collection
            file_count = db.add_file_to_bulk(user_id, file_id)
            
            file_type_emoji = {
                'text': '📝',
                'photo': '🖼️',
                'video': '🎥',
                'audio': '🎵',
                'document': '📄',
                'voice': '🎤',
                'sticker': '🤡'
            }
            
            emoji = file_type_emoji.get(file_type, '📁')
            bot.reply_to(message, f"{emoji} *File #{file_count} Added!*\n\n📊 Total collected: *{file_count} files*\n\n📤 Send more files or /c to complete", parse_mode='Markdown')
            logger.info(f"✅ Added to bulk. Total files: {file_count}")
            
        else:
            # SINGLE MODE - Generate link immediately
            bot_username = bot.get_me().username
            share_link = f"https://t.me/{bot_username}?start={file_id}"
            
            auto_delete_status = db.get_setting('auto_delete', 'off')
            auto_delete_minutes = db.get_setting('auto_delete_minutes', '3')
            
            file_type_emoji = {
                'text': '📝',
                'photo': '🖼️',
                'video': '🎥',
                'audio': '🎵',
                'document': '📄',
                'voice': '🎤',
                'sticker': '🤡'
            }
            
            emoji = file_type_emoji.get(file_type, '📁')
            
            message_text = (
                f"{emoji} *SINGLE LINK GENERATED!*\n\n"
                f"🔗 Your Share Link:\n`{share_link}`\n\n"
                f"📤 Anyone can access this file with the link above!"
            )
            
            if auto_delete_status == 'on':
                message_text += f"\n⏰ This file will auto delete after {auto_delete_minutes} minutes"
            else:
                message_text += f"\n⏰ Auto delete is OFF"
            
            # Create share button
            keyboard = InlineKeyboardMarkup()
            keyboard.add(InlineKeyboardButton("📤 Share Link", url=f"https://t.me/share/url?url={share_link}"))
            
            bot.reply_to(message, message_text, parse_mode='Markdown', reply_markup=keyboard)
            
            # Clear single session after generating link
            db.clear_user_session(user_id)
            logger.info(f"✅ Single file link generated: {file_id}")
            
    except Exception as e:
        logger.error(f"File upload error: {e}")
        bot.reply_to(message, f"❌ Error processing file: {str(e)}")

# FILE ACCESS function
def handle_file_access(message: Message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    # Block in group chats
    if not is_private_chat(chat_id):
        return
    
    access_id = message.text.split()[1]
    
    logger.info(f"File access attempt: {access_id} by {user_id}")
    
    if db.is_banned(user_id):
        bot.reply_to(message, "❌ You are banned from accessing files.")
        return
    
    if not check_force_join(user_id):
        send_force_join_message(message.chat.id, user_id)
        return
    
    forward_lock_status = db.get_setting('forward_lock', 'Disabled')
    if forward_lock_status == 'Enabled':
        bot.reply_to(message, "❌ File sharing is currently disabled by admin.")
        return
    
    # Check protect mode
    if get_protect_mode():
        protect_notice = "🛡️ *Here is Your Stuffs*\n\nThis Bot Coded by @Aotpy."
        bot.send_message(message.chat.id, protect_notice, parse_mode='Markdown')
    
    if access_id.startswith('bulk_'):
        bulk_data = db.get_bulk_collection(access_id)
        
        if not bulk_data:
            bot.reply_to(message, "❌ Bulk collection not found or expired")
            return
        
        file_ids = json.loads(bulk_data[1])
        
        if not file_ids:
            bot.reply_to(message, "❌ No files in this collection")
            return
        
        try:
            sent_message_ids = []
            
            auto_delete_status = db.get_setting('auto_delete', 'off')
            auto_delete_minutes = db.get_setting('auto_delete_minutes', '3')
            
            info_msg = bot.send_message(message.chat.id, f"📦 *Bulk Collection - {len(file_ids)} files*", parse_mode='Markdown')
            sent_message_ids.append(info_msg.message_id)
            
            if auto_delete_status == 'on':
                auto_msg = bot.send_message(message.chat.id, f"⏰ These files will auto delete after {auto_delete_minutes} minutes")
                sent_message_ids.append(auto_msg.message_id)
            
            for i, file_id in enumerate(file_ids, 1):
                file_data = db.get_file(file_id)
                if not file_data:
                    continue
                
                file_type = file_data[1]
                content = file_data[2]
                
                if file_type == 'text':
                    msg = bot.send_message(message.chat.id, content, disable_web_page_preview=True)
                    sent_message_ids.append(msg.message_id)
                elif file_type == 'photo':
                    msg = bot.send_photo(message.chat.id, content)
                    sent_message_ids.append(msg.message_id)
                elif file_type == 'video':
                    msg = bot.send_video(message.chat.id, content)
                    sent_message_ids.append(msg.message_id)
                elif file_type == 'audio':
                    msg = bot.send_audio(message.chat.id, content)
                    sent_message_ids.append(msg.message_id)
                elif file_type == 'document':
                    msg = bot.send_document(message.chat.id, content)
                    sent_message_ids.append(msg.message_id)
                elif file_type == 'voice':
                    msg = bot.send_voice(message.chat.id, content)
                    sent_message_ids.append(msg.message_id)
                elif file_type == 'sticker':
                    msg = bot.send_sticker(message.chat.id, content)
                    sent_message_ids.append(msg.message_id)
            
            complete_msg = bot.send_message(message.chat.id, f"✅ All {len(file_ids)} files sent successfully!")
            sent_message_ids.append(complete_msg.message_id)
            
            credit_msg = bot.send_message(message.chat.id, "⚡ This bot Coded by @Aotpy 🌀")
            sent_message_ids.append(credit_msg.message_id)
            
            if auto_delete_status == 'on':
                schedule_auto_delete(message.chat.id, sent_message_ids, access_id)
            
        except Exception as e:
            logger.error(f"Bulk file access error: {e}")
            bot.reply_to(message, f"❌ Error accessing bulk files: {str(e)}")
        
        return
    
    file_data = db.get_file(access_id)
    
    if not file_data:
        bot.reply_to(message, "❌ File not found or expired")
        return
    
    try:
        file_type = file_data[1]
        content = file_data[2]
        
        sent_message_ids = []
        
        auto_delete_status = db.get_setting('auto_delete', 'off')
        auto_delete_minutes = db.get_setting('auto_delete_minutes', '3')
        
        if auto_delete_status == 'on':
            info_msg = bot.send_message(message.chat.id, f"⏰ This file will auto delete after {auto_delete_minutes} minutes")
            sent_message_ids.append(info_msg.message_id)
        
        if file_type == 'text':
            msg = bot.send_message(message.chat.id, content, disable_web_page_preview=True)
            sent_message_ids.append(msg.message_id)
        elif file_type == 'photo':
            msg = bot.send_photo(message.chat.id, content)
            sent_message_ids.append(msg.message_id)
        elif file_type == 'video':
            msg = bot.send_video(message.chat.id, content)
            sent_message_ids.append(msg.message_id)
        elif file_type == 'audio':
            msg = bot.send_audio(message.chat.id, content)
            sent_message_ids.append(msg.message_id)
        elif file_type == 'document':
            msg = bot.send_document(message.chat.id, content)
            sent_message_ids.append(msg.message_id)
        elif file_type == 'voice':
            msg = bot.send_voice(message.chat.id, content)
            sent_message_ids.append(msg.message_id)
        elif file_type == 'sticker':
            msg = bot.send_sticker(message.chat.id, content)
            sent_message_ids.append(msg.message_id)
        
        credit_msg = bot.send_message(message.chat.id, "⚡ This bot Coded by @Aotpy 🌀")
        sent_message_ids.append(credit_msg.message_id)
        
        if auto_delete_status == 'on':
            schedule_auto_delete(message.chat.id, sent_message_ids, access_id)
        
    except Exception as e:
        logger.error(f"File access error: {e}")
        bot.reply_to(message, f"❌ Error: {str(e)}")

# CALLBACK HANDLERS
@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    # Block callbacks in group chats (except check_join)
    if not is_private_chat(chat_id) and call.data != "check_join":
        bot.answer_callback_query(call.id, "❌ This bot only works in private chats!")
        return
    
    try:
        if call.data == "check_join":
            if check_force_join(user_id):
                bot.delete_message(chat_id, message_id)
                bot.send_message(chat_id, "✅ Verification successful!\n\n🎉 You can now use all bot features!")
            else:
                bot.answer_callback_query(call.id, "❌ Please join all channels first!")
            return
        
        # Menu callbacks
        if call.data == "menu_genlink":
            bot.answer_callback_query(call.id, "📁 Generating link...")
            fake_msg = type('obj', (object,), {
                'chat': type('obj', (object,), {'id': chat_id}),
                'from_user': type('obj', (object,), {'id': user_id}),
                'message_id': message_id,
                'text': '/genlink',
                'reply_to_message': None
            })()
            genlink_command(fake_msg)
            return
        
        elif call.data == "menu_bulkgen":
            bot.answer_callback_query(call.id, "📦 Starting bulk generation...")
            fake_msg = type('obj', (object,), {
                'chat': type('obj', (object,), {'id': chat_id}),
                'from_user': type('obj', (object,), {'id': user_id}),
                'message_id': message_id,
                'text': '/bulkgen',
                'reply_to_message': None
            })()
            bulkgen_command(fake_msg)
            return
        
        elif call.data == "menu_id":
            bot.answer_callback_query(call.id, "👤 Getting ID...")
            response = f"👤 Your User ID: `{user_id}`\n💬 Chat ID: `{chat_id}`\n📝 Chat Type: {call.message.chat.type}"
            
            # For group chats, send as a reply to avoid permission issues
            if not is_private_chat(chat_id):
                bot.send_message(chat_id, response, parse_mode='Markdown', reply_to_message_id=message_id)
            else:
                bot.send_message(chat_id, response, parse_mode='Markdown')
            return
        
        elif call.data == "menu_help":
            bot.answer_callback_query(call.id, "🆘 Help sent!")
            help_text = (
                "🤖 *File Link Bot Help*\n\n"
                "📁 */genlink* - Create shareable link for single file\n"
                "📦 */bulkgen* - Create multiple file links (use /c to complete)\n"
                "👤 */id* or *!id* - Get your user ID\n"
                "👑 */admin* - Admin panel (admins only)\n"
                "🔨 */ban* - Ban a user (admins only)\n"
                "✅ */unban* - Unban a user (admins only)\n"
                "🛡️ */protect_on* - Enable protect mode (admins only)\n"
                "🔓 */protect_off* - Disable protect mode (admins only)\n\n"
                "⚡ *How to use:*\n"
                "1. Send /genlink and then send your file\n"
                "2. Or send /bulkgen and add multiple files\n"
                "3. Share the generated link with anyone!\n\n"
                "🆔 *ID Command Usage:*\n"
                "- `/id` or `!id` - Get your ID\n"
                "- Reply to someone with `/id` - Get their ID"
            )
            bot.send_message(chat_id, help_text, parse_mode='Markdown')
            return
        
        elif call.data == "menu_admin":
            if is_admin(user_id):
                bot.answer_callback_query(call.id, "👑 Opening admin panel...")
                fake_msg = type('obj', (object,), {
                    'chat': type('obj', (object,), {'id': chat_id}),
                    'from_user': type('obj', (object,), {'id': user_id}),
                    'message_id': message_id,
                    'text': '/admin',
                    'reply_to_message': None
                })()
                admin_command(fake_msg)
            else:
                bot.answer_callback_query(call.id, "❌ Not admin!")
            return
        
        # Admin callbacks - only for admins
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "❌ Not admin!")
            return
        
        # Admin panel callbacks
        if call.data == "admin_stats":
            cursor = db.conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM files')
            total_files = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM bulk_collections')
            total_bulk = cursor.fetchone()[0]
            
            channels = db.get_force_join_channels()
            banned_users = db.get_banned_users()
            forward_lock_status = db.get_setting('forward_lock', 'Disabled')
            auto_delete_status = db.get_setting('auto_delete', 'off')
            auto_delete_minutes = db.get_setting('auto_delete_minutes', '3')
            protect_mode_status = "ON" if get_protect_mode() else "OFF"
            total_users = db.get_user_count()
            
            stats_text = (
                f"📊 Bot Statistics\n\n"
                f"📁 Total Files: {total_files}\n"
                f"📦 Bulk Collections: {total_bulk}\n"
                f"👥 Total Users: {total_users}\n"
                f"📢 Channels: {len(channels)}\n"
                f"🚫 Banned Users: {len(banned_users)}\n"
                f"🔒 Forward Lock: {forward_lock_status}\n"
                f"⏰ Auto Delete: {auto_delete_status.upper()}"
            )
            if auto_delete_status == 'on':
                stats_text += f" ({auto_delete_minutes} minutes)"
            
            stats_text += f"\n🛡️ Protect Mode: {protect_mode_status}"
            
            keyboard = InlineKeyboardMarkup()
            keyboard.add(InlineKeyboardButton("🔙 Back", callback_data="admin_back"))
            
            bot.edit_message_text(stats_text, chat_id, message_id, reply_markup=keyboard)
            bot.answer_callback_query(call.id, "📊 Stats loaded!")
        
        elif call.data == "admin_forward_lock":
            current_status = db.get_setting('forward_lock', 'Disabled')
            new_status = 'Enabled' if current_status == 'Disabled' else 'Disabled'
            db.set_setting('forward_lock', new_status)
            
            bot.answer_callback_query(call.id, f"🔒 Forward Lock {new_status}")
            fake_msg = type('obj', (object,), {
                'chat': type('obj', (object,), {'id': chat_id}),
                'from_user': type('obj', (object,), {'id': user_id}),
                'message_id': message_id,
                'text': '/admin',
                'reply_to_message': None
            })()
            admin_command(fake_msg)
        
        elif call.data == "admin_protect_menu":
            protect_status = get_protect_mode()
            keyboard = InlineKeyboardMarkup(row_width=2)
            if protect_status:
                keyboard.add(InlineKeyboardButton("🔓 Turn OFF", callback_data="protect_off"))
                status_text = "🛡 Protect Mode: ON\n\nForward, Copy & Screenshot restricted."
            else:
                keyboard.add(InlineKeyboardButton("🔒 Turn ON", callback_data="protect_on"))
                status_text = "🛡 Protect Mode: OFF\n\nForwarding allowed."
            keyboard.add(InlineKeyboardButton("🔙 Back", callback_data="admin_back"))
            bot.edit_message_text(status_text, chat_id, message_id, reply_markup=keyboard)
            bot.answer_callback_query(call.id, "🛡 Protection menu opened")

        elif call.data == "protect_on":
            set_protect_mode(True)
            bot.answer_callback_query(call.id, "🔒 Protection Enabled")
            bot.send_message(chat_id, "✅ Protect Mode ON — All messages now locked 🔐")

        elif call.data == "protect_off":
            set_protect_mode(False)
            bot.answer_callback_query(call.id, "🔓 Protection Disabled")
            bot.send_message(chat_id, "🚫 Protect Mode OFF — Forwarding allowed again.")
       
        elif call.data == "admin_auto_delete_menu":
            auto_delete_status = db.get_setting('auto_delete', 'off')
            auto_delete_minutes = db.get_setting('auto_delete_minutes', '3')
            
            keyboard = InlineKeyboardMarkup(row_width=2)
            if auto_delete_status == 'on':
                keyboard.add(
                    InlineKeyboardButton("❌ Turn OFF", callback_data="auto_delete_off"),
                    InlineKeyboardButton("⚙️ Change Time", callback_data="auto_delete_time")
                )
                status_text = f"⏰ Auto Delete: ON ({auto_delete_minutes} minutes)\n\nSelect action:"
            else:
                keyboard.add(
                    InlineKeyboardButton("✅ Turn ON", callback_data="auto_delete_on"),
                    InlineKeyboardButton("⚙️ Set Time", callback_data="auto_delete_time")
                )
                status_text = "⏰ Auto Delete: OFF\n\nSelect action:"
            
            keyboard.add(InlineKeyboardButton("🔙 Back", callback_data="admin_back"))
            
            bot.edit_message_text(status_text, chat_id, message_id, reply_markup=keyboard)
            bot.answer_callback_query(call.id, "⚙️ Auto delete menu")
        
        elif call.data == "auto_delete_on":
            db.set_setting('auto_delete', 'on')
            bot.answer_callback_query(call.id, "✅ Auto Delete ON")
            fake_msg = type('obj', (object,), {
                'chat': type('obj', (object,), {'id': chat_id}),
                'from_user': type('obj', (object,), {'id': user_id}),
                'message_id': message_id,
                'text': '/admin',
                'reply_to_message': None
            })()
            admin_command(fake_msg)
        
        elif call.data == "auto_delete_off":
            db.set_setting('auto_delete', 'off')
            bot.answer_callback_query(call.id, "❌ Auto Delete OFF")
            fake_msg = type('obj', (object,), {
                'chat': type('obj', (object,), {'id': chat_id}),
                'from_user': type('obj', (object,), {'id': user_id}),
                'message_id': message_id,
                'text': '/admin',
                'reply_to_message': None
            })()
            admin_command(fake_msg)
        
        elif call.data == "auto_delete_time":
            bot.answer_callback_query(call.id, "⏰ Set delete time")
            msg = bot.send_message(chat_id, "Enter auto delete time in minutes (1-60):")
            bot.register_next_step_handler(msg, process_auto_delete_time)
        
        elif call.data == "admin_add_force_join":
            bot.answer_callback_query(call.id, "🔗 Adding channel...")
            msg = bot.send_message(chat_id, "📨 Forward any message from your channel OR send channel username (e.g., @channelusername):")
            bot.register_next_step_handler(msg, process_force_join_channel)
        
        elif call.data == "admin_view_force_join":
            channels = db.get_force_join_channels()
            if not channels:
                bot.answer_callback_query(call.id, "❌ No channels")
                bot.edit_message_text("📋 No channels added for force join", chat_id, message_id)
                return
            
            text = "📋 Force Join Channels:\n\n"
            for channel in channels:
                channel_id = channel[0]
                channel_username = channel[1] or "No username"
                channel_title = channel[2] or "Unknown"
                
                text += f"📢 Channel: {channel_title}\n"
                text += f"   👥 @{channel_username}\n"
                text += f"   🆔 {channel_id}\n\n"
            
            keyboard = InlineKeyboardMarkup(row_width=2)
            keyboard.add(
                InlineKeyboardButton("🗑️ Delete Channel", callback_data="admin_delete_force_join"),
                InlineKeyboardButton("🔙 Back", callback_data="admin_back")
            )
            
            bot.edit_message_text(text, chat_id, message_id, reply_markup=keyboard)
            bot.answer_callback_query(call.id, "📋 Channels list loaded!")
        
        elif call.data == "admin_delete_force_join":
            channels = db.get_force_join_channels()
            if not channels:
                bot.answer_callback_query(call.id, "❌ No channels")
                return
            
            keyboard = InlineKeyboardMarkup()
            for channel in channels:
                channel_title = channel[2] or "Unknown"
                keyboard.add(InlineKeyboardButton(
                    f"🗑️ Delete {channel_title}",
                    callback_data=f"delete_channel_{channel[0]}"
                ))
            
            keyboard.add(InlineKeyboardButton("🔙 Back", callback_data="admin_view_force_join"))
            
            bot.edit_message_text("🗑️ Select channel to delete:", chat_id, message_id, reply_markup=keyboard)
            bot.answer_callback_query(call.id, "🗑️ Delete channel")
        
        elif call.data.startswith('delete_channel_'):
            channel_id_to_delete = call.data.replace('delete_channel_', '')
            channels = db.get_force_join_channels()
            channel_title = "Unknown"
            for channel in channels:
                if channel[0] == channel_id_to_delete:
                    channel_title = channel[2] or "Unknown"
                    break
            
            db.delete_force_join_channel(channel_id_to_delete)
            bot.answer_callback_query(call.id, f"✅ {channel_title} deleted!")
            bot.edit_message_text(f"✅ '{channel_title}' deleted from force join!", chat_id, message_id)
        
        elif call.data == "admin_set_start":
            bot.answer_callback_query(call.id, "👋 Set start message")
            msg = bot.send_message(chat_id, "Send start message (text/photo/video):")
            bot.register_next_step_handler(msg, process_start_message)
        
        elif call.data == "admin_banned_users":
            banned_users = db.get_banned_users()
            if not banned_users:
                bot.edit_message_text("🚫 No banned users", chat_id, message_id)
                return
            
            text = "🚫 Banned Users:\n\n"
            for user in banned_users:
                user_id_ban = user[0]
                banned_by = user[1]
                reason = user[3] or "No reason"
                text += f"👤 User ID: `{user_id_ban}`\n"
                text += f"🔨 Banned by: `{banned_by}`\n"
                text += f"📝 Reason: {reason}\n\n"
            
            bot.edit_message_text(text, chat_id, message_id, parse_mode='Markdown')
            bot.answer_callback_query(call.id, "🚫 Banned users list")
        
        elif call.data == "admin_broadcast":
            total_users = db.get_user_count()
            if total_users == 0:
                bot.answer_callback_query(call.id, "❌ No users to broadcast!")
                return
            
            bot.answer_callback_query(call.id, "📢 Starting broadcast...")
            msg = bot.send_message(chat_id, 
                f"📢 Broadcast to {total_users} users\n\n"
                "Send your broadcast message (text/photo/video):\n"
                "⚠️ This will be sent to ALL users!"
            )
            bot.register_next_step_handler(msg, process_broadcast_message)
        
        elif call.data == "admin_back":
            fake_msg = type('obj', (object,), {
                'chat': type('obj', (object,), {'id': chat_id}),
                'from_user': type('obj', (object,), {'id': user_id}),
                'message_id': message_id,
                'text': '/admin',
                'reply_to_message': None
            })()
            admin_command(fake_msg)
            bot.answer_callback_query(call.id, "🔙 Back to admin panel")
        
    except Exception as e:
        logger.error(f"Callback error: {e}")
        bot.answer_callback_query(call.id, "❌ Error!")

# PROCESS FUNCTIONS FOR ADMIN
def process_force_join_channel(message: Message):
    try:
        if message.forward_from_chat and message.forward_from_chat.type in ['channel', 'group', 'supergroup']:
            channel_id = str(message.forward_from_chat.id)
            channel_username = getattr(message.forward_from_chat, 'username', None)
            channel_title = getattr(message.forward_from_chat, 'title', 'Unknown Channel')
            
            db.add_force_join_channel(
                channel_id=channel_id,
                channel_username=channel_username,
                channel_title=channel_title
            )
            
            success_msg = f"✅ '{channel_title}' added!"
            if channel_username:
                success_msg += f"\n👥 @{channel_username}"
            else:
                success_msg += f"\n🔒 Private channel"
                
            bot.reply_to(message, success_msg)
        
        elif message.text and message.text.startswith('@'):
            channel_username = message.text.lstrip('@')
            try:
                chat = bot.get_chat(f"@{channel_username}")
                channel_id = str(chat.id)
                channel_title = chat.title or "Unknown Channel"
                
                db.add_force_join_channel(
                    channel_id=channel_id,
                    channel_username=channel_username,
                    channel_title=channel_title
                )
                
                success_msg = f"✅ '{channel_title}' added!\n👥 @{channel_username}"
                bot.reply_to(message, success_msg)
                
            except Exception as e:
                bot.reply_to(message, f"❌ Cannot access channel @{channel_username}. Make sure:\n1. Channel exists\n2. Bot is admin in channel\n3. Username is correct")
        
        elif message.text and message.text.startswith('-100'):
            channel_id = message.text
            try:
                chat = bot.get_chat(channel_id)
                channel_username = getattr(chat, 'username', None)
                channel_title = chat.title or "Unknown Channel"
                
                db.add_force_join_channel(
                    channel_id=channel_id,
                    channel_username=channel_username,
                    channel_title=channel_title
                )
                
                success_msg = f"✅ '{channel_title}' added!"
                if channel_username:
                    success_msg += f"\n👥 @{channel_username}"
                else:
                    success_msg += f"\n🔒 Private channel"
                    
                bot.reply_to(message, success_msg)
                
            except Exception as e:
                bot.reply_to(message, f"❌ Cannot access channel with ID {channel_id}. Make sure:\n1. Channel exists\n2. Bot is admin in channel\n3. ID is correct")
        
        else:
            bot.reply_to(message, "❌ Please:\n1. Forward a message from the channel\nOR\n2. Send channel username (e.g., @channelusername)\nOR\n3. Send channel ID (e.g., -1001234567890)")
            
    except Exception as e:
        logger.error(f"Force join channel error: {e}")
        bot.reply_to(message, f"❌ Error: {str(e)}")

def process_start_message(message: Message):
    try:
        message_text = message.caption or message.text or "🤖 Welcome!"
        media_file_id = None
        media_type = None
        
        if message.photo:
            media_file_id = message.photo[-1].file_id
            media_type = 'photo'
        elif message.video:
            media_file_id = message.video.file_id
            media_type = 'video'
        
        db.set_start_message(message_text, media_file_id, media_type)
        bot.reply_to(message, "✅ Start message updated!")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

def process_auto_delete_time(message: Message):
    try:
        minutes = int(message.text)
        if 1 <= minutes <= 60:
            db.set_setting('auto_delete_minutes', str(minutes))
            bot.reply_to(message, f"✅ Auto delete time set to {minutes} minutes")
        else:
            bot.reply_to(message, "❌ Please enter a number between 1 and 60")
    except ValueError:
        bot.reply_to(message, "❌ Please enter a valid number")

def process_broadcast_message(message: Message):
    try:
        user_id = message.from_user.id
        
        if not is_admin(user_id):
            bot.reply_to(message, "❌ Not authorized!")
            return
        
        total_users = db.get_user_count()
        if total_users == 0:
            bot.reply_to(message, "❌ No users to broadcast!")
            return
        
        users = db.get_all_users()
        success_count = 0
        fail_count = 0
        
        status_msg = bot.send_message(message.chat.id, f"📤 Broadcasting to {total_users} users...\n✅ Sent: 0\n❌ Failed: 0")
        
        message_text = message.caption or message.text
        media_file_id = None
        media_type = None
        
        if message.photo:
            media_file_id = message.photo[-1].file_id
            media_type = 'photo'
        elif message.video:
            media_file_id = message.video.file_id
            media_type = 'video'
        
        for user in users:
            try:
                user_id_to_send = user[0]
                
                if media_file_id and media_type:
                    if media_type == 'photo':
                        bot.send_photo(user_id_to_send, media_file_id, caption=message_text)
                    elif media_type == 'video':
                        bot.send_video(user_id_to_send, media_file_id, caption=message_text)
                else:
                    bot.send_message(user_id_to_send, message_text)
                
                success_count += 1
                
                if success_count % 10 == 0:
                    try:
                        bot.edit_message_text(
                            f"📤 Broadcasting to {total_users} users...\n✅ Sent: {success_count}\n❌ Failed: {fail_count}",
                            message.chat.id,
                            status_msg.message_id
                        )
                    except:
                        pass
                
                time.sleep(0.1)
                
            except Exception as e:
                fail_count += 1
                logger.error(f"Broadcast error for user {user[0]}: {e}")
        
        bot.edit_message_text(
            f"✅ Broadcast Completed!\n\n"
            f"👥 Total Users: {total_users}\n"
            f"✅ Success: {success_count}\n"
            f"❌ Failed: {fail_count}\n"
            f"📊 Success Rate: {(success_count/total_users)*100:.1f}%",
            message.chat.id,
            status_msg.message_id
        )
        
    except Exception as e:
        logger.error(f"Broadcast process error: {e}")
        bot.reply_to(message, f"❌ Broadcast error: {str(e)}")

# Flask app for Vercel
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 Bot is running!"

@app.route('/' + BOT_TOKEN, methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return 'OK', 200
    return 'Bad Request', 400

@app.route('/set_webhook', methods=['GET'])
def set_webhook():
    # Get Vercel URL from environment
    vercel_url = os.environ.get('VERCEL_URL', 'https://' + request.host)
    webhook_url = vercel_url + '/' + BOT_TOKEN
    
    try:
        s = bot.set_webhook(url=webhook_url)
        if s:
            return f"✅ Webhook set: {webhook_url}", 200
        else:
            return "❌ Failed to set webhook", 500
    except Exception as e:
        return f"❌ Error: {str(e)}", 500

@app.route('/remove_webhook', methods=['GET'])
def remove_webhook():
    try:
        s = bot.remove_webhook()
        if s:
            return "✅ Webhook removed", 200
        else:
            return "❌ Failed to remove webhook", 500
    except Exception as e:
        return f"❌ Error: {str(e)}", 500

if __name__ == "__main__":
    logger.info("🚀 Starting BOT in Vercel...")
    logger.info("✅ Database initialized")
    logger.info("🔒 Forward Lock system active")
    logger.info("🛡️ Protect Mode system ready")
    logger.info("⏰ Auto Delete system ready")
    logger.info("📦 Bulk generation feature READY")
    logger.info("🔗 All commands added to menu")
    logger.info(f"🛠️ Admins: {ADMINS}")
    
    # Set bot commands
    set_bot_commands()
    
    try:
        bot_info = bot.get_me()
        logger.info(f"✅ Bot: @{bot_info.username}")
        
        # Send startup message to admins
        for admin_id in ADMINS:
            try:
                bot.send_message(admin_id, "🤖 Bot started successfully!\nUse /admin to access admin panel.")
                logger.info(f"✅ Startup message sent to admin {admin_id}")
            except Exception as e:
                logger.error(f"❌ Cannot send message to admin {admin_id}: {e}")
        
        logger.info("🎯 Bot is ready with webhooks!")
        # In Vercel, we don't start polling, we use Flask app
        app.run(host='0.0.0.0', port=8080)
    except Exception as e:
        logger.error(f"❌ Failed to start bot: {e}")