import os
import re
import time
import asyncio
import numpy as np
import pandas as pd
import requests
import openmeteo_requests
import requests_cache
from datetime import datetime
from bs4 import BeautifulSoup
from retry_requests import retry
from playwright.async_api import async_playwright
from google.colab import drive
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from IPython.display import clear_output

# 1. MONTAGGIO DRIVE E STRUTTURA PERCORSI
drive.mount('/content/drive', force_remount=False)
CARTELLA_DRIVE = "/content/drive/My Drive/MeteoData"
os.makedirs(CARTELLA_DRIVE, exist_ok=True)

CSV_REALE = os.path.join(CARTELLA_DRIVE, "dati_stazioni_miste.csv")
CSV_OPENMETEO = os.path.join(CARTELLA_DRIVE, "previsioni_openmeteo_10stazioni.csv")
CSV_CONFRONTO = os.path.join(CARTELLA_DRIVE, "distanza_realtime_modello.csv")

# 2. CONFIGURAZIONE DELLE 10 STAZIONI
URL_METEONETWORK = [
    "https://www.meteonetwork.eu/it/weather-station/tsc275-stazione-meteorologica-di-rosignano-solvay",
    "https://www.meteonetwork.eu/it/weather-station/tsc265-stazione-meteorologica-di-calignaia",
    "https://www.meteonetwork.eu/it/weather-station/tsc228-stazione-meteorologica-di-borgata-poggetto",
    "https://www.meteonetwork.eu/it/weather-station/tsc038-stazione-meteorologica-di-pisa-barbaricina",
    "https://www.meteonetwork.eu/it/weather-station/fr0370-stazione-meteorologica-di-luri",
    "https://www.meteonetwork.eu/it/weather-station/lig045-stazione-meteorologica-di-la-spezia-mazzetta",
    "https://www.meteonetwork.eu/it/weather-station/lig369-stazione-meteorologica-di-porto-antico-genova"
]

WINDGURU_STATIONS = [980, 5695, 5637]
HEADERS_MN = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

CACHE_COORDINATE = {
    "wg_980": (43.388, 10.435, "Rosignano / Vada"),
    "wg_5695": (43.465, 10.347, "Calignaia WG"),
    "wg_5637": (43.352, 10.455, "Vada Spot WG")
}

cache_session = requests_cache.CachedSession('.cache', expire_after=3600)
retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
openmeteo_client = openmeteo_requests.Client(session=retry_session)

# --- UTILS MATEMATICHE ---
def calcola_distanza_angolare(dir1, dir2):
    """Calcola la distanza minima angolare circolare tra 0° e 180°."""
    if pd.isna(dir1) or pd.isna(dir2): 
        return np.nan
    diff = np.abs(dir1 - dir2) % 360
    return np.minimum(diff, 360 - diff)

# --- METEONETWORK SCRAPING & COORDS ---
def recupera_coords_meteonetwork(url_base):
    slug = url_base.split("/weather-station/")[-1]
    code = slug.split("-")[0]
    if code in CACHE_COORDINATE: 
        return CACHE_COORDINATE[code]
        
    url_details = url_base.rstrip('/') + '/details'
    lat, lon = "N/D", "N/D"
    try:
        res = requests.get(url_details, headers=HEADERS_MN, timeout=10)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            testo = soup.get_text()
            lat_m = re.search(r'Latit(?:udine)?[:\s]+([+-]?\d+\.\d+)', testo, re.IGNORECASE)
            lon_m = re.search(r'Longit(?:udine)?[:\s]+([+-]?\d+\.\d+)', testo, re.IGNORECASE)
            if lat_m and lon_m: 
                lat, lon = float(lat_m.group(1)), float(lon_m.group(1))
    except Exception: 
        pass
    CACHE_COORDINATE[code] = (lat, lon)
    return lat, lon

