import os
import re
from datetime import datetime
from io import BytesIO
import pandas as pd
import requests
from PIL import Image

IMG_URL = os.environ.get("LAMMA_VADA_IMG_URL", "").strip()
OUT_CSV = os.environ.get("VADA_LIVE_CSV", "data/live/vada_lamma_15m.csv")


def build_default_url(now_solare: datetime) -> str:
    year = now_solare.strftime("%Y")
    day_str = now_solare.strftime("%Y-%m-%d")
    return f"https://www.lamma.toscana.it/dbmeteo/3950/{year}/3950_{day_str}_vdmk.png"


def parse_header_solar_time(header_text: str) -> datetime | None:
    m = re.search(r"(\d{2}-\d{2}-\d{4})\s+\d{2}:\d{2}\s*-\s*(\d{2}:\d{2})", header_text)
    if not m:
        return None
    date_str, end_hm = m.group(1), m.group(2)
    return datetime.strptime(f"{date_str} {end_hm}", "%d-%m-%Y %H:%M")


def extract_current_speed_kmh(img: Image.Image) -> float | None:
    try:
        import pytesseract
    except ImportError:
        return None

    w, h = img.size
    crop = img.crop((int(w * 0.72), 0, w, int(h * 0.14)))
    txt = pytesseract.image_to_string(crop, config="--psm 6")
    vals = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", txt)]
    return vals[0] if vals else None


def main():
    now_solare = datetime.utcnow() + pd.Timedelta(hours=1)
    url = IMG_URL if IMG_URL else build_default_url(now_solare)

    resp = requests.get(url, timeout=25)
    resp.raise_for_status()
    img = Image.open(BytesIO(resp.content))

    obs_dt = None
    speed_kmh = extract_current_speed_kmh(img)

    try:
        import pytesseract
        w, h = img.size
        hdr_crop = img.crop((int(w * 0.30), 0, int(w * 0.75), int(h * 0.09)))
        hdr_txt = pytesseract.image_to_string(hdr_crop, config="--psm 7").strip()
        obs_dt = parse_header_solar_time(hdr_txt)
    except Exception:
        obs_dt = None

    if obs_dt is None:
        obs_dt = now_solare.replace(minute=(now_solare.minute // 15) * 15, second=0, microsecond=0)

    fetched_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    ws_kn = round(speed_kmh / 1.852, 2) if speed_kmh is not None else None

    row = {
        "time_solare": obs_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "ws_kmh": speed_kmh,
        "ws_kn": ws_kn,
        "source_url": url,
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
    print("Aggiornato:", row)


if __name__ == "__main__":
    main()
