from flask import Flask, jsonify, send_from_directory
from threading import Lock
import threading
import time
import requests
from datetime import datetime
from bs4 import BeautifulSoup
import re
import json
import os


app = Flask(__name__)

# ============================================================
# CONFIG
# ============================================================

URL = "https://tah-o.ru/activation/status"

RECORD_FILE = "/opt/taho-monitor/record.json"
DATA_FILE = "/opt/taho-monitor/data.json"

MAX_POINTS = 1440

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")


# ============================================================
# GLOBALS
# ============================================================

data_lock = Lock()

data_cache = []

last_status = None

visits = 0

last_act_time = None
last_smev_time = None
last_cert_delay = None

last_smev_available = True


# ============================================================
# ALL TIME RECORD
# ============================================================

try:
    with open(RECORD_FILE, "r") as f:
        all_time_record = json.load(f)

except Exception:
    all_time_record = {
        "users": 0,
        "time": "-"
    }


# ============================================================
# RAW STATUS
# ============================================================

raw_status = {
    "activation": "нет данных",
    "smev": "нет данных",
    "users": 0,
    "users_avg": 0,
    "processing": 0,
    "cert": None,
    "smev_available": False
}


# ============================================================
# TELEGRAM
# ============================================================

def send_alert(text):

    try:

        if not BOT_TOKEN or not CHAT_ID:
            return

        requests.post(
            "https://api.telegram.org/bot{}/sendMessage".format(
                BOT_TOKEN
            ),
            json={
                "chat_id": CHAT_ID,
                "text": text
            },
            timeout=10
        )

    except Exception as e:

        print(
            "Telegram error:",
            e
        )


# ============================================================
# SAVE DATA
# ============================================================

def save_data(point):

    try:

        tmp_file = DATA_FILE + ".tmp"

        if os.path.exists(DATA_FILE):

            with open(DATA_FILE, "r") as f:

                try:
                    data = json.load(f)

                except Exception:
                    data = []

        else:

            data = []


        if not isinstance(data, list):
            data = []


        data.append(point)


        if len(data) > 1440:

            data = data[-1440:]


        with open(tmp_file, "w") as f:

            json.dump(
                data,
                f,
                ensure_ascii=False
            )


        os.replace(
            tmp_file,
            DATA_FILE
        )


    except Exception as e:

        print(
            "SAVE ERROR:",
            e
        )


# ============================================================
# LOAD DATA
# ============================================================

def load_data():

    global data_cache

    try:

        if not os.path.exists(DATA_FILE):
            return


        with open(DATA_FILE, "r") as f:

            try:
                data = json.load(f)

            except Exception:
                data = []


        if isinstance(data, list):

            with data_lock:

                data_cache = data[-MAX_POINTS:]


            print(
                "LOADED:",
                len(data_cache)
            )


    except Exception as e:

        print(
            "LOAD ERROR:",
            e
        )


# ============================================================
# PARSE SOURCE PAGE
# ============================================================

