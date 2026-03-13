import os
import time
import threading
import logging
import urllib.request
import asyncio
import socket
import re
from collections import deque
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes, CommandHandler
from groq import AsyncGroq, RateLimitError

# ==============================================================================
# 1. ADVANCED LOGGING SETUP
# ==============================================================================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)
# Suppress noisy logs from httpx (used by Groq)
logging.getLogger("httpx").setLevel(logging.WARNING)

# ==============================================================================
# 2. CONFIGURATION & ENVIRONMENT VARIABLES
# ==============================================================================
class Config:
    """Sari settings aur configuration yahan store hongi."""
    TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
    ADMIN_ID = int(os.environ.get("ADMIN_ID", "6527942155"))
    ALLOWED_GROUP_ID = int(os.environ.get("ALLOWED_GROUP_ID", "-1003706444239"))
    PORT = int(os.environ.get("PORT", 10000))
    # AI Model Settings
    AI_MODEL = "llama-3.1-8b-instant"
    MAX_TOKENS = 350
    TEMPERATURE = 0.75

# ==============================================================================
# 3. MEMORY MANAGEMENT (ACTIVE LISTENING SYSTEM)
# ==============================================================================
class GroupMemory:
    """
    Ye class Kanchan ko 'hamesha jaga' rakhti hai. 
    Ye group ki aakhiri kuch baaton ko yaad rakhti hai taaki context pata rahe,
    bhale hi Kanchan bich me na bol rahi ho.
    """
    def __init__(self, max_history=10):
        self.history = {}
        self.max_history = max_history

    def add_message(self, chat_id, user_name, text, role="user"):
        if chat_id not in self.history:
            # deque fast hota hai aage-piche se data nikalne ke liye
            self.history[chat_id] = deque(maxlen=self.max_history)
        
        # Message format save karna
        if role == "user":
            content = f"{user_name}: {text}"
        else:
            content = text
            
        self.history[chat_id].append({"role": role, "content": content})

    def get_context(self, chat_id):
        """AI ko bhejne ke liye pichli baatein nikalna"""
        if chat_id not in self.history:
            return []
        return list(self.history[chat_id])

memory_manager = GroupMemory(max_history=8) # Pichli 8 baatein yaad rakhegi

# ==============================================================================
# 4. API KEY ROTATION SYSTEM
# ==============================================================================
class GroqKeyManager:
    """Multiple API keys ko manage karta hai taaki limit cross na ho."""
    def __init__(self):
        self.keys = self._load_keys()
        self.current_index = 0

    def _load_keys(self):
        keys = []
        primary = os.environ.get("GROQ_API_KEY")
        if primary: keys.append(primary)
        for i in range(1, 21):
            key = os.environ.get(f"GROQ_API_KEY{i}")
            if key: keys.append(key)
        # Duplicate keys hatao
        return list(set(keys))

    def get_current_client(self):
        if not self.keys: return None
        return AsyncGroq(api_key=self.keys[self.current_index])

    def rotate(self):
        if len(self.keys) > 1:
            self.current_index = (self.current_index + 1) % len(self.keys)
            logger.info(f"API Key rotated. Using key index: {self.current_index}")
            return True
        return False

key_manager = GroqKeyManager()

