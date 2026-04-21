import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import json, uuid, requests

st.set_page_config(
    page_title="Okto Marine – Admin",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
<style>
#MainMenu {visibility:hidden;}
footer {visibility:hidden;}
header {visibility:hidden;}
[data-testid="stToolbar"] {display:none;}
[data-testid="stDecoration"] {display:none;}
[data-testid="stStatusWidget"] {display:none;}
.block-container {padding:0 !important; max-width:100% !important;}
[data-testid="stAppViewContainer"] {background:#f0f4f8 !important;}
[data-testid="stAppViewBlockContainer"] {padding:0 !important;}
div[data-testid="stVerticalBlock"] {gap:0 !important; padding:0 !important;}
iframe {border:none !important; display:block;}
</style>
""", unsafe_allow_html=True)

SUPABASE_URL = "https://ejyznzhdninuikctcepb.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVqeXpuemhkbmludWlrY3RjZXBiIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzY3NjQ0NzcsImV4cCI6MjA5MjM0MDQ3N30.eYOBozOzkZzxArAyTt4UwMZgy6wX-32TSSPoOQGyXLg"
BASE_URL = "https://okto-weekly-tanker-report.streamlit.app"

# ── GOOGLE SHEETS ─────────────────────────────────────────────
@st.cache_data(ttl=60)
def load_all():
    creds = Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]),
        scopes=["https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive"]
    )
    gc = gspread.authorize(creds)
    sh = gc.open(st.secrets["sheet_name"])

    cfg = {r[0]: r[1] for r in sh.worksheet("1_Config").get_all_values()[1:] if len(r) >= 2}
    mr_raw = sh.worksheet("2_MarketRates").get_all_values()

    def ws_to_df(ws):
        rows = ws.get_all_values()
        if not rows or len(rows) < 2:
            return pd.DataFrame()
        header_idx = 0
        for i, row in enumerate(rows):
            non_empty = [c for c in row if str(c).strip()]
            first = str(row[0]).strip() if row else ""
            skip = ["↓","OKTO","MARKET","FREIGHT","BUNKER","FIXTURES","RATES"]
            if len(non_empty) > 1 and not any(first.startswith(s) for s in skip):
                header_idx = i
                break
        headers = rows[header_idx]
        data = [r for r in rows[header_idx+1:] if not str(r[0]).startswith("↓")]
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

    fx_df = ws_to_df(sh.worksheet("5_Fixtures"))
    r6_df = ws_to_df(sh.worksheet("6_RatesHistory"))
    b7_df = ws_to_df(sh.worksheet("7_BunkerHistory"))
    return cfg, mr_raw, fx_df, r6_df, b7_df

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

def build_rates_json(r6_df):
    if r6_df.empty:
        return {"dates":[],"USG":{},"ARA":{},"AG":{},"FEAST":{}}
    r6_df["Date"] = pd.to_datetime(r6_df["Date"], errors="coerce")
    route_cols = [c for c in r6_df.columns if c != "Date"]
    dates = r6_df["Date"].dt.strftime("%Y-%m-%d").tolist()
    result = {"dates": dates, "USG":{}, "ARA":{}, "AG":{}, "FEAST":{}}
    for col in route_cols:
        vals = pd.to_numeric(r6_df[col], errors="coerce").tolist()
        vals = [v if pd.notna(v) else None for v in vals]
        if col.startswith("USG"):     result["USG"][col] = vals
        elif col.startswith("ARA"):   result["ARA"][col] = vals
        elif col.startswith("AG"):    result["AG"][col] = vals
        elif col.startswith("FEAST"): result["FEAST"][col] = vals
    return result

def build_bunker_json(b7_df):
    if b7_df.empty:
        return {"VLSFO":[],"MGO":[],"IFO 380":[]}
    b7_df["Date"] = pd.to_datetime(b7_df["Date"], errors="coerce")
    ports = [c for c in b7_df.columns if c not in ["Date","Fuel"]]
    result = {}
    for fuel in ["VLSFO","MGO","IFO 380"]:
        df_f = b7_df[b7_df["Fuel"]==fuel].sort_values("Date")
        rows = []
        for _, r in df_f.iterrows():
            entry = {"date": r["Date"].strftime("%Y-%m-%d") if pd.notna(r["Date"]) else ""}
            for p in ports:
                try: entry[p] = float(r[p]) if str(r[p]).strip() else None
                except: entry[p] = None
            rows.append(entry)
        result[fuel] = rows
    return result

def build_fixtures_json(fx_df):
    if fx_df.empty: return []
    out = []
    for _, r in fx_df.iterrows():
        out.append({
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
    return out

def build_rate_tables_html(groups):
    html = ""
    for gname, rows in groups.items():
        is_ag = gname == "Loading AG"
        s = ["5,000 mts","10,000 mts","15,000 mts"] if is_ag else ["3,000 mts","5,000 mts","10,000 mts"]
        html += f'<div class="rate-block"><div class="rate-head">{gname}</div>'
        html += '<table class="rate-tbl">'
        html += f'<tr><th>Discharge</th><th>{s[0]}</th><th>{s[1]}</th><th>{s[2]}</th><th>Var</th></tr>'
        for r in rows:
            v = r["var"].strip()
            var_td = '<td class="up">▲</td>' if v=="▲" else ('<td class="dn">▼</td>' if v=="▼" else '<td class="fl">—</td>')
            html += f'<tr><td>{r["discharge"]}</td><td>{r["c1"]}</td><td>{r["c2"]}</td><td>{r["c3"]}</td>{var_td}</tr>'
        html += "</table></div>"
    return html

def save_to_supabase(report_id, week_label, report_date, data):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal"
    }
    payload = {
        "id": report_id,
        "week_label": week_label,
        "report_date": report_date,
        "data": data
    }
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/reports",
        headers=headers,
        json=payload
    )
    return r.status_code in [200, 201]

def load_from_supabase(report_id):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}"
    }
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/reports?id=eq.{report_id}&select=*",
        headers=headers
    )
    if r.status_code == 200 and r.json():
        return r.json()[0]
    return None

# ── CHECK URL PARAMS ──────────────────────────────────────────
params = st.query_params
report_id = params.get("id", None)

# ══════════════════════════════════════════════════════════════
# RAPOR GÖRÜNTÜLEME MODU
# ══════════════════════════════════════════════════════════════
if report_id:
    report = load_from_supabase(report_id)
    if not report:
        st.error("Rapor bulunamadı.")
        st.stop()

    data = report["data"]
    week_label  = report["week_label"]
    report_date = report["report_date"]
    period      = data.get("period","")
    rate_tables = data.get("rate_tables_html","")

    with open("template.html") as f:
        tmpl = f.read()

    tmpl = tmpl.replace("__WEEK_LABEL__", week_label)
    tmpl = tmpl.replace("__REPORT_DATE__", report_date)
    tmpl = tmpl.replace("__PERIOD__", period)
    tmpl = tmpl.replace("__RATE_TABLES__", rate_tables)
    tmpl = tmpl.replace("__DATA_JSON__", json.dumps({
        "rates":    data.get("rates",{}),
        "bunker":   data.get("bunker",{}),
        "fixtures": data.get("fixtures",[])
    }))

    components.html(tmpl, height=950, scrolling=True)
    st.stop()

# ══════════════════════════════════════════════════════════════
# ADMIN MODU
# ══════════════════════════════════════════════════════════════
try:
    cfg, mr_raw, fx_df, r6_df, b7_df = load_all()
except Exception as e:
    st.error(f"Veri yüklenemedi: {e}")
    st.stop()

week_label  = cfg.get("Week Label","W--")
report_date = cfg.get("Report Date","—")
period      = cfg.get("Period","—")
mr_groups   = parse_market_rates(mr_raw)
rates_data  = build_rates_json(r6_df)
bunker_data = build_bunker_json(b7_df)
fixtures    = build_fixtures_json(fx_df)
rate_tables = build_rate_tables_html(mr_groups)

# Admin header
st.markdown(f"""
<div style="background:#1a2e5a;border-bottom:3px solid #e07820;padding:10px 28px;
display:flex;align-items:center;justify-content:space-between;">
  <img src="https://raw.githubusercontent.com/oktomarine/okto-dashboard/main/Logo.png" style="height:34px;">
  <div style="color:#fff;font-family:Tahoma;font-size:16px;font-weight:700;">
    Weekly Tanker Report &ndash; <span style="color:#f0b060;">{week_label}</span>
    <span style="font-size:11px;color:#a0b8d8;margin-left:12px;">ADMIN PANELİ</span>
  </div>
  <div style="text-align:right;font-size:10px;color:#a0b8d8;font-family:Tahoma;">
    <strong style="color:#f0c060;font-size:12px;">{report_date}</strong><br>{period}
  </div>
</div>
""", unsafe_allow_html=True)

# Dashboard preview
with open("template.html") as f:
    tmpl = f.read()

tmpl = tmpl.replace("__WEEK_LABEL__", week_label)
tmpl = tmpl.replace("__REPORT_DATE__", report_date)
tmpl = tmpl.replace("__PERIOD__", period)
tmpl = tmpl.replace("__RATE_TABLES__", rate_tables)
tmpl = tmpl.replace("__DATA_JSON__", json.dumps({
    "rates": rates_data,
    "bunker": bunker_data,
    "fixtures": fixtures
}))

components.html(tmpl, height=900, scrolling=True)

# ── PUBLISH BUTTON ────────────────────────────────────────────
st.markdown("""
<div style="background:#fff;border-top:3px solid #e07820;padding:20px 28px;
display:flex;align-items:center;gap:20px;box-shadow:0 -2px 8px rgba(0,0,0,0.08);">
  <div style="font-family:Tahoma;font-size:12px;color:#3a5068;">
    Veriyi kontrol ettikten sonra raporu yayınla. Yayınlanan link değişmez.
  </div>
</div>
""", unsafe_allow_html=True)

col1, col2, col3 = st.columns([2,1,3])
with col2:
    publish = st.button("🚀 Raporu Yayınla", type="primary", use_container_width=True)

if publish:
    new_id = uuid.uuid4().hex[:8]
    success = save_to_supabase(
        report_id   = new_id,
        week_label  = week_label,
        report_date = report_date,
        data = {
            "period":           period,
            "rate_tables_html": rate_tables,
            "rates":            rates_data,
            "bunker":           bunker_data,
            "fixtures":         fixtures
        }
    )
    if success:
        link = f"{BASE_URL}?id={new_id}"
        st.success(f"✅ Rapor yayınlandı!")
        st.markdown(f"""
        <div style="background:#e6f4ee;border:1px solid #b8dece;border-radius:6px;
        padding:16px 20px;font-family:Tahoma;margin-top:8px;">
          <div style="font-size:11px;color:#3a5068;margin-bottom:6px;">Paylaşım linki:</div>
          <div style="font-size:14px;font-weight:700;color:#1a2e5a;">
            <a href="{link}" target="_blank" style="color:#1e5fa8;">{link}</a>
          </div>
          <div style="font-size:10px;color:#7a96ab;margin-top:6px;">
            Bu link değişmez. Haftanın raporu bu adreste kalıcı olarak yayında olacak.
          </div>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.error("Kayıt hatası. Tekrar dene.")
