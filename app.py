import os
import time
import threading
import logging
import urllib.request
import json
import asyncio
import socket
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes, CommandHandler
from groq import AsyncGroq, RateLimitError

# ================= LOGGING SETUP =================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ================= CONFIGURATION =================
# WARNING: GitHub par kabhi apna real token mat likhna!
# Render.com par "Environment Variables" mein TELEGRAM_TOKEN set karein
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
SILENCED_USERS = {}

def get_all_api_keys():
    keys = []
    primary = os.environ.get("GROQ_API_KEY")
    if primary: keys.append(primary)
    for i in range(1, 21):
        key = os.environ.get(f"GROQ_API_KEY{i}")
        if key: keys.append(key)
    return list(set(keys))

API_KEYS = get_all_api_keys()
current_key_index = 0

def get_client():
    if not API_KEYS: return None
    return AsyncGroq(api_key=API_KEYS[current_key_index])

def rotate_key():
    global current_key_index
    if len(API_KEYS) > 1:
        current_key_index = (current_key_index + 1) % len(API_KEYS)
        return True
    return False

# ================= AI LOGIC =================
async def ai_chat(user_input):
    max_retries = len(API_KEYS) if API_KEYS else 1
    attempts = 0
    system_prompt = (
        "You are 'Santosh Dev AI'. Helpful study expert and friendly chatter. "
        "Correct wrong answers, solve doubts, under 145 chars. Hindi-English mix."
    )

    while attempts < max_retries:
        client = get_client()
        if not client: 
            return "Groq API Key missing hai bhai! Render Environment Variables me check karo."
        try:
            response = await client.chat.completions.create(
                model="llama-3.1-8b-instant", 
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_input}
                ],
                max_tokens=80,
                temperature=0.7
            )
            return response.choices[0].message.content.strip()
        except RateLimitError:
            logger.warning("Rate limit hit, key rotate kar raha hoon...")
            if not rotate_key(): break
            attempts += 1
        except Exception as e:
            logger.error(f"AI Error: {e}")
            if not rotate_key(): break
            attempts += 1
    return "Server thoda down hai bhai, thodi der me try karna!"

# ================= TELEGRAM COMMANDS =================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Naya user jab bot start kare toh usko welcome message milega"""
    user_name = update.effective_user.first_name
    logger.info(f"🚀 /start command used by {user_name}")
    await update.message.reply_text(
        f"Namaste {user_name}! 🙏 Main Santosh Dev AI hoon.\n\n"
        "Aap mujhse padhai ke doubts (Physics, Maths) pooch sakte hain. "
        "Mujhe check karne ke liye /ping likhein!"
    )

async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Test command"""
    logger.info(f"📥 Received Ping! Chat ID: {update.effective_chat.id}")
    await update.message.reply_text("Pong! 🏓 Bot 100% zinda hai! Sabhi groups me allowed hoon.")

# ================= TELEGRAM HANDLER =================
async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    chat_id = update.effective_chat.id
    chat_type = update.message.chat.type
    user_id = update.message.from_user.id
    user_name = update.message.from_user.first_name
    text = update.message.text.lower()

    logger.info(f"📥 Received in {chat_type} [{chat_id}]: '{update.message.text}'")

    if "santosh dev" in text and "chup raho" in text:
        SILENCED_USERS[user_id] = time.time() + 3600
        await update.message.reply_text(f"Theek hai {user_name}, 1 ghante ki shanti. 🤫")
        return

    if user_id in SILENCED_USERS and time.time() < SILENCED_USERS[user_id]:
        return

    bot_name = "santosh"
    study_keywords = ["doubt", "wrong", "galat", "sahi", "answer", "formula", "physics", "maths"]
    chat_keywords = ["hi", "hello", "kaise ho", "kya kar rahe"]
    
    should_reply = False
    if bot_name in text: should_reply = True
    elif any(word in text for word in study_keywords): should_reply = True
    elif chat_type == "private": should_reply = True
    elif any(text.startswith(word) for word in chat_keywords): should_reply = True

    if should_reply:
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action='typing')
            
            response = await ai_chat(f"User {user_name}: {update.message.text}")
            final_res = (response[:147] + "..") if len(response) > 150 else response
            await update.message.reply_text(final_res)
        except Exception as e:
            logger.error(f"Handler Error: {e}")

# ================= NETWORK CHECKS =================
def wait_for_internet():
    """Network chalne tak shaanti se wait karega"""
    logger.info("📡 Checking Internet and DNS connection...")
    while True:
        try:
            # Pinging Telegram API via socket (Lightweight, won't crash async loops)
            socket.create_connection(("api.telegram.org", 443), timeout=5)
            logger.info("🌐 Internet connected successfully! DNS is working.")
            break
        except OSError as e:
            logger.warning(f"⚠️ Network abhi start nahi hua. Retrying in 5 seconds... Error: {e}")
            time.sleep(5)

webhook_cleared = False
def clear_webhook():
    """Ye function kisi purane atke hue webhook ko delete kar dega"""
    global webhook_cleared
    if webhook_cleared:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook?drop_pending_updates=True"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as response:
            res = json.loads(response.read().decode())
            logger.info(f"🧹 Webhook Cleared: {res}")
            webhook_cleared = True
    except Exception as e:
        logger.warning(f"Webhook clear warning: {e}")

# ================= RUNNER =================
def run_flask():
    """Runs Flask app in background for Render/Health checks"""
    # Render default port 10000 use karta hai agar PORT env var nahi diya gaya ho
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

@app.route("/")
def index():
    return "Santosh Dev AI is running smoothly on Render!"

if __name__ == '__main__':
    logger.info("🚀 Secure Santosh Dev AI starting...")
    
    if not TELEGRAM_TOKEN:
        logger.error("❌ TELEGRAM_TOKEN set nahi hai! Kripya Render ki Environment Variables me token dalein.")
        exit(1)
        
    # 1. Start Flask
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # 2. Wait for internet connection
    wait_for_internet()
    
    # 3. Safe to start Telegram Bot
    while True:
        try:
            # Naya Event loop set karo
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            clear_webhook()

            logger.info("🤖 Initializing Telegram Bot...")
            application = ApplicationBuilder().token(TELEGRAM_TOKEN).connect_timeout(40).read_timeout(40).build()
            
            application.add_handler(CommandHandler("start", start_cmd))
            application.add_handler(CommandHandler("ping", ping_cmd)) 
            application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_msg))

            logger.info("✅ Telegram Bot is Online and Polling!")
            
            application.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
            break
            
        except Exception as e:
            logger.error(f"⚠️ Telegram Error aaya: {e}")
            logger.info("🔄 10 seconds me dobara connect karne ki koshish kar raha hoon...")
            time.sleep(10)