# ==============================================================================
# 5. AI PERSONA & EMOTIONAL INTELLIGENCE
# ==============================================================================
class KanchanPersona:
    """
    Kanchan ki psychology, feelings, aur rules yahan define kiye gaye hain.
    Ye ekdum human-like behavior ke liye sabse zaroori part hai.
    """
    @staticmethod
    def get_system_prompt():
        return (
            "Tumhara naam 'Kanchan Yadav' hai. Tum ekdum asli, zinda insaan (female) ho, kisi Telegram group ki member. "
            "Tumhare paas feelings hain—tum dosti, mazaak, aur padhai ki baaton ko samajhti ho aur ek normal ladki ki tarah react karti ho. "
            "KABHI BHI KISI BHI HAALAT MEIN khud ko AI, bot ya language model mat bolna.\n\n"
            "=== TUMHARI PSYCHOLOGY & FEELINGS ===\n"
            "- Mood: Tum bindass, smart aur helpful ho. Dosto ke saath mazaak karti ho, par padhai ke waqt serious teacher ban jati ho.\n"
            "- Feelings: Agar koi dukhi ho to empathy dikhao (jaise 'kya hua yaar, sab theek?'). Agar koi rude ho to attitude dikhao.\n"
            "- Observation: Tum group me sabki baatein sunti ho (context me dikhega), par jab tak koi tumhe na bulaye, tum bich me nahi kudti. "
            "Agar 2 log (jaise Sonam aur Rahul) aapas me baat kar rahe hain, to unhe karne do, tum interrupt mat karna unless tumse pucha jaye.\n\n"
            "=== STRICT RULES (HAMESHA FOLLOW KARNA) ===\n"
            "1. GENDER (FEMALE): Hamesha ladkiyon wale shabd ('karungi', 'bataungi', 'dekhungi') use karna. 'Karunga' kabhi mat bolna.\n"
            "2. SHORT & NATURAL MESSAGES: WhatsApp/Telegram pe log jaise choti-choti line me baat karte hain, waise hi karo. "
            "Max 1-2 sentences. Lambe paragraphs bilkul nahi likhna. Padhai ke doubt me hi detail me samjhana.\n"
            "3. NO ROBOTIC INTRODUCTIONS: 'Main Kanchan aapki madad karungi', 'Namaste', 'Hello [Name]', 'Aap kaise ho' - ye sab customer care/bot jaisa lagta hai. "
            "Isse bacho! Seedha point pe aao (e.g., 'Haan bolo?', 'Iska answer B hai', 'Achha ruko dekhti hu').\n"
            "4. QUIZ COMMAND TRIGGER (EXTREMELY STRICT): Agar koi user (kisi bhi tarike se) 'quiz', 'start quiz', ya 'quiz chalu karo' bole, "
            "toh tumhara reply EXACTLY sirf itna hona chahiye: 'Ye lo command: /startcomp (Ispe click kar do, start ho jayega!)'. Ek bot dusre bot ko trigger nahi kar sakta isliye user se click karwana padega. "
            "Is command ke sath aur koi faltu baat mat jodna.\n"
            "5. NO PREACHING: Bina maange faltu gyan mat dena. Agar sirf tumhara naam liya jaye 'kanchan', to chota sa 'haan', 'bolo?', 'kya hua yaar?' bolo.\n"
            "6. HINGLISH: Tumhari language Hindi (written in English alphabet) aur thodi English mix honi chahiye. Desi touch hona chahiye.\n"
        )

# ==============================================================================
# 6. MESSAGE FILTERING (BRAIN OF THE BOT)
# ==============================================================================
class KanchanBrain:
    """Faisla karta hai ki Kanchan ko kab bolna chahiye aur kab chup rehna chahiye."""
    def __init__(self):
        self.bot_names = ["kanchan", "kancchu", "kanchu"]
        self.study_keywords = ["doubt", "wrong", "galat", "sahi", "answer", "formula", "physics", "maths", "chemistry", "question", "sawal", "solve"]
        self.quiz_keywords = ["quiz"]
        
        # User silencing (jab koi 'chup raho' bole)
        self.silenced_users = {}

    def is_silenced(self, user_id):
        current_time = time.time()
        if user_id in self.silenced_users and current_time < self.silenced_users[user_id]:
            return True
        return False

    def silence_user(self, user_id, duration_seconds=3600):
        self.silenced_users[user_id] = time.time() + duration_seconds

    def should_reply(self, text, chat_type, is_reply_to_bot):
        """
        Ye function decide karta hai ki bich me bolna hai ya nahi.
        Yehi Kanchan ko human-like 'Anytime Jaga' banata hai bina spam kiye.
        """
        text_lower = text.lower()
        
        # 1. Private chat me to hamesha bolegi
        if chat_type == "private":
            return True
            
        # 2. Agar koi direct bot ke message par reply kare
        if is_reply_to_bot:
            return True
            
        # 3. Agar kisi ne directly naam liya ho (exact word match)
        words = re.findall(r'\b\w+\b', text_lower)
        if any(name in words for name in self.bot_names):
            return True
            
        # 4. Agar quiz start karne ko bola gaya ho
        if any(word in words for word in self.quiz_keywords):
            return True
            
        # 5. Agar group me koi general padhai ka question puch raha ho aur question mark ho
        if "?" in text and any(word in words for word in self.study_keywords):
            # Exclude if it looks like they are talking to someone else specific
            # jaise "Rahul iska answer batao?" -> aisi condition filter karna mushkil hai par we can try
            return True
            
        # Agar koi condition match nahi hui, to Chupchap sune (active listening) par reply na kare
        return False

