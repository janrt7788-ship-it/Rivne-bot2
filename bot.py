import math
import os
import time
import threading
from datetime import datetime, timezone, timedelta
import telebot
import requests
from flask import Flask

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
        except:
            pass
        time.sleep(240)

API_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not API_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN не встановлено!")

bot = telebot.TeleBot(API_TOKEN)

URL = "https://neptun.in.ua/api/v1/threats"
INTERVAL = 12
CHAT_ID_FILE = "chat_id.txt"

RIVNE_LAT = 50.6231
RIVNE_LON = 26.2274

KEEP_ALIVE_SECONDS = 360          # 6 хвилин
MATCH_DISTANCE_KM = 18            # якщо новий об'єкт ближче 18 км до старого — вважаємо тим самим
MATCH_TIME_SECONDS = 240          # і не старше 4 хвилин

threat_database = {}
next_uav_number = 1

def save_chat_id(chat_id):
    try:
        with open(CHAT_ID_FILE, "w") as f:
            f.write(str(chat_id))
    except:
        pass

def load_chat_id():
    if os.path.exists(CHAT_ID_FILE):
        try:
            with open(CHAT_ID_FILE, "r") as f:
                return int(f.read().strip())
        except:
            return None
    return None

USER_CHAT_ID = load_chat_id()

def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return 2 * R * math.asin(math.sqrt(a))

def calculate_bearing(lat1, lon1, lat2, lon2):
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlam = math.radians(lon2 - lon1)
    x = math.sin(dlam) * math.cos(phi2)
    y = math.cos(phi1)*math.sin(phi2) - math.sin(phi1)*math.cos(phi2)*math.cos(dlam)
    return (math.degrees(math.atan2(x, y)) + 360) % 360

