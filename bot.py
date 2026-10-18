import math
import os
import time
import threading
from datetime import datetime
import telebot
import requests
from flask import Flask

# Веб-сервер для того, щоб Render тримав порт відкритим
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# Налаштування бота
API_TOKEN = "8977498475:AAHbsQqF-5XOF9h2o8KTnC5nbmnMMkYV-Hg"
bot = telebot.TeleBot(API_TOKEN)

URL = "https://neptun.in.ua/api/v1/threats"
INTERVAL = 15
CHAT_ID_FILE = "chat_id.txt"

RIVNE_LAT = 50.6231
RIVNE_LON = 26.2274

def save_chat_id(chat_id):
    with open(CHAT_ID_FILE, "w") as f:
        f.write(str(chat_id))

def load_chat_id():
    if os.path.exists(CHAT_ID_FILE):
        try:
            with open(CHAT_ID_FILE, "r") as f:
                return int(f.read().strip())
        except Exception:
            return None
    return None

USER_CHAT_ID = load_chat_id()

def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2)**2
    return 2 * R * math.asin(math.sqrt(a))

def get_items(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("threats", "data", "items", "features"):
            if isinstance(data.get(key), list):
                return data[key]
    return []

def is_shahed(item):
    t = (item.get("type") or "").lower()
    title = (item.get("title") or "").lower()
    return t in ("uav", "shahed") or "шахед" in title or "shahed" in title

def format_nearest_threat(item, dist_km):
    locality = item.get("locality") or item.get("title") or "БПЛА"
    
    # Визначаємо, чи є БПЛА реактивним (за швидкістю або типом/назвою)
    velocity = item.get("velocity") or {}
    speed = velocity.get("speedKmh") or 0
    t_str = f"{item.get('type', '')} {item.get('title', '')}".lower()
    
    if "реактивн" in t_str or speed > 300:
        threat_type = "Реактивний БПЛА"
    else:
        threat_type = item.get("title") or "БПЛА"

    return (
        f"⚠️ <b>Найближча ціль:</b>\n"
        f"• Наслідує / поблизу: <b>{locality}</b>\n"
        f"• Відстань до міста Рівне: <b>{dist_km:.1f} км</b>\n"
        f"• Тип: {threat_type}"
    )

def fetch_current_threats():
    try:
        response = requests.get(URL, timeout=10)
        response.raise_for_status()
        data = response.json()
        items = get_items(data)

        shaheds = []
        for item in items:
            if not is_shahed(item):
                continue
            lat = item.get("lat")
            lon = item.get("lon")
            if lat is None or lon is None:
                continue
            if item.get("areaOnly"):
                continue
            dist = haversine(RIVNE_LAT, RIVNE_LON, lat, lon)
            shaheds.append((dist, item))

        if not shaheds:
            return None
        
        # Знаходимо найближчу ціль до міста Рівне
        shaheds.sort(key=lambda x: x[0])
        dist, item = shaheds[0]
        
        return format_nearest_threat(item, dist)
        
    except Exception as e:
        return None

@bot.message_handler(commands=['start'])
def handle_start(message):
    global USER_CHAT_ID
    USER_CHAT_ID = message.chat.id
    save_chat_id(USER_CHAT_ID)
    bot.reply_to(message, "✅ Моніторинг активовано! Бот надсилатиме найближчу ціль автоматично.")

@bot.message_handler(func=lambda message: True)
def handle_all(message):
    global USER_CHAT_ID
    USER_CHAT_ID = message.chat.id
    save_chat_id(USER_CHAT_ID)
    
    text = fetch_current_threats()
    if text:
        bot.reply_to(message, text, parse_mode="HTML")
    else:
        bot.reply_to(message, "✅ Моніторинг активний. Наразі загроз не виявлено.", parse_mode="HTML")

def background_checker():
    global USER_CHAT_ID
    last_report = None
    while True:
        if USER_CHAT_ID is None:
            USER_CHAT_ID = load_chat_id()
            
        if USER_CHAT_ID:
            report = fetch_current_threats()
            if report and report != last_report:
                try:
                    bot.send_message(USER_CHAT_ID, report, parse_mode="HTML")
                    last_report = report
                except Exception as e:
                    print(f"Помилка відправки: {e}")
            elif not report:
                last_report = None
        time.sleep(INTERVAL)

# Запуск веб-сервера і фонової перевірки
threading.Thread(target=run_web, daemon=True).start()
threading.Thread(target=background_checker, daemon=True).start()

print("Бот запущено у хмарі...")
bot.infinity_polling(none_stop=True)
