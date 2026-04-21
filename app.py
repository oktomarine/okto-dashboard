import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import json, re, base64

st.set_page_config(
    page_title="Okto Marine – Weekly Tanker Report",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Hide streamlit chrome completely
st.markdown("""
<style>
#MainMenu, footer, header {visibility: hidden;}
.block-container {padding: 0 !important; max-width: 100% !important;}
[data-testid="stAppViewContainer"] > div:first-child {padding: 0 !important;}
</style>
""", unsafe_allow_html=True)

# ── GOOGLE SHEETS ─────────────────────────────────────────────
@st.cache_data(ttl=300)
def load_all():
    creds = Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]),
        scopes=["https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive"]
    )
    gc = gspread.authorize(creds)
    sh = gc.open(st.secrets["sheet_name"])

    # 1_Config
    cfg = {r[0]: r[1] for r in sh.worksheet("1_Config").get_all_values()[1:] if len(r) >= 2}

    # 2_MarketRates → parse into rate tables structure
    mr_raw = sh.worksheet("2_MarketRates").get_all_values()

    def ws_to_df(ws):
        rows = ws.get_all_values()
        if not rows or len(rows) < 2:
            return pd.DataFrame()
        header_idx = 0
        for i, row in enumerate(rows):
            if len([c for c in row if str(c).strip()]) > 1:
                header_idx = i
                break
        headers = rows[header_idx]
        data = rows[header_idx + 1:]
        seen = {}
        clean = []
        for h in headers:
            h = str(h).strip() or "_col"
            if h in seen:
                seen[h] += 1
                h = f"{h}_{seen[h]}"
            else:
                seen[h] = 0
            clean.append(h)
        df = pd.DataFrame(data, columns=clean)
        df = df[df.apply(lambda r: any(str(v).strip() for v in r), axis=1)]
        return df.reset_index(drop=True)

    # 5_Fixtures
    fx_df = ws_to_df(sh.worksheet("5_Fixtures"))

    # 6_RatesHistory
    r6_df = ws_to_df(sh.worksheet("6_RatesHistory"))

    # 7_BunkerHistory
    b7_df = ws_to_df(sh.worksheet("7_BunkerHistory"))

    return cfg, mr_raw, fx_df, r6_df, b7_df

try:
    cfg, mr_raw, fx_df, r6_df, b7_df = load_all()
except Exception as e:
    st.error(f"Veri yüklenemedi: {e}")
    st.stop()

# ── PARSE MARKET RATES ────────────────────────────────────────
def parse_market_rates(raw):
    groups = {}
    current = None
    skip_next = False
    for row in raw[1:]:
        if not any(row): continue
        label = str(row[0]).strip()
        if label.startswith("Loading"):
            current = label
            groups[current] = []
            skip_next = True
        elif skip_next:
            skip_next = False
        elif current and label and label != "Discharge":
            if len(row) >= 5:
                groups[current].append({
                    "discharge": row[0], "c1": row[1],
                    "c2": row[2], "c3": row[3],
                    "var": row[4] if len(row) > 4 else "—"
                })
    return groups

# ── BUILD RATES DATA FOR CHARTS ───────────────────────────────
def build_rates_json(r6_df):
    if r6_df.empty:
        return {"dates": [], "USG": {}, "ARA": {}, "AG": {}, "FEAST": {}}
    r6_df["Date"] = pd.to_datetime(r6_df["Date"], errors="coerce")
    route_cols = [c for c in r6_df.columns if c != "Date"]
    dates = r6_df["Date"].dt.strftime("%Y-%m-%d").tolist()
    result = {"dates": dates, "USG": {}, "ARA": {}, "AG": {}, "FEAST": {}}
    for col in route_cols:
        vals = pd.to_numeric(r6_df[col], errors="coerce").tolist()
        vals = [v if pd.notna(v) else None for v in vals]
        if col.startswith("USG"):   result["USG"][col] = vals
        elif col.startswith("ARA"): result["ARA"][col] = vals
        elif col.startswith("AG"):  result["AG"][col] = vals
        elif col.startswith("FEAST"): result["FEAST"][col] = vals
    return result

