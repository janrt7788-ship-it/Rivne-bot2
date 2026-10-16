import math
import os
import time
import threading
from datetime import datetime
import telebot
import requests

API_TOKEN = "8977498475:AAHbsQqF-5XOF9h2o8KTnC5nbmnMMkYV-Hg"
bot = telebot.TeleBot(API_TOKEN)

URL = "https://neptun.in.ua/api/v1/threats"
INTERVAL = 15  # секунд
CHAT_ID_FILE = "chat_id.txt"

RIVNE_LAT = 50.6231
RIVNE_LON = 26.2274
CITY_RADIUS_KM = 8.0

# Функції збереження та зчитування ID чату
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

def bearing_to_cardinal(deg):
    if deg is None:
        return "невідомий"
    directions = [
        "північ", "північний схід", "схід", "південний схід",
        "південь", "південний захід", "захід", "північний захід"
    ]
    idx = int((deg + 22.5) % 360 // 45)
    return directions[idx]

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

def format_threat(item, dist_km):
    locality = item.get("locality") or "Невідомий н.п."
    region = item.get("region") or ""
    title = item.get("title") or "Шахед"
    heading = item.get("heading")
    velocity = item.get("velocity") or {}
    speed = velocity.get("speedKmh")
    explanation = item.get("explanationShort") or ""
    destination = item.get("destination")

    direction = bearing_to_cardinal(heading)
    heading_str = f"{heading:.0f}° ({direction})" if heading is not None else "невідомий"

    if destination and locality:
        dest_str = f"курсом на {locality}"
    else:
        dest_str = explanation if explanation else "напрямок не вказано"

    speed_str = f", швидкість ≈ {speed:.0f} км/год" if speed else ""

    if dist_km <= CITY_RADIUS_KM:
        return (
            f"⚠️  <b>{title} УЖЕ В МІСТІ РІВНЕ</b> (≈ {dist_km:.1f} км від центру)\n"
            f"   Напрямок: {heading_str}{speed_str}\n"
            f"   {dest_str}"
        )
    else:
        return (
            f"• {title} — <b>{locality}</b>, {region} (≈ {dist_km:.1f} км від Рівного)\n"
            f"  Напрямок: {heading_str}{speed_str}\n"
            f"  {dest_str}"
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
        
        now = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        report = [f"🛡 <b>Оновлення станом на {now} (Рівне):</b>\n"]
        for dist, item in nearest:
            report.append(format_threat(item, dist))
            report.append("")
        return "\n".join(report)
        
    except Exception as e:
        return None

# Реєстрація чату при першому старті чи будь-якому повідомленні
@bot.message_handler(func=lambda message: True)
def handle_all(message):
    global USER_CHAT_ID
    USER_CHAT_ID = message.chat.id
    save_chat_id(USER_CHAT_ID)
    
    text = fetch_current_threats()
    if text:
        bot.reply_to(message, text, parse_mode="HTML")
    else:
        bot.reply_to(message, "✅ Моніторинг активний. Наразі загроз поблизу Рівного не виявлено.", parse_mode="HTML")

# Фоновий цикл перевірки
def background_checker():
    global USER_CHAT_ID
    print("Фоновий моніторинг запущено...")
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
                    print(f"Помилка відправки в Telegram: {e}")
            elif not report:
                last_report = None
        
        time.sleep(INTERVAL)

threading.Thread(target=background_checker, daemon=True).start()

print("Telegram-бот запущений і працює у фоновому режимі...")
bot.infinity_polling()
