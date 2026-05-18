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

all_time_record = {
    "users": 0,
    "time": "-"
}

data_cache = []
MAX_POINTS = 300

last_status = None
visits = 0

# fallback значения
last_act_time = None
last_smev_time = None

# сырые данные источника
raw_status = {
    "activation": "нет данных",
    "smev": "нет данных",
    "users": 0,
    "users_avg": 0
}


# ---------------- TELEGRAM ----------------
def send_alert(text):
    try:
        if not BOT_TOKEN or not CHAT_ID:
            return

        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": text
            },
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

    global raw_status

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    act_match = re.search(
        r"Последняя завершённая активизация.*?(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})",
        text
    )

    smev_match = re.search(
        r"Последний ответ СМЭВ.*?(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})",
        text
    )

    users_match = re.search(
    r"(?:высокая|средняя|низкая)\s*\((\d+)/(\d+)\)",
    text,
    re.IGNORECASE
    )

    act_time = None
    smev_time = None

    act_raw = "нет данных"
    smev_raw = "нет данных"

    users = 0 
    users_avg = 0

    if act_match:
        act_raw = act_match.group(1)

        act_time = datetime.strptime(
            act_raw,
            "%Y-%m-%d %H:%M:%S"
        )

    if smev_match:
        smev_raw = smev_match.group(1)

        smev_time = datetime.strptime(
            smev_raw,
            "%Y-%m-%d %H:%M:%S"
        )

    if users_match:
        users = int(users_match.group(1))
        users_avg = int(users_match.group(2))
        
    raw_status = {
        "activation": act_raw,
        "smev": smev_raw,
        "users": users, 
        "users_avg": users_avg
    }

    return act_time, smev_time, users, users_avg
    
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



# ---------------- STATS ----------------
def analyze_history(data):

    #//global all_time_record

    if not data:
        return {"error": "no data"}

    now = datetime.utcnow()

    last_hour = []

    for x in data:

        try:

            t = datetime.strptime(
                x["time"],
                "%Y-%m-%dT%H:%M:%S.%fZ"
            )

            if (now - t).total_seconds() <= 3600:
                last_hour.append(x)

        except:
            continue

    if not last_hour:
        return {"error": "no recent data"}

    smev = [
        x.get("smev", 0)
        for x in last_hour
    ]

    act = [
        x.get("act", 0)
        for x in last_hour
    ]

    # рекорд users за текущие сутки
    day_record = None

    today = now.date()

    for x in data:

        try:

            t = datetime.strptime(
                x["time"],
                "%Y-%m-%dT%H:%M:%S.%fZ"
            )

            users = x.get("users", 0)

            if t.date() == today:

                if (
                    day_record is None or
                    users > day_record["users"]
                ):

                    day_record = {
                        "users": users,
                        "time": t.isoformat() + "Z"
                    }

        except:
            continue

    return {
        "points": len(last_hour),

        "avg_smev": round(
            sum(smev) / len(smev),
            2
        ),

        "max_smev": round(
            max(smev),
            2
        ),

        "avg_act": round(
            sum(act) / len(act),
            2
        ),

        "max_act": round(
            max(act),
            2
        ),

        "all_time_record": all_time_record,

        "day_record": day_record
    }
# ---------------- MONITOR ----------------
def monitor():
    global data_cache
    global last_status
    global last_act_time
    global last_smev_time
    global all_time_record

    while True:
        try:
            response = requests.get(URL, timeout=15)

            act_new, smev_new, users, users_avg = parse_times(response.text)
            
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

            status, act_delay, smev_delay = analyze(
                act_time,
                smev_time
            )
            point = {
                "time": datetime.utcnow().isoformat() + "Z",
                "act": round(act_delay, 2),
                "smev": round(smev_delay, 2),
                "users": users,
                "users_avg": users_avg,
                "status": status
            }
    
            if users > all_time_record["users"]:
    
                all_time_record = {
                    "users": users,
                    "time": datetime.utcnow().isoformat() + "Z"
                }
           
            data_cache.append(point)

            if len(data_cache) > MAX_POINTS:
                data_cache.pop(0)

            save_data(point)

            print(point)

            if status != last_status:
                send_alert(
                    f"{status}\n"
                    f"СМЭВ: {smev_delay:.1f} мин"
                )

                last_status = status

        except Exception as e:
            print("MONITOR ERROR:", e)

        time.sleep(60)

# ---------------- ROUTES ----------------
@app.route("/")
def index():
    return send_from_directory(
        "/opt/taho-monitor",
        "index.html"
    )


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


@app.route("/raw")
def raw():
    return jsonify(raw_status)


@app.route("/visits")
def get_visits():
    global visits

    visits += 1

    return {"visits": visits}


# ---------------- START ----------------
load_data()

threading.Thread(
    target=monitor,
    daemon=True
).start()
