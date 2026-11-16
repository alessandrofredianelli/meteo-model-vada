import os
import json
import time
import requests
from datetime import datetime

BASE_URL = os.environ["CDS_URL"].rstrip("/")
API_KEY = os.environ["CDS_KEY"]
HEADERS = {"PRIVATE-TOKEN": API_KEY, "Content-Type": "application/json"}
STATE_FILE = "cds_state.json"

YEARS = ["2022", "2023", "2024", "2025", "2026"]
MONTHS = ["03", "04", "05", "06", "07", "08", "09", "10"]

# --- DURATA MASSIMA SINGOLA RUN ---
MAX_RUNTIME_SEC = 5 * 60        # TEST: 5 minuti (300 secondi)
# MAX_RUNTIME_SEC = 5.3 * 3600  # PRODUZIONE: 5 ore e 18 minuti (decommenta dopo il test)

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def load_or_init_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)

    state = []
    for y in YEARS:
        for m in MONTHS:
            state.append({
                "id": f"{y}_press_M{m}",
                "dataset": "reanalysis-era5-pressure-levels",
                "year": y,
                "month": m,
                "status": "pending",
                "job_id": None
            })
    save_state(state)
    return state

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def build_payload(year, month):
    return {
        "inputs": {
            "product_type": ["reanalysis"],
            "year": [year],
            "month": [month],
            "day": [str(d).zfill(2) for d in range(1, 32)],
            "time": [
                "06:00", "07:00", "08:00", "09:00", "10:00", "11:00",
                "12:00", "13:00", "14:00", "15:00", "16:00", "17:00",
                "18:00", "19:00"
            ],
            "variable": ["u_component_of_wind", "v_component_of_wind", "temperature"],
            "pressure_level": ["850", "925"],
            "data_format": "netcdf",
            "download_format": "unarchived",
            "area": [43.5, 10.25, 43.25, 10.5]
        }
    }

def get_global_accepted_count():
    url = f"{BASE_URL}/retrieve/v1/jobs?limit=30"
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    jobs = resp.json().get("jobs", [])
    acc = sum(1 for j in jobs if j.get("status") == "accepted")
    run = sum(1 for j in jobs if j.get("status") == "running")
    return acc, run

def check_single_job(job_id):
    url = f"{BASE_URL}/retrieve/v1/jobs/{job_id}"
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.json().get("status")

def submit_task(task):
    url = f"{BASE_URL}/retrieve/v1/processes/{task['dataset']}/execute"
    payload = build_payload(task["year"], task["month"])
    resp = requests.post(url, headers=HEADERS, json=payload, timeout=25)
    if not resp.ok:
        log(f"[ERRORE {resp.status_code}] {task['id']}: {resp.text[:250]}")
        resp.raise_for_status()
    return resp.json().get("jobID")

log(f"=== AVVIO POLLER STATEFUL (Max runtime: {MAX_RUNTIME_SEC}s) ===")
state = load_or_init_state()
start_time = time.time()

try:
    while True:
        # 1. Aggiorna stato dei task già inviati ('submitted')
        changed = False
        for t in state:
            if t["status"] == "submitted" and t["job_id"]:
                try:
                    remote_status = check_single_job(t["job_id"])
                    if remote_status in ("successful", "failed", "deleted"):
                        t["status"] = remote_status
                        changed = True
                        log(f"[AGGIORNAMENTO] {t['id']} ({t['job_id']}) -> {remote_status.upper()}")
                except Exception as e:
                    log(f"[WARN] Check singolo job {t['id']}: {e}")

        if changed:
            save_state(state)

        pending_tasks = [t for t in state if t["status"] == "pending"]
        done_count = sum(1 for t in state if t["status"] == "successful")
        sub_count = sum(1 for t in state if t["status"] == "submitted")

        if not pending_tasks:
            log(f"=== TUTTI I {len(state)} TASK SONO STATI ACCODATI! (Completati: {done_count}, In corso: {sub_count}) ===")
            break

        if time.time() - start_time > MAX_RUNTIME_SEC:
            log(f"[STOP SICUREZZA] Raggiunto limite tempo ({MAX_RUNTIME_SEC}s). Rimasti pending: {len(pending_tasks)}. Salvo stato ed esco.")
            break

        # 2. Controlla gli slot 'accepted' liberi su CDS e accoda i prossimi pending
        try:
            acc_count, run_count = get_global_accepted_count()
            log(f"Stato -> Pending: {len(pending_tasks)} | In corso: {sub_count} | Finiti: {done_count} || CDS ACCEPTED: {acc_count}/3, RUNNING: {run_count}")

            while acc_count < 3 and pending_tasks:
                next_t = pending_tasks.pop(0)
                log(f"[INVIO] {next_t['id']} (slot accepted {acc_count}/3)...")
                try:
                    jid = submit_task(next_t)
                    next_t["job_id"] = jid
                    next_t["status"] = "submitted"
                    save_state(state)
                    acc_count += 1
                    log(f"[OK] {next_t['id']} accodato -> {jid}")
                    time.sleep(2)
                except Exception:
                    break
        except Exception as e:
            log(f"[ERRORE API] Lettura coda CDS fallita: {e}")

        time.sleep(60)

finally:
    save_state(state)
    log("File cds_state.json salvato su disco.")
