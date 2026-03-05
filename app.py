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

# SECURITY CONFIGURATION:
# Yahan apna Telegram User ID aur Allowed Group ID daalein (Environment Variables se bhi set kar sakte hain)
ADMIN_ID = int(os.environ.get("ADMIN_ID", "6527942155")) 
ALLOWED_GROUP_ID = int(os.environ.get("ALLOWED_GROUP_ID", "-1003706444239"))

SILENCED_USERS = {}
# Naya feature: Bot ki yaaddash (Memory) taaki wo insaano ki tarah pichli baat yaad rakhe
CHAT_HISTORY = {} 

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

# ================= SECURITY CHECK =================
def is_authorized(update: Update) -> bool:
    """Check karta hai ki user ya group authorized hai ya nahi"""
    if not update.effective_chat or not update.effective_user:
        return False
        
    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type
    user_id = update.effective_user.id

    if chat_type == "private":
        # Direct Message: Sirf Admin (Aap) use kar sakte hain
        return user_id == ADMIN_ID
    elif chat_type in ["group", "supergroup"]:
        # Group: Sirf allowed group me chalega
        return chat_id == ALLOWED_GROUP_ID
        
    return False

# ================= AI LOGIC =================
async def ai_chat(user_input, chat_id, user_name):
    max_retries = len(API_KEYS) if API_KEYS else 1
    attempts = 0
    
    # Prompt ko human-like, adaptive aur smart banaya gaya hai
    system_prompt = (
        "Tumhara naam 'Santosh Dev' hai. Tum ek group ke normal, smart aur friendly member ho (AI bilkul nahi lagna chahiye). "
        "Rules:\n"
        "1. Baar-baar apna introduction ('Main Santosh Dev hoon') mat do. Direct baat shuru karo.\n"
        "2. Samne wale ki tone aur vibe ko pakdo. Agar wo casual hain toh tum bhi casual raho. Hindi, Hinglish aur English ka mix use karo.\n"
        "3. Agar baat comedy, love, ya entertainment ki ho, toh pure fun, roasting, ya dosti wale mood me reply karo.\n"
        "4. Agar koi study doubt puche, question kare, ya koi aur galat jawab de, toh ek expert ki tarah usko sahi jawab do aur correct karo.\n"
        "5. Ek human ki tarah bina ruke naturally flow me baat karo. 1-2 unique emojis ka use karo jo baat se match karte hon.\n"
        "6. Jawab engaging rakho, bahut lamba paragraph mat likhna."
    )

    # Pichli baatein (Memory) add karna
    messages = [{"role": "system", "content": system_prompt}]
    if chat_id in CHAT_HISTORY:
        messages.extend(CHAT_HISTORY[chat_id])
    
    # Naya message add karna
    messages.append({"role": "user", "content": f"{user_name}: {user_input}"})

    while attempts < max_retries:
        client = get_client()
        if not client: 
            return "Groq API Key missing hai bhai! Render Environment Variables me check karo. 🛑"
        try:
            # max_tokens ko 400 kar diya gaya hai taaki sentences kabhi aade me cut na hon
            response = await client.chat.completions.create(
                model="llama-3.1-8b-instant", 
                messages=messages,
                max_tokens=400, 
                temperature=0.75
            )
            bot_reply = response.choices[0].message.content.strip()
            
            # History me ye conversation save karna (Sirf last 10 messages rakhenge memory bachane ke liye)
            if chat_id not in CHAT_HISTORY:
                CHAT_HISTORY[chat_id] = []
            CHAT_HISTORY[chat_id].append({"role": "user", "content": f"{user_name}: {user_input}"})
            CHAT_HISTORY[chat_id].append({"role": "assistant", "content": bot_reply})
            CHAT_HISTORY[chat_id] = CHAT_HISTORY[chat_id][-10:]
            
            return bot_reply
        except RateLimitError:
            logger.warning("Rate limit hit, key rotate kar raha hoon...")
            if not rotate_key(): break
            attempts += 1
        except Exception as e:
            logger.error(f"AI Error: {e}")
            if not rotate_key(): break
            attempts += 1
    return "Server thoda down hai bhai, thodi der me try karna! 🛠️"

# ================= TELEGRAM COMMANDS =================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Naya user jab bot start kare toh usko welcome message milega (Agar authorized hai)"""
    if not is_authorized(update):
        logger.warning(f"Unauthorized /start attempt by User: {update.effective_user.id}")
        return

    user_name = update.effective_user.first_name
    logger.info(f"🚀 /start command used by {user_name}")
    await update.message.reply_text(
        f"Namaste {user_name}! 🙏 Main Santosh Dev hoon.\n\n"
        "Padhai ho ya masti, main dono me expert hoon! Pucho kya puchna hai. ✨"
    )

async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Test command"""
    if not is_authorized(update):
        return
        
    logger.info(f"📥 Received Ping! Chat ID: {update.effective_chat.id}")
    await update.message.reply_text("Pong! 🏓 Bot 100% zinda hai aur group me active hai. 🛡️")

# ================= TELEGRAM HANDLER =================
async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    # Security Check
    if not is_authorized(update):
        logger.warning(f"Unauthorized message attempt from User: {update.message.from_user.id} in Chat: {update.effective_chat.id}")
        return

    chat_id = update.effective_chat.id
    chat_type = update.message.chat.type
    user_id = update.message.from_user.id
    user_name = update.message.from_user.first_name
    text = update.message.text.lower()

    logger.info(f"📥 Received in {chat_type} [{chat_id}]: '{update.message.text}'")

    if "santosh dev" in text and "chup raho" in text:
        SILENCED_USERS[user_id] = time.time() + 3600
        await update.message.reply_text(f"Theek hai bhai {user_name}, 1 ghante ke liye shant ho raha hoon. 🤐")
        return

    if user_id in SILENCED_USERS and time.time() < SILENCED_USERS[user_id]:
        return

    bot_name = "santosh"
    
    # Naye aur zyada smart keywords jo bot ko batayenge ki kab bolna hai
    study_keywords = ["doubt", "wrong", "galat", "sahi", "answer", "formula", "physics", "maths", "chemistry", "question", "sawal", "kya hoga", "kaise hoga"]
    fun_keywords = ["comedy", "joke", "haha", "hehe", "lol", "pyaar", "love", "gf", "bf", "movie", "song", "mazak", "masti", "entertainment", "bhai"]
    
    # Agar user seedha bot ke message ka reply kare
    is_reply_to_bot = False
    if update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id:
        is_reply_to_bot = True
    
    should_reply = False
    
    # 1. Agar sidha naam liya ya DM me ho, toh humesha reply karega
    if bot_name in text or is_reply_to_bot or chat_type == "private": 
        should_reply = True
    # 2. Agar group me study/wrong answer ka discussion chal raha ho
    elif any(word in text for word in study_keywords): 
        should_reply = True
    # 3. Agar group me comedy, love ya fun ki baat ho rahi ho
    elif any(word in text for word in fun_keywords): 
        should_reply = True
    # 4. Agar koi question mark (?) se sawal puche
    elif "?" in text:
        should_reply = True

    if should_reply:
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action='typing')
            
            # Context aur yaaddash ke sath AI ko bhejna
            final_res = await ai_chat(user_input=update.message.text, chat_id=chat_id, user_name=user_name)
            
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
    return "Santosh Dev AI is running smoothly on Render! 🚀"

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
