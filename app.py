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

def get_bot_token():
    return os.getenv("BOT_TOKEN")

def get_chat_id():
    return os.getenv("CHAT_ID")

DATA_FILE = "/opt/taho-monitor/data.json"

data_cache = []
MAX_POINTS = 300
last_status = None
visits = 0

from flask import send_from_directory

@app.route("/")
def index():
    return send_from_directory("/opt/taho-monitor", "index.html")

# ---------------- TELEGRAM ----------------
def send_alert(text):
    try:
        token = get_bot_token()
        chat_id = get_chat_id()

        print("SEND TG:", token, chat_id)

        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10
        )
    except Exception as e:
        print("Telegram error:", e)


# ---------------- SAVE (atomic) ----------------
def save_data(point):
    try:
        tmp_file = DATA_FILE + ".tmp"

        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
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
        print("LOADING DATA...")

        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r") as f:
                data = json.load(f)

                if isinstance(data, list):
                    data_cache = data[-MAX_POINTS:]
                    print(f"LOADED {len(data_cache)} points")
                else:
                    print("DATA NOT LIST")

        else:
            print("NO DATA FILE")

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
    
def analyze_history(data):
    if not data or not isinstance(data, list):
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

    smev = [x["smev"] for x in last_hour if "smev" in x]
    act = [x["act"] for x in last_hour if "act" in x]

    return {
        "points": len(last_hour),

        "avg_smev": round(sum(smev)/len(smev), 2) if smev else 0,
        "max_smev": round(max(smev), 2) if smev else 0,

        "avg_act": round(sum(act)/len(act), 2) if act else 0,
        "max_act": round(max(act), 2) if act else 0
    }

    # --- SMEV ---
    avg_smev = sum(smev_values) / len(smev_values)
    max_smev = max(smev_values)

    smev_critical = len([x for x in smev_values if x > 60])
    smev_percent = (smev_critical / len(smev_values)) * 100

    # --- ACTIVATION ---
    avg_act = sum(act_values) / len(act_values)
    max_act = max(act_values)

    act_slow = len([x for x in act_values if x > 10])
    act_percent = (act_slow / len(act_values)) * 100

    # --- TREND ---
    trend = "stable"
    if len(smev_values) > 10:
        last = smev_values[-10:]
        if last[-1] > last[0]:
            trend = "worsening"
        elif last[-1] < last[0]:
            trend = "improving"

    # --- ACT TREND ---
    act_trend = "stable"
    if len(act_values) > 10:
        last = act_values[-10:]
        if last[-1] > last[0]:
            act_trend = "worsening"
        elif last[-1] < last[0]:
            act_trend = "improving"

    return {
        "points": len(smev_values),

        # SMEV
        "avg_smev": round(avg_smev, 2),
        "max_smev": round(max_smev, 2),
        "smev_critical_percent": round(smev_percent, 1),
        "smev_trend": trend,

        # ACTIVATION
        "avg_act": round(avg_act, 2),
        "max_act": round(max_act, 2),
        "act_slow_percent": round(act_percent, 1),
        "act_trend": act_trend
    }

# ---------------- MONITOR ----------------
def monitor():
    global data_cache, last_status

    while True:
        try:
            response = requests.get(URL, timeout=15)

            act_time, smev_time = parse_times(response.text)

            try:
                extra = parse_extra(response.text)
            except:
                extra = {"prepared": None, "smev_request": None}

            if act_time and smev_time:
                status, act_delay, smev_delay = analyze(act_time, smev_time)

                point = {
                    "time": datetime.now().strftime("%H:%M"),
                    "act": round(act_delay, 2),
                    "smev": round(smev_delay, 2),
                    "status": status,
                    "prepared": extra.get("prepared"),
                    "smev_request": extra.get("smev_request")
                }

                data_cache.append(point)

                if len(data_cache) > MAX_POINTS:
                    data_cache.pop(0)

                save_data(point)

                print(point)

                if status != last_status:
                    send_alert(
                        f"{status}\nСМЭВ: {smev_delay:.1f} мин\nACT: {act_delay:.1f} мин"
                    )
                    last_status = status

            else:
                print("PARSE ERROR")

        except Exception as e:
            print("MONITOR ERROR:", e)

        time.sleep(60)
send_alert("TEST MESSAGE")

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


@app.route("/stats")
def stats():
    try:
        if not os.path.exists(DATA_FILE):
            return {"error": "file not found"}

        with open(DATA_FILE, "r") as f:
            try:
                data = json.load(f)
            except:
                return {"error": "invalid json"}

    except Exception as e:
        return {"error": str(e)}

    return analyze_history(data)


@app.route("/visits")
def get_visits():
    global visits
    visits += 1
    return {"visits": visits}





# ---------------- START ----------------
@app.before_first_request
def startup():
    load_data()
    threading.Thread(target=monitor, daemon=True).start()
