const { Telegraf } = require('telegraf');
const axios = require('axios');
const express = require('express');
const fs = require('fs');

const app = express();
const PORT = process.env.PORT || 10000;

app.get('/', (req, res) => {
    res.send("Bot is running!");
});

app.listen(PORT, '0.0.0.0', () => {
    console.log(`Server is listening on port ${PORT}`);
});

const API_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
if (!API_TOKEN) {
    console.error("TELEGRAM_BOT_TOKEN не встановлено!");
    process.exit(1);
}

const bot = new Telegraf(API_TOKEN);

const URL_API = "https://neptun.in.ua/api/v1/threats";
const INTERVAL = 12000; // 12 секунд
const CHAT_ID_FILE = "chat_id.txt";

const RIVNE_LAT = 50.6231;
const RIVNE_LON = 26.2274;

const KEEP_ALIVE_SECONDS = 420;
const MATCH_DISTANCE_KM = 18;
const MATCH_TIME_SECONDS = 240;

const MARKERS = [
    "🔴", "🟠", "🟡", "🟢", "🔵", "🟣", "🟤",
    "⚫", "⚪", "🔺", "🔻", "🔶", "🔷", "🔸", "🔹",
    "🟥", "🟧", "🟨", "🟩", "🟦", "🟪", "⬛", "⬜"
];

let threatDatabase = {};
let nextUavNumber = 1;
let userChatId = loadChatId();

function saveChatId(chatId) {
    try {
        fs.writeFileSync(CHAT_ID_FILE, chatId.toString());
    } catch (e) {}
}

function loadChatId() {
    if (fs.existsSync(CHAT_ID_FILE)) {
        try {
            return parseInt(fs.readFileSync(CHAT_ID_FILE, 'utf8').trim());
        } catch (e) {
            return null;
        }
    }
    return null;
}

function haversine(lat1, lon1, lat2, lon2) {
    const R = 6371.0;
    const dLat = (lat2 - lat1) * Math.PI / 180;
    const dLon = (lon2 - lon1) * Math.PI / 180;
    const a = Math.sin(dLat / 2) ** 2 +
              Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
              Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(a));
}

function calculateBearing(lat1, lon1, lat2, lon2) {
    const dLon = (lon2 - lon1) * Math.PI / 180;
    const phi1 = lat1 * Math.PI / 180;
    const phi2 = lat2 * Math.PI / 180;
    const y = Math.sin(dLon) * Math.cos(phi2);
    const x = Math.cos(phi1) * Math.sin(phi2) - Math.sin(phi1) * Math.cos(phi2) * Math.cos(dLon);
    let brng = Math.atan2(y, x) * 180 / Math.PI;
    return (brng + 360) % 360;
}

function bearingToCardinal(deg) {
    const dirs = ["північ", "північний схід", "схід", "південний схід", "південь", "південний захід", "захід", "північний захід"];
    return dirs[Math.floor((deg + 22.5) / 45) % 8];
}

function getItems(data) {
    if (Array.isArray(data)) return data;
    if (data && typeof data === 'object') {
        for (let k of ["threats", "data", "items"]) {
            if (Array.isArray(data[k])) return data[k];
        }
    }
    return [];
}

function isShahed(item) {
    const s = JSON.stringify(item).toLowerCase();
    return ["uav", "shahed", "шахед", "бпла", "дрон"].some(k => s.includes(k));
}

function findMatchingTrack(lat, lon, now) {
    let bestId = null;
    let bestDist = 9999;

    for (let [tid, data] of Object.entries(threatDatabase)) {
        const last = data.history[data.history.length - 1];
        const timeDiff = (now - data.lastSeen) / 1000;
        if (timeDiff > MATCH_TIME_SECONDS) continue;

        const d = haversine(last.lat, last.lon, lat, lon);
        if (d < MATCH_DISTANCE_KM && d < bestDist) {
            bestDist = d;
            bestId = tid;
        }
    }
    return bestId;
}