def estrai_meteonetwork(url):
    slug = url.split("/weather-station/")[-1]
    code = slug.split("-")[0]
    nome_stazione = re.sub(r'stazione-meteorologica-di-', '', slug)
    lat, lon = recupera_coords_meteonetwork(url)
    try:
        res = requests.get(url, headers=HEADERS_MN, timeout=10)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, 'html.parser')
        page_text = soup.get_text()

        def trova_valore(etichetta, stop_char):
            if etichetta in page_text:
                val = page_text.split(etichetta)[1].split(stop_char)[0].strip()
                val_clean = re.sub(r'[^\d\.\,-]', '', val).replace(',', '.')
                return float(val_clean) if val_clean else np.nan
            return np.nan

        time_tag = soup.find("time") or soup.find(class_="last-update")

        return {
            "timestamp_scraping": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "fonte": "MeteoNetwork", "stazione_code": code, "stazione_nome": nome_stazione,
            "latitudine": lat, "longitudine": lon,
            "ora_stazione": time_tag.text.strip() if time_tag else "N/D",
            "temperatura_C": trova_valore("Temperatura", "°C"),
            "umidita_pct": trova_valore("Umidità", "%"),
            "pressione_hPa": trova_valore("Pressione", "hPa"),
            "pioggia_mm": trova_valore("Pioggia", "mm"),
            "vento_speed": trova_valore("Vento", "km/h"),
            "vento_gust": np.nan, "vento_dir_deg": np.nan
        }
    except Exception as e:
        print(f"  ❌ Errore MeteoNetwork ({code}): {e}", flush=True)
        return None

# --- WINDGURU SCRAPING VIA PLAYWRIGHT ---
async def estrai_windguru_playwright_batch(id_stations):
    risultati = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        for id_st in id_stations:
            url = f"https://www.windguru.cz/station/{id_st}"
            try:
                await page.goto(url, wait_until="networkidle", timeout=25000)
                await page.wait_for_timeout(2500)
                testo_pagina = await page.inner_text("body")
                nome_spot = await page.evaluate("() => document.querySelector('.station-title, h1, .spot-name')?.innerText || ''")
                nome_spot = nome_spot.strip().split('\n')[0] if nome_spot else f"Windguru_{id_st}"

                dir_match = re.search(r'([N|S|E|W|NE|NW|SE|SW]+)\s*(\d+)°', testo_pagina)
                speed_match = re.search(r'(\d+(?:\.\d+)?)\s*nodi', testo_pagina, re.IGNORECASE) or re.search(r'(\d+(?:\.\d+)?)\s*kn', testo_pagina, re.IGNORECASE)
                gust_match = re.search(r'max:\s*(\d+(?:\.\d+)?)', testo_pagina, re.IGNORECASE)
                temp_match = re.search(r'(\d+(?:\.\d+)?)\s*°C', testo_pagina)
                rh_match = re.search(r'rh:\s*(\d+)%', testo_pagina, re.IGNORECASE)

                rec = {
                    "timestamp_scraping": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "fonte": "Windguru", "stazione_code": f"wg_{id_st}", "stazione_nome": nome_spot,
                    "latitudine": CACHE_COORDINATE.get(f"wg_{id_st}", (43.388, 10.435))[0],
                    "longitudine": CACHE_COORDINATE.get(f"wg_{id_st}", (43.388, 10.435))[1],
                    "ora_stazione": "N/D",
                    "temperatura_C": float(temp_match.group(1)) if temp_match else np.nan,
                    "umidita_pct": float(rh_match.group(1)) if rh_match else np.nan,
                    "pressione_hPa": np.nan, "pioggia_mm": np.nan,
                    "vento_speed": float(speed_match.group(1)) if speed_match else np.nan,
                    "vento_gust": float(gust_match.group(1)) if gust_match else np.nan,
                    "vento_dir_deg": float(dir_match.group(2)) if dir_match else np.nan
                }
                risultati.append(rec)
            except Exception as e:
                print(f"  ❌ Errore Playwright Windguru ({id_st}): {e}", flush=True)
        await browser.close()
    return risultati

