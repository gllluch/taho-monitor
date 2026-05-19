#```python
import os
import re
import json
import time
import threading
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, send_from_directory

app = Flask(__name__)

URL = "https://tah-o.ru/activation/status"

DATA_FILE = "/opt/taho-monitor/data.json"
RECORD_FILE = "/opt/taho-monitor/record.json"

data_cache = []

raw_status = {
    "activation": "нет данных",
    "smev": "нет данных",
    "users": 0,
    "users_avg": 0,
    "queue": 0
}

# загрузка all-time рекорда
try:

    with open(RECORD_FILE, "r") as f:
        all_time_record = json.load(f)

except:

    all_time_record = {
        "users": 0,
        "time": "-"
    }

# загрузка истории
try:

    with open(DATA_FILE, "r") as f:
        data_cache = json.load(f)

except:
    data_cache = []


# ---------------- PARSER ----------------
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

    queue_match = re.search(
        r"долго\s*\((\d+)\s*min",
        text,
        re.IGNORECASE
    )

    act_time = None
    smev_time = None

    act_raw = "нет данных"
    smev_raw = "нет данных"

    users = 0
    users_avg = 0
    queue_minutes = 0

    if act_match:

        act_raw = act_match.group(1)

        act_time = datetime.strptime(
            act_raw,
            "%Y-%m-%d %H:%M:%S"
        )

# MSK -> UTC
        act_time = act_time.replace(hour=act_time.hour - 3)

    if smev_match:

        smev_raw = smev_match.group(1)

        smev_time = datetime.strptime(
            smev_raw,
            "%Y-%m-%d %H:%M:%S"
        )

# MSK -> UTC
        smev_time = smev_time.replace(
            hour=smev_time.hour - 3
        )

    if users_match:

        users = int(users_match.group(1))
        users_avg = int(users_match.group(2))

    if queue_match:

        queue_minutes = int(
            queue_match.group(1)
        )

    raw_status = {
        "activation": act_raw,
        "smev": smev_raw,
        "users": users,
        "users_avg": users_avg,
        "queue": queue_minutes
    }

    return (
        act_time,
        smev_time,
        users,
        users_avg,
        queue_minutes
    )


# ---------------- STATS ----------------
def analyze_history(data):

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
    global all_time_record

    last_smev_time = None
    last_act_time = None

    while True:

        try:

            response = requests.get(
                URL,
                timeout=20
            )

            act_new, smev_new, users, users_avg, queue_minutes = parse_times(
                response.text
            )

            if act_new:
                last_act_time = act_new

            if smev_new:
                last_smev_time = smev_new

            if not last_act_time or not last_smev_time:
                time.sleep(30)
                continue

            now = datetime.utcnow()

            act_delay = (
                now - last_act_time
            ).total_seconds() / 60

            smev_delay = (
                now - last_smev_time
            ).total_seconds() / 60

            status = "OK"

            if smev_delay > 60:
                status = "ПРОБЛЕМЫ"

            point = {
                "time": datetime.utcnow().isoformat() + "Z",
                "act": round(act_delay, 2),
                "smev": round(smev_delay, 2),
                "users": users,
                "users_avg": users_avg,
                "queue": queue_minutes,
                "status": status
            }

            # all-time record
            if users > all_time_record["users"]:

                all_time_record = {
                    "users": users,
                    "time": datetime.utcnow().isoformat() + "Z"
                }

                try:

                    with open(RECORD_FILE, "w") as f:
                        json.dump(
                            all_time_record,
                            f,
                            indent=2
                        )

                except Exception as e:
                    print("RECORD SAVE ERROR:", e)

            data_cache.append(point)

            # ограничение размера
            if len(data_cache) > 5000:
                data_cache = data_cache[-5000:]

            try:

                with open(DATA_FILE, "w") as f:
                    json.dump(
                        data_cache,
                        f,
                        indent=2
                    )

            except Exception as e:
                print("DATA SAVE ERROR:", e)

            print(point)

        except Exception as e:

            print("MONITOR ERROR:", e)

        time.sleep(30)


# ---------------- ROUTES ----------------
@app.route("/")
def index():

    return send_from_directory(
        "/opt/taho-monitor",
        "index.html"
    )


@app.route("/data")
def data():

    return jsonify(data_cache)


@app.route("/stats")
def stats():

    return jsonify(
        analyze_history(data_cache)
    )


@app.route("/raw")
def raw():

    return jsonify(raw_status)


# ---------------- START ----------------
threading.Thread(
    target=monitor,
    daemon=True
).start()

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=8000
    )