function updateThreatTracking(items) {
    const now = new Date();

    items.forEach(item => {
        if (!isShahed(item)) return;

        const lat = item.lat;
        const lon = item.lon;
        if (lat === undefined || lat === null || lon === undefined || lon === null || item.areaOnly) return;

        const locality = item.locality || item.title || item.name || "Невідомо";
        const region = item.region || item.state || "";
        const dist = haversine(RIVNE_LAT, RIVNE_LON, lat, lon);

        let matchedId = findMatchingTrack(lat, lon, now);

        if (matchedId) {
            let entry = threatDatabase[matchedId];
            entry.lastSeen = now;
            let history = entry.history;
            let last = history[history.length - 1];

            if ((now - last.time) / 1000 >= 8 || Math.abs(last.lat - lat) > 0.00001 || Math.abs(last.lon - lon) > 0.00001) {
                history.push({ time: now, lat, lon, dist, locality, region });
            }

            if (history.length > 60) {
                entry.history = history.slice(-60);
            }
        } else {
            let history = [];
            if (Array.isArray(item.trail)) {
                item.trail.forEach(p => {
                    const t = p.t ? new Date(p.t) : null;
                    if (t && p.lat !== undefined && p.lon !== undefined) {
                        history.push({
                            time: t,
                            lat: p.lat,
                            lon: p.lon,
                            dist: haversine(RIVNE_LAT, RIVNE_LON, p.lat, p.lon),
                            locality,
                            region
                        });
                    }
                });
            }

            history.sort((a, b) => a.time - b.time);
            history.push({ time: now, lat, lon, dist, locality, region });

            const first = history[0];
            const newId = `track_${nextUavNumber}`;
            const marker = MARKERS[(nextUavNumber - 1) % MARKERS.length];

            threatDatabase[newId] = {
                num: nextUavNumber,
                marker,
                firstSeen: first.time,
                firstLocality: first.locality,
                firstRegion: first.region,
                history,
                avgSpeed: 180.0,
                heading: "невідомий",
                isJet: false,
                lastSeen: now
            };
            nextUavNumber++;
        }

        let entry = matchedId ? threatDatabase[matchedId] : threatDatabase[`track_${nextUavNumber - 1}`];
        if (!entry) return;

        let hist = entry.history;
        if (hist.length >= 2) {
            let totalKm = 0;
            for (let i = 1; i < hist.length; i++) {
                totalKm += haversine(hist[i-1].lat, hist[i-1].lon, hist[i].lat, hist[i].lon);
            }
            let sec = (hist[hist.length - 1].time - hist[0].time) / 1000;
            if (sec >= 10) {
                let speed = (totalKm / sec) * 3600;
                if (speed >= 25 && speed <= 900) {
                    entry.avgSpeed = speed;
                }
            }

            let prev = hist[hist.length - 2];
            if (haversine(prev.lat, prev.lon, lat, lon) > 0.05) {
                let brng = calculateBearing(prev.lat, prev.lon, lat, lon);
                entry.heading = bearingToCardinal(brng);
            }
        }

        let vel = item.velocity || {};
        let apiSpeed = vel.speedKmh || item.speed || 0;
        let checkSpeed = apiSpeed > 0 ? apiSpeed : entry.avgSpeed;
        let itemText = JSON.stringify(item).toLowerCase();

        if (checkSpeed > 200 || apiSpeed > 200 || ["реактив", "jet", "кета"].some(w => itemText.includes(w))) {
            entry.isJet = true;
        }
    });

    let toDel = [];
    for (let [tid, data] of Object.entries(threatDatabase)) {
        if ((now - data.lastSeen) / 1000 > KEEP_ALIVE_SECONDS) {
            toDel.push(tid);
        }
    }
    toDel.forEach(tid => delete threatDatabase[tid]);
}

function formatReport() {
    if (Object.keys(threatDatabase).length === 0) return null;

    const now = new Date();
    let alive = Object.values(threatDatabase).filter(t => (now - t.lastSeen) / 1000 <= KEEP_ALIVE_SECONDS);

    if (alive.length === 0) return null;

    alive.sort((a, b) => a.history[a.history.length - 1].dist - b.history[b.history.length - 1].dist);
    let top = alive.slice(0, 3);

    let lines = [`⚠️ <b>Найближчі загрози • ${now.toTimeString().split(' ')[0]}</b>\n`];

    top.forEach(t => {
        let h = t.history;
        let last = h[h.length - 1];
        let dist = last.dist;
        let marker = t.marker;

        let firstTime = t.firstSeen.toTimeString().split(' ')[0];
        let firstPlace = t.firstLocality + (t.firstRegion ? ` (${t.firstRegion})` : '');
        let currPlace = last.locality + (last.region ? ` (${last.region})` : '');

        let move = "";
        if (h.length > 1) {
            let diff = dist - h[h.length - 2].dist;
            if (diff < -0.2) move = ` 🟢 наближається (${Math.abs(diff).toFixed(1)} км)`;
            else if (diff > 0.2) move = ` 🔴 віддаляється (${diff.toFixed(1)} км)`;
            else move = " 🟡 стабільно";
        }

        let jet = t.isJet ? " <b>РЕАКТИВНИЙ</b>" : "";
        let speed = `\\~${Math.round(t.avgSpeed)} км/год`;
        let course = t.heading !== "невідомий" ? `, курс: ${t.heading}` : "";

        lines.push(
            `${marker} <b>#$ {t.num}</b>${jet}\n` +
            `• З'явився: <b>${firstTime}</b> → ${firstPlace}\n` +
            `• Зараз: <b>${currPlace}</b>\n` +
            `• До Рівного: <b>${dist.toFixed(1)} км</b>${move}\n` +
            `• Швидкість: ${speed}${course}\n` +
            `• Точок історії: <b>${h.length}</b>\n`
        );
    });

    return lines.join("\n");
}

async function fetchAndProcess() {
    try {
        let response = await axios.get(URL_API, { timeout: 12000 });
        let items = getItems(response.data);
        updateThreatTracking(items);
        return formatReport();
    } catch (e) {
        console.error("API error:", e.message);
        return null;
    }
}

bot.start((ctx) => {
    userChatId = ctx.chat.id;
    saveChatId(userChatId);
    ctx.reply("✅ Трекінг активовано");
});

bot.on('text', async (ctx) => {
    userChatId = ctx.chat.id;
    saveChatId(userChatId);
    let text = await fetchAndProcess();
    ctx.reply(text || "✅ Загроз поруч немає", { parse_mode: "HTML" });
});

setInterval(async () => {
    try {
        if (!userChatId) userChatId = loadChatId();
        if (userChatId) {
            let report = await fetchAndProcess();
            if (report) {
                await bot.telegram.sendMessage(userChatId, report, { parse_mode: "HTML" }).catch(() => {});
            }
        }
    } catch (e) {
        console.error("BG error:", e.message);
    }
}, INTERVAL);

bot.launch().then(() => console.log("Bot started")).catch(err => console.error(err));

process.once('SIGINT', () => bot.stop('SIGINT'));
process.once('SIGTERM', () => bot.stop('SIGTERM'));
                       