def parse_times(html):

    global raw_status


    soup = BeautifulSoup(
        html,
        "html.parser"
    )


    text = soup.get_text(
        " ",
        strip=True
    )


    # ========================================================
    # ACTIVATION
    # ========================================================

    act_match = re.search(
        r"Последняя завершённая активизация.*?"
        r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})",
        text,
        re.IGNORECASE | re.DOTALL
    )


    # ========================================================
    # SMEV
    # ========================================================

    smev_match = re.search(
        r"Последний ответ СМЭВ:\s*(?:<[^>]+>\s*)?"
        r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})",
        html,
        re.IGNORECASE | re.DOTALL
    )


    # ========================================================
    # SMEV: ЯВНО НЕТ ВЗАИМОДЕЙСТВИЯ
    #
    # Реальная фраза на сайте:
    #
    # "В течение часа не было взаимодействия с СМЭВ"
    # ========================================================

    no_smev_interaction = re.search(
        r"В течение часа не было взаимодействия с СМЭВ",
        text,
        re.IGNORECASE
    )


    # ========================================================
    # SMEV: ПОСЛЕДНИЙ ОТВЕТ = -
    # ========================================================

    smev_response_dash = re.search(
        r"Последний ответ СМЭВ:\s*(?:<[^>]+>\s*)?-\s*",
        html,
        re.IGNORECASE | re.DOTALL
    )


    # ========================================================
    # SMEV UNAVAILABLE
    # ========================================================

    smev_unavailable = (
        no_smev_interaction is not None
        or smev_response_dash is not None
    )


    # ========================================================
    # USERS
    # ========================================================

    users_match = re.search(
        r"(?:высокая|средняя|низкая)"
        r"\s*\((\d+)/(\d+)\)",
        text,
        re.IGNORECASE
    )


    # ========================================================
    # PROCESSING
    # ========================================================

    processing_match = re.search(
        r"Обработка последних запросов.*?"
        r"долго\s*\((\d+)\s*min",
        text,
        re.IGNORECASE | re.DOTALL
    )


    # ========================================================
    # CERTIFICATE
    # ========================================================

    cert_match = re.search(
        r"Среднее время получения сертификата.*?"
        r"\((\d+)\s*мин",
        text,
        re.IGNORECASE | re.DOTALL
    )


    # ========================================================
    # INITIAL VALUES
    # ========================================================

    act_time = None
    smev_time = None

    act_raw = "нет данных"
    smev_raw = "нет данных"

    users = 0
    users_avg = 0

    processing_minutes = 0
    cert_delay = None


    # ========================================================
    # ACTIVATION TIME
    # ========================================================

    if act_match:

        act_raw = act_match.group(1)

        try:

            act_time = datetime.strptime(
                act_raw,
                "%Y-%m-%d %H:%M:%S"
            )

        except Exception:

            act_time = None


    # ========================================================
    # SMEV TIME
    # ========================================================

    if smev_match:

        smev_raw = smev_match.group(1)

        try:

            smev_time = datetime.strptime(
                smev_raw,
                "%Y-%m-%d %H:%M:%S"
            )

        except Exception:

            smev_time = None


    # ========================================================
    # USERS
    # ========================================================

    if users_match:

        try:

            users = int(
                users_match.group(1)
            )

            users_avg = int(
                users_match.group(2)
            )

        except Exception:

            users = 0
            users_avg = 0


    # ========================================================
    # PROCESSING
    # ========================================================

    if processing_match:

        try:

            processing_minutes = int(
                processing_match.group(1)
            )

        except Exception:

            processing_minutes = 0


    # ========================================================
    # ЕСЛИ СМЭВ НЕ ОТВЕЧАЕТ
    #
    # Если есть последнее время ответа, можно показать,
    # сколько прошло с него для processing.
    # ========================================================

    if smev_unavailable and smev_time:

        processing_minutes = max(
            0,
            int(
                (
                    datetime.now() -
                    smev_time
                ).total_seconds() / 60
            )
        )


    # ========================================================
    # CERTIFICATE
    # ========================================================

    if cert_match:

        try:

            cert_delay = int(
                cert_match.group(1)
            )

        except Exception:

            cert_delay = None


    # ========================================================
    # RAW STATUS
    # ========================================================

    raw_status = {
        "activation": act_raw,
        "smev": smev_raw,
        "users": users,
        "users_avg": users_avg,
        "processing": processing_minutes,
        "cert": cert_delay,
        "smev_available": not smev_unavailable
    }


    print(
        "PARSE:",
        "SMEV_UNAVAILABLE=",
        smev_unavailable,
        "SMEV=",
        smev_raw,
        "ACT=",
        act_raw,
        "CERT=",
        cert_delay
    )


    return (
        act_time,
        smev_time,
        users,
        users_avg,
        processing_minutes,
        cert_delay,
        smev_unavailable
    )


# ============================================================
# ANALYZE
# ============================================================

def analyze(
    act_time,
    smev_time
):

    now = datetime.now()


    act_delay = (
        now - act_time
    ).total_seconds() / 60


    smev_delay = (
        now - smev_time
    ).total_seconds() / 60


    status = "OK"


    if smev_delay > 60:

        status = "SMEV_CRITICAL"

    elif smev_delay > 30:

        status = "SMEV_SLOW"

    elif act_delay > 20:

        status = "ACTIVATION_DELAY"


    return (
        status,
        act_delay,
        smev_delay
    )


# ============================================================
# PARSE HISTORY TIMESTAMP
# ============================================================