# --- DOWNLOAD MODELLO OPEN-METEO ---
def scarica_openmeteo():
    print("⚡ [OPEN-METEO] Download previsioni...", flush=True)
    timestamp_download = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stazioni_geo = []
    
    for url in URL_METEONETWORK:
        code = url.split("/weather-station/")[-1].split("-")[0]
        nome = re.sub(r'stazione-meteorologica-di-', '', url.split("/weather-station/")[-1])
        lat, lon = recupera_coords_meteonetwork(url)
        if lat != "N/D" and lon != "N/D":
            stazioni_geo.append({"code": code, "nome": nome, "fonte": "MeteoNetwork", "lat": lat, "lon": lon})
            
    for wg_id in WINDGURU_STATIONS:
        key = f"wg_{wg_id}"
        if key in CACHE_COORDINATE:
            lat, lon, name = CACHE_COORDINATE[key]
            stazioni_geo.append({"code": key, "nome": name, "fonte": "Windguru", "lat": lat, "lon": lon})

    if not stazioni_geo: 
        return

    lats = [st["lat"] for st in stazioni_geo]
    lons = [st["lon"] for st in stazioni_geo]

    url_openmeteo = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lats, "longitude": lons,
        "hourly": ["wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "temperature_2m", "relative_humidity_2m", "pressure_msl"],
        "forecast_days": 3, "wind_speed_unit": "kn", "timezone": "Europe/Berlin"
    }

    try:
        responses = openmeteo_client.weather_api(url_openmeteo, params=params)
        all_dfs = []
        for i, response in enumerate(responses):
            st_info = stazioni_geo[i]
            hourly = response.Hourly()
            date_range = pd.date_range(
                start=pd.to_datetime(hourly.Time(), unit="s", utc=True),
                end=pd.to_datetime(hourly.TimeEnd(), unit="s", utc=True),
                freq=pd.Timedelta(seconds=hourly.Interval()), inclusive="left"
            ).strftime('%Y-%m-%dT%H:%M:%SZ')

            df_st = pd.DataFrame({
                "timestamp_download": timestamp_download, "time": date_range,
                "stazione_code": st_info["code"], "stazione_nome": st_info["nome"],
                "fonte_stazione": st_info["fonte"], "latitudine": st_info["lat"], "longitudine": st_info["lon"],
                "wind_speed_10m": hourly.Variables(0).ValuesAsNumpy(), "unit_wind_speed": "kn",
                "wind_direction_10m": hourly.Variables(1).ValuesAsNumpy(), "unit_wind_direction": "°",
                "wind_gusts_10m": hourly.Variables(2).ValuesAsNumpy(), "unit_wind_gusts": "kn",
                "temperature_2m": hourly.Variables(3).ValuesAsNumpy(), "unit_temperature": "°C",
                "relative_humidity_2m": hourly.Variables(4).ValuesAsNumpy(), "unit_relative_humidity": "%",
                "pressure_msl": hourly.Variables(5).ValuesAsNumpy(), "unit_pressure": "hPa"
            })
            all_dfs.append(df_st)
            
        df_totale = pd.concat(all_dfs, ignore_index=True)
        
        try:
            df_old = pd.read_csv(CSV_OPENMETEO)
            df_combined = pd.concat([df_old, df_totale], ignore_index=True)
            df_combined = df_combined.drop_duplicates(subset=['stazione_code', 'time'], keep='last')
            df_combined.to_csv(CSV_OPENMETEO, index=False)
        except FileNotFoundError:
            df_totale.to_csv(CSV_OPENMETEO, index=False)
            
        print(f"  ✔ [OPEN-METEO] Aggiornate e pulite {len(df_totale)} righe", flush=True)
    except Exception as e:
        print(f"  ❌ Errore Open-Meteo: {e}", flush=True)

