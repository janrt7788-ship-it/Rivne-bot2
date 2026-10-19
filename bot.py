import math
import os
import time
import threading
from datetime import datetime, timezone, timedelta
import telebot
import requests
from flask import Flask

# ====================== ВЕБ-СЕРВЕР (Render) ======================
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

def self_ping():
    app_url = os.environ.get("RENDER_EXTERNAL_URL")
    while True:
        try:
            if app_url:
                requests.get(app_url, timeout=10)
        except Exception:
            pass
        time.sleep(240)

# ====================== НАЛАШТУВАННЯ ======================
API_TOKEN = os.environ.get("8977498475:AAFXdvd0Imdtkzcm2wbMmxUkr7IpqwBiBQw")
if not API_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN не встановлено в змінних середовища!")

bot = telebot.TeleBot(API_TOKEN)

URL = "https://neptun.in.ua/api/v1/threats"
INTERVAL = 15
CHAT_ID_FILE = "chat_id.txt"

RIVNE_LAT = 50.6231
RIVNE_LON = 26.2274

# Об'єкт живе ще 1 хвилину після того, як зник зі снапшоту
KEEP_ALIVE_MINUTES = 1

threat_database = {}
next_uav_number = 1

def save_chat_id(chat_id):
    try:
        with open(CHAT_ID_FILE, "w") as f:
            f.write(str(chat_id))
    except Exception:
        pass

def load_chat_id():
    if os.path.exists(CHAT_ID_FILE):
        try:
            with open(CHAT_ID_FILE, "r") as f:
                return int(f.read().strip())
        except Exception:
            return None
    return None

USER_CHAT_ID = load_chat_id()

# ====================== ГЕОМЕТРІЯ ======================
def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2)**2
    return 2 * R * math.asin(math.sqrt(a))

def calculate_bearing(lat1, lon1, lat2, lon2):
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlam = math.radians(lon2 - lon1)
    x = math.sin(dlam) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    initial_bearing = math.atan2(x, y)
    initial_bearing = math.degrees(initial_bearing)
    return (initial_bearing + 360) % 360

