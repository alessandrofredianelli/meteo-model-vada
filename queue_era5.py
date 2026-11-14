import os
import time
import json
import traceback
import requests
import cdsapi
from datetime import datetime

BASE_URL = os.environ["CDS_URL"].rstrip("/")
API_KEY = os.environ["CDS_KEY"]
HEADERS = {"PRIVATE-TOKEN": API_KEY, "Content-Type": "application/json"}

# Client ufficiale CDS in modalità asincrona
client = cdsapi.Client(
    url=BASE_URL,
    key=API_KEY,
    wait_until_complete=False,
    quiet=True
)

# Genera automaticamente i blocchi bimestrali per i Pressure Levels (2022 - 2026)
# 2 mesi x 3 variables x 2 levels = 50% del costo rispetto a 4 mesi -> passa sotto il limite CDS
years = ["2022", "2023", "2024", "2025", "2026"]
month_chunks = [
    (["03", "04"], "press_M03_04"),
    (["05", "06"], "press_M05_06"),
    (["07", "08"], "press_M07_08"),
    (["09", "10"], "press_M09_10"),
]

tasks = []
for y in years:
    for months, label in month_chunks:
        tasks.append({
            "dataset": "reanalysis-era5-pressure-levels",
            "year": y,
            "months": months,
            "type": label
        })

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

def log_detailed_error(context, exc, payload=None):
    log("============================================================")
    log(f"[ERRORE DETTAGLIATO] Contesto: {context}")
    log(f"Tipo Eccezione : {type(exc).__name__}")
    log(f"Messaggio      : {exc}")

    resp = getattr(exc, "response", None)
    if resp is not None:
        log(f"HTTP Status    : {getattr(resp, 'status_code', 'N/A')}")
        log(f"URL Chiamato   : {getattr(resp, 'url', 'N/A')}")
        try:
            body_json = resp.json()
            log("Body Risposta (JSON):")
            print(json.dumps(body_json, indent=2, ensure_ascii=False), flush=True)
        except Exception:
            log(f"Body Risposta (RAW): {getattr(resp, 'text', '')}")

    if payload is not None:
        log("Payload inviato:")
        print(json.dumps(payload, indent=2), flush=True)

    log("Traceback:")
    traceback.print_exc()
    log("============================================================")

def build_request_dict(year, months):
    return {
        "product_type": ["reanalysis"],
        "year": [year],
        "month": months,
        "day": [str(d).zfill(2) for d in range(1, 32)],
        "time": [
            "06:00", "07:00", "08:00", "09:00", "10:00", "11:00",
            "12:00", "13:00", "14:00", "15:00", "16:00", "17:00",
            "18:00", "19:00"
        ],
        "variable": [
            "u_component_of_wind",
            "v_component_of_wind",
            "temperature"
        ],
        "pressure_level": ["850", "925"],
        "data_format": "netcdf",
        "download_format": "unarchived",
        "area": [43.5, 10.25, 43.25, 10.5]
    }

def get_jobs_status():
    url = f"{BASE_URL}/retrieve/v1/jobs?limit=60"
    resp = requests.get(url, headers=HEADERS, timeout=25)
    resp.raise_for_status()
    return resp.json().get("jobs", [])

def filter_already_submitted(task_list, existing_jobs):
    """Salta i task già presenti sul server in stato accepted, running o successful"""
    active_sigs = set()
    for j in existing_jobs:
        if j.get("status") in ("accepted", "running", "successful"):
            proc = j.get("processID", "")
            inp = j.get("metadata", {}).get("request", {}).get("inputs", {})
            yr = tuple(inp.get("year", []))
            mo = tuple(sorted(inp.get("month", [])))
            active_sigs.add((proc, yr, mo))

    filtered = []
    for t in task_list:
        sig = (t["dataset"], (t["year"],), tuple(sorted(t["months"])))
        if sig in active_sigs:
            log(f"[SKIP] {t['year']}_{t['type']} già presente sul server CDS.")
        else:
            filtered.append(t)
    return filtered

def submit_job(task):
    req_dict = build_request_dict(task["year"], task["months"])
    try:
        remote = client.retrieve(task["dataset"], req_dict)
        return remote.request_id
    except Exception as e:
        log_detailed_error(f"Submit {task['year']}_{task['type']} -> {task['dataset']}", e, payload=req_dict)
        raise

# --- MAIN LOOP ---
log("=== AVVIO POLLER GITHUB ACTIONS (PRESSURE LEVELS BIMESTRALI) ===")
try:
    initial_jobs = get_jobs_status()
    tasks = filter_already_submitted(tasks, initial_jobs)
except Exception as e:
    log_detailed_error("Controllo iniziale job esistenti", e)

log(f"Task effettivi da accodare: {len(tasks)}")
start_time = time.time()
MAX_RUNTIME_SEC = 5.5 * 3600

while tasks:
    if time.time() - start_time > MAX_RUNTIME_SEC:
        log("[STOP SICUREZZA] Raggiunte 5.5 ore. Il cron rilancerà automaticamente il workflow.")
        break

    log("------------------------------------------------------------")
    try:
        jobs = get_jobs_status()
        acc_count = sum(1 for j in jobs if j.get("status") == "accepted")
        run_count = sum(1 for j in jobs if j.get("status") == "running")
        succ_count = sum(1 for j in jobs if j.get("status") == "successful")
        log(f"Coda server -> ACCEPTED: {acc_count}/3 | RUNNING: {run_count} | SUCCESSFUL: {succ_count}")

        while acc_count < 3 and tasks:
            next_task = tasks.pop(0)
            task_label = f"{next_task['year']}_{next_task['type']}"
            log(f"[ACCODAMENTO] Slot 'accepted' libero ({acc_count}/3)! Invio {task_label}...")
            try:
                job_id = submit_job(next_task)
                log(f"[OK] {task_label} accodato -> Job ID: {job_id}")
                acc_count += 1
                time.sleep(2)
            except Exception:
                tasks.insert(0, next_task)
                break

    except Exception as e:
        log_detailed_error("Lettura stato coda CDS", e)

    if tasks:
        log(f"Rimanenti da accodare: {len(tasks)}. Prossimo check tra 60s...")
        time.sleep(60)
    else:
        log("=== TUTTI I TASK PRESSURE LEVELS SONO STATI ACCODATI CON SUCCESSO! ===")
        break
