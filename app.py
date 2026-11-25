import os
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

st.set_page_config(page_title="Vada Meteo Live (LaMMA)", layout="wide")

CSV_PATH = os.environ.get("VADA_LIVE_CSV", "data/live/vada_lamma_15m.csv")

st.title("Vada — Acquisizione Stazione Live (LaMMA)")
st.caption("Vento, Pressione, Temperatura, Umidità e Radiazione Solare • Passo 15 minuti (ora solare)")

unit = st.radio("Unità vento", ["km/h", "kn"], horizontal=True)

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

fig = make_subplots(
    rows=3,
    cols=1,
    shared_xaxes=True,
    vertical_spacing=0.06,
    subplot_titles=(
        f"Velocità vento ({unit})",
        "Pressione atmosferica (hPa) & Radiazione solare (W/m²)",
        "Temperatura aria (°C) & Umidità relativa (%)",
    ),
    specs=[[{"secondary_y": False}], [{"secondary_y": True}], [{"secondary_y": True}]],
)

fig.add_trace(
    go.Scatter(x=df["time_solare_dt"], y=df[val_col], mode="lines+markers", name=f"Vento ({unit})"),
    row=1, col=1,
)

fig.add_trace(
    go.Scatter(x=df["time_solare_dt"], y=df["press_hpa"], mode="lines+markers", name="Pressione (hPa)"),
    row=2, col=1, secondary_y=False,
)
fig.add_trace(
    go.Scatter(x=df["time_solare_dt"], y=df["rad_wm2"], mode="lines", name="Radiazione (W/m²)"),
    row=2, col=1, secondary_y=True,
)

fig.add_trace(
    go.Scatter(x=df["time_solare_dt"], y=df["temp_c"], mode="lines+markers", name="Temperatura (°C)"),
    row=3, col=1, secondary_y=False,
)
fig.add_trace(
    go.Scatter(x=df["time_solare_dt"], y=df["rh_pct"], mode="lines+markers", name="Umidità (%)"),
    row=3, col=1, secondary_y=True,
)

fig.update_layout(height=760, hovermode="x unified")
fig.update_yaxes(title_text=unit, row=1, col=1)
fig.update_yaxes(title_text="hPa", row=2, col=1, secondary_y=False)
fig.update_yaxes(title_text="W/m²", row=2, col=1, secondary_y=True)
fig.update_yaxes(title_text="°C", row=3, col=1, secondary_y=False)
fig.update_yaxes(title_text="%", row=3, col=1, secondary_y=True)

st.plotly_chart(fig, use_container_width=True)

with st.expander("Ultimi dati acquisiti"):
    st.dataframe(df.tail(25), use_container_width=True)
