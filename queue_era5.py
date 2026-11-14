import os
import time
import requests
import cdsapi
from datetime import datetime

BASE_URL = os.environ["CDS_URL"].rstrip("/")
API_KEY = os.environ["CDS_KEY"]
HEADERS = {"PRIVATE-TOKEN": API_KEY, "Content-Type": "application/json"}

client = cdsapi.Client(
    url=BASE_URL,
    key=API_KEY,
    wait_until_complete=False,
    quiet=True
)

# 1 mese alla volta per i Pressure Levels (2022 - 2026, marzo-ottobre = 40 task)
years = ["2022", "2023", "2024", "2025", "2026"]
months = ["03", "04", "05", "06", "07", "08", "09", "10"]

tasks = [
    {
        "dataset": "reanalysis-era5-pressure-levels",
        "year": y,
        "months": [m],
        "type": f"press_M{m}"
    }
    for y in years
    for m in months
]

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def build_request_dict(year, month_list):
    return {
        "product_type": ["reanalysis"],
        "year": [year],
        "month": month_list,
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
    url = f"{BASE_URL}/retrieve/v1/jobs?limit=100"
    resp = requests.get(url, headers=HEADERS, timeout=25)
    resp.raise_for_status()
    return resp.json().get("jobs", [])

def filter_already_submitted(task_list, existing_jobs):
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
            log(f"[SKIP] {t['year']}_{t['type']} già presente.")
        else:
            filtered.append(t)
    return filtered

def submit_job(task):
    req_dict = build_request_dict(task["year"], task["months"])
    try:
        remote = client.retrieve(task["dataset"], req_dict)
        return remote.request_id
    except Exception as e:
        resp = getattr(e, "response", None)
        if resp is not None:
            try:
                err_json = resp.json()
                detail = f"{err_json.get('title', '')}: {err_json.get('detail', '')}".strip(": ")
            except Exception:
                detail = resp.text[:200]
            log(f"[ERRORE {resp.status_code}] {task['year']}_{task['type']} -> {detail}")
        else:
            log(f"[ERRORE] {task['year']}_{task['type']} -> {e}")
        raise

# --- MAIN LOOP ---
log("=== AVVIO POLLER CDS (PRESSURE LEVELS MENSILI) ===")
try:
    initial_jobs = get_jobs_status()
    tasks = filter_already_submitted(tasks, initial_jobs)
except Exception as e:
    log(f"[WARN] Controllo iniziale saltato: {e}")

log(f"Task effettivi da accodare: {len(tasks)}")
start_time = time.time()
MAX_RUNTIME_SEC = 5.5 * 3600

while tasks:
    if time.time() - start_time > MAX_RUNTIME_SEC:
        log("[STOP SICUREZZA] Raggiunte 5.5 ore. Il cron rilancerà il workflow.")
        break

    try:
        jobs = get_jobs_status()
        acc_count = sum(1 for j in jobs if j.get("status") == "accepted")
        run_count = sum(1 for j in jobs if j.get("status") == "running")
        succ_count = sum(1 for j in jobs if j.get("status") == "successful")
        log(f"Coda -> ACCEPTED: {acc_count}/3 | RUNNING: {run_count} | SUCCESSFUL: {succ_count} | Rimasti: {len(tasks)}")

        while acc_count < 3 and tasks:
            next_task = tasks.pop(0)
            task_label = f"{next_task['year']}_{next_task['type']}"
            log(f"[INVIO] {task_label} (slot {acc_count}/3)...")
            try:
                job_id = submit_job(next_task)
                log(f"[OK] {task_label} -> {job_id}")
                acc_count += 1
                time.sleep(2)
            except Exception:
                tasks.insert(0, next_task)
                break

    except Exception as e:
        log(f"[ERRORE API] {e}")

    if tasks:
        time.sleep(60)
    else:
        log("=== TUTTI I 40 TASK PRESSURE LEVELS ACCODATI! ===")
        break