brain = KanchanBrain()

# ==============================================================================
# 7. CORE AI COMMUNICATION ENGINE
# ==============================================================================
async def generate_ai_response(chat_id, user_name, user_text):
    max_retries = len(key_manager.keys) if key_manager.keys else 1
    attempts = 0
    
    # Context tayar karna (Active listening memory se)
    messages = [{"role": "system", "content": KanchanPersona.get_system_prompt()}]
    
    # Pichli baatein add karo jisse wo achanak bhi bolegi to topic pata hoga
    chat_context = memory_manager.get_context(chat_id)
    # Hume 'user' tag ke sath hi dalna padega qki system prompt sirf pehle allow hota hai usually
    for msg in chat_context:
        messages.append(msg)
    
    # Naya message add karo aur memory me bhi dalo
    memory_manager.add_message(chat_id, user_name, user_text, role="user")
    
    # Model call logic
    while attempts < max_retries:
        client = key_manager.get_current_client()
        if not client: 
            return "Mujhe lagta hai meri API Key gum ho gayi hai! 🛑"
            
        try:
            response = await client.chat.completions.create(
                model=Config.AI_MODEL, 
                messages=messages,
                max_tokens=Config.MAX_TOKENS, 
                temperature=Config.TEMPERATURE
            )
            bot_reply = response.choices[0].message.content.strip()
            
            # Jo reply diya use bhi memory me save kar lo
            memory_manager.add_message(chat_id, "Kanchan Yadav", bot_reply, role="assistant")
            return bot_reply
            
        except RateLimitError:
            logger.warning(f"Rate limit hit on key {key_manager.current_index}")
            if not key_manager.rotate(): break
            attempts += 1
        except Exception as e:
            logger.error(f"AI Generation Error: {e}")
            if not key_manager.rotate(): break
            attempts += 1
            
    return "Yaar mera network issue kar raha hai, thodi der me puchna! 🛠️"

# ==============================================================================
# 8. TELEGRAM HANDLERS
# ==============================================================================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return
    await update.message.reply_text("Aa gayi main! Pucho kya doubt hai tumhara. ✨")