def parse_timestamp(value):

    if not value:
        return None


    formats = [
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S"
    ]


    for fmt in formats:

        try:

            return datetime.strptime(
                value,
                fmt
            )

        except Exception:
            pass


    return None


# ============================================================
# ANALYZE HISTORY
# ============================================================

def analyze_history(data):

    if not data:

        return {
            "error": "no data"
        }


    now = datetime.utcnow()


    last_hour = []


    for x in data:

        try:

            t = parse_timestamp(
                x.get("time")
            )

            if not t:
                continue


            age = (
                now - t
            ).total_seconds()


            if 0 <= age <= 3600:

                last_hour.append(x)


        except Exception:

            continue


    if not last_hour:

        return {
            "error": "no recent data"
        }


    smev = []
    act = []
    cert = []


    for x in last_hour:

        smev_value = x.get("smev")
        act_value = x.get("act")
        cert_value = x.get("cert")


        if isinstance(
            smev_value,
            (int, float)
        ):

            smev.append(
                smev_value
            )


        if isinstance(
            act_value,
            (int, float)
        ):

            act.append(
                act_value
            )


        if isinstance(
            cert_value,
            (int, float)
        ):

            cert.append(
                cert_value
            )


    # ========================================================
    # TODAY RECORD
    # ========================================================

    day_record = None

    today = now.date()


    for x in data:

        try:

            t = parse_timestamp(
                x.get("time")
            )

            if not t:
                continue


            users = x.get(
                "users",
                0
            )


            if t.date() == today:

                if (
                    day_record is None
                    or users > day_record["users"]
                ):

                    day_record = {
                        "users": users,
                        "time": t.isoformat() + "Z"
                    }


        except Exception:

            continue


    return {

        "points":
            len(last_hour),

        "avg_smev":
            (
                round(
                    sum(smev) / len(smev),
                    2
                )
                if smev
                else None
            ),

        "max_smev":
            (
                round(
                    max(smev),
                    2
                )
                if smev
                else None
            ),

        "avg_act":
            (
                round(
                    sum(act) / len(act),
                    2
                )
                if act
                else None
            ),

        "max_act":
            (
                round(
                    max(act),
                    2
                )
                if act
                else None
            ),

        "avg_cert":
            (
                round(
                    sum(cert) / len(cert),
                    2
                )
                if cert
                else None
            ),

        "max_cert":
            (
                round(
                    max(cert),
                    2
                )
                if cert
                else None
            ),

        "all_time_record":
            all_time_record,

        "day_record":
            day_record
    }


# ============================================================
# MONITOR
# ============================================================

