import os
from datetime import datetime, timezone, timedelta
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import streamlit as st

st.set_page_config(page_title="Vada Meteo Live (LaMMA)", layout="wide")

CSV_PATH = os.environ.get("VADA_LIVE_CSV", "data/live/vada_lamma_15m.csv")
VADA_LAT = 43.35
VADA_LON = 10.45

MODELS = {
    "arome": {
        "id": "meteofrance_arome_france_hd",
        "label": "AROME HD",
        "dash": "dash",
    },
    "icon2i": {
        "id": "italia_meteo_arpae_icon_2i",
        "label": "ICON-2I",
        "dash": "dot",
    },
}


@st.cache_data(ttl=600)
def fetch_openmeteo_data():
    runs = {}
    for key, info in MODELS.items():
        try:
            r_meta = requests.get(
                f"https://api.open-meteo.com/data/{info['id']}/static/meta.json",
                timeout=8,
            )
            if r_meta.ok:
                ts = r_meta.json().get("last_run_initialisation_time")
                if ts:
                    dt_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
                    dt_sol = dt_utc.astimezone(timezone(timedelta(hours=1)))
                    runs[key] = f"{dt_utc.strftime('%Y-%m-%d %H:%M UTC')} ({dt_sol.strftime('%H:%M')} solare)"
        except Exception:
            pass

    r = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": VADA_LAT,
            "longitude": VADA_LON,
            "hourly": "wind_speed_10m,pressure_msl,temperature_2m,relative_humidity_2m,shortwave_radiation",
            "models": ",".join(m["id"] for m in MODELS.values()),
            "timezone": "Etc/GMT-1",
            "past_days": 1,
            "forecast_days": 2,
        },
        timeout=10,
    )
    r.raise_for_status()
    hourly = r.json().get("hourly", {})
    df_om = pd.DataFrame(hourly)
    if not df_om.empty and "time" in df_om.columns:
        df_om["time_dt"] = pd.to_datetime(df_om["time"])
    return df_om, runs


st.title("Vada — Acquisizione Stazione Live (LaMMA)")
st.caption("Vento, Pressione, Temperatura, Umidità e Radiazione Solare • Passo 15 minuti (ora solare) + Modelli Open-Meteo")

col_u, col_m1, col_m2 = st.columns([2, 1, 1])
with col_u:
    unit = st.radio("Unità vento", ["km/h", "kn"], horizontal=True)
with col_m1:
    show_arome = st.checkbox("Mostra AROME HD", value=True)
with col_m2:
    show_icon2i = st.checkbox("Mostra ICON-2I", value=True)

if not os.path.exists(CSV_PATH):
    st.warning(f"File dati non ancora presente in `{CSV_PATH}`. Esegui prima la GitHub Action.")
    st.stop()

df = pd.read_csv(CSV_PATH)
if df.empty:
    st.info("Il file CSV esiste ma non contiene ancora rilevazioni.")
    st.stop()

for col in ["ws_kmh", "ws_kn", "press_hpa", "temp_c", "rh_pct", "rad_wm2"]:
    if col not in df.columns:
        df[col] = pd.NA

df["time_solare_dt"] = pd.to_datetime(df["time_solare"])
df = df.sort_values("time_solare_dt").reset_index(drop=True)

df_om = pd.DataFrame()
om_runs = {}
if show_arome or show_icon2i:
    try:
        df_om, om_runs = fetch_openmeteo_data()
    except Exception as exc:
        st.warning(f"Impossibile caricare i dati Open-Meteo: {exc}")

if om_runs:
    run_parts = [
        f"**{MODELS[k]['label']}**: Run `{om_runs[k]}`"
        for k in ("arome", "icon2i")
        if k in om_runs
    ]
    if run_parts:
        st.info(" • ".join(run_parts))

val_col = "ws_kmh" if unit == "km/h" else "ws_kn"
last = df.iloc[-1]

def fmt_metric(val, suffix):
    return f"{val} {suffix}" if pd.notna(val) else "N/D"

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Vento attuale", fmt_metric(last[val_col], unit))
c2.metric("Pressione", fmt_metric(last["press_hpa"], "hPa"))
c3.metric("Temperatura", fmt_metric(last["temp_c"], "°C"))
c4.metric("Umidità", fmt_metric(last["rh_pct"], "%"))
c5.metric("Radiazione", fmt_metric(last["rad_wm2"], "W/m²"))
c6.metric("Ora solare", str(last["time_solare"])[-8:-3])

def build_chart(title: str, y_title: str, lamma_y, om_col_prefix: str, convert_kn: bool = False):
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df["time_solare_dt"],
            y=lamma_y,
            mode="lines+markers",
            name="LaMMA (misurato)",
        )
    )

    if not df_om.empty:
        active_models = []
        if show_arome:
            active_models.append("arome")
        if show_icon2i:
            active_models.append("icon2i")

        for key in active_models:
            m_id = MODELS[key]["id"]
            m_label = MODELS[key]["label"]
            m_dash = MODELS[key]["dash"]
            col_name = f"{om_col_prefix}_{m_id}"
            if col_name in df_om.columns:
                y_vals = (
                    (df_om[col_name] / 1.852).round(2)
                    if (convert_kn and unit == "kn")
                    else df_om[col_name]
                )
                fig.add_trace(
                    go.Scatter(
                        x=df_om["time_dt"],
                        y=y_vals,
                        mode="lines",
                        name=m_label,
                        line=dict(dash=m_dash),
                    )
                )

    fig.update_layout(
        title=title,
        yaxis_title=y_title,
        height=340,
        hovermode="x unified",
        margin=dict(l=40, r=20, t=45, b=30),
    )
    return fig


st.plotly_chart(
    build_chart(f"Velocità vento ({unit})", unit, df[val_col], "wind_speed_10m", convert_kn=True),
    use_container_width=True,
)
st.plotly_chart(
    build_chart("Pressione atmosferica (hPa)", "hPa", df["press_hpa"], "pressure_msl"),
    use_container_width=True,
)
st.plotly_chart(
    build_chart("Radiazione solare (W/m²)", "W/m²", df["rad_wm2"], "shortwave_radiation"),
    use_container_width=True,
)
st.plotly_chart(
    build_chart("Temperatura aria (°C)", "°C", df["temp_c"], "temperature_2m"),
    use_container_width=True,
)
st.plotly_chart(
    build_chart("Umidità relativa (%)", "%", df["rh_pct"], "relative_humidity_2m"),
    use_container_width=True,
)

with st.expander("Ultimi dati acquisiti"):
    st.dataframe(df.tail(25), use_container_width=True)