# --- ALGORITMO DI CONFRONTO MULTI-VARIABILE ---
def esegui_confronto_e_salva(dati_attuali):
    if not dati_attuali or not os.path.exists(CSV_OPENMETEO): 
        return

    df_fcst = pd.read_csv(CSV_OPENMETEO)
    df_fcst['dt_fcst'] = pd.to_datetime(df_fcst['time']).dt.tz_localize(None)

    confronti = []
    for row in dati_attuali:
        code = row['stazione_code']
        t_reale = pd.to_datetime(row['timestamp_scraping'])

        v_raw = pd.to_numeric(row['vento_speed'], errors='coerce')
        if row['fonte'] == 'MeteoNetwork' and pd.notna(v_raw):
            v_reale_kn = v_raw * 0.539957
        else:
            v_reale_kn = v_raw

        gust_reale_kn = pd.to_numeric(row['vento_gust'], errors='coerce')
        dir_reale_deg = pd.to_numeric(row['vento_dir_deg'], errors='coerce')
        temp_reale_c = pd.to_numeric(row['temperatura_C'], errors='coerce')
        press_reale_hpa = pd.to_numeric(row['pressione_hPa'], errors='coerce')

        fcst_st = df_fcst[df_fcst['stazione_code'] == code].sort_values('dt_fcst')
        if fcst_st.empty: 
            continue

        fcst_prev = fcst_st[fcst_st['dt_fcst'] <= t_reale].tail(1)
        fcst_next = fcst_st[fcst_st['dt_fcst'] >= t_reale].head(1)

        if not fcst_prev.empty and not fcst_next.empty and fcst_prev['dt_fcst'].values[0] != fcst_next['dt_fcst'].values[0]:
            t0 = pd.to_datetime(fcst_prev['dt_fcst'].values[0])
            t1 = pd.to_datetime(fcst_next['dt_fcst'].values[0])
            weight = (t_reale - t0) / (t1 - t0)

            v_modello = fcst_prev['wind_speed_10m'].values[0] + weight * (fcst_next['wind_speed_10m'].values[0] - fcst_prev['wind_speed_10m'].values[0])
            gust_modello = fcst_prev['wind_gusts_10m'].values[0] + weight * (fcst_next['wind_gusts_10m'].values[0] - fcst_prev['wind_gusts_10m'].values[0])
            dir_modello = fcst_prev['wind_direction_10m'].values[0] + weight * (fcst_next['wind_direction_10m'].values[0] - fcst_prev['wind_direction_10m'].values[0])
            temp_modello = fcst_prev['temperature_2m'].values[0] + weight * (fcst_next['temperature_2m'].values[0] - fcst_prev['temperature_2m'].values[0])
            press_modello = fcst_prev['pressure_msl'].values[0] + weight * (fcst_next['pressure_msl'].values[0] - fcst_prev['pressure_msl'].values[0])
        else:
            row_c = fcst_st.iloc[(fcst_st['dt_fcst'] - t_reale).abs().argmin()]
            v_modello = row_c['wind_speed_10m']
            gust_modello = row_c['wind_gusts_10m']
            dir_modello = row_c['wind_direction_10m']
            temp_modello = row_c['temperature_2m']
            press_modello = row_c['pressure_msl']

        delta_v = v_reale_kn - v_modello if pd.notna(v_reale_kn) else np.nan
        delta_g = gust_reale_kn - gust_modello if pd.notna(gust_reale_kn) else np.nan
        delta_d = calcola_distanza_angolare(dir_reale_deg, dir_modello)
        delta_temp = temp_reale_c - temp_modello if pd.notna(temp_reale_c) else np.nan
        delta_press = press_reale_hpa - press_modello if pd.notna(press_reale_hpa) else np.nan

        confronti.append({
            "timestamp": row['timestamp_scraping'],
            "stazione_code": code,
            "stazione_nome": row['stazione_nome'],
            "fonte": row['fonte'],
            "v_reale_kn": v_reale_kn, "v_modello_kn": v_modello, "delta_vento_kn": delta_v,
            "gust_reale_kn": gust_reale_kn, "gust_modello_kn": gust_modello, "delta_gust_kn": delta_g,
            "dir_reale_deg": dir_reale_deg, "dir_modello_deg": dir_modello, "delta_dir_deg": delta_d,
            "temp_reale_C": temp_reale_c, "temp_modello_C": temp_modello, "delta_temp_C": delta_temp,
            "press_reale_hPa": press_reale_hpa, "press_modello_hPa": press_modello, "delta_press_hPa": delta_press
        })

    df_res = pd.DataFrame(confronti)
    try:
        df_old = pd.read_csv(CSV_CONFRONTO)
        df_tot = pd.concat([df_old, df_res], ignore_index=True)
        df_tot.drop_duplicates(subset=['stazione_code', 'timestamp'], keep='last').to_csv(CSV_CONFRONTO, index=False)
    except FileNotFoundError:
        df_res.to_csv(CSV_CONFRONTO, index=False)