def monitor():

    global data_cache

    global last_status

    global last_act_time
    global last_smev_time
    global last_cert_delay

    global all_time_record

    global last_smev_available


    while True:

        try:

            response = requests.get(
                URL,
                timeout=15
            )

            response.raise_for_status()


            (
                act_new,
                smev_new,
                users,
                users_avg,
                processing_minutes,
                cert_new,
                smev_unavailable
            ) = parse_times(
                response.text
            )


            # ==================================================
            # ACTIVATION FALLBACK
            # ==================================================

            if act_new:

                last_act_time = act_new


            act_time = last_act_time


            # ==================================================
            # SMEV
            #
            # ВАЖНО:
            #
            # Если сайт говорит:
            #
            # "В течение часа не было взаимодействия с СМЭВ"
            #
            # или:
            #
            # "Последний ответ СМЭВ: -"
            #
            # старое значение НЕ используется.
            # ==================================================

            if smev_unavailable:

                last_smev_available = False

                smev_time = None


            else:

                last_smev_available = True


                if smev_new:

                    last_smev_time = smev_new


                smev_time = last_smev_time


            # ==================================================
            # CERTIFICATE FALLBACK
            # ==================================================

            if cert_new is not None:

                last_cert_delay = cert_new


            cert_delay = last_cert_delay


            # ==================================================
            # NO ACTIVATION
            # ==================================================

            if not act_time:

                print(
                    "NO ACTIVATION DATA"
                )

                time.sleep(60)

                continue


            # ==================================================
            # SMEV UNAVAILABLE
            # ==================================================

            if smev_unavailable:

                status = "SMEV_NO_DATA"


                act_delay = (
                    datetime.now() -
                    act_time
                ).total_seconds() / 60


                smev_delay = None


            # ==================================================
            # SMEV AVAILABLE
            # ==================================================

            else:

                if not smev_time:

                    print(
                        "NO SMEV DATA YET"
                    )

                    time.sleep(60)

                    continue


                (
                    status,
                    act_delay,
                    smev_delay
                ) = analyze(
                    act_time,
                    smev_time
                )


            # ==================================================
            # POINT
            # ==================================================

            point = {

                "time":
                    datetime.utcnow().isoformat() + "Z",

                "act":
                    round(
                        act_delay,
                        2
                    ),

                "smev":
                    (
                        round(
                            smev_delay,
                            2
                        )
                        if smev_delay is not None
                        else None
                    ),

                "users":
                    users,

                "users_avg":
                    users_avg,

                "processing":
                    processing_minutes,

                "cert":
                    cert_delay,

                "status":
                    status
            }


            # ==================================================
            # ALL TIME USERS RECORD
            # ==================================================

            if users > all_time_record["users"]:

                all_time_record = {

                    "users":
                        users,

                    "time":
                        datetime.utcnow().isoformat() + "Z"
                }


                try:

                    with open(
                        RECORD_FILE,
                        "w"
                    ) as f:

                        json.dump(
                            all_time_record,
                            f,
                            indent=2
                        )


                except Exception as e:

                    print(
                        "RECORD SAVE ERROR:",
                        e
                    )


            # ==================================================
            # CACHE
            # ==================================================

            with data_lock:

                data_cache.append(
                    point
                )


                if len(data_cache) > MAX_POINTS:

                    data_cache.pop(0)


            # ==================================================
            # SAVE DATA
            # ==================================================

            save_data(
                point
            )


            print(
                "POINT:",
                point
            )


            # ==================================================
            # TELEGRAM
            # ==================================================

            if status != last_status:

                if status == "SMEV_NO_DATA":

                    send_alert(
                        "СМЭВ: НЕТ ДАННЫХ\n"
                        "На tah-o.ru нет взаимодействия "
                        "с СМЭВ в течение часа."
                    )


                elif status == "OK":

                    send_alert(
                        "СМЭВ: ВОССТАНОВЛЕН\n"
                        "СМЭВ снова отвечает."
                    )


                else:

                    smev_text = (

                        "{:.1f} мин".format(
                            smev_delay
                        )

                        if smev_delay is not None

                        else "нет данных"
                    )


                    send_alert(
                        "{}\nСМЭВ: {}".format(
                            status,
                            smev_text
                        )
                    )


                last_status = status


        except Exception as e:

            print(
                "MONITOR ERROR:",
                e
            )


        time.sleep(60)


# ============================================================
# ROUTES
# ============================================================

@app.route("/")
def index():

    return send_from_directory(
        "/opt/taho-monitor",
        "index.html"
    )


@app.route("/data")
def get_data():

    try:

        with open(DATA_FILE, "r") as f:
            data = json.load(f)

        if not isinstance(data, list):
            data = []

        now = datetime.utcnow()
        result = []

        for x in data:

            t = parse_timestamp(x.get("time"))

            if not t:
                continue

            age = (now - t).total_seconds()

            if 0 <= age <= 86400:
                result.append(x)

        return jsonify(result)

    except Exception as e:

        print("DATA ERROR:", e)

        return jsonify([])


@app.route("/stats")
def stats():

    try:

        if not os.path.exists(
            DATA_FILE
        ):

            return jsonify({
                "error": "no file"
            })


        with open(
            DATA_FILE,
            "r"
        ) as f:

            data = json.load(f)


    except Exception as e:

        print(
            "STATS ERROR:",
            e
        )

        return jsonify({
            "error": "bad file"
        })


    return jsonify(
        analyze_history(
            data
        )
    )


@app.route("/raw")
def raw():

    return jsonify(
        raw_status
    )


@app.route("/visits")
def get_visits():

    global visits

    visits += 1


    return jsonify({
        "visits": visits
    })


# ============================================================
# START
# ============================================================

load_data()


threading.Thread(
    target=monitor,
    daemon=True
).start()
