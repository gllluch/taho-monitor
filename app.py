from flask import Flask, jsonify, send_from_directory
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

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

DATA_FILE = "/opt/taho-monitor/data.json"

data_cache = []
MAX_POINTS = 300
last_status = None
visits = 0

# fallback значения
last_act_time = None
last_smev_time = None


# ---------------- TELEGRAM ----------------
def send_alert(text):
    try:
        if not BOT_TOKEN or not CHAT_ID:
            return

        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text},
            timeout=10
        )
    except Exception as e:
        print("Telegram error:", e)


# ---------------- SAVE ----------------
def save_data(point):
    try:
        tmp_file = DATA_FILE + ".tmp"

        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r") as f:
                try:
                    data = json.load(f)
                except:
                    data = []
        else:
            data = []

        data.append(point)

        if len(data) > 1000:
            data = data[-1000:]

        with open(tmp_file, "w") as f:
            json.dump(data, f)

        os.replace(tmp_file, DATA_FILE)

    except Exception as e:
        print("SAVE ERROR:", e)


# ---------------- LOAD ----------------
def load_data():
    global data_cache

    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r") as f:
                try:
                    data = json.load(f)
                except:
                    data = []

                if isinstance(data, list):
                    data_cache = data[-MAX_POINTS:]
                    print("LOADED:", len(data_cache))
    except Exception as e:
        print("LOAD ERROR:", e)


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

    act_time = None
    smev_time = None

    if act_match:
        act_time = datetime.strptime(act_match.group(1), "%Y-%m-%d %H:%M:%S")

    if smev_match:
        smev_time = datetime.strptime(smev_match.group(1), "%Y-%m-%d %H:%M:%S")

    return act_time, smev_time


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


# ---------------- STATS (ПОСЛЕДНИЙ ЧАС) ----------------
def analyze_history(data):
    if not data:
        return {"error": "no data"}

    now = datetime.now()
    last_hour = []

    for x in data:
        try:
            t = datetime.strptime(x["time"], "%H:%M")
            t = t.replace(year=now.year, month=now.month, day=now.day)

            if (now - t).total_seconds() <= 3600:
                last_hour.append(x)
        except:
            continue

    if not last_hour:
        return {"error": "no recent data"}

    smev = [x["smev"] for x in last_hour]
    act = [x["act"] for x in last_hour]

    return {
        "points": len(last_hour),
        "avg_smev": round(sum(smev)/len(smev), 2),
        "max_smev": round(max(smev), 2),
        "avg_act": round(sum(act)/len(act), 2),
        "max_act": round(max(act), 2)
    }


# ---------------- MONITOR ----------------
def monitor():
    global data_cache, last_status, last_act_time, last_smev_time

    while True:
        try:
            response = requests.get(URL, timeout=15)

            act_new, smev_new = parse_times(response.text)

            # fallback логика
            if act_new:
                last_act_time = act_new

            if smev_new:
                last_smev_time = smev_new

            act_time = last_act_time
            smev_time = last_smev_time

            if not act_time or not smev_time:
                print("NO DATA YET")
                time.sleep(60)
                continue

            status, act_delay, smev_delay = analyze(act_time, smev_time)

            point = {
                "time": datetime.now().strftime("%H:%M"),
                "act": round(act_delay, 2),
                "smev": round(smev_delay, 2),
                "status": status
            }

            data_cache.append(point)

            if len(data_cache) > MAX_POINTS:
                data_cache.pop(0)

            save_data(point)

            print(point)

            if status != last_status:
                send_alert(f"{status}\nСМЭВ: {smev_delay:.1f} мин")
                last_status = status

        except Exception as e:
            print("MONITOR ERROR:", e)

        time.sleep(60)


# ---------------- API ----------------
@app.route("/")
def index():
    return send_from_directory("/opt/taho-monitor", "index.html")


@app.route("/data")
def get_data():
    return jsonify(data_cache)


@app.route("/stats")
def stats():
    try:
        if not os.path.exists(DATA_FILE):
            return {"error": "no file"}

        with open(DATA_FILE, "r") as f:
            data = json.load(f)
    except:
        return {"error": "bad file"}

    return analyze_history(data)


@app.route("/visits")
def get_visits():
    global visits
    visits += 1
    return {"visits": visits}


# ---------------- START ----------------
load_data()
threading.Thread(target=monitor, daemon=True).start()