# --- GENERAZIONE DASHBOARD GRAFICO DOPPIO (TRACKING + DELTA DISTANZE) ---
def genera_dashboard_completa(stazione_code="wg_980", nome_spot="Rosignano / Vada"):
    if not os.path.exists(CSV_REALE) or not os.path.exists(CSV_OPENMETEO) or not os.path.exists(CSV_CONFRONTO): 
        return

    df_fcst = pd.read_csv(CSV_OPENMETEO)
    df_reale = pd.read_csv(CSV_REALE)
    df_delta = pd.read_csv(CSV_CONFRONTO)

    fcst_st = df_fcst[df_fcst['stazione_code'] == stazione_code].copy()
    reale_st = df_reale[df_reale['stazione_code'] == stazione_code].copy()
    delta_st = df_delta[df_delta['stazione_code'] == stazione_code].copy()

    if fcst_st.empty or reale_st.empty: 
        return

    # Parsing date & Deduplicazione
    fcst_st['dt'] = pd.to_datetime(fcst_st['time']).dt.tz_localize(None)
    fcst_st = fcst_st.sort_values('dt').drop_duplicates(subset=['dt'], keep='last')

    reale_st['dt'] = pd.to_datetime(reale_st['timestamp_scraping'])
    reale_st = reale_st.sort_values('dt').drop_duplicates(subset=['dt'], keep='last')

    delta_st['dt'] = pd.to_datetime(delta_st['timestamp'])
    delta_st = delta_st.sort_values('dt').drop_duplicates(subset=['dt'], keep='last')

    reale_st['vento_speed_kn'] = pd.to_numeric(reale_st['vento_speed'], errors='coerce')
    if (reale_st['fonte'] == 'MeteoNetwork').any():
        reale_st.loc[reale_st['fonte'] == 'MeteoNetwork', 'vento_speed_kn'] *= 0.539957
    reale_st['vento_gust_kn'] = pd.to_numeric(reale_st['vento_gust'], errors='coerce')

    # CREAZIONE SUBPLOT COMPLESSIVO (3 PANNELLI)
    fig = make_subplots(
        rows=3, cols=1, 
        shared_xaxes=True, 
        vertical_spacing=0.06,
        subplot_titles=(
            f"💨 TRACKING LIVE: Vento Medio & Raffiche (kn) - {nome_spot}", 
            "📉 DISTANZE DAL MODELLO: Δ Vento e Δ Raffiche (Nodi: Reale - Modello)", 
            "🧭 Δ Direzione (0°-180°) e Δ Temp (°C)"
        ), 
        row_heights=[0.45, 0.30, 0.25]
    )
    
    # --- PANNELLO 1: TRACKING LIVE ---
    fig.add_trace(go.Scatter(x=fcst_st['dt'], y=fcst_st['wind_gusts_10m'], mode='lines', name='Raffica Prevista', line=dict(color='#aec7e8', width=1.5, dash='dash')), row=1, col=1)
    fig.add_trace(go.Scatter(x=fcst_st['dt'], y=fcst_st['wind_speed_10m'], mode='lines', name='Modello Open-Meteo', line=dict(color='#1f77b4', width=3)), row=1, col=1)
    fig.add_trace(go.Scatter(x=reale_st['dt'], y=reale_st['vento_speed_kn'], mode='markers+lines', name='REALE (3 min)', marker=dict(color='#d62728', size=6), line=dict(color='#d62728', width=1.5)), row=1, col=1)
    if reale_st['vento_gust_kn'].notna().any():
        fig.add_trace(go.Scatter(x=reale_st['dt'], y=reale_st['vento_gust_kn'], mode='markers', name='Raffica Reale', marker=dict(color='#ff7f0e', size=7, symbol='triangle-up')), row=1, col=1)

    # --- PANNELLO 2: DELTA VENTI & RAFFICHE ---
    fig.add_trace(go.Scatter(x=delta_st['dt'], y=delta_st['delta_vento_kn'], mode='lines+markers', name='Δ Vento (kn)', line=dict(color='#2ca02c', width=2), marker=dict(size=4)), row=2, col=1)
    if delta_st['delta_gust_kn'].notna().any():
        fig.add_trace(go.Scatter(x=delta_st['dt'], y=delta_st['delta_gust_kn'], mode='lines+markers', name='Δ Raffica (kn)', line=dict(color='#ff7f0e', width=1.8), marker=dict(size=4)), row=2, col=1)
    fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.8, row=2, col=1)

    # --- PANNELLO 3: DELTA DIREZIONE & TEMPERATURA ---
    fig.add_trace(go.Scatter(x=delta_st['dt'], y=delta_st['delta_dir_deg'], mode='lines+markers', name='Δ Direzione (°)', line=dict(color='#9467bd', width=1.8), marker=dict(size=4)), row=3, col=1)
    if delta_st['delta_temp_C'].notna().any():
        fig.add_trace(go.Scatter(x=delta_st['dt'], y=delta_st['delta_temp_C'], mode='lines+markers', name='Δ Temp (°C)', line=dict(color='#8c564b', width=1.8)), row=3, col=1)
    fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.8, row=3, col=1)

    # Styling Layout
    fig.update_layout(
        height=750, 
        title_text=f"📊 LIVE TRACKING & DASHBOARD DISTANZE MULTI-VARIABILE | {stazione_code}", 
        template="plotly_white",
        hovermode="x unified"
    )
    fig.update_yaxes(title_text="Nodi (kn)", row=1, col=1)
    fig.update_yaxes(title_text="Δ Nodi", row=2, col=1)
    fig.update_yaxes(title_text="Δ Unit", row=3, col=1)

    clear_output(wait=True)
    fig.show()

