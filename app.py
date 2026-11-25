import os
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="Vada Wind Live (LaMMA)", layout="wide")

CSV_PATH = os.environ.get("VADA_LIVE_CSV", "data/live/vada_lamma_15m.csv")

st.title("Vada — Acquisizione Vento Live (LaMMA)")
st.caption("Serie temporale aggiornata ogni 15 minuti (ora solare) • Senza massimi né direzione")

unit = st.radio("Unità di misura", ["km/h", "kn"], horizontal=True)

if not os.path.exists(CSV_PATH):
    st.warning(f"File dati non ancora presente in `{CSV_PATH}`. Esegui prima la GitHub Action o genera il CSV.")
    st.stop()

df = pd.read_csv(CSV_PATH)
if df.empty:
    st.info("Il file CSV esiste ma non contiene ancora rilevazioni.")
    st.stop()

df["time_solare_dt"] = pd.to_datetime(df["time_solare"])
df = df.sort_values("time_solare_dt").reset_index(drop=True)

val_col = "ws_kmh" if unit == "km/h" else "ws_kn"
last_row = df.iloc[-1]

col1, col2, col3 = st.columns(3)
col1.metric("Ultimo valore rilevato", f"{last_row[val_col]} {unit}" if pd.notna(last_row[val_col]) else "N/D")
col2.metric("Ora solare rilevazione", str(last_row["time_solare"]))
col3.metric("Punti acquisiti", str(len(df)))

fig = go.Figure()
fig.add_trace(
    go.Scatter(
        x=df["time_solare_dt"],
        y=df[val_col],
        mode="lines+markers",
        name=f"Vento ({unit})",
    )
)
fig.update_layout(
    xaxis_title="Ora solare",
    yaxis_title=f"Velocità vento ({unit})",
    hovermode="x unified",
    height=420,
)
st.plotly_chart(fig, use_container_width=True)

with st.expander("Ultimi dati acquisiti"):
    st.dataframe(df.tail(25), use_container_width=True)
