import os
import time
import requests
from datetime import datetime

BASE_URL = os.environ["CDS_URL"].rstrip("/")
API_KEY = os.environ["CDS_KEY"]
HEADERS = {"PRIVATE-TOKEN": API_KEY, "Content-Type": "application/json"}

# Nota: sono 11 task totali (2025 H2 surf, 2026 H1/H2 surf, 2022-2025 H1/H2 press)
tasks = [

    # --- 2022 PRESSURE LEVELS ---
    {"dataset": "reanalysis-era5-pressure-levels", "year": "2022", "months": ["03", "04", "05", "06"], "type": "press_H1"},
    {"dataset": "reanalysis-era5-pressure-levels", "year": "2022", "months": ["07", "08", "09", "10"], "type": "press_H2"},

    # --- 2023 PRESSURE LEVELS ---
    {"dataset": "reanalysis-era5-pressure-levels", "year": "2023", "months": ["03", "04", "05", "06"], "type": "press_H1"},
    {"dataset": "reanalysis-era5-pressure-levels", "year": "2023", "months": ["07", "08", "09", "10"], "type": "press_H2"},

    # --- 2024 PRESSURE LEVELS ---
    {"dataset": "reanalysis-era5-pressure-levels", "year": "2024", "months": ["03", "04", "05", "06"], "type": "press_H1"},
    {"dataset": "reanalysis-era5-pressure-levels", "year": "2024", "months": ["07", "08", "09", "10"], "type": "press_H2"},

    # --- 2025 PRESSURE LEVELS ---
    {"dataset": "reanalysis-era5-pressure-levels", "year": "2025", "months": ["03", "04", "05", "06"], "type": "press_H1"},
    {"dataset": "reanalysis-era5-pressure-levels", "year": "2025", "months": ["07", "08", "09", "10"], "type": "press_H2"},
]

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def build_payload(year, months, dataset):
    inputs = {
        "product_type": ["reanalysis"],
        "year": [year],
        "month": months,
        "day": [str(d).zfill(2) for d in range(1, 32)],
        "time": [
            "06:00", "07:00", "08:00", "09:00", "10:00", "11:00",
            "12:00", "13:00", "14:00", "15:00", "16:00", "17:00",
            "18:00", "19:00"
        ],
        "data_format": "netcdf",
        "download_format": "unarchived",
        "area": [43.5, 10.25, 43.25, 10.5]
    }
    if "single-levels" in dataset:
        inputs["variable"] = [
            "10m_u_component_of_wind", "10m_v_component_of_wind",
            "2m_temperature", "surface_pressure",
            "total_cloud_cover", "boundary_layer_height"
        ]
    else:
        inputs["variable"] = ["u_component_of_wind", "v_component_of_wind", "temperature"]
        inputs["pressure_level"] = ["850", "925"]
    return {"inputs": inputs}

def get_jobs_status():
    url = f"{BASE_URL}/retrieve/v1/jobs?limit=50"
    resp = requests.get(url, headers=HEADERS, timeout=20)
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
    url = f"{BASE_URL}/retrieve/v1/processes/{task['dataset']}/execute"
    payload = build_payload(task["year"], task["months"], task["dataset"])
    resp = requests.post(url, headers=HEADERS, json=payload, timeout=20)
    resp.raise_for_status()
    return resp.json().get("jobID")

log("=== AVVIO POLLER GITHUB ACTIONS PER CODA CDS ===")
try:
    initial_jobs = get_jobs_status()
    tasks = filter_already_submitted(tasks, initial_jobs)
except Exception as e:
    log(f"[WARN] Controllo duplicati iniziale saltato: {e}")

log(f"Task effettivi da accodare: {len(tasks)}")
start_time = time.time()
MAX_RUNTIME_SEC = 5.5 * 3600  # Stop di sicurezza prima del timeout di 6h di GitHub

while tasks:
    if time.time() - start_time > MAX_RUNTIME_SEC:
        log("[STOP SICUREZZA] Raggiunte 5.5 ore. Rilancia il workflow per completare i rimanenti.")
        break

    log("--------------------------------------------------")
    try:
        jobs = get_jobs_status()
        acc_count = sum(1 for j in jobs if j.get("status") == "accepted")
        run_count = sum(1 for j in jobs if j.get("status") == "running")
        log(f"Coda server -> ACCEPTED: {acc_count}/3 | RUNNING: {run_count}")

        while acc_count < 3 and tasks:
            next_task = tasks.pop(0)
            task_label = f"{next_task['year']}_{next_task['type']}"
            log(f"[ACCODAMENTO] Slot 'accepted' libero ({acc_count}/3)! Invio {task_label}...")
            try:
                job_id = submit_job(next_task)
                log(f"[OK] {task_label} accodato -> Job ID: {job_id}")
                acc_count += 1
                time.sleep(2)
            except Exception as e:
                log(f"[ERRORE INVIO] Fallito accodamento per {task_label}: {e}")
                tasks.insert(0, next_task)
                break
    except Exception as e:
        log(f"[ERRORE API] Impossibile leggere la coda da Copernicus: {e}")

    if tasks:
        log(f"Rimanenti da accodare: {len(tasks)}. Prossimo check tra 60s...")
        time.sleep(60)
    else:
        log("=== TUTTI I TASK SONO STATI ACCODATI CON SUCCESSO! ===")
        break