# ── BUILD BUNKER DATA ─────────────────────────────────────────
def build_bunker_json(b7_df):
    if b7_df.empty:
        return {"VLSFO": [], "MGO": [], "IFO 380": []}
    b7_df["Date"] = pd.to_datetime(b7_df["Date"], errors="coerce")
    ports = [c for c in b7_df.columns if c not in ["Date","Fuel"]]
    result = {}
    for fuel in ["VLSFO","MGO","IFO 380"]:
        df_f = b7_df[b7_df["Fuel"]==fuel].sort_values("Date")
        rows = []
        for _, r in df_f.iterrows():
            entry = {"date": r["Date"].strftime("%Y-%m-%d") if pd.notna(r["Date"]) else ""}
            for p in ports:
                try:
                    entry[p] = float(r[p]) if r[p] else None
                except:
                    entry[p] = None
            rows.append(entry)
        result[fuel] = rows
    return result

# ── BUILD FIXTURES DATA ───────────────────────────────────────
def build_fixtures_json(fx_df):
    if fx_df.empty:
        return []
    fixtures = []
    for _, r in fx_df.iterrows():
        fixtures.append({
            "type":      str(r.get("Type","")),
            "cargoType": str(r.get("Cargo_Type","")),
            "charterer": str(r.get("Charterer","")),
            "vessel":    str(r.get("Vessel","")),
            "qty":       str(r.get("Quantity","")),
            "cargo":     str(r.get("Cargo","")),
            "loadDisch": str(r.get("Load_Disch","")),
            "date":      str(r.get("Laycan","")),
            "rate":      str(r.get("Rate","")),
        })
    return fixtures

# ── ASSEMBLE DATA ─────────────────────────────────────────────
mr_groups  = parse_market_rates(mr_raw)
rates_data = build_rates_json(r6_df)
bunker_data = build_bunker_json(b7_df)
fixtures   = build_fixtures_json(fx_df)

week_label  = cfg.get("Week Label", "W--")
report_date = cfg.get("Report Date", "—")
period      = cfg.get("Period", "—")

# ── BUILD MARKET RATES HTML TABLES ───────────────────────────
def build_rate_tables_html(groups):
    html = ""
    ag_groups = {"Loading AG"}
    for gname, rows in groups.items():
        is_ag = gname in ag_groups
        s = ["5,000 mts","10,000 mts","15,000 mts"] if is_ag else ["3,000 mts","5,000 mts","10,000 mts"]
        html += f'<div class="rate-block"><div class="rate-head">{gname}</div>'
        html += '<table class="rate-tbl">'
        html += f'<tr><th>Discharge</th><th>{s[0]}</th><th>{s[1]}</th><th>{s[2]}</th><th>Var</th></tr>'
        for r in rows:
            v = r["var"].strip()
            if v == "▲":
                var_td = '<td class="up">▲</td>'
            elif v == "▼":
                var_td = '<td class="dn">▼</td>'
            else:
                var_td = '<td class="fl">—</td>'
            html += f'<tr><td>{r["discharge"]}</td><td>{r["c1"]}</td><td>{r["c2"]}</td><td>{r["c3"]}</td>{var_td}</tr>'
        html += "</table></div>"
    return html

rate_tables_html = build_rate_tables_html(mr_groups)

# ── LOAD BASE HTML & INJECT DATA ─────────────────────────────
with open("template.html") as f:
    template = f.read()

# Inject dynamic data
template = template.replace("__WEEK_LABEL__", week_label)
template = template.replace("__REPORT_DATE__", report_date)
template = template.replace("__PERIOD__", period)
template = template.replace("__RATE_TABLES__", rate_tables_html)
template = template.replace("__DATA_JSON__", json.dumps({
    "rates": rates_data,
    "bunker": bunker_data,
    "fixtures": fixtures
}))

# Render
components.html(template, height=900, scrolling=True)