def bearing_to_cardinal(deg):
    dirs = ["північ", "північний схід", "схід", "південний схід",
            "південь", "південний захід", "захід", "північний захід"]
    return dirs[int((deg + 22.5) % 360 // 45)]

def parse_api_time(t):
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(timezone(timedelta(hours=3)))
    except:
        return None

def get_items(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("threats", "data", "items"):
            if isinstance(data.get(k), list):
                return data[k]
    return []

def is_shahed(item):
    s = str(item).lower()
    return any(k in s for k in ["uav", "shahed", "шахед", "бпла", "дрон"])

def find_matching_track(lat, lon, now):
    """Шукаємо існуючий трек, до якого можна прив'язати нову точку"""
    best_id = None
    best_dist = 9999

    for tid, data in threat_database.items():
        last = data["history"][-1]
        time_diff = (now - data["last_seen"]).total_seconds()
        if time_diff > MATCH_TIME_SECONDS:
            continue

        d = haversine(last["lat"], last["lon"], lat, lon)
        if d < MATCH_DISTANCE_KM and d < best_dist:
            best_dist = d
            best_id = tid

    return best_id

def update_threat_tracking(items):
    global threat_database, next_uav_number
    now = datetime.now(timezone(timedelta(hours=3)))
    seen_ids = set()

    for item in items:
        if not is_shahed(item):
            continue

        lat = item.get("lat")
        lon = item.get("lon")
        if lat is None or lon is None or item.get("areaOnly"):
            continue

        api_id = item.get("id") or item.get("uuid") or f"{lat:.4f}_{lon:.4f}"
        locality = item.get("locality") or item.get("title") or "Невідомо"
        region = item.get("region") or ""
        dist = haversine(RIVNE_LAT, RIVNE_LON, lat, lon)

        # Спочатку пробуємо знайти існуючий трек по позиції
        matched_id = find_matching_track(lat, lon, now)

        if matched_id:
            # Оновлюємо існуючий трек
            entry = threat_database[matched_id]
            entry["last_seen"] = now
            history = entry["history"]

            last = history[-1]
            if abs(last["lat"] - lat) > 0.0004 or abs(last["lon"] - lon) > 0.0004:
                history.append({
                    "time": now,
                    "lat": lat,
                    "lon": lon,
                    "dist": dist,
                    "locality": locality,
                    "region": region
                })

            if len(history) > 60:
                entry["history"] = history[-60:]

            seen_ids.add(matched_id)

        else:
            # Створюємо новий трек
            history = []

            for p in (item.get("trail") or []):
                t = parse_api_time(p.get("t"))
                if t and p.get("lat") is not None and p.get("lon") is not None:
                    history.append({
                        "time": t,
                        "lat": p["lat"],
                        "lon": p["lon"],
                        "dist": haversine(RIVNE_LAT, RIVNE_LON, p["lat"], p["lon"]),
                        "locality": locality,
                        "region": region
                    })

            history.sort(key=lambda x: x["time"])
            history.append({
                "time": now,
                "lat": lat,
                "lon": lon,
                "dist": dist,
                "locality": locality,
                "region": region
            })

            first = history[0]
            new_id = f"track_{next_uav_number}"

            threat_database[new_id] = {
                "num": next_uav_number,
                "first_seen": first["time"],
                "first_locality": first["locality"],
                "first_region": first["region"],
                "history": history,
                "avg_speed": 180.0,
                "heading": "невідомий",
                "is_jet": False,
                "last_seen": now,
                "api_ids": {api_id}
            }
            next_uav_number += 1
            seen_ids.add(new_id)

        # Оновлюємо швидкість і курс
        entry = threat_database.get(matched_id) if matched_id else threat_database.get(new_id)
        if not entry:
            continue

        hist = entry["history"]
        if len(hist) >= 2:
            total_km = sum(
                haversine(hist[i-1]["lat"], hist[i-1]["lon"], hist[i]["lat"], hist[i]["lon"])
                for i in range(1, len(hist))
            )
            sec = (hist[-1]["time"] - hist[0]["time"]).total_seconds()
            if sec >= 25:
                speed = (total_km / sec) * 3600
                if 25 <= speed <= 750:
                    entry["avg_speed"] = speed

            prev = hist[-2]
            if haversine(prev["lat"], prev["lon"], lat, lon) > 0.2:
                brng = calculate_bearing(prev["lat"], prev["lon"], lat, lon)
                entry["heading"] = bearing_to_cardinal(brng)

        vel = item.get("velocity") or {}
        api_speed = vel.get("speedKmh") or 0
        if api_speed > 250 or entry["avg_speed"] > 250 or "реактив" in str(item).lower():
            entry["is_jet"] = True

    # Очищення старих
    to_del = []
    for tid, data in threat_database.items():
        if (now - data["last_seen"]).total_seconds() > KEEP_ALIVE_SECONDS:
            to_del.append(tid)
    for tid in to_del:
        del threat_database[tid]

def format_report():
    if not threat_database:
        return None

    now = datetime.now(timezone(timedelta(hours=3)))
    alive = [t for t in threat_database.values()
             if (now - t["last_seen"]).total_seconds() <= KEEP_ALIVE_SECONDS]

    if not alive:
        return None

    alive.sort(key=lambda x: x["history"][-1]["dist"])
    top = alive[:3]

    lines = [f"⚠️ <b>Найближчі загрози • {now.strftime('%H:%M:%S')}</b>\n"]

    for t in top:
        h = t["history"]
        last = h[-1]
        dist = last["dist"]

        first_time = t["first_seen"].strftime("%H:%M:%S")
        first_place = t["first_locality"]
        if t["first_region"]:
            first_place += f" ({t['first_region']})"

        curr_place = last["locality"]
        if last["region"]:
            curr_place += f" ({last['region']})"

        move = ""
        if len(h) > 1:
            diff = dist - h[-2]["dist"]
            if diff < -0.2:
                move = f" 🟢 наближається ({abs(diff):.1f} км)"
            elif diff > 0.2:
                move = f" 🔴 віддаляється ({diff:.1f} км)"
            else:
                move = " 🟡 стабільно"

        jet = " <b>РЕАКТИВНИЙ</b>" if t["is_jet"] else ""
        speed = f"\~{t['avg_speed']:.0f} км/год"
        course = f", курс: {t['heading']}" if t["heading"] != "невідомий" else ""

        lines.append(
            f"🎯 <b>#{t['num']}</b>{jet}\n"
            f"• З'явився: <b>{first_time}</b> → {first_place}\n"
            f"• Зараз: <b>{curr_place}</b>\n"
            f"• До Рівного: <b>{dist:.1f} км</b>{move}\n"
            f"• Швидкість: {speed}{course}\n"
            f"• Точок історії: <b>{len(h)}</b>\n"
        )

    return "\n".join(lines)

def fetch_and_process():
    try:
        r = requests.get(URL, timeout=12)
        r.raise_for_status()
        items = get_items(r.json())
        update_threat_tracking(items)
        return format_report()
    except Exception as e:
        print("API error:", e)
        return None

@bot.message_handler(commands=['start'])
def start(message):
    global USER_CHAT_ID
    USER_CHAT_ID = message.chat.id
    save_chat_id(USER_CHAT_ID)
    bot.reply_to(message, "✅ Трекінг активовано (покращена версія)")

@bot.message_handler(func=lambda m: True)
def any_msg(message):
    global USER_CHAT_ID
    USER_CHAT_ID = message.chat.id
    save_chat_id(USER_CHAT_ID)
    text = fetch_and_process()
    bot.reply_to(message, text or "✅ Загроз поруч немає", parse_mode="HTML")

def background():
    global USER_CHAT_ID
    last = None
    while True:
        try:
            if not USER_CHAT_ID:
                USER_CHAT_ID = load_chat_id()
            if USER_CHAT_ID:
                report = fetch_and_process()
                if report and report != last:
                    bot.send_message(USER_CHAT_ID, report, parse_mode="HTML")
                    last = report
                elif not report:
                    last = None
        except Exception as e:
            print("BG error:", e)
        time.sleep(INTERVAL)

threading.Thread(target=run_web, daemon=True).start()
threading.Thread(target=self_ping, daemon=True).start()
threading.Thread(target=background, daemon=True).start()

print("Bot started (fuzzy tracking)")

while True:
    try:
        bot.infinity_polling(none_stop=True, interval=2, timeout=40)
    except Exception as e:
        print("Polling error:", e)
        time.sleep(5)