# --- MAIN ASYNCHRONOUS LOOP ---
async def avvia_monitoraggio_completo():
    INTERVALLO = 180  # 3 minuti
    ciclo = 0
    print("🚀 Avvio monitoraggio integrato e calcolo distanze...", flush=True)
    
    while True:
        print(f"\n--- [Ciclo {ciclo + 1} - {datetime.now().strftime('%H:%M:%S')}] Estrazione in corso... ---", flush=True)
        dati_ciclo = []
        
        # 1. MeteoNetwork
        for url in URL_METEONETWORK:
            rec = estrai_meteonetwork(url)
            if rec: 
                dati_ciclo.append(rec)
            await asyncio.sleep(0.1)
            
        # 2. Windguru
        dati_wg = await estrai_windguru_playwright_batch(WINDGURU_STATIONS)
        dati_ciclo.extend(dati_wg)
        
        # 3. Salvataggio Reali
        if dati_ciclo:
            df_n = pd.DataFrame(dati_ciclo)
            try:
                df_ex = pd.read_csv(CSV_REALE)
                df_comb = pd.concat([df_ex, df_n], ignore_index=True)
                df_comb.drop_duplicates(subset=['stazione_code', 'timestamp_scraping'], keep='last').to_csv(CSV_REALE, index=False)
            except FileNotFoundError:
                df_n.to_csv(CSV_REALE, index=False)

        # 4. Open-Meteo
        if ciclo % 10 == 0 or not os.path.exists(CSV_OPENMETEO):
            scarica_openmeteo()

        # 5. Calcola e salva i Delta multi-variabile su CSV
        esegui_confronto_e_salva(dati_ciclo)

        # 6. Generazione Dashboard Grafica Live per Vada (wg_980)
        genera_dashboard_completa("wg_980", "Rosignano / Vada")
        
        ciclo += 1
        await asyncio.sleep(INTERVALLO)

# AVVIO LIVE IN COLAB
await avvia_monitoraggio_completo()