def bearing_to_cardinal(deg):
    directions = [
        "північ", "північний схід", "схід", "південний схід",
        "південь", "південний захід", "захід", "північний захід"
    ]
    idx = int((deg + 22.5) % 360 // 45)
    return directions[idx]

# ====================== ДОПОМІЖНІ ======================
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

def parse_api_time(time_str):
    """Парсить час з API (ISO з Z) у київський час"""
    try:
        dt = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
        return dt.astimezone(timezone(timedelta(hours=3)))
    except Exception:
        return None

# ====================== ОСНОВНИЙ ТРЕКІНГ ======================
def update_threat_tracking(items):
    global threat_database, next_uav_number
    current_ids = set()
    kyiv_tz = timezone(timedelta(hours=3))
    now_dt = datetime.now(kyiv_tz)

    for item in items:
        if not is_shahed(item):
            continue

        lat = item.get("lat")
        lon = item.get("lon")
        if lat is None or lon is None or item.get("areaOnly"):
            continue

        # Стабільний ID — найважливіше
        raw_id = item.get("id") or item.get("uuid")
        if not raw_id:
            raw_id = f"{round(lat, 3)}_{round(lon, 3)}"

        current_ids.add(raw_id)

        locality = item.get("locality") or item.get("title") or "Невідомо"
        region = item.get("region") or ""
        dist_to_rivne = haversine(RIVNE_LAT, RIVNE_LON, lat, lon)

        # ========== НОВИЙ ОБ'ЄКТ ==========
        if raw_id not in threat_database:
            num = next_uav_number
            next_uav_number += 1

            history = []

            # Беремо історію з trail, якщо API її дає
            trail = item.get("trail") or []
            for point in trail:
                try:
                    p_lat = point.get("lat")
                    p_lon = point.get("lon")
                    p_time = parse_api_time(point.get("t"))
                    if p_lat is None or p_lon is None or p_time is None:
                        continue
                    p_dist = haversine(RIVNE_LAT, RIVNE_LON, p_lat, p_lon)
                    history.append({
                        "time": p_time,
                        "lat": p_lat,
                        "lon": p_lon,
                        "dist": p_dist,
                        "locality": locality,
                        "region": region
                    })
                except Exception:
                    continue

            history.sort(key=lambda x: x["time"])

            # Додаємо поточну точку
            history.append({
                "time": now_dt,
                "lat": lat,
                "lon": lon,
                "dist": dist_to_rivne,
                "locality": locality,
                "region": region
            })

            first_point = history[0]

            threat_database[raw_id] = {
                "num": num,
                "first_seen": first_point["time"],
                "first_locality": first_point.get("locality") or locality,
                "first_region": first_point.get("region") or region,
                "history": history,
                "avg_speed": 180.0,
                "heading": "невідомий",
                "is_jet": False,
                "last_seen": now_dt
            }

        # ========== ІСНУЮЧИЙ ОБ'ЄКТ ==========
        else:
            data_entry = threat_database[raw_id]
            history = data_entry["history"]
            data_entry["last_seen"] = now_dt

            last = history[-1]
            # Додаємо нову точку тільки якщо координати реально змінилися
            if abs(last["lat"] - lat) > 0.0008 or abs(last["lon"] - lon) > 0.0008:
                history.append({
                    "time": now_dt,
                    "lat": lat,
                    "lon": lon,
                    "dist": dist_to_rivne,
                    "locality": locality,
                    "region": region
                })

        # ========== ПЕРЕРАХУНОК ШВИДКОСТІ І КУРСУ ==========
        data_entry = threat_database[raw_id]
        history = data_entry["history"]

        if len(history) >= 2:
            total_distance_km = 0.0
            for i in range(1, len(history)):
                total_distance_km += haversine(
                    history[i-1]["lat"], history[i-1]["lon"],
                    history[i]["lat"], history[i]["lon"]
                )

            total_time_seconds = (history[-1]["time"] - history[0]["time"]).total_seconds()
            if total_time_seconds >= 15:
                calculated_speed = (total_distance_km / total_time_seconds) * 3600
                if 25 <= calculated_speed <= 750:
                    data_entry["avg_speed"] = calculated_speed

            prev = history[-2]
            dist_moved = haversine(prev["lat"], prev["lon"], lat, lon)
            if dist_moved > 0.15:
                bearing = calculate_bearing(prev["lat"], prev["lon"], lat, lon)
                data_entry["heading"] = bearing_to_cardinal(bearing)

        # Визначення реактивного
        velocity = item.get("velocity") or {}
        api_speed = velocity.get("speedKmh") or 0
        effective_speed = max(api_speed, data_entry.get("avg_speed", 180))

        item_str = str(item).lower()
        if any(m in item_str for m in ["реактив", "jet", "швидкісн"]) or effective_speed > 250:
            data_entry["is_jet"] = True

    # ========== М'ЯКЕ ОЧИЩЕННЯ (1 хвилина) ==========
    to_delete = []
    for tid, data in threat_database.items():
        if tid not in current_ids:
            minutes_gone = (now_dt - data["last_seen"]).total_seconds() / 60
            if minutes_gone > KEEP_ALIVE_MINUTES:
                to_delete.append(tid)

    for tid in to_delete:
        del threat_database[tid]

def format_all_tracked_threats():
    if not threat_database:
        return None

    alive = []
    kyiv_tz = timezone(timedelta(hours=3))
    now_dt = datetime.now(kyiv_tz)

    for t in threat_database.values():
        minutes_gone = (now_dt - t["last_seen"]).total_seconds() / 60
        if minutes_gone <= KEEP_ALIVE_MINUTES and t["history"]:
            alive.append(t)

    if not alive:
        return None

    sorted_threats = sorted(alive, key=lambda x: x["history"][-1]["dist"])[:3]

    now_str = now_dt.strftime("%d.%m.%Y %H:%M:%S")
    report = [f"⚠️ <b>Аналіз маршрутів найближчих загроз станом на {now_str}:</b>\n"]

    for t in sorted_threats:
        num = t["num"]
        history = t["history"]
        last = history[-1]
        dist = last["dist"]
        locality = last["locality"]
        region = last["region"]

        loc_str = f"<b>{locality}</b> ({region})" if region else f"<b>{locality}</b>"

        first_seen_time = t["first_seen"].strftime("%H:%M:%S")
        first_loc = t["first_locality"]
        first_reg = t["first_region"]
        first_loc_str = f"<b>{first_loc}</b> ({first_reg})" if first_reg else f"<b>{first_loc}</b>"

        movement_str = ""
        if len(history) > 1:
            prev = history[-2]
            diff = dist - prev["dist"]
            if diff < -0.08:
                movement_str = f" 🟢 <i>(наближається на {abs(diff):.1f} км)</i>"
            elif diff > 0.08:
                movement_str = f" 🔴 <i>(віддаляється на {diff:.1f} км)</i>"
            else:
                movement_str = f" 🟡 <i>(стабільно)</i>"

        route_info = f"З'явився о {first_seen_time} в районі: {first_loc_str}"
        threat_type = "БПЛА <b>(Реактивний)</b>" if t["is_jet"] else "БПЛА (звичайний)"
        speed_info = f", середня швидкість: \~{t['avg_speed']:.0f} км/год"
        heading_info = f", курс: {t['heading']}" if t['heading'] != "невідомий" else ""

        report.append(
            f"🎯 <b>Шахед #{num}</b>\n"
            f"• Маршрут: {route_info}\n"
            f"• Поточна позиція: {loc_str}\n"
            f"  Відстань до Рівного: <b>{dist:.1f} км</b>{movement_str}\n"
            f"  Тип: {threat_type}{speed_info}{heading_info}\n"
        )

    return "\n".join(report)

def fetch_and_process():
    try:
        response = requests.get(URL, timeout=10)
        response.raise_for_status()
        data = response.json()
        items = get_items(data)
        update_threat_tracking(items)
        return format_all_tracked_threats()
    except Exception as e:
        print(f"Помилка запиту до API: {e}")
        return None

# ====================== TELEGRAM HANDLERS ======================
@bot.message_handler(commands=['start'])
def handle_start(message):
    global USER_CHAT_ID
    USER_CHAT_ID = message.chat.id
    save_chat_id(USER_CHAT_ID)
    bot.reply_to(message, "✅ Автономний моніторинг із повним трекінгом маршруту активовано!")

@bot.message_handler(func=lambda message: True)
def handle_all(message):
    global USER_CHAT_ID
    USER_CHAT_ID = message.chat.id
    save_chat_id(USER_CHAT_ID)

    text = fetch_and_process()
    if text:
        bot.reply_to(message, text, parse_mode="HTML")
    else:
        bot.reply_to(message, "✅ Моніторинг активний. Наразі загроз не виявлено.", parse_mode="HTML")

def background_checker():
    global USER_CHAT_ID
    last_report = None

    while True:
        try:
            if USER_CHAT_ID is None:
                USER_CHAT_ID = load_chat_id()

            if USER_CHAT_ID:
                report = fetch_and_process()
                if report and report != last_report:
                    bot.send_message(USER_CHAT_ID, report, parse_mode="HTML")
                    last_report = report
                elif not report:
                    last_report = None
        except Exception as e:
            print(f"Помилка у фоновому потоці: {e}")

        time.sleep(INTERVAL)

# ====================== ЗАПУСК ======================
threading.Thread(target=run_web, daemon=True).start()
threading.Thread(target=self_ping, daemon=True).start()
threading.Thread(target=background_checker, daemon=True).start()

print("Бот з нормальним трекінгом запущено...")

while True:
    try:
        bot.infinity_polling(none_stop=True, interval=2, timeout=30)
    except Exception as e:
        print(f"Зв'язок із Telegram перервано ({e}), перепідключення через 5 сек...")
        time.sleep(5)
