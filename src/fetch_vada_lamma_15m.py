import os
import re
from datetime import datetime
from io import BytesIO
import pandas as pd
import requests
from PIL import Image

OUT_CSV = os.environ.get("VADA_LIVE_CSV", "data/live/vada_lamma_15m.csv")
STATION_ID = "3950"

# Codici grafici giornalieri LaMMA (stazione 3950 - Vada)
CHART_CODES = {
    "ws_kmh": os.environ.get("LAMMA_VADA_WIND_URL", "").strip(),
    "temp_c": os.environ.get("LAMMA_VADA_TEMP_URL", "").strip(),
    "rh_pct": os.environ.get("LAMMA_VADA_RH_URL", "").strip(),
    "press_hpa": os.environ.get("LAMMA_VADA_PRESS_URL", "").strip(),
    "rad_wm2": os.environ.get("LAMMA_VADA_RAD_URL", "").strip(),
}

DEFAULT_SUFFIX = {
    "ws_kmh": "vdmk",
    "temp_c": "tdmk",
    "rh_pct": "udmk",
    "press_hpa": "pdmk",
    "rad_wm2": "rdmk",
}


def build_url(var_key: str, now_solare: datetime) -> str:
    override = CHART_CODES.get(var_key, "")
    if override:
        return override
    year = now_solare.strftime("%Y")
    day_str = now_solare.strftime("%Y-%m-%d")
    suffix = DEFAULT_SUFFIX[var_key]
    return f"https://www.lamma.toscana.it/dbmeteo/{STATION_ID}/{year}/{STATION_ID}_{day_str}_{suffix}.png"


def parse_header_solar_time(header_text: str) -> datetime | None:
    m = re.search(r"(\d{2}-\d{2}-\d{4})\s+\d{2}:\d{2}\s*-\s*(\d{2}:\d{2})", header_text)
    if not m:
        return None
    date_str, end_hm = m.group(1), m.group(2)
    return datetime.strptime(f"{date_str} {end_hm}", "%d-%m-%Y %H:%M")


def ocr_top_right_value(img: Image.Image) -> float | None:
    try:
        import pytesseract
    except ImportError:
        return None

    w, h = img.size
    crop = img.crop((int(w * 0.72), 0, w, int(h * 0.14)))
    txt = pytesseract.image_to_string(crop, config="--psm 6")
    vals = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", txt)]
    return vals[0] if vals else None


def ocr_header_time(img: Image.Image) -> datetime | None:
    try:
        import pytesseract
        w, h = img.size
        hdr_crop = img.crop((int(w * 0.30), 0, int(w * 0.75), int(h * 0.09)))
        hdr_txt = pytesseract.image_to_string(hdr_crop, config="--psm 7").strip()
        return parse_header_solar_time(hdr_txt)
    except Exception:
        return None


def fetch_single_chart(url: str) -> tuple[float | None, datetime | None, bool]:
    try:
        resp = requests.get(url, timeout=20)
        if resp.status_code != 200:
            return None, None, False
        img = Image.open(BytesIO(resp.content))
        val = ocr_top_right_value(img)
        dt = ocr_header_time(img)
        return val, dt, True
    except Exception:
        return None, None, False


def main():
    now_solare = datetime.utcnow() + pd.Timedelta(hours=1)
    obs_dt = None
    values = {}
    urls = {}

    for var_key in ["ws_kmh", "press_hpa", "temp_c", "rh_pct", "rad_wm2"]:
        url = build_url(var_key, now_solare)
        urls[var_key] = url
        val, dt, ok = fetch_single_chart(url)
        values[var_key] = val if ok else None
        if obs_dt is None and dt is not None:
            obs_dt = dt

    if obs_dt is None:
        obs_dt = now_solare.replace(minute=(now_solare.minute // 15) * 15, second=0, microsecond=0)

    fetched_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    ws_kmh = values["ws_kmh"]
    ws_kn = round(ws_kmh / 1.852, 2) if ws_kmh is not None else None

    row = {
        "time_solare": obs_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "ws_kmh": ws_kmh,
        "ws_kn": ws_kn,
        "press_hpa": values["press_hpa"],
        "temp_c": values["temp_c"],
        "rh_pct": values["rh_pct"],
        "rad_wm2": values["rad_wm2"],
        "source_url": urls["ws_kmh"],
        "fetched_at": fetched_at,
    }

    os.makedirs(os.path.dirname(OUT_CSV) or ".", exist_ok=True)
    new_df = pd.DataFrame([row])

    if os.path.exists(OUT_CSV):
        old_df = pd.read_csv(OUT_CSV)
        combined = pd.concat([old_df, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["time_solare"], keep="last").sort_values("time_solare")
    else:
        combined = new_df

    combined.to_csv(OUT_CSV, index=False)
    print("Aggiornato Vada 15m:", row)


if __name__ == "__main__":
    main()
