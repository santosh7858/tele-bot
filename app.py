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
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")

# SECURITY CONFIGURATION:
# Yahan apna Telegram User ID aur Allowed Group ID daalein
ADMIN_ID = int(os.environ.get("ADMIN_ID", "6527942155")) 
ALLOWED_GROUP_ID = int(os.environ.get("ALLOWED_GROUP_ID", "-1003706444239"))

SILENCED_USERS = {}
CHAT_HISTORY = {} 

# NAYA FEATURE: Active Sessions track karega taaki baar baar naam na lena pade
ACTIVE_SESSIONS = {}
SESSION_TIMEOUT = 180  # 3 minute tak conversation active rahegi

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
        return user_id == ADMIN_ID
    elif chat_type in ["group", "supergroup"]:
        # Group me sabhi log baat kar payenge, par group ALLOWED hona chahiye
        return chat_id == ALLOWED_GROUP_ID
        
    return False

# ================= AI LOGIC =================
async def ai_chat(user_input, chat_id, user_name):
    max_retries = len(API_KEYS) if API_KEYS else 1
    attempts = 0
    
    # Prompt ko Kanchan Yadav ke hisaab se fast, unique aur smart banaya gaya hai
    system_prompt = (
        "Tumhara naam 'Kanchan Yadav' hai. Tum is Telegram group ki ek super smart, fast aur unique human member ho (AI bilkul nahi lagna chahiye). "
        "STRICT RULES: "
        "1. KABHI BHI greetings use mat karo (jaise 'Namaste', 'Hi', 'Hello [Name]'). Seedhe point par aao aur fast reply karo. "
        "2. KABHI BHI apna intro mat do ('Main Kanchan hoon'). "
        "3. Agar user koi doubt puche, question kare, ya galat jawab de, toh ek expert ki tarah usko sabse unique aur aasan tarike se samjhao. "
        "4. Agar baat comedy, love, ya masti ki ho, toh group ke dosto ki tarah funny aur witty reply do. "
        "5. Har message me 1-2 unique aur mast emojis zaroor lagao jo conversation se match karein. "
        "6. Ek normal insaan ki tarah naturally aur flow me baat karo (Hinglish/Hindi me). Chote 'haan', 'ok' wale messages ko pichli baat se jod kar dekho."
    )

    messages = [{"role": "system", "content": system_prompt}]
    if chat_id in CHAT_HISTORY:
        messages.extend(CHAT_HISTORY[chat_id])
    
    messages.append({"role": "user", "content": f"{user_name}: {user_input}"})

    while attempts < max_retries:
        client = get_client()
        if not client: 
            return "API Key missing hai bhai! 🛑"
        try:
            response = await client.chat.completions.create(
                model="openai/gpt-oss-120b", 
                messages=messages,
                max_tokens=400, 
                temperature=0.75
            )
            bot_reply = response.choices[0].message.content.strip()
            
            if chat_id not in CHAT_HISTORY:
                CHAT_HISTORY[chat_id] = []
            CHAT_HISTORY[chat_id].append({"role": "user", "content": f"{user_name}: {user_input}"})
            CHAT_HISTORY[chat_id].append({"role": "assistant", "content": bot_reply})
            CHAT_HISTORY[chat_id] = CHAT_HISTORY[chat_id][-12:] # History track karega
            
            return bot_reply
        except RateLimitError:
            if not rotate_key(): break
            attempts += 1
        except Exception as e:
            logger.error(f"AI Error: {e}")
            if not rotate_key(): break
            attempts += 1
    return "Server down hai bhai! 🛠️"

# ================= TELEGRAM COMMANDS =================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    await update.message.reply_text("Aa gayi main! Pucho kya doubt hai tumhara. ✨")

async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    await update.message.reply_text("Pong! 🏓 Kanchan ekdum fast chal rahi hai.")

# ================= TELEGRAM HANDLER =================
async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    if not is_authorized(update):
        return

    chat_id = update.effective_chat.id
    chat_type = update.message.chat.type
    user_id = update.message.from_user.id
    user_name = update.message.from_user.first_name
    text = update.message.text.lower()
    current_time = time.time()

    # Bot ka naam Kanchan Yadav kar diya gaya hai
    if "kanchan yadav" in text and "chup raho" in text:
        SILENCED_USERS[user_id] = current_time + 3600
        await update.message.reply_text(f"Theek hai {user_name}, 1 ghante ke liye shant. 🤐")
        return

    if user_id in SILENCED_USERS and current_time < SILENCED_USERS[user_id]:
        return

    # Trigger keyword update kar diya gaya hai
    bot_name = "kanchan"
    study_keywords = ["doubt", "wrong", "galat", "sahi", "answer", "formula", "physics", "maths", "chemistry", "question", "sawal"]
    fun_keywords = ["comedy", "joke", "haha", "hehe", "lol", "pyaar", "love", "gf", "bf", "movie", "song", "mazak", "masti", "entertainment"]
    
    is_reply_to_bot = False
    if update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id:
        is_reply_to_bot = True
        
    # Check if this user is in an active session with the bot (within last 3 mins)
    is_active_session = False
    if chat_id in ACTIVE_SESSIONS:
        session = ACTIVE_SESSIONS[chat_id]
        if session["user_id"] == user_id and (current_time - session["timestamp"] < SESSION_TIMEOUT):
            is_active_session = True
    
    should_reply = False
    
    # Ab ye 'kanchan' word par trigger hoga
    if bot_name in text or is_reply_to_bot or chat_type == "private": 
        should_reply = True
    elif is_active_session:
        # Agar user ne picchle 3 minute me bot se baat ki hai, toh seedha reply karega bina naam liye!
        should_reply = True
    elif any(word in text for word in study_keywords): 
        should_reply = True
    elif any(word in text for word in fun_keywords): 
        should_reply = True
    elif "?" in text:
        should_reply = True

    if should_reply:
        try:
            # Typing action turant bheja jayega taaki fast feel ho
            await context.bot.send_chat_action(chat_id=chat_id, action='typing')
            final_res = await ai_chat(user_input=update.message.text, chat_id=chat_id, user_name=user_name)
            await update.message.reply_text(final_res)
            
            # Message bhejne ke baad, session ko active mark karo
            ACTIVE_SESSIONS[chat_id] = {
                "user_id": user_id,
                "timestamp": current_time
            }
        except Exception as e:
            logger.error(f"Handler Error: {e}")

# ================= NETWORK CHECKS =================
def wait_for_internet():
    while True:
        try:
            socket.create_connection(("api.telegram.org", 443), timeout=5)
            break
        except OSError:
            time.sleep(5)

webhook_cleared = False
def clear_webhook():
    global webhook_cleared
    if webhook_cleared: return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook?drop_pending_updates=True"
        req = urllib.request.Request(url)
        urllib.request.urlopen(req, timeout=10)
        webhook_cleared = True
    except Exception:
        pass

# ================= RUNNER =================
def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

if __name__ == '__main__':
    if not TELEGRAM_TOKEN:
        exit(1)
        
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    wait_for_internet()
    
    while True:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            clear_webhook()
            application = ApplicationBuilder().token(TELEGRAM_TOKEN).connect_timeout(40).read_timeout(40).build()
            application.add_handler(CommandHandler("start", start_cmd))
            application.add_handler(CommandHandler("ping", ping_cmd)) 
            application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_msg))
            application.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
            break
        except Exception:
            time.sleep(10)
