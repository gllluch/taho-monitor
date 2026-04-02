from flask import Flask, jsonify
import threading
import time
import requests
from datetime import datetime
from bs4 import BeautifulSoup
import re
import json
import os

app = Flask(__name__)

URL = "https://tah-o.ru/activation/status"

# 🔴 ОБЯЗАТЕЛЬНО СМЕНИ ТОКЕН (ты его уже светил)
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

DATA_FILE = "/opt/taho-monitor/data.json"

data_cache = []
MAX_POINTS = 300
last_status = None
visits = 0


# ---------------- TELEGRAM ----------------
def send_alert(text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text}
        )
    except Exception as e:
        print("Telegram error:", e)


# ---------------- SAVE ----------------
def save_data(point):
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
        else:
            data = []

        data.append(point)

        if len(data) > 1000:
            data = data[-1000:]

        with open(DATA_FILE, "w") as f:
            json.dump(data, f)

    except Exception as e:
        print("SAVE ERROR:", e)


# ---------------- PARSE ----------------
def parse_times(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text()

    act_match = re.search(
        r"Последняя завершённая активизация.*?(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})",
        text
    )
    smev_match = re.search(
        r"Последний ответ СМЭВ.*?(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})",
        text
    )

    if not act_match or not smev_match:
        return None, None

    act_time = datetime.strptime(act_match.group(1), "%Y-%m-%d %H:%M:%S")
    smev_time = datetime.strptime(smev_match.group(1), "%Y-%m-%d %H:%M:%S")

    return act_time, smev_time


def parse_extra(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text()

    prepared = re.search(
        r"Последняя подготовленная активизация.*?(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})",
        text
    )

    smev_request = re.search(
        r"Последний запрос СМЭВ.*?(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})",
        text
    )

    return {
        "prepared": prepared.group(1) if prepared else None,
        "smev_request": smev_request.group(1) if smev_request else None
    }


# ---------------- ANALYZE ----------------
def analyze(act_time, smev_time):
    now = datetime.now()

    act_delay = (now - act_time).total_seconds() / 60
    smev_delay = (now - smev_time).total_seconds() / 60

    status = "OK"

    if smev_delay > 60:
        status = "SMEV_CRITICAL"
    elif smev_delay > 30:
        status = "SMEV_SLOW"
    elif act_delay > 20:
        status = "ACTIVATION_DELAY"

    return status, act_delay, smev_delay


# ---------------- MONITOR ----------------
def monitor():
    global data_cache, last_status

    while True:
        try:
            response = requests.get(URL, timeout=15)

            act_time, smev_time = parse_times(response.text)
            extra = parse_extra(response.text)

            if act_time and smev_time:
                status, act_delay, smev_delay = analyze(act_time, smev_time)

                point = {
                    "time": datetime.now().strftime("%H:%M"),
                    "act": round(act_delay, 2),
                    "smev": round(smev_delay, 2),
                    "status": status,
                    "prepared": extra["prepared"],
                    "smev_request": extra["smev_request"]
                }

                data_cache.append(point)

                if len(data_cache) > MAX_POINTS:
                    data_cache.pop(0)

                save_data(point)

                print(point)

                # Telegram только при изменении
                if status != last_status:
                    send_alert(
                        f"{status}\n"
                        f"СМЭВ: {smev_delay:.1f} мин\n"
                        f"Активации: {act_delay:.1f} мин"
                    )
                    last_status = status

            else:
                print("Ошибка парсинга")

        except Exception as e:
            print("MONITOR ERROR:", e)

        time.sleep(60)


# ---------------- API ----------------
@app.route("/data")
def get_data():
    return jsonify(data_cache)


@app.route("/history")
def history():
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except:
        return []


@app.route("/visits")
def get_visits():
    global visits
    visits += 1
    return {"visits": visits}
@app.route("/stats")

def stats():
    try:
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
    except:
        data = []

    return analyze_history(data)

@app.route("/")
def index():
    return {"status": "running", "points": len(data_cache)}


# ---------------- START ----------------
threading.Thread(target=monitor, daemon=True).start()
