import math
import os
import time
import threading
from datetime import datetime, timezone, timedelta
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

# Словник для збереження попередньої відстані кожної цілі за її ID (моніторинг руху в часі)
threat_history = {}

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
    item_str = str(item).lower()
    keywords = ["uav", "shahed", "шахед", "бпла", "дрон"]
    return any(kw in item_str for kw in keywords)

def is_jet_threat(item):
    item_str = str(item).lower()
    jet_markers = ["реактив", "jet", "швидкісн", "· р"]
    text_match = any(marker in item_str for marker in jet_markers)
    
    velocity = item.get("velocity") or {}
    speed = velocity.get("speedKmh") or 0
    speed_match = speed > 250
    
    return text_match or speed_match

def format_threat(item, dist_km):
    # Отримуємо унікальний ідентифікатор цілі (якщо він є унікальним на сервері)
    threat_id = item.get("id") or item.get("uuid") or item.get("locality")
    
    # Визначаємо динаміку руху в часі
    global threat_history
    movement_str = ""
    if threat_id in threat_history:
        prev_dist = threat_history[threat_id]
        diff = dist_km - prev_dist
        # Якщо відстань зменшилася більше ніж на 0.1 км — наближається
        if diff < -0.1:
            movement_str = " 🟢 <i>(наближається)</i>"
        # Якщо збільшилася — віддаляється
        elif diff > 0.1:
            movement_str = " 🔴 <i>(віддаляється)</i>"
        else:
            movement_str = " 🟡 <i>(стабільно)</i>"
            
    # Зберігаємо поточну відстань для наступного порівняння
    if threat_id:
        threat_history[threat_id] = dist_km

    locality = item.get("locality") or item.get("title") or "Невідомо"
    region = item.get("region") or ""
    
    if region:
        loc_str = f"<b>{locality}</b> ({region})"
    else:
        loc_str = f"<b>{locality}</b>"
    
    if is_jet_threat(item):
        threat_type = "БПЛА <b>(Реактивний)</b>"
    else:
        threat_type = "БПЛА (звичайний)"

    return (
        f"• Найближчий НП: {loc_str}\n"
        f"  Відстань до міста Рівне: <b>{dist_km:.1f} км</b>{movement_str}\n"
        f"  Тип: {threat_type}"
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
        
        shaheds.sort(key=lambda x: x[0])
        nearest = shaheds[:3]
        
        kyiv_tz = timezone(timedelta(hours=3))
        now = datetime.now(kyiv_tz).strftime("%d.%m.%Y %H:%M:%S")
        
        report = [f"⚠️ <b>Моніторинг руху цілей станом на {now}:</b>\n"]
        for dist, item in nearest:
            report.append(format_threat(item, dist))
            report.append("")
        return "\n".join(report)
        
    except Exception as e:
        print(f"Помилка запиту: {e}")
        return None

@bot.message_handler(commands=['start'])
def handle_start(message):
    global USER_CHAT_ID
    USER_CHAT_ID = message.chat.id
    save_chat_id(USER_CHAT_ID)
    bot.reply_to(message, "✅ Динамічний моніторинг руху активовано! Бот відстежуватиме наближення цілей.")

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
    fail_counter = 0

    while True:
        if USER_CHAT_ID is None:
            USER_CHAT_ID = load_chat_id()
            
        if USER_CHAT_ID:
            try:
                report = fetch_current_threats()
                if report and report != last_report:
                    bot.send_message(USER_CHAT_ID, report, parse_mode="HTML")
                    last_report = report
                    fail_counter = 0
                elif not report:
                    last_report = None
                fail_counter = 0
            except Exception as e:
                fail_counter += 1
                print(f"Помилка у фоновому потоці (спроба {fail_counter}): {e}")
                if fail_counter >= 5:
                    fail_counter = 0
                    
        time.sleep(INTERVAL)

# Запуск веб-сервера і фонової перевірки
threading.Thread(target=run_web, daemon=True).start()
threading.Thread(target=background_checker, daemon=True).start()

print("Динамічний бот запущено у хмарі...")
bot.infinity_polling(none_stop=True)
  