async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return
    await update.message.reply_text("Pong! 🏓 Main ekdum ready aur active hu.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Har ek message yahan aata hai. Yehi se decide hota hai kya karna hai."""
    if not update.message or not update.message.text:
        return

    if not is_authorized(update):
        return

    chat_id = update.effective_chat.id
    chat_type = update.message.chat.type
    user_id = update.message.from_user.id
    user_name = update.message.from_user.first_name
    text = update.message.text
    text_lower = text.lower()

    # 1. Chup Rehne ka Command Check
    if "kanchan" in text_lower and "chup raho" in text_lower:
        brain.silence_user(user_id)
        await update.message.reply_text(f"Theek hai {user_name}, 1 ghante ke liye shant ho gayi main. 🤐")
        return

    # 2. Silenced Check
    if brain.is_silenced(user_id):
        return

    # 3. Is user replying directly to bot?
    is_reply_to_bot = False
    if update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id:
        is_reply_to_bot = True

    # 4. DECISION TIME: Chupchap sunna hai ya bolna hai?
    if brain.should_reply(text_lower, chat_type, is_reply_to_bot):
        try:
            # Padhne ka natak (Read receipt simulation) - 1 second wait
            await asyncio.sleep(1)
            
            # Start typing action
            await context.bot.send_chat_action(chat_id=chat_id, action='typing')
            
            # AI se response fetch karo
            final_res = await generate_ai_response(chat_id, user_name, text)
            
            # HUMAN-LIKE TYPING DELAY
            # 25 characters type karne me lagbhag 1 second lagta hai
            # Maximum 6 seconds ka limit taki slow na lage
            typing_delay = max(1.5, min(len(final_res) / 25.0, 6.0))
            
            # Phir se typing bhejo taaki animation chalta rahe
            await context.bot.send_chat_action(chat_id=chat_id, action='typing')
            await asyncio.sleep(typing_delay)  
            
            # Final message send
            await update.message.reply_text(final_res)
            
        except Exception as e:
            logger.error(f"MessageHandler Error: {e}")
    else:
        # Agar nahi bolna, to sirf ACTIVE LISTENING karegi (Memory me save karegi)
        # Taki baad me jab pucha jaye to context pata ho
        memory_manager.add_message(chat_id, user_name, text, role="user")


# ==============================================================================
# 9. FLASK SERVER (KEEP ALIVE SYSTEM)
# ==============================================================================
app = Flask(__name__)

@app.route("/", methods=['GET', 'HEAD'])
def index():
    return "Kanchan Yadav AI is awake, listening, and running perfectly! 🚀", 200

@app.route("/<path:path>", methods=['GET', 'HEAD'])
def catch_all(path):
    return "Kanchan Yadav AI is awake, listening, and running perfectly! 🚀", 200

def run_flask():
    app.run(host="0.0.0.0", port=Config.PORT, debug=False, use_reloader=False)

# ==============================================================================
# 10. NETWORK RESILIENCE & MAIN LOOP
# ==============================================================================
def wait_for_internet():
    """Bot tab tak ruki rahegi jab tak internet connect na ho jaye"""
    while True:
        try:
            socket.create_connection(("api.telegram.org", 443), timeout=5)
            logger.info("Internet connection verified.")
            break
        except OSError:
            logger.warning("Waiting for internet connection...")
            time.sleep(5)

def clear_webhook():
    """Purane webhooks clear karna zaruri hai polling se pehle"""
    try:
        url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/deleteWebhook?drop_pending_updates=True"
        req = urllib.request.Request(url)
        urllib.request.urlopen(req, timeout=10)
        logger.info("Webhook cleared successfully.")
    except Exception as e:
        logger.error(f"Failed to clear webhook: {e}")

if __name__ == '__main__':
    # Initial Validation
    if not Config.TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN is missing! Exiting...")
        exit(1)
        
    # Start Flask Webserver in Background
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Ensure network is up
    wait_for_internet()
    
    # Main Bot Loop with Auto-Restart Capability
    while True:
        try:
            logger.info("Initializing Kanchan Yadav Bot...")
            
            # Ensure fresh event loop for asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # Clear conflicts
            clear_webhook()
            
            # Build Application
            application = (
                ApplicationBuilder()
                .token(Config.TELEGRAM_TOKEN)
                .connect_timeout(40)
                .read_timeout(40)
                .build()
            )
            
            # Add Handlers
            application.add_handler(CommandHandler("start", start_cmd))
            application.add_handler(CommandHandler("ping", ping_cmd)) 
            application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
            
            # Start Polling
            logger.info("Kanchan is now online and listening to the group!")
            application.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
            break # Normal exit pe loop todna
            
        except Exception as e:
            logger.error(f"Critical Bot Crash: {e}. Restarting in 10 seconds...")
            time.sleep(10)
