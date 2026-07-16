#!/usr/bin/env python3
"""
Market Watcher Daily Agent
--------------------------
Pulls live macro data, runs Druckenmiller-style 5-component conviction model,
analyzes sector flow rotation (Offense vs Defense based on regime), and
generates a full investment research HTML report.

Usage:
  python market_watcher.py           # Run and open report in browser
  python market_watcher.py --help    # Show Task Scheduler setup guide

Windows Task Scheduler (daily at 4pm):
  1. Win+R -> taskschd.msc
  2. Create Basic Task -> Daily, 4:00 PM
  3. Program: python
  4. Arguments: "C:\\Users\\georg\\OneDrive\\Desktop\\Python Finance\\macro-dashboard\\market_watcher.py"
  5. Start in: C:\\Users\\georg\\OneDrive\\Desktop\\Python Finance\\macro-dashboard
"""

import os, sys, re, csv, io, math, json, webbrowser, requests, html as html_lib
from datetime import datetime, timedelta, timezone
import pandas as pd
from bs4 import BeautifulSoup

# â”€â”€â”€ CONFIG â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

REPORT_FOLDER = r"C:\Users\georg\OneDrive\Desktop\Python Finance\macro-dashboard"
HY_CACHE_JSON = os.path.join(REPORT_FOLDER, "hy_spread_cache.json")
HY_CACHE_JS   = os.path.join(REPORT_FOLDER, "hy_spread_cache.js")

YAHOO_DXY    = "https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?interval=1d&range=1y"
YAHOO_10Y    = "https://query1.finance.yahoo.com/v8/finance/chart/%5ETNX?interval=1d&range=1y"
FRED_HY      = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAMLH0A0HYM2"
CF_PROXY     = "https://macro-dashboard.george982014.workers.dev/?url="
FINVIZ_URL   = "https://finviz.com/screener.ashx?v=111&f=geo_usa%2Csh_avgvol_o50%2Csh_price_o3%2Cta_perf_1w20o"
BREADTH_CSV  = "https://docs.google.com/spreadsheets/d/1O6OhS7ciA8zwfycBfGPbP2fWJnR0pn2UUvFZVDP9jpE/gviz/tq?tqx=out:csv"
FLOW_CURRENT = "https://docs.google.com/spreadsheets/d/1u-4AVn514KIjRVmObQQ9BtxGZzgJwgu2fcs04hESWyE/export?format=csv&gid=337433437"
FLOW_HISTORY = "https://docs.google.com/spreadsheets/d/1u-4AVn514KIjRVmObQQ9BtxGZzgJwgu2fcs04hESWyE/export?format=csv&gid=625099371"

DXY_THRESH = 1.0
Y10_THRESH = 2.0
HY_THRESH  = 0.30

TIMEOUT = 30
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# â”€â”€â”€ REGIME TABLE â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Ported from macro_regime_dashboard_redesign.html line 1423

REGIMES = {
    'cell-ydown-dup':   {'icon':'âš ï¸',  'name':'Global Stress',     'color':'#a385f0','bg':'rgba(76,29,149,.3)',   'desc':'Bond flight + safe haven dollar.',         'plays':['Cash','Short EQ','ST UST'],   'risk_on':False},
    'cell-ydown-dflat': {'icon':'â†”ï¸',  'name':'Fed Pivoting',       'color':'#5b9cf5','bg':'rgba(30,58,138,.3)',   'desc':'Easing starting. Wait for confirmation.',  'plays':['REITs','Utils','HC'],          'risk_on':False},
    'cell-ydown-ddown': {'icon':'ðŸš€',  'name':'Goldilocks',         'color':'#3dd9a0','bg':'rgba(5,46,22,.5)',     'desc':'Best liquidity. Load the boat.',            'plays':['Growth','EM','Commod'],        'risk_on':True},
    'cell-yflat-dup':   {'icon':'âš¡',  'name':'Dollar Tightening',  'color':'#f09848','bg':'rgba(124,45,18,.3)',   'desc':'Strong $ as hike. Trim EM.',               'plays':['Defensive','USD Cash'],        'risk_on':False},
    'cell-yflat-dflat': {'icon':'âž¡ï¸',  'name':'Sideways',           'color':'#636d88','bg':'rgba(30,41,59,.5)',    'desc':'Stock pickers market.',                    'plays':['Quality','Dividends','LC'],    'risk_on':False},
    'cell-yflat-ddown': {'icon':'âœ…',  'name':'Mild Bull',          'color':'#3dd9a0','bg':'rgba(7,30,18,.5)',     'desc':'Dollar weakness easing conditions.',       'plays':['Cyclicals','Fins','EM'],       'risk_on':True},
    'cell-yup-dup':     {'icon':'ðŸ”´',  'name':'Max Tightening',     'color':'#f27872','bg':'rgba(127,29,29,.3)',   'desc':'Worst. Cash is king.',                     'plays':['Cash','Short','Reduce'],       'risk_on':False},
    'cell-yup-dflat':   {'icon':'ðŸ”„',  'name':'Tightening Begins',  'color':'#f2c04e','bg':'rgba(113,63,18,.3)',   'desc':'Rotate to value.',                         'plays':['Banks','Energy','Value'],      'risk_on':False},
    'cell-yup-ddown':   {'icon':'ðŸŒ±',  'name':'Reflationary',       'color':'#36d6c4','bg':'rgba(19,78,74,.3)',    'desc':'Cyclicals win.',                           'plays':['Energy','Materials','Fins'],   'risk_on':True},
}
REGIMES['cell-yflat-dup'].update(desc='Strong $ as de facto rate hike.', plays=['Domestic','Fins','Trim EM'])
REGIMES['cell-yflat-dflat'].update(desc='No macro wind. Stock pickers.', plays=['Picks','Balanced','Wait'])
REGIMES['cell-yflat-ddown'].update(plays=['EM','Commod','Energy'])
REGIMES['cell-ydown-ddown'].update(desc='Most supportive liquidity backdrop.')
REGIMES['cell-yup-dup'].update(desc='Tight dollar and rate pressure.')
REGIMES['cell-yup-ddown'].update(desc='Growth hot, liquidity decent. Cyclicals win.')
REGIMES['cell-ydown-dup']['icon'] = '&#9888;&#65039;'
REGIMES['cell-ydown-dflat']['icon'] = '&#8596;&#65039;'
REGIMES['cell-ydown-ddown']['icon'] = '&#128640;'
REGIMES['cell-yflat-dup']['icon'] = '&#9889;'
REGIMES['cell-yflat-dflat']['icon'] = '&#10145;&#65039;'
REGIMES['cell-yflat-ddown']['icon'] = '&#9989;'
REGIMES['cell-yup-dup']['icon'] = '&#128308;'
REGIMES['cell-yup-dflat']['icon'] = '&#128260;'
REGIMES['cell-yup-ddown']['icon'] = '&#127793;'
Y_MAP = {'up':'yup',   'flat':'yflat', 'down':'ydown'}
D_MAP = {'up':'dup',   'flat':'dflat', 'down':'ddown'}

# â”€â”€â”€ UTILITIES â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def pct_chg(n, t):
    if n is None or t is None or t == 0: return None
    return ((n - t) / t) * 100

def bps_diff(n, t):
    if n is None or t is None: return None
    return (n - t) * 100

def abs_diff(n, t):
    if n is None or t is None: return None
    return n - t

def safe_fetch(name, fn):
    try:
        result = fn()
        status = "OK" if result is not None else "EMPTY"
        print(f"  [{status}] {name}")
        return result
    except Exception as e:
        print(f"  [FAILED] {name}: {e}")
        return None

def _badge(text, color, text_color="#fff"):
    return f'<span style="display:inline-block;font-size:.72rem;font-weight:700;padding:2px 9px;border-radius:4px;background:{color};color:{text_color};white-space:nowrap">{text}</span>'

def _val(v, fmt=".2f", prefix="", suffix="", positive_green=True):
    if v is None: return '<span style="color:#636d88">â€”</span>'
    color = "#3dd9a0" if (v > 0 and positive_green) else ("#f27872" if (v < 0 and positive_green) else "#94a3b8")
    sign = "+" if v > 0 else ""
    return f'<span style="color:{color}">{sign}{prefix}{v:{fmt}}{suffix}</span>'

# â”€â”€â”€ FETCH FUNCTIONS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _get(url, extra_headers=None):
    h = {**HEADERS, **(extra_headers or {})}
    r = requests.get(url, headers=h, timeout=TIMEOUT)
    r.raise_for_status()
    return r

def fetch_yahoo(url):
    r = _get(url)
    data = r.json()
    result = data["chart"]["result"][0]
    timestamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]
    rows = [{"ts": t, "close": c} for t, c in zip(timestamps, closes) if c is not None]
    if len(rows) < 10: return None
    return rows

def closest_row(rows, days):
    now = datetime.now(timezone.utc).timestamp()
    target = now - days * 86400
    best = min(rows, key=lambda r: abs(r["ts"] - target))
    date_str = datetime.fromtimestamp(best["ts"], tz=timezone.utc).strftime("%b %d, %Y")
    return {"close": best["close"], "date_str": date_str}

def build_indicator_obj(rows):
    if not rows: return None
    current_row = rows[-1]
    current = current_row["close"]
    now_str = datetime.fromtimestamp(current_row["ts"], tz=timezone.utc).strftime("%b %d, %Y")
    m1 = closest_row(rows, 30)
    m3 = closest_row(rows, 90)
    m6 = closest_row(rows, 180)
    return {
        "current": current, "now_str": now_str,
        "one_month_ago": m1["close"],    "m1_str": m1["date_str"],
        "three_months_ago": m3["close"], "m3_str": m3["date_str"],
        "six_months_ago": m6["close"],   "m6_str": m6["date_str"],
    }

def save_hy_cache(hy_obj):
    if not hy_obj:
        return
    payload = {**hy_obj, "source": "FRED BAMLH0A0HYM2", "cached_at": datetime.now(timezone.utc).isoformat()}
    with open(HY_CACHE_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    with open(HY_CACHE_JS, "w", encoding="utf-8") as f:
        f.write("window.HY_SPREAD_CACHE = ")
        json.dump(payload, f, indent=2)
        f.write(";\n")

def load_hy_cache():
    if not os.path.exists(HY_CACHE_JSON):
        return None
    with open(HY_CACHE_JSON, "r", encoding="utf-8") as f:
        payload = json.load(f)
    required = ("current", "one_month_ago", "three_months_ago", "six_months_ago")
    if not all(k in payload for k in required):
        return None
    return payload

def fetch_hy_spread():
    # Add date range (210 days) to reduce payload size â€” same as dashboard
    now_dt = datetime.now(timezone.utc)
    cosd = (now_dt - timedelta(days=210)).strftime("%Y-%m-%d")
    coed = now_dt.strftime("%Y-%m-%d")
    url = f"{FRED_HY}&cosd={cosd}&coed={coed}"
    # Try direct FRED first. If public proxies hang, falling back to them first makes
    # the static HY cache look frozen even when FRED itself is current.
    proxy_urls = [
        url,
        CF_PROXY + requests.utils.quote(url, safe=''),
        f"https://api.allorigins.win/raw?url={requests.utils.quote(url, safe='')}",
        f"https://api.codetabs.com/v1/proxy?quest={requests.utils.quote(url, safe='')}",
    ]
    text = None
    for proxy_url in proxy_urls:
        try:
            is_direct_fred = proxy_url.startswith("https://fred.stlouisfed.org/")
            headers = {"Accept": "text/csv,*/*;q=0.8"} if is_direct_fred else HEADERS
            r = requests.get(proxy_url, headers=headers, timeout=12 if is_direct_fred else 8)
            if r.ok and len(r.text) > 200 and "DATE" in r.text[:50].upper():
                text = r.text; break
        except Exception:
            continue
    if not text:
        cached = load_hy_cache()
        if cached:
            print(f"  [CACHE] HY Spread using cached FRED data from {cached.get('now_str', cached.get('cached_at', 'unknown'))}")
            return cached
        raise ConnectionError("FRED HY Spread unavailable (all proxies failed)")
    lines = text.strip().split("\n")
    rows = []
    for line in lines[1:]:
        parts = line.strip().split(",")
        if len(parts) < 2: continue
        try:
            date = datetime.strptime(parts[0].strip(), "%Y-%m-%d")
            val = float(parts[1].strip())
            rows.append({"date": date, "close": val})
        except ValueError:
            continue
    if len(rows) < 10: return None
    rows.sort(key=lambda x: x["date"])
    current = rows[-1]["close"]
    now_str = rows[-1]["date"].strftime("%b %d, %Y")
    now_ts = rows[-1]["date"].timestamp()

    def find_row(days):
        target = now_ts - days * 86400
        best = None
        for row in rows:
            da = (now_ts - row["date"].timestamp()) / 86400
            if 28 <= da <= 35:
                best = row; break
        if best is None:
            idx = max(0, len(rows) - 22)
            best = rows[idx]
        return best

    def find_row_range(min_d, max_d, fallback_idx):
        for row in reversed(rows):
            da = (now_ts - row["date"].timestamp()) / 86400
            if min_d <= da <= max_d:
                return row
        return rows[max(0, len(rows) - fallback_idx)]

    m1r = find_row_range(28, 35, 22)
    m3r = find_row_range(85, 95, 64)
    m6r = find_row_range(175, 190, 130)
    hy_obj = {
        "current": current, "now_str": now_str,
        "one_month_ago": m1r["close"],    "m1_str": m1r["date"].strftime("%b %d, %Y"),
        "three_months_ago": m3r["close"], "m3_str": m3r["date"].strftime("%b %d, %Y"),
        "six_months_ago": m6r["close"],   "m6_str": m6r["date"].strftime("%b %d, %Y"),
    }
    save_hy_cache(hy_obj)
    return hy_obj

def fetch_finviz_momentum():
    r = _get(FINVIZ_URL)
    m = re.search(r'(\d[\d,]*)\s*Total', r.text, re.IGNORECASE)
    if m: return int(m.group(1).replace(",", ""))
    soup = BeautifulSoup(r.text, "html.parser")
    el = soup.find(id="screener-total")
    if el:
        nums = re.findall(r'\d[\d,]*', el.get_text())
        if nums: return int(nums[-1].replace(",", ""))
    return None

def fetch_breadth():
    r = _get(BREADTH_CSV)
    lines = r.text.strip().split("\n")
    if len(lines) < 3: return None
    hdr_idx = 0
    for i, line in enumerate(lines[:5]):
        if "date" in line.lower():
            hdr_idx = i; break
    rows = []
    reader = csv.reader(io.StringIO("\n".join(lines[hdr_idx+1:])))
    for cols in reader:
        if len(cols) < 15: continue
        date_str = cols[0].replace('"', '').strip()
        if not date_str or len(date_str) < 6: continue
        def p(s):
            try: return float(re.sub(r'[",\s]', '', str(s)))
            except: return float('nan')
        row = {
            "date": date_str, "up4": p(cols[1]), "dn4": p(cols[2]),
            "r5": p(cols[3]),  "r10": p(cols[4]),
            "q25u": p(cols[5]), "q25d": p(cols[6]),
            "m25u": p(cols[7]), "m25d": p(cols[8]),
            "m50u": p(cols[9]), "m50d": p(cols[10]),
            "b3413": p(cols[11]), "s3413": p(cols[12]),
            "total": p(cols[13]), "t2108": p(cols[14]),
        }
        if not math.isnan(row["up4"]):
            rows.append(row)
    if len(rows) < 2: return None
    try:
        rows.sort(key=lambda x: datetime.strptime(x["date"], "%m/%d/%Y"))
    except Exception:
        pass
    return rows

def fetch_flow_current():
    r = _get(FLOW_CURRENT)
    df = pd.read_csv(io.StringIO(r.text))
    # Normalize column names: strip whitespace and replace newlines
    df.columns = [c.strip().replace('\n', ' ').replace('\r', ' ') for c in df.columns]
    # Strip % from all string-like columns (check dtype.kind == 'O' for object/string)
    for col in df.columns:
        if df[col].dtype.kind in ('O', 'U', 'S'):  # object, unicode, byte string
            df[col] = df[col].apply(lambda x: str(x).replace('%','').strip() if pd.notna(x) else x)
    # Convert perf/change/flow columns to numeric
    for col in df.columns:
        if any(kw in col.lower() for kw in ['perf', 'change', 'flow']):
            df[col] = pd.to_numeric(df[col], errors='coerce')
    return df

def fetch_flow_history():
    r = _get(FLOW_HISTORY)
    df = pd.read_csv(io.StringIO(r.text))
    # Normalize column names (strip whitespace and newlines)
    df.columns = [c.strip().replace('\n', ' ').replace('\r', ' ') for c in df.columns]
    # Strip % from string columns
    for col in df.columns:
        if df[col].dtype.kind in ('O', 'U', 'S'):
            df[col] = df[col].apply(lambda x: str(x).replace('%','').strip() if pd.notna(x) else x)
    for col in ['Rotation Flow Monthly','Quality Flow Monthly','Rotation Flow Weekly']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    # Parse dates
    if 'Archive Date' in df.columns:
        df['Archive Date'] = pd.to_datetime(df['Archive Date'], errors='coerce')
    return df

# â”€â”€â”€ CALCULATIONS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Ported from macro_regime_dashboard_redesign.html

def calc_velocity(now, m1, m3, m6, is_yield):
    """V1=recent, V2=prior(normalized), V3=6M(normalized). Ported from line 1414."""
    if any(x is None or (isinstance(x, float) and math.isnan(x)) for x in [now, m1, m3]):
        return None
    h6 = m6 is not None and not (isinstance(m6, float) and math.isnan(m6))
    try:
        if is_yield:
            V1 = (now - m1) * 100
            V2 = ((m1 - m3) * 100) / 2
            V3 = ((m3 - m6) * 100) / 3 if h6 else None
        else:
            if m1 == 0 or m3 == 0: return None
            V1 = ((now - m1) / m1) * 100
            V2 = ((m1 - m3) / m3) * 100 / 2
            V3 = ((m3 - m6) / m6) * 100 / 3 if (h6 and m6 != 0) else None
        ar = V1 - V2
        ap = (V2 - V3) if V3 is not None else None
        vA = [V1, V2, V3] if V3 is not None else [V1, V2]
        mn = sum(vA) / len(vA)
        va = sum((v - mn) ** 2 for v in vA) / len(vA)
        sd = math.sqrt(va)
        thresh = max(sd * 0.5, 2 if is_yield else 0.15)
        return {"V1": V1, "V2": V2, "V3": V3, "accel_recent": ar, "accel_prior": ap, "thresh": thresh, "stdev": sd}
    except Exception:
        return None

def classify_dxy(val, thresh):
    if val is None: return None
    return "up" if val > thresh else ("down" if val < -thresh else "flat")

def classify_y10(val, thresh):
    if val is None: return None
    return "up" if val > thresh else ("down" if val < -thresh else "flat")

def classify_hy(val, thresh):
    if val is None: return None
    return "up" if val > thresh else ("down" if val < -thresh else "flat")

def determine_regime(dxy_dir, y10_dir):
    if not dxy_dir or not y10_dir: return None
    cell_id = f"cell-{Y_MAP[y10_dir]}-{D_MAP[dxy_dir]}"
    reg = REGIMES.get(cell_id)
    if reg: return {"id": cell_id, **reg}
    return None

def _base_conviction(v, th):
    """Base conviction: how far past threshold (0-100). Line 1413."""
    if v is None: return 0
    e = abs(v) - th
    return 0 if e <= 0 else min(100, (e / th) * 33.3)

def dxy_conviction(dd1, v_D, t_D):
    """DXY: flow variable â€” direction + acceleration matter. Lines 1467-1479."""
    if dd1 is None: return None
    raw = _base_conviction(dd1, t_D)
    if not v_D: return raw
    same_dir = (v_D["V1"] > 0 and v_D["V2"] > 0) or (v_D["V1"] < 0 and v_D["V2"] < 0)
    accel = same_dir and abs(v_D["V1"]) > abs(v_D["V2"])
    decel = same_dir and abs(v_D["V1"]) < abs(v_D["V2"]) * 0.6
    if accel: raw = min(100, raw * 1.25)
    if decel: raw = raw * 0.7
    return raw

def y10_conviction(yd1, v_Y, t_Y, y10_level):
    """Y10: same velocity is more dangerous at 5% than 3.5%. Lines 1483-1503."""
    if yd1 is None: return None
    raw = _base_conviction(yd1, t_Y)
    if y10_level is not None:
        if y10_level < 3.0:   frag = 0.5
        elif y10_level < 3.5: frag = 0.7
        elif y10_level < 4.5: frag = 1.0
        elif y10_level < 5.0: frag = 1.4
        else:                  frag = 1.8
        raw = min(100, raw * frag)
    if v_Y:
        same_dir = (v_Y["V1"] > 0 and v_Y["V2"] > 0) or (v_Y["V1"] < 0 and v_Y["V2"] < 0)
        if same_dir and abs(v_Y["V1"]) > abs(v_Y["V2"]): raw = min(100, raw * 1.2)
        if same_dir and abs(v_Y["V1"]) < abs(v_Y["V2"]) * 0.6: raw = raw * 0.7
    return raw

def hy_conviction(hd1, v_H, t_H, hy_level):
    """HY: widening at 2.80 is normalization; at 4.50 it's real stress. Lines 1509-1536."""
    if hd1 is None or hy_level is None: return None
    raw = _base_conviction(hd1, t_H)
    widening = hd1 > 0
    if widening:
        if hy_level < 3.00:   mult = 0.2
        elif hy_level < 3.50: mult = 0.5
        elif hy_level < 4.25: mult = 1.0
        elif hy_level < 5.00: mult = 1.5
        elif hy_level < 6.00: mult = 1.8
        else:                  mult = 2.0
    else:
        if hy_level > 5.00:   mult = 1.8
        elif hy_level > 4.25: mult = 1.3
        elif hy_level > 3.50: mult = 1.0
        else:                  mult = 0.5
    raw = min(100, raw * mult)
    if v_H and widening:
        accel_w = v_H["V1"] > 0 and v_H["V2"] >= 0 and v_H["V1"] > v_H["V2"]
        if accel_w: raw = min(100, raw * 1.3)
    return raw

def spec_zone(count):
    """6-zone speculative momentum with graduated conviction. Lines 1179-1187."""
    if count is None:
        return {"zone": "Unknown", "color": "#636d88", "conviction": 0, "signal": "â€”", "note": ""}
    if count <= 15:
        return {"zone": "Washed Out", "color": "#3dd9a0", "conviction": 100,
                "signal": "&#128994; Bullish", "note": "Capitulation. Historically powerful rallies follow."}
    if count <= 40:
        cv = 80 - ((count - 15) / 25) * 50
        return {"zone": "Low", "color": "#6aedb8", "conviction": cv,
                "signal": "&#128994; Cautiously Bullish", "note": "Low speculation. Market washed out but not capitulated."}
    if count <= 80:
        return {"zone": "Neutral", "color": "#a0a8be", "conviction": 0,
                "signal": "&#10145;&#65039; No Signal", "note": "Normal conditions. No directional signal from speculation."}
    if count <= 100:
        cv = 30 + ((count - 80) / 20) * 40
        return {"zone": "Elevated", "color": "#f2c04e", "conviction": cv,
                "signal": "&#128993; Caution", "note": "Speculative momentum is elevated; keep risk controls tight."}
    if count <= 200:
        cv = min(100, 70 + ((count - 100) / 100) * 30)
        return {"zone": "Climactic", "color": "#f09848", "conviction": cv,
                "signal": "&#128308; Bearish", "note": "Historical precursor to sharp corrections."}
    return {"zone": "Extreme", "color": "#f27872", "conviction": 100,
            "signal": "&#128308; Very Bearish", "note": "Speculation is extended. Favor tighter risk control."}

def breadth_conviction(phase):
    """Phase -> 0-100 conviction. Lines 1248-1262."""
    table = {"extreme-bull": 100, "bull-thrust": 85, "bull": 40,
             "extreme-bear": 70, "bear-thrust": 90, "bear": 50}
    return table.get(phase, 0)

def signal_direction(dd1, yd1, hd1, spec_count, b_phase):
    """Returns list of 'on'/'off' for each signal. Lines 1540-1558."""
    dirs = []
    if dd1 is not None and abs(dd1) > 0.3:
        dirs.append("off" if dd1 > 0 else "on")
    if yd1 is not None and abs(yd1) > 5:
        dirs.append("off" if yd1 > 0 else "on")
    if hd1 is not None and abs(hd1) > 0.05:
        dirs.append("off" if hd1 > 0 else "on")
    if spec_count is not None:
        if spec_count <= 40:  dirs.append("on")
        elif spec_count > 80: dirs.append("off")
    if b_phase:
        if "bull" in b_phase: dirs.append("on")
        elif "bear" in b_phase: dirs.append("off")
    return dirs

def calc_conviction(dd1, yd1, hd1, t_D, t_Y, t_H, v_D, v_Y, v_H, y10_level, hy_level, spec_count, b_phase):
    """Full 5-component conviction with alignment multiplier. Lines 1561-1614."""
    c_D     = dxy_conviction(dd1, v_D, t_D)
    c_Y     = y10_conviction(yd1, v_Y, t_Y, y10_level)
    c_H     = hy_conviction(hd1, v_H, t_H, hy_level)
    c_spec  = spec_zone(spec_count)["conviction"] if spec_count is not None else None
    c_bread = breadth_conviction(b_phase) if b_phase else None

    components = {"dxy": c_D, "y10": c_Y, "hy": c_H, "spec": c_spec, "breadth": c_bread}
    valid_vals = [v for v in components.values() if v is not None]
    count = len(valid_vals)
    raw_avg = sum(valid_vals) / count if count > 0 else 0

    dirs = signal_direction(dd1, yd1, hd1, spec_count, b_phase)
    total_dirs = len(dirs)
    on_count  = dirs.count("on")
    off_count = dirs.count("off")
    majority  = max(on_count, off_count)

    if total_dirs >= 4 and majority >= 4:
        align_mult  = 1.30; align_label = f"STRONG ALIGNMENT ({majority}/{total_dirs})"; align_icon = "&#127919;"
        align_color = "#3dd9a0" if on_count > off_count else "#f27872"
        align_detail = "All signals risk-on" if on_count > off_count else "All signals risk-off"
    elif total_dirs >= 3 and majority >= 3:
        align_mult  = 1.15; align_label = f"ALIGNED ({majority}/{total_dirs})"; align_icon = "&#9989;"
        align_color = "#6aedb8" if on_count > off_count else "#f5a0a0"
        align_detail = "Majority risk-on" if on_count > off_count else "Majority risk-off"
    elif total_dirs >= 3 and majority <= math.ceil(total_dirs / 2):
        align_mult  = 0.70; align_label = f"CONFLICTING ({on_count} on / {off_count} off)"; align_icon = "&#9889;"
        align_color = "#f2c04e"; align_detail = "Mixed signals â€” reduce size, wait for clarity"
    elif total_dirs < 3:
        align_mult  = 0.85; align_label = f"INSUFFICIENT ({total_dirs} signals)"; align_icon = "?"
        align_color = "#636d88"; align_detail = "Not enough data for alignment read"
    else:
        align_mult  = 1.00; align_label = f"LEANING ({majority}/{total_dirs})"; align_icon = "&#10145;&#65039;"
        align_color = "#a0a8be"; align_detail = "Slight risk-on tilt" if on_count > off_count else "Slight risk-off tilt"

    final = min(100, raw_avg * align_mult)
    direction = "RISK-ON" if on_count > off_count else ("RISK-OFF" if off_count > on_count else "NEUTRAL")

    return {
        "score": final, "raw_avg": raw_avg, "align_mult": align_mult,
        "align_label": align_label, "align_icon": align_icon,
        "align_color": align_color, "align_detail": align_detail,
        "direction": direction, "components": components,
        "on_count": on_count, "off_count": off_count,
    }

def determine_breadth_phase(rows):
    """Latest row -> phase. Lines 1318-1324."""
    if not rows: return None
    t = rows[-1]
    q25u, q25d, r10 = t["q25u"], t["q25d"], t["r10"]
    bull = q25u > q25d
    if q25u < 200:
        return {"phase":"extreme-bull","icon":"&#128995;","title":"EXTREME BEARISHNESS - Capitulation Buy Zone",
                "sub":f"25%Q Up below 200. Powerful rallies start here.","color":"#a385f0"}
    if q25d < 200:
        return {"phase":"extreme-bear","icon":"&#128995;","title":"EXTREME BULLISHNESS - Froth Warning",
                "sub":"25%Q Down below 200. Correction possible in 2-6 weeks.","color":"#f2c04e"}
    if bull and r10 >= 2:
        return {"phase":"bull-thrust","icon":"&#128640;","title":"BULLISH THRUST - Strong Risk-On",
                "sub":f"Primary bullish. 10D ratio ({r10:.2f}) confirms thrust.","color":"#3dd9a0"}
    if bull:
        return {"phase":"bull","icon":"&#9989;","title":"BULL PHASE",
                "sub":f"25%Q Up ({int(q25u)}) > 25%Q Down ({int(q25d)}).","color":"#6aedb8"}
    if not bull and r10 <= 0.5:
        return {"phase":"bear-thrust","icon":"&#128308;","title":"BEARISH THRUST - Risk-Off",
                "sub":f"Primary bearish. 10D ratio ({r10:.2f}) confirms thrust.","color":"#f27872"}
    return {"phase":"bear","icon":"&#9888;&#65039;","title":"BEAR PHASE",
            "sub":f"25%Q Up ({int(q25u)}) < 25%Q Down ({int(q25d)}).","color":"#f5a0a0"}

def accel_label(vel, is_yield):
    if not vel: return "â€”"
    ar, ap, th = vel["accel_recent"], vel["accel_prior"], vel["thresh"]
    V1p = vel["V1"] > 0
    u = "bps" if is_yield else "%"
    tl = f"(Â±{th:.1f}{u}/mo)" if is_yield else f"(Â±{th:.2f}%/mo)"
    if abs(ar) < th:                                              return f"â†’ Steady {tl}"
    if ar > th and ap and ap > th and V1p:                        return f"ðŸ”ºðŸ”º Consistent accel {tl}"
    if ar < -th and ap and ap < -th and not V1p:                  return f"ðŸ”»ðŸ”» Consistent easing {tl}"
    if ar > th and V1p:                                           return f"ðŸ”º Accelerating {tl}"
    if ar < -th and not V1p:                                      return f"ðŸ”» Easing {tl}"
    return f"ðŸ“‰ Decelerating {tl}"

def stress_verdict(dd1, yd1, hd1, v_D, v_Y, v_H, hy_lvl, t_D, t_Y, t_H):
    """Returns dict with icon/title/subtitle/action/class. Lines 1440-1458."""
    d_dir = classify_dxy(dd1, t_D)
    y_dir = classify_y10(yd1, t_Y)
    h_dir = classify_hy(hd1, t_H) if hd1 is not None else None
    if not hy_lvl:          hy_lr = "unknown"
    elif hy_lvl < 3.25:     hy_lr = "tight"
    elif hy_lvl < 4.25:     hy_lr = "normal"
    elif hy_lvl < 6:        hy_lr = "elevated"
    else:                   hy_lr = "crisis"
    h_aw  = v_H and v_H["accel_recent"] > v_H["thresh"] and v_H["V1"] > 0
    s_pat = (d_dir == "up" and y_dir == "down")
    h_cs  = h_dir == "up" and (hy_lr != "tight" or h_aw)
    h_ds  = h_dir == "down"
    h_n   = (not h_dir or h_dir not in ("up","down"))
    if   s_pat and h_cs:  return {"icon":"ðŸš¨","title":"Confirmed Stress","sub":"All 3 aligned.","action":"Reduce equity. Cash.","cls":"stress-confirmed"}
    elif s_pat and h_ds:  return {"icon":"ðŸ”„","title":"Fed Pivot Trade","sub":"Credit healthy.","action":"Rate-sensitive sectors.","cls":"stress-pivot"}
    elif s_pat and h_n:   return {"icon":"âš ï¸","title":"Stress Pattern â€” Awaiting HY","sub":"HY is tiebreaker.","action":"Watch HY.","cls":"stress-caution"}
    elif not s_pat and h_cs: return {"icon":"ðŸŸ¡","title":"Credit Stress","sub":"HY widening.","action":"Tighten stops.","cls":"stress-caution"}
    elif not s_pat and h_ds: return {"icon":"âœ…","title":"Credit Supportive","sub":"HY tightening; no credit stress confirmation.","action":"Macro still controls sizing.","cls":"stress-pivot"}
    else:                 return {"icon":"âž¡ï¸","title":"No Stress Signal","sub":"Mixed.","action":"Monitor.","cls":"stress-noise"}

def rank_sectors(current_df, history_df, active_flow):
    """Rank sectors using flow thresholds + WoW delta from history."""
    df = current_df.copy()

    # WoW delta: compare BOTH flows to previous week (regime doesn't matter for dead cat bounce detection)
    # History column mapping: Rotation Flow Monthly = Offense, Quality Flow Monthly = Defense
    df["Offense WoW"] = float('nan')
    df["Defense WoW"] = float('nan')

    if history_df is not None and not history_df.empty and 'Archive Date' in history_df.columns and 'Name' in history_df.columns:
        latest_date = history_df['Archive Date'].dropna().max()
        if pd.notna(latest_date):
            prev_date = history_df[history_df['Archive Date'] < latest_date]['Archive Date'].dropna().max()
            if pd.notna(prev_date):
                prev_rows = history_df[history_df['Archive Date'] == prev_date]

                # Offense WoW: current Offense Flow vs previous Rotation Flow Monthly
                if 'Rotation Flow Monthly' in history_df.columns and 'Offense Flow Monthly' in df.columns:
                    prev_offense = dict(zip(prev_rows['Name'], prev_rows['Rotation Flow Monthly']))
                    df["Offense WoW"] = df.apply(
                        lambda row: row['Offense Flow Monthly'] - prev_offense.get(row.get('Name', ''), float('nan'))
                        if pd.notna(prev_offense.get(row.get('Name', ''), float('nan'))) else float('nan'), axis=1
                    )

                # Defense WoW: current Defense Flow vs previous Quality Flow Monthly
                if 'Quality Flow Monthly' in history_df.columns and 'Defense Flow Monthly' in df.columns:
                    prev_defense = dict(zip(prev_rows['Name'], prev_rows['Quality Flow Monthly']))
                    df["Defense WoW"] = df.apply(
                        lambda row: row['Defense Flow Monthly'] - prev_defense.get(row.get('Name', ''), float('nan'))
                        if pd.notna(prev_defense.get(row.get('Name', ''), float('nan'))) else float('nan'), axis=1
                    )

    # Keep legacy wow_col for compatibility, use active flow's WoW
    wow_col = "Offense WoW" if "Offense" in active_flow else "Defense WoW"

    buy_gauge_col = "Buy Gauge" if "Buy Gauge" in df.columns else None
    strength_col  = "Sector Strength" if "Sector Strength" in df.columns else None

    if active_flow not in df.columns:
        return {"strong_buys": pd.DataFrame(), "improving": pd.DataFrame(),
                "trap": pd.DataFrame(), "extreme_sell": pd.DataFrame(), "full": df}

    strong_buy_gauges = ["Buy", "ðŸ† Holy Grail", "Holy Grail", "ðŸš€ Reversal", "Reversal"]

    # Ensure active_flow column is numeric
    df[active_flow] = pd.to_numeric(df[active_flow], errors='coerce')

    strong_buys = df[df[active_flow] > 0.05].copy()
    if buy_gauge_col:
        mask = strong_buys[buy_gauge_col].apply(lambda x: any(g in str(x) for g in strong_buy_gauges))
        strong_buys = strong_buys[mask].copy()
    if not strong_buys.empty:
        strong_buys = strong_buys.nlargest(10, active_flow)
    else:
        strong_buys = pd.DataFrame(columns=df.columns)

    improving = df[
        (df[active_flow] > 0.05) &
        (df[strength_col] == "Improving" if strength_col else True)
    ].nlargest(8, active_flow) if strength_col else pd.DataFrame()

    trap = df[df[buy_gauge_col].str.contains("Trap", na=False)].copy() if buy_gauge_col else pd.DataFrame()

    # Different thresholds for potential bottom signals: Offense < -0.25, Defense < -0.20
    extreme_threshold = -0.20 if "defense" in active_flow.lower() else -0.25
    extreme_sell = df[df[active_flow] < extreme_threshold].nsmallest(10, active_flow)

    full = df.sort_values(active_flow, ascending=False) if active_flow in df.columns else df

    return {"strong_buys": strong_buys, "improving": improving,
            "trap": trap, "extreme_sell": extreme_sell, "full": full,
            "wow_col": wow_col, "active_flow": active_flow}

# â”€â”€â”€ FLOW FALLBACK (history sheet when IMPORTHTML is blocked) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def has_valid_flow(df, flow_col):
    if df is None or df.empty or flow_col not in df.columns:
        return False
    return df[flow_col].notna().any()

def flow_curr_from_history(flow_hist):
    """Use most recent history archive as current sheet fallback when Finviz IMPORTHTML is blocked."""
    if flow_hist is None or flow_hist.empty or 'Archive Date' not in flow_hist.columns:
        return None
    latest = flow_hist['Archive Date'].dropna().max()
    if pd.isna(latest):
        return None
    snapshot = flow_hist[flow_hist['Archive Date'] == latest].copy()
    # Rename history columns to match expected current-sheet column names
    snapshot = snapshot.rename(columns={
        'Rotation Flow Monthly': 'Offense Flow Monthly',
        'Quality Flow Monthly':  'Defense Flow Monthly',
    })
    for col in ['Offense Flow Monthly', 'Defense Flow Monthly', 'Rotation Flow Weekly']:
        if col in snapshot.columns:
            snapshot[col] = pd.to_numeric(snapshot[col], errors='coerce')
    print(f"  [FALLBACK] Using history archive {latest.strftime('%Y-%m-%d')} â€” {len(snapshot)} industries")
    return snapshot

# â”€â”€â”€ NARRATIVE GENERATION â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def generate_executive_summary(regime, breadth, conviction, sectors, dxy_obj, y10_obj, hy_obj, v_D=None, v_Y=None, spec_count=None):
    regime_name = regime["name"] if regime else "Unknown"
    conv_score  = round(conviction["score"]) if conviction else 0
    direction   = conviction["direction"] if conviction else "NEUTRAL"
    align_label = conviction["align_label"] if conviction else ""
    is_risk_on  = regime["risk_on"] if regime else False

    # â”€â”€ Paragraph 1: Rate-of-change reality (what's moving and why it matters) â”€â”€
    lines = []
    if y10_obj and v_Y:
        y_now  = y10_obj["current"]
        y_1m   = bps_diff(y_now, y10_obj["one_month_ago"])
        y_3m   = bps_diff(y_now, y10_obj["three_months_ago"])
        accel_y = v_Y.get("accel_recent", 0)
        if y_now >= 5.0:
            lvl = "a restrictive zone for rate-sensitive assets"
        elif y_now >= 4.5:
            lvl = "a headwind for housing, long-duration equities, and credit-sensitive assets"
        elif y_now >= 4.0:
            lvl = "elevated but not crisis-level"
        else:
            lvl = "a more supportive level for broad risk assets"
        mom = "and accelerating" if accel_y > (v_Y.get("thresh", 2)) else ("and decelerating" if accel_y < -(v_Y.get("thresh", 2)) else "with momentum stabilizing")
        if y_1m is not None:
            lines.append(f"The 10Y yield is at <strong>{y_now:.2f}%</strong>. {lvl.capitalize()}. "
                        f"Yields moved {y_1m:+.0f}bps in the past month {mom} "
                        f"({y_3m:+.0f}bps vs. 3M).")

    if dxy_obj and v_D:
        d_now  = dxy_obj["current"]
        d_1m   = pct_chg(d_now, dxy_obj["one_month_ago"])
        accel_d = v_D.get("accel_recent", 0)
        if d_1m is not None:
            moving = "strengthening" if d_1m > 0 else "weakening"
            accel_desc = ("and accelerating" if (d_1m > 0 and accel_d > 0)
                         else "and accelerating" if (d_1m < 0 and accel_d < 0)
                         else "but losing momentum")
            consequence = ("a headwind for global liquidity-sensitive assets" if d_1m > 0
                          else "a liquidity tailwind for risk assets, EM, and hard assets")
            lines.append(f"The dollar (DXY {d_now:.2f}) is {moving} {d_1m:+.1f}% on the month {accel_desc}, "
                        f"{consequence}.")

    if hy_obj:
        h_now = hy_obj["current"]
        h_1m  = abs_diff(h_now, hy_obj["one_month_ago"])
        h_3m  = abs_diff(h_now, hy_obj["three_months_ago"])
        if h_1m is not None:
            direction_hy = "widening" if h_1m > 0 else "tightening"
            if h_now < 3.5:
                lvl_hy = "tight; credit is not confirming stress, but spread normalization risk remains"
            elif h_now < 4.25:
                lvl_hy = "a normal range for credit conditions"
            elif h_now < 5.0:
                lvl_hy = "near a stress threshold"
            else:
                lvl_hy = "a high-stress credit regime"
            lines.append(f"HY credit at <strong>{h_now:.2f}%</strong> is {direction_hy} "
                        f"({h_1m:+.2f}pp vs. 1M, {h_3m:+.2f}pp vs. 3M). {lvl_hy.capitalize()}.")

    if spec_count is not None:
        sz = spec_zone(spec_count)
        lines.append(f"Speculative momentum: <strong>{spec_count} stocks</strong> above the momentum threshold "
                    f"({sz['zone']} zone). {sz['note']}")

    p1 = " ".join(lines) if lines else "Macro indicators unavailable â€” running on partial data."

    # â”€â”€ Paragraph 2: Regime verdict + conviction (the summary call) â”€â”€
    b_title = breadth["title"] if breadth else "Breadth unavailable"
    b_sub   = breadth.get("sub", "") if breadth else ""

    if is_risk_on:
        stance = (f"Current positioning bias is <strong>offense</strong>. {regime['desc']} "
                 f"Favor the strongest groups while conditions remain aligned.")
    else:
        stance = (f"Current positioning bias is <strong>defense</strong>. {regime['desc']} "
                 f"Favor selectivity, smaller size, and confirmation from breadth/credit.")

    align_insight = ""
    if "STRONG" in align_label:
        align_insight = " All five signal inputs are aligned, which increases confidence in the read."
    elif "CONFLICTING" in align_label:
        align_insight = " Signals are mixed, so sizing should be more selective until the inputs resolve."
    elif "INSUFFICIENT" in align_label:
        align_insight = " Data is incomplete, so treat the read as lower confidence."

    p2 = (f"Regime: <strong>{regime_name}</strong> | Conviction: <strong>{conv_score}%</strong> {direction}{align_insight} "
          f"{stance} Breadth: <strong>{b_title}</strong>. {b_sub}")

    # â”€â”€ Paragraph 3: Sector rotation â€” the actionable edge â”€â”€
    active_flow = sectors.get("active_flow", "") if sectors else ""
    flow_type = "offense" if "Offense" in active_flow else "defense"
    top_names, trap_names, sell_names = [], [], []

    if sectors:
        name_col_fn = lambda df: "Name" if "Name" in df.columns else (df.columns[0] if not df.empty else None)
        if not sectors["strong_buys"].empty:
            nc = name_col_fn(sectors["strong_buys"])
            if nc: top_names = sectors["strong_buys"][nc].head(5).tolist()
        if not sectors.get("trap", pd.DataFrame()).empty:
            nc = name_col_fn(sectors["trap"])
            if nc: trap_names = sectors["trap"][nc].head(3).tolist()
        if not sectors["extreme_sell"].empty:
            nc = name_col_fn(sectors["extreme_sell"])
            if nc: sell_names = sectors["extreme_sell"][nc].head(3).tolist()

    if top_names:
        p3 = (f"Capital rotation ({flow_type} flow signal): "
              f"<strong>{', '.join(top_names[:3])}</strong> show the strongest 2nd-derivative acceleration. "
              f"These groups show the strongest recent improvement in the active flow signal.")
        if trap_names:
            p3 += (f" Trap warning: <strong>{', '.join(trap_names)}</strong> show weaker flow confirmation despite price strength.")
        if sell_names:
            p3 += (f" Extreme selling screen: <strong>{', '.join(sell_names)}</strong>; treat as watchlist only until price and breadth confirm.")
    else:
        p3 = (f"Sector rotation data is limited to macro framework. "
              f"In a {regime_name} regime, bias toward: {', '.join(regime['plays']) if regime else 'N/A'}.")

    p1 = " ".join(p1.split())
    p2 = " ".join(p2.split())
    p3 = " ".join(p3.split())

    return (f'<p style="margin:0 0 14px;line-height:1.75">{p1}</p>'
            f'<p style="margin:0 0 14px;line-height:1.75">{p2}</p>'
            f'<p style="margin:0;line-height:1.75">{p3}</p>')

def generate_risk_scenarios(regime, dxy_obj, y10_obj, hy_obj):
    regime_name = regime["name"] if regime else "Unknown"
    risk_on = regime["risk_on"] if regime else False

    if risk_on:
        bull = (f"<li>DXY continues falling â€” further dollar weakness amplifies the liquidity impulse</li>"
                f"<li>HY spreads remain tight or tighten further â€” credit confirms risk appetite</li>"
                f"<li>Breadth expands with more stocks joining the advance â€” broad participation</li>")
        bear = (f"<li>DXY reversal above threshold would signal regime shift toward Dollar Tightening or Global Stress</li>"
                f"<li>HY spreads widening above 4.25% would trigger Credit Stress pattern</li>"
                f"<li>Breadth deterioration (25%Q divergence) as price makes new highs = topping signal</li>")
    else:
        bull = (f"<li>DXY reversal (falling) combined with stable/falling yields = shift toward Goldilocks</li>"
                f"<li>HY spreads tightening from current levels â€” credit improvement precedes equity recovery</li>"
                f"<li>Breadth reaching Capitulation zone (25%Q Up < 200) = powerful mean-reversion setup</li>")
        bear = (f"<li>DXY + yield acceleration in current direction deepens the {regime_name} regime</li>"
                f"<li>HY spreads widening through 5.0% would confirm Crisis trajectory</li>"
                f"<li>Breadth thrust to the downside (10D ratio < 0.5) would signal institutional distribution</li>")

    return bull, bear

# â”€â”€â”€ HTML GENERATION â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

CSS = """
<style>
:root {
  --bg:#07111d; --bg2:#0e1522; --bg3:#1a2234;
  --border:rgba(148,166,203,.16); --border2:rgba(148,166,203,.10);
  --t1:#c5cfe0; --t2:#94a3b8; --t3:#636d88;
  --green:#3dd9a0; --teal:#36d6c4; --blue:#5b9cf5;
  --purple:#a385f0; --amber:#f2c04e; --orange:#f09848; --red:#f27872;
  --font:'Segoe UI',system-ui,-apple-system,sans-serif;
  --mono:'Cascadia Code','Consolas','Courier New',monospace;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--t1);font-family:var(--font);font-size:14px;line-height:1.5;padding:20px 16px 48px}
a{color:var(--blue)}
h1{font-size:1.5rem;font-weight:800;letter-spacing:-.02em}
h2{font-size:1rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--t3);margin-bottom:12px}
h3{font-size:.9rem;font-weight:700;color:var(--t2);margin-bottom:8px}
.wrap{max-width:1200px;margin:0 auto}
.header{margin-bottom:24px;padding-bottom:16px;border-bottom:1px solid var(--border)}
.header-pills{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px;align-items:center}
.pill{display:inline-flex;align-items:center;gap:5px;font-size:.75rem;font-weight:700;padding:4px 12px;border-radius:999px;border:1px solid currentColor}
.subtitle{color:var(--t3);font-size:.8rem;margin-top:4px}
.section{margin-bottom:28px}
.card-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:14px}
@media(max-width:1050px){.card-grid{grid-template-columns:repeat(2,1fr)}}
@media(max-width:700px){.card-grid{grid-template-columns:1fr}}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:16px}
.card-wide{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:16px;margin-bottom:14px}
.metric-label{font-size:.65rem;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:var(--t3);margin-bottom:4px}
.metric-big{font-size:1.6rem;font-weight:800;font-family:var(--mono);line-height:1}
.metric-note{font-size:.68rem;color:var(--t3);margin-top:5px;line-height:1.35}
.metric-row{display:flex;gap:12px;margin-top:8px;font-size:.75rem}
.metric-item{flex:1;background:rgba(0,0,0,.2);border-radius:6px;padding:6px 8px;position:relative}
.metric-item[data-date]{cursor:help}
.metric-item[data-date]::after{content:attr(data-date);position:absolute;bottom:calc(100% + 6px);left:50%;transform:translateX(-50%);background:#0f172a;border:1px solid #3b82f6;color:#93c5fd;font-size:.62rem;font-weight:600;white-space:nowrap;padding:4px 9px;border-radius:5px;pointer-events:none;opacity:0;transition:opacity .15s;z-index:100}
.metric-item[data-date]:hover::after{opacity:1}
.metric-item-label{color:var(--t3);font-size:.62rem;margin-bottom:2px}
.vel-bar-wrap{margin-top:10px}
.vel-row{display:flex;align-items:center;gap:8px;margin-bottom:4px;font-size:.68rem}
.vel-label{width:22px;color:var(--t3);font-family:var(--mono)}
.vel-bar-bg{flex:1;height:6px;background:rgba(255,255,255,.07);border-radius:3px;overflow:hidden}
.vel-bar{height:100%;border-radius:3px;transition:width .4s}
.vel-val{width:90px;text-align:right;font-family:var(--mono);font-size:.66rem}
.accel-badge{display:inline-block;font-size:.68rem;font-weight:700;padding:3px 8px;border-radius:4px;background:rgba(255,255,255,.07);margin-top:6px}
.threshold-note{text-align:center;font-size:.63rem;color:var(--t3);margin:-2px 0 14px}
.threshold-note span{display:inline-block;background:var(--bg2);border:1px solid var(--border);border-radius:4px;padding:2px 7px;margin:2px;color:var(--t2)}
.regime-matrix{display:grid;grid-template-columns:auto repeat(3,1fr);gap:5px;font-size:.68rem;margin-top:14px}
.rm-cell{background:var(--bg3);border:1px solid var(--border2);border-radius:8px;padding:9px;text-align:left;min-height:118px;display:flex;flex-direction:column;gap:5px}
.rm-cell.active{border-width:2px;font-weight:700}
.rm-cell-name{font-size:.72rem;font-weight:800;color:var(--t1);line-height:1.25}
.rm-cell-desc{font-size:.62rem;color:var(--t3);line-height:1.35;flex:1}
.rm-cell-plays{display:flex;flex-wrap:wrap;gap:3px;margin-top:2px}
.rm-play{font-size:.57rem;padding:2px 5px;border-radius:3px;background:rgba(255,255,255,.05);color:var(--t2);border:1px solid rgba(255,255,255,.07)}
.rm-header{color:var(--t3);font-size:.6rem;display:flex;align-items:center;justify-content:center}
.rm-corner{background:transparent;border:none}
.conv-section{margin-top:14px}
.conv-row{display:flex;align-items:center;gap:10px;margin-bottom:6px;font-size:.75rem}
.conv-label{width:60px;color:var(--t3);font-family:var(--mono);font-size:.68rem}
.conv-bar-bg{flex:1;height:8px;background:rgba(255,255,255,.07);border-radius:4px;overflow:hidden}
.conv-bar{height:100%;border-radius:4px}
.conv-val{width:45px;text-align:right;font-family:var(--mono);font-weight:700;font-size:.72rem}
.conv-total{display:flex;align-items:center;gap:10px;padding:10px 0;border-top:1px solid var(--border);margin-top:8px}
.conv-score{font-size:1.4rem;font-weight:800;font-family:var(--mono)}
.align-box{background:rgba(0,0,0,.2);border-radius:6px;padding:6px 10px;font-size:.72rem;margin-top:6px}
.breadth-phase{display:flex;align-items:flex-start;gap:10px;padding:12px 14px;border-radius:8px;margin-bottom:12px;border:1px solid var(--border)}
.phase-icon{font-size:1.6rem;line-height:1}
.phase-title{font-weight:700;font-size:.9rem}
.phase-sub{color:var(--t2);font-size:.72rem;margin-top:2px}
.breadth-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:10px}
@media(max-width:700px){.breadth-grid{grid-template-columns:repeat(2,1fr)}}
.bg-cell{background:var(--bg3);border:1px solid var(--border2);border-radius:6px;padding:8px 10px}
a.bg-cell{display:block;text-decoration:none;color:inherit;transition:border-color .15s ease,transform .15s ease}
a.bg-cell:hover{border-color:var(--blue);transform:translateY(-1px)}
.bg-label{font-size:.62rem;color:var(--t3);text-transform:uppercase;letter-spacing:.06em;margin-bottom:3px}
.bg-val{font-size:1.1rem;font-weight:700;font-family:var(--mono)}
.bg-note{font-size:.62rem;color:var(--t2);margin-top:2px}
table{width:100%;border-collapse:collapse;font-size:.78rem}
th{background:rgba(0,0,0,.3);color:var(--t3);font-size:.65rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;padding:7px 10px;text-align:left;border-bottom:1px solid var(--border)}
th.sortable{cursor:pointer;user-select:none;white-space:nowrap}
th.sortable::after{content:"\\2195";font-size:.58rem;color:var(--t3);margin-left:5px;opacity:.65}
th.sortable.sort-asc::after{content:"\\2191";color:var(--blue);opacity:1}
th.sortable.sort-desc::after{content:"\\2193";color:var(--blue);opacity:1}
td{padding:6px 10px;border-bottom:1px solid var(--border2);vertical-align:middle}
tr:hover td{background:rgba(255,255,255,.03)}
.table-wrap{background:var(--bg2);border:1px solid var(--border);border-radius:10px;overflow:hidden;margin-bottom:14px}
.table-title{padding:10px 14px;font-size:.75rem;font-weight:700;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:6px}
.scenario-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px}
@media(max-width:600px){.scenario-grid{grid-template-columns:1fr}}
.scenario-card{background:var(--bg2);border-radius:10px;padding:14px 16px}
.scenario-card ul{padding-left:18px;margin-top:8px}
.scenario-card li{margin-bottom:6px;font-size:.78rem;color:var(--t2);line-height:1.4}
.exec-card{background:var(--bg2);border-left:3px solid var(--blue);border-radius:0 10px 10px 0;padding:16px 18px;margin-bottom:14px;font-size:.85rem;line-height:1.7;color:var(--t2)}
.trade-card{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:14px 16px;margin-bottom:14px}
.play-tag{display:inline-block;background:rgba(255,255,255,.07);border-radius:4px;padding:3px 10px;font-size:.72rem;font-weight:700;margin:3px 3px 0 0}
details summary{cursor:pointer;padding:10px 14px;font-size:.75rem;font-weight:700;color:var(--t2);border-bottom:1px solid var(--border);list-style:none}
details summary::before{content:"\\25B6 ";font-size:.6rem}
details[open] summary::before{content:"\\25BC "}
details.flow-details{margin-bottom:28px}
details.flow-details>summary{background:var(--bg2);border:1px solid var(--border);border-radius:10px;display:flex;align-items:center;justify-content:space-between;gap:12px}
details.flow-details>summary::after{content:"Expand";font-size:.65rem;color:var(--t3);font-weight:700;text-transform:uppercase;letter-spacing:.08em}
details.flow-details[open]>summary{border-radius:10px 10px 0 0;border-bottom-color:transparent}
details.flow-details[open]>summary::after{content:"Collapse"}
.flow-details-body{border:1px solid var(--border);border-top:none;border-radius:0 0 10px 10px;padding:14px;background:rgba(15,23,42,.35)}
.footer{text-align:center;color:var(--t3);font-size:.68rem;margin-top:32px;padding-top:14px;border-top:1px solid var(--border)}
.up{color:var(--red)}
.down{color:var(--green)}
.flat{color:var(--t3)}
.num{font-family:var(--mono)}
</style>
"""

def _conviction_color(v):
    if v is None: return "#636d88"
    if v < 25:  return "#636d88"
    if v < 50:  return "#f2c04e"
    if v < 75:  return "#f09848"
    return "#f27872"

def section_header(report_date, regime, conviction, breadth_phase):
    regime_name  = regime["name"]  if regime else "Insufficient Data"
    regime_color = regime["color"] if regime else "#636d88"
    regime_icon  = regime["icon"]  if regime else "?"
    conv_score   = round(conviction["score"]) if conviction else 0
    direction    = conviction["direction"]    if conviction else "NEUTRAL"
    conv_color   = _conviction_color(conv_score)
    b_title      = breadth_phase["title"] if breadth_phase else "â€”"
    b_color      = breadth_phase["color"] if breadth_phase else "#636d88"

    return f"""
<div class="header">
  <div style="color:var(--t3);font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.1em">Market Watcher Daily Brief</div>
  <h1 style="margin-top:4px">{regime_icon} {regime_name}</h1>
  <div class="subtitle">{report_date} &nbsp;|&nbsp; Macro Regime Ã— Sector Flow Ã— Breadth Analysis</div>
  <div class="header-pills" style="margin-top:12px">
    <span class="pill" style="color:{regime_color};border-color:{regime_color}">{regime_icon} {regime_name}</span>
    <span class="pill" style="color:{conv_color};border-color:{conv_color}">{conv_score}% Conviction Â· {direction}</span>
    <span class="pill" style="color:{b_color};border-color:{b_color}">{b_title}</span>
  </div>
</div>"""

def section_executive_summary(summary_html, regime):
    accent = regime["color"] if regime else "#5b9cf5"
    return f"""
<div class="section">
  <h2>Executive Summary</h2>
  <div class="exec-card" style="border-color:{accent}">{summary_html}</div>
</div>"""

def _indicator_card(label, obj, change1, change3, vel, is_yield, unit="", is_hy=False):
    if obj is None:
        return f'<div class="card"><div class="metric-label">{label}</div><div style="color:var(--t3);margin-top:8px">Unavailable</div></div>'
    cur = obj["current"]
    cur_fmt = f"{cur:.2f}{'%' if is_hy else unit}"

    def date_attr(date_str):
        return f' data-date="{html_lib.escape("Date: " + date_str, quote=True)}"' if date_str else ""

    def chg_html(v, is_y, is_h):
        if v is None: return "<span style='color:var(--t3)'>â€”</span>"
        if is_h:   s = f"{v:+.2f}pp"
        elif is_y: s = f"{v:+.0f}bps"
        else:      s = f"{v:+.2f}%"
        color = "#f27872" if v > 0 else ("#3dd9a0" if v < 0 else "#636d88")
        if is_h and v > 0: color = "#f27872"  # widening = bad
        if is_h and v < 0: color = "#3dd9a0"  # tightening = good
        return f'<span style="color:{color};font-family:var(--mono)">{s}</span>'

    bars = ""
    if vel:
        sc = max(vel["stdev"] * 2, 10 if is_yield else 0.5)
        for sfx, v in [("1M", vel["V1"]), ("3M", vel["V2"]), ("6M", vel.get("V3"))]:
            if v is None: continue
            pct = min(100, (abs(v) / sc) * 100) if sc > 0 else 0
            bar_color = "#f27872" if v > 0 else "#3dd9a0"
            u = "bps/mo" if is_yield else "%/mo"
            bars += f"""
<div class="vel-row">
  <div class="vel-label">{sfx}</div>
  <div class="vel-bar-bg"><div class="vel-bar" style="width:{pct:.0f}%;background:{bar_color}"></div></div>
  <div class="vel-val" style="color:{bar_color}">{v:+.1f}{u}</div>
</div>"""
    accel = accel_label(vel, is_yield) if vel else "â€”"

    return f"""
<div class="card">
  <div class="metric-label">{label}</div>
  <div class="metric-row">
    <div class="metric-item"{date_attr(obj.get("now_str"))}><div class="metric-item-label">Current</div><span style="font-family:var(--mono);font-weight:700">{cur_fmt}</span></div>
    <div class="metric-item"{date_attr(obj.get("m1_str"))}><div class="metric-item-label">1M chg</div>{chg_html(change1, is_yield, is_hy)}</div>
    <div class="metric-item"{date_attr(obj.get("m3_str"))}><div class="metric-item-label">3M chg</div>{chg_html(change3, is_yield, is_hy)}</div>
  </div>
  <div class="vel-bar-wrap">{bars}</div>
  <div class="accel-badge">{accel}</div>
</div>"""

def _spec_momentum_card(spec_count):
    if spec_count is None:
        return '<div class="card"><div class="metric-label">Spec Momentum</div><div style="color:var(--t3);margin-top:8px">Unavailable</div></div>'
    sz = spec_zone(spec_count)
    return f"""
<div class="card">
  <div class="metric-label">Spec Momentum</div>
  <div class="metric-big" style="color:{sz['color']}">{spec_count}</div>
  <div class="metric-note">US stocks &gt;$3, avg vol &gt;50K, +20%/week</div>
  <div class="metric-row">
    <div class="metric-item"><div class="metric-item-label">Zone</div><span style="color:{sz['color']};font-weight:700">{sz['zone']}</span></div>
    <div class="metric-item"><div class="metric-item-label">Signal</div><span style="color:{sz['color']};font-weight:700">{sz['signal']}</span></div>
  </div>
  <div class="metric-note">{sz['note']}</div>
</div>"""

def _regime_matrix_html(regime):
    active_id = regime["id"] if regime else None
    rows = [
        ("Yields Down", "ydown"),
        ("Yields Flat", "yflat"),
        ("Yields Up", "yup"),
    ]
    cols = [
        ("$ Rising", "dup"),
        ("$ Stable", "dflat"),
        ("$ Falling", "ddown"),
    ]
    html = '<div class="regime-matrix">'
    html += '<div class="rm-corner"></div>'
    for col_label, _ in cols:
        html += f'<div class="rm-header">{col_label}</div>'
    for row_label, y_key in rows:
        html += f'<div class="rm-header" style="justify-content:flex-start;padding-left:4px">{row_label}</div>'
        for _, d_key in cols:
            cell_id = f"cell-{y_key}-{d_key}"
            reg = REGIMES.get(cell_id, {})
            is_active = cell_id == active_id
            color = reg.get("color", "#636d88")
            bg = f"background:{reg.get('bg','rgba(30,41,59,.3)')};border-color:{color};" if is_active else ""
            fw = "font-weight:700;" if is_active else "color:var(--t3);"
            plays_html = "".join(f'<span class="rm-play">{html_lib.escape(str(p))}</span>' for p in reg.get("plays", [])[:3])
            html += f"""
<div class="rm-cell {"active" if is_active else ""}" style="{bg}{fw}">
  <div class="rm-cell-name">{reg.get("icon","?")} {html_lib.escape(reg.get("name",""))}</div>
  <div class="rm-cell-desc">{html_lib.escape(reg.get("desc",""))}</div>
  <div class="rm-cell-plays">{plays_html}</div>
</div>"""
    html += '</div>'
    return html

def section_macro_regime(dxy_obj, y10_obj, hy_obj, v_D, v_Y, v_H, regime, conviction, stress, spec_count=None):
    dd1 = pct_chg(dxy_obj["current"], dxy_obj["one_month_ago"]) if dxy_obj else None
    dd3 = pct_chg(dxy_obj["current"], dxy_obj["three_months_ago"]) if dxy_obj else None
    yd1 = bps_diff(y10_obj["current"], y10_obj["one_month_ago"]) if y10_obj else None
    yd3 = bps_diff(y10_obj["current"], y10_obj["three_months_ago"]) if y10_obj else None
    hd1 = abs_diff(hy_obj["current"], hy_obj["one_month_ago"]) if hy_obj else None
    hd3 = abs_diff(hy_obj["current"], hy_obj["three_months_ago"]) if hy_obj else None

    cards = (
        _indicator_card("DXY (Dollar Index)", dxy_obj, dd1, dd3, v_D, False) +
        _indicator_card("US 10Y Yield", y10_obj, yd1, yd3, v_Y, True, "%") +
        _indicator_card("HY Credit Spread", hy_obj, hd1, hd3, v_H, True, "%", is_hy=True) +
        _spec_momentum_card(spec_count)
    )
    t_D = max(v_D["stdev"], 0.15) if v_D else DXY_THRESH
    t_Y = max(v_Y["stdev"], 2.0) if v_Y else Y10_THRESH
    t_H = max(v_H["stdev"] / 100, 0.02) if v_H else HY_THRESH
    threshold_html = f"""
  <div class="threshold-note">
    Thresholds:
    <span>$ Rising &gt;+{t_D:.2f}%</span>
    <span>$ Falling &lt;-{t_D:.2f}%</span>
    <span>Yield Rising &gt;+{t_Y:.1f}bps</span>
    <span>Yield Falling &lt;-{t_Y:.1f}bps</span>
    <span>HY Wide &gt;+{(t_H * 100):.1f}bps</span>
    <em>(data-derived)</em>
  </div>"""

    # Conviction breakdown
    conv_html = ""
    if conviction:
        comp = conviction["components"]
        labels = {"dxy": "DXY", "y10": "Y10", "hy": "HY", "spec": "Spec", "breadth": "Brdth"}
        bars_html = ""
        for key, label in labels.items():
            v = comp.get(key)
            color = _conviction_color(v)
            pct = v if v is not None else 0
            val_html = f'<span class="conv-val" style="color:{color}">{v:.0f}%</span>' if v is not None else '<span class="conv-val" style="color:var(--t3)">N/A</span>'
            bars_html += f"""
<div class="conv-row">
  <div class="conv-label">{label}</div>
  <div class="conv-bar-bg"><div class="conv-bar" style="width:{pct:.0f}%;background:{color}"></div></div>
  {val_html}
</div>"""
        total_color = _conviction_color(conviction["score"])
        conv_html = f"""
<div class="conv-section">
  <h3>5-Component Conviction</h3>
  {bars_html}
  <div class="conv-total">
    <div class="conv-score" style="color:{total_color}">{conviction['score']:.0f}%</div>
    <div style="font-size:.72rem;color:var(--t2)">{conviction['score']:.0f}% Ã— {conviction['align_mult']:.2f}x align = <strong style="color:{total_color}">{conviction['direction']}</strong></div>
  </div>
  <div class="align-box" style="border-left:3px solid {conviction['align_color']}">
    <span style="font-size:.85rem">{conviction['align_icon']}</span>
    <strong style="color:{conviction['align_color']};margin-left:6px">{conviction['align_label']}</strong>
    <div style="color:var(--t3);margin-top:2px">{conviction['align_detail']}</div>
  </div>
</div>"""

    return f"""
<div class="section">
  <h2>Macro Regime</h2>
  <div class="card-grid">{cards}</div>
  {threshold_html}
  <div class="card-wide">
    {_regime_matrix_html(regime)}
    {conv_html}
  </div>
</div>"""

def section_breadth(breadth_rows, phase):
    if not breadth_rows or not phase:
        return '<div class="section"><h2>Market Breadth</h2><div class="card-wide" style="color:var(--t3)">Breadth data unavailable.</div></div>'
    t = breadth_rows[-1]
    net3413 = t["b3413"] - t["s3413"]
    def bg_cell(label, val_html, note_html="", href=None):
        tag = "a" if href else "div"
        attr = f' href="{href}" target="_blank" rel="noopener"' if href else ""
        return f'<{tag} class="bg-cell"{attr}><div class="bg-label">{label}</div><div class="bg-val">{val_html}</div><div class="bg-note">{note_html}</div></{tag}>'
    up4c = "Extreme" if t["up4"]>=1000 else ("Very High" if t["up4"]>=500 else ("High" if t["up4"]>=300 else "Normal"))
    up4col = "#a385f0" if t["up4"]>=1000 else ("#3dd9a0" if t["up4"]>=500 else ("#f2c04e" if t["up4"]>=300 else "#a0a8be"))
    dn4c = "Extreme" if t["dn4"]>=1000 else ("Very High" if t["dn4"]>=500 else ("High" if t["dn4"]>=300 else "Normal"))
    dn4col = "#f5a0a0" if t["dn4"]>=1000 else ("#f27872" if t["dn4"]>=500 else ("#f2c04e" if t["dn4"]>=300 else "#a0a8be"))
    r10c = "Bullish thrust" if t["r10"]>=2 else ("Bearish thrust" if t["r10"]<=0.5 else ("Bullish" if t["r10"]>=1.5 else ("Bearish" if t["r10"]<=0.75 else "Neutral")))
    r10col = "#3dd9a0" if t["r10"]>=2 else ("#f27872" if t["r10"]<=0.5 else ("#6aedb8" if t["r10"]>=1.5 else ("#f5a0a0" if t["r10"]<=0.75 else "#a0a8be")))
    bull = t["q25u"] > t["q25d"]
    q25u_col = "#a385f0" if t["q25u"]<200 else ("#3dd9a0" if bull else "#f27872")
    net_col = "#3dd9a0" if net3413>0 else ("#f27872" if net3413<0 else "#a0a8be")
    t2108c = "Bottoming zone" if t["t2108"]<=20 else ("Overbought" if t["t2108"]>=80 else ("Healthy" if t["t2108"]>=60 else "Below avg"))
    t2108col = "#a385f0" if t["t2108"]<=20 else ("#f2c04e" if t["t2108"]>=80 else ("#3dd9a0" if t["t2108"]>=60 else "#a0a8be"))

    grid = (
        bg_cell("4% Up",   f'<span style="color:{up4col}">{int(t["up4"]):,}</span>', f'{up4c} buying') +
        bg_cell("4% Down",  f'<span style="color:{dn4col}">{int(t["dn4"]):,}</span>', f'{dn4c} selling') +
        bg_cell("10D Ratio",f'<span style="color:{r10col}">{t["r10"]:.2f}</span>', r10c) +
        bg_cell("5D Ratio", f'<span style="color:var(--t2)">{t["r5"]:.2f}</span>', "") +
        bg_cell("25%Q Up",  f'<span style="color:{q25u_col}">{int(t["q25u"]):,}</span>', "") +
        bg_cell("25%Q Down",f'<span style="color:var(--t2)">{int(t["q25d"]):,}</span>', "") +
        bg_cell("34/13 Net",f'<span style="color:{net_col}">{net3413:+,.0f}</span>', "Positive mom" if net3413>0 else ("Negative mom" if net3413<0 else "Flat")) +
        bg_cell("T2108 %",  f'<span style="color:{t2108col}">{t["t2108"]:.1f}%</span>', f"{t2108c} Â· view chart", "stockbee_t2108_graph.html")
    )

    return f"""
<div class="section">
  <h2>Market Breadth</h2>
  <div class="card-wide">
    <div class="breadth-phase" style="background:{phase['color']}18;border-color:{phase['color']}50">
      <div class="phase-icon">{phase["icon"]}</div>
      <div>
        <div class="phase-title" style="color:{phase['color']}">{phase["title"]}</div>
        <div class="phase-sub">{phase["sub"]}</div>
      </div>
    </div>
    <div class="breadth-grid">{grid}</div>
    <div style="font-size:.72rem;color:var(--t3);margin-top:8px">Date: {t["date"]}</div>
  </div>
</div>"""

def _clean_gauge(text):
    """Extract known Buy Gauge keywords, stripping emojis and other chars."""
    if pd.isna(text): return ""
    s = str(text)
    # Extract known keywords
    for keyword in ["Holy Grail", "Reversal", "Wait", "Avoid", "Trap"]:
        if keyword in s:
            return keyword
    return s.encode('ascii', 'ignore').decode('ascii').strip()

def _flow_table(df, active_flow, wow_col, title, color, cols_to_show, note=""):
    if df is None or df.empty:
        return f'<div class="table-wrap"><div class="table-title">{title}</div><div style="padding:12px;color:var(--t3);font-size:.78rem">No data matching criteria.</div></div>'

    # Filter to only columns that exist in the DataFrame
    cols_to_show = [c for c in cols_to_show if c in df.columns or c == "WoW Î”"]

    strength_colors = {"Leading":"#3dd9a0","Improving":"#5b9cf5","Weakening":"#f2c04e","Lagging":"#f27872"}
    gauge_colors = {"Holy Grail":"#f2c04e","Reversal":"#3dd9a0","Wait":"#636d88","Avoid":"#f27872","Trap":"#f09848"}

    def gauge_badge(val):
        if pd.isna(val) or str(val).strip() == "": return "â€”"
        s = _clean_gauge(val)  # Extract keyword, strip emojis
        if not s: return "â€”"
        for k, c in gauge_colors.items():
            if k in s:
                return _badge(s, c + "22", c)
        return s

    def strength_badge(val):
        if pd.isna(val) or str(val).strip() == "": return "â€”"
        s = str(val)
        c = strength_colors.get(s, "#636d88")
        return _badge(s, c + "22", c)

    headers = "".join(f'<th class="sortable">{c}</th>' for c in cols_to_show)
    rows_html = ""
    for _, row in df.iterrows():
        cells = ""
        for col in cols_to_show:
            if col == "Sector Strength":
                cells += f"<td>{strength_badge(row.get(col,''))}</td>"
            elif col == "Buy Gauge":
                cells += f"<td>{gauge_badge(row.get(col,''))}</td>"
            elif col in ["WoW Î”", "Offense WoW", "Defense WoW"]:
                v = row.get(col, float('nan'))
                if pd.isna(v): cells += "<td style='color:var(--t3)'>â€”</td>"
                else: cells += f"<td class='num' style='color:{'#3dd9a0' if v>0 else '#f27872'}'>{v:+.3f}</td>"
            elif col in ["Offense Flow Monthly", "Defense Flow Monthly"]:
                v = row.get(col, float('nan'))
                # Different thresholds: Offense < -0.25, Defense < -0.20
                extreme_thresh = -0.20 if "Defense" in col else -0.25
                color_val = "#3dd9a0" if (not pd.isna(v) and v > 0.05) else ("#f27872" if (not pd.isna(v) and v < extreme_thresh) else "#c5cfe0")
                cells += f"<td class='num' style='color:{color_val};font-weight:700'>{v:.3f}</td>" if not pd.isna(v) else "<td style='color:var(--t3)'>â€”</td>"
            elif col in ["Perf Week","Perf Month","Perf Quart","Perf YTD","Change"]:
                v = row.get(col, float('nan'))
                if pd.isna(v): cells += "<td style='color:var(--t3)'>â€”</td>"
                else: cells += f"<td class='num' style='color:{'#3dd9a0' if v>0 else '#f27872'}'>{v:+.1f}%</td>"
            else:
                cells += f"<td>{row.get(col,'â€”')}</td>"
        rows_html += f"<tr>{cells}</tr>"

    note_html = f'<div style="padding:8px 12px;font-size:.68rem;color:var(--t3)">{note}</div>' if note else ""
    return f"""
<div class="table-wrap">
  <div class="table-title" style="color:{color}">
    <span style="display:inline-block;width:8px;height:8px;background:{color};border-radius:50%;margin-right:4px"></span>
    {title}
  </div>
  {note_html}
  <table class="sortable-table"><thead><tr>{headers}</tr></thead><tbody>{rows_html}</tbody></table>
</div>"""

def section_sector_flow(sectors, active_flow):
    if not sectors:
        return '<div class="section"><h2>Sector Flow Analysis</h2><div style="color:var(--t3)">Flow data unavailable.</div></div>'
    wow = sectors.get("wow_col", "WoW Î”")
    flow_type = "Offense" if "Offense" in active_flow else "Defense"
    col_set = ["Name", "Offense Flow Monthly", "Defense Flow Monthly", "Offense WoW", "Defense WoW", "Sector Strength", "Buy Gauge", "Perf Week", "Perf Month"]

    sb = _flow_table(sectors["strong_buys"], active_flow, wow,
        f"Strong Buys â€” {flow_type} Flow > 0.05 + Buy Gauge", "#3dd9a0", col_set)
    imp = _flow_table(sectors["improving"], active_flow, wow,
        "Improving Watch â€” Building Momentum", "#5b9cf5", col_set,
        note="Sector Strength = Improving + Flow > 0.05. May be early in the move.")
    trap_html = ""
    if sectors["trap"] is not None and not sectors["trap"].empty:
        trap_html = _flow_table(sectors["trap"], active_flow, wow,
            "âš  Trap Warning â€” Looks Good, May Not Be", "#f09848", col_set,
            note="Buy Gauge shows Trap signal. Elevated risk of reversal despite surface-level strength.")

    return f"""
<details class="flow-details" open>
  <summary>Sector Flow Analysis</summary>
  <div class="flow-details-body">
  <div style="font-size:.72rem;color:var(--t3);margin-bottom:10px">
    Active signal: <strong style="color:#c5cfe0">{active_flow}</strong>
    (switches Offense â†” Defense based on macro regime)
  </div>
  {sb}{imp}{trap_html}
  </div>
</details>"""

def section_extreme_signals(sectors, active_flow):
    if not sectors:
        return ""
    extreme = sectors.get("extreme_sell", pd.DataFrame())
    wow = sectors.get("wow_col", "WoW Î”")
    col_set = ["Name", "Offense Flow Monthly", "Defense Flow Monthly", "Offense WoW", "Defense WoW", "Sector Strength", "Buy Gauge", "Perf Month", "Perf Quart"]
    # Different thresholds: Offense < -0.25, Defense < -0.20
    extreme_thresh = -0.20 if "defense" in active_flow.lower() else -0.25
    ext_html = _flow_table(extreme, active_flow, wow,
        f"Extreme Selling â€” {active_flow} < {extreme_thresh}", "#f27872", col_set,
        note="These industries show extreme capital outflow. Treat as a watchlist only; confirm with price, breadth, and macro before acting.")
    return f"""
<div class="section">
  <h2>Extreme Signals â€” Potential Bottoms / Dead Cat Bounces</h2>
  {ext_html}
</div>"""

def section_trade_framework(regime):
    if not regime:
        return ""
    plays = regime.get("plays", [])
    plays_html = "".join(f'<span class="play-tag">{p}</span>' for p in plays)
    return f"""
<div class="section">
  <h2>Trade Framework</h2>
  <div class="trade-card">
    <div style="font-size:.8rem;color:var(--t2);margin-bottom:8px">
      Based on <strong style="color:{regime['color']}">{regime['icon']} {regime['name']}</strong> regime â€” {regime['desc']}
    </div>
    <div style="margin-bottom:10px"><strong style="font-size:.8rem">Suggested positioning:</strong><br>{plays_html}</div>
    <div style="font-size:.72rem;color:var(--t3)">
      Confirm with HY spreads. Transitions between regime quadrants = largest risk/reward trades.
    </div>
  </div>
</div>"""

def section_risk_scenarios(bull_points, bear_points):
    return f"""
<div class="section">
  <h2>Risk Scenarios</h2>
  <div class="scenario-grid">
    <div class="scenario-card" style="border:1px solid rgba(61,217,160,.25)">
      <div style="font-weight:700;color:#3dd9a0">Bull Case</div>
      <ul>{bull_points}</ul>
    </div>
    <div class="scenario-card" style="border:1px solid rgba(242,120,114,.25)">
      <div style="font-weight:700;color:#f27872">Bear Case</div>
      <ul>{bear_points}</ul>
    </div>
  </div>
</div>"""

def section_full_table(df, active_flow):
    if df is None or df.empty:
        return ""
    wow_col = "WoW Î”" if "WoW Î”" in df.columns else None
    cols = ["Name", "Offense Flow Monthly", "Defense Flow Monthly", "Offense WoW", "Defense WoW"]
    cols += ["Sector Strength","Buy Gauge","Perf Week","Perf Month","Perf YTD"]
    cols = [c for c in cols if c in df.columns]
    rows_html = ""
    for i, (_, row) in enumerate(df.iterrows()):
        cells = f"<td style='color:var(--t3);font-size:.65rem'>{i+1}</td>"
        for col in cols:
            cells += _flow_table.__wrapped_cell__(row, col, active_flow, wow_col) if hasattr(_flow_table, '__wrapped_cell__') else f"<td>{row.get(col, 'â€”')}</td>"
        rows_html += f"<tr>{cells}</tr>"

    # Build table without the helper (inline logic)
    strength_colors = {"Leading":"#3dd9a0","Improving":"#5b9cf5","Weakening":"#f2c04e","Lagging":"#f27872"}
    gauge_colors = {"Holy Grail":"#f2c04e","Reversal":"#3dd9a0","Wait":"#636d88","Avoid":"#f27872","Trap":"#f09848"}
    rows_html = ""
    for i, (_, row) in enumerate(df.iterrows()):
        cells = f"<td style='color:var(--t3);font-size:.65rem'>{i+1}</td>"
        for col in cols:
            if col == "Sector Strength":
                s = str(row.get(col,""))
                c = strength_colors.get(s,"#636d88")
                cells += f"<td>{_badge(s, c+'22', c) if s else 'â€”'}</td>"
            elif col == "Buy Gauge":
                s = _clean_gauge(row.get(col,""))  # Extract keyword, strip emojis
                bg_c = "#636d88"
                for k, c in gauge_colors.items():
                    if k in s: bg_c = c; break
                cells += f"<td>{_badge(s, bg_c+'22', bg_c) if s and s != 'nan' else 'â€”'}</td>"
            elif col in ["Offense Flow Monthly", "Defense Flow Monthly"]:
                v = row.get(col, float('nan'))
                if pd.isna(v): cells += "<td style='color:var(--t3)'>â€”</td>"
                else:
                    # Different thresholds: Offense < -0.25, Defense < -0.20
                    extreme_thresh = -0.20 if "Defense" in col else -0.25
                    c = "#3dd9a0" if v > 0.05 else ("#f27872" if v < extreme_thresh else "#c5cfe0")
                    cells += f"<td class='num' style='color:{c};font-weight:700'>{v:.3f}</td>"
            elif col in ["Offense WoW", "Defense WoW"]:
                v = row.get(col, float('nan'))
                if pd.isna(v): cells += "<td style='color:var(--t3)'>â€”</td>"
                else: cells += f"<td class='num' style='color:{'#3dd9a0' if v>0 else '#f27872'}'>{v:+.3f}</td>"
            elif col in ["Perf Week","Perf Month","Perf Quart","Perf YTD"]:
                v = row.get(col, float('nan'))
                if pd.isna(v): cells += "<td style='color:var(--t3)'>â€”</td>"
                else: cells += f"<td class='num' style='color:{'#3dd9a0' if v>0 else '#f27872'}'>{v:+.1f}%</td>"
            else:
                cells += f"<td>{row.get(col,'â€”')}</td>"
        rows_html += f"<tr>{cells}</tr>"

    headers = '<th class="sortable">#</th>' + "".join(f'<th class="sortable">{c}</th>' for c in cols)
    return f"""
<div class="section">
  <details>
    <summary>Full Industry Table â€” All {len(df)} Industries (sorted by {active_flow})</summary>
    <div class="table-wrap" style="border-radius:0 0 10px 10px;border-top:none">
      <table class="sortable-table"><thead><tr>{headers}</tr></thead><tbody>{rows_html}</tbody></table>
    </div>
  </details>
</div>"""

def wrap_html(sections_html, report_date):
    sort_script = """
<script>
function mwCellValue(row, index){
  const cell = row.children[index];
  if(!cell) return "";
  return cell.textContent.trim();
}
function mwParseValue(text){
  const cleaned = text.replace(/[%,$]/g, "").replace(/[+]/g, "").trim();
  if(cleaned !== "" && /^-?\\d+(\\.\\d+)?$/.test(cleaned)) return Number(cleaned);
  return text.toLowerCase();
}
function mwSortTable(table, colIndex, dir){
  const tbody = table.tBodies[0];
  if(!tbody) return;
  const rows = Array.from(tbody.rows);
  rows.sort((a,b) => {
    const av = mwParseValue(mwCellValue(a, colIndex));
    const bv = mwParseValue(mwCellValue(b, colIndex));
    let result;
    if(typeof av === "number" && typeof bv === "number") result = av - bv;
    else result = String(av).localeCompare(String(bv), undefined, {numeric:true, sensitivity:"base"});
    return dir === "asc" ? result : -result;
  });
  rows.forEach(row => tbody.appendChild(row));
}
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("table.sortable-table").forEach(table => {
    table.querySelectorAll("th.sortable").forEach((th, index) => {
      th.addEventListener("click", () => {
        const nextDir = th.classList.contains("sort-asc") ? "desc" : "asc";
        table.querySelectorAll("th.sortable").forEach(h => h.classList.remove("sort-asc", "sort-desc"));
        th.classList.add(nextDir === "asc" ? "sort-asc" : "sort-desc");
        mwSortTable(table, index, nextDir);
      });
    });
  });
});
</script>"""
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Market Watcher â€” {report_date}</title>
{CSS}
</head>
<body>
<div class="wrap">
{sections_html}
<div class="footer">
  Generated by Market Watcher &nbsp;Â·&nbsp; {datetime.now().strftime('%Y-%m-%d %H:%M')} &nbsp;Â·&nbsp;
  Sources: Yahoo Finance &nbsp;Â·&nbsp; FRED &nbsp;Â·&nbsp; Finviz &nbsp;Â·&nbsp; Stockbee &nbsp;Â·&nbsp; Custom Flow Tracker
</div>
</div>
{sort_script}
</body>
</html>"""
    page = page.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[ \t]{2,}", " ", page)

# â”€â”€â”€ SAVE & OPEN â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def save_stockbee_t2108_graph(breadth_rows):
    if not breadth_rows:
        return None

    points = []
    for row in breadth_rows:
        try:
            val = float(row.get("t2108"))
            if math.isnan(val):
                continue
            points.append({"date": str(row.get("date", "")), "value": round(val, 2)})
        except Exception:
            continue

    if not points:
        return None

    latest = points[-1]
    payload = json.dumps(points)
    filepath = os.path.join(REPORT_FOLDER, "stockbee_t2108_graph.html")
    html_doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stockbee T2108 Graph</title>
<style>
:root{{--bg:#070b16;--panel:#111827;--border:#263248;--text:#f8fafc;--muted:#94a3b8;--blue:#38bdf8;--green:#3dd9a0;--yellow:#f2c04e;--purple:#a385f0}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--text);font-family:Inter,Segoe UI,Arial,sans-serif}}
.wrap{{width:min(1180px,calc(100vw - 32px));margin:0 auto;padding:28px 0 36px}}
header{{display:flex;justify-content:space-between;gap:18px;align-items:flex-end;margin-bottom:16px}}
h1{{margin:0;font-size:2rem;letter-spacing:0}}
.muted{{color:var(--muted);font-size:.86rem}}
.card{{background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:14px}}
.summary{{display:grid;grid-template-columns:1fr .65fr .65fr;gap:12px;margin-bottom:14px}}
.label{{color:var(--muted);text-transform:uppercase;font-size:.7rem;font-weight:800;letter-spacing:.06em}}
.value{{font-size:2.2rem;font-weight:850;margin-top:4px;color:var(--blue)}}
canvas{{width:100%;height:520px;display:block;background:var(--panel);border:1px solid var(--border);border-radius:8px}}
.bands{{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}}
.band{{border:1px solid var(--border);border-radius:4px;padding:4px 8px;color:var(--muted);font-size:.78rem;background:#172033}}
@media(max-width:760px){{header,.summary{{display:block}}.card{{margin-bottom:10px}}canvas{{height:380px}}}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>Stockbee T2108</h1>
      <div class="muted">% of stocks above 40-day price moving average, from the Stockbee breadth feed used by the dashboard.</div>
    </div>
    <div class="muted">Rough draft Â· {len(points):,} readings</div>
  </header>
  <section class="summary">
    <div class="card"><div class="label">Latest</div><div class="value">{latest["value"]:.1f}%</div><div class="muted">{latest["date"]}</div></div>
    <div class="card"><div class="label">Low Band</div><div class="value" style="color:var(--purple)">20</div><div class="muted">washout zone</div></div>
    <div class="card"><div class="label">High Band</div><div class="value" style="color:var(--yellow)">80</div><div class="muted">overbought zone</div></div>
  </section>
  <canvas id="chart" width="1120" height="520"></canvas>
  <div class="bands"><span class="band">â‰¤20 washout</span><span class="band">20-40 weak</span><span class="band">40-60 neutral</span><span class="band">60-80 strong</span><span class="band">â‰¥80 overbought</span></div>
</div>
<script>
const points = {payload};
const canvas = document.getElementById('chart');
const ctx = canvas.getContext('2d');
let active = null;
function draw(){{
  const w=canvas.width,h=canvas.height,p={{l:54,r:18,t:18,b:46}},x0=p.l,x1=w-p.r,y0=p.t,y1=h-p.b;
  const x=i=>x0+i/Math.max(1,points.length-1)*(x1-x0);
  const y=v=>y1-(v/100)*(y1-y0);
  ctx.clearRect(0,0,w,h); ctx.fillStyle='#111827'; ctx.fillRect(0,0,w,h);
  [[80,100,'rgba(242,192,78,.12)'],[60,80,'rgba(61,217,160,.10)'],[40,60,'rgba(148,163,184,.08)'],[20,40,'rgba(242,120,114,.10)'],[0,20,'rgba(163,133,240,.14)']].forEach(b=>{{ctx.fillStyle=b[2];ctx.fillRect(x0,y(b[1]),x1-x0,y(b[0])-y(b[1]));}});
  ctx.strokeStyle='#263248';ctx.fillStyle='#94a3b8';ctx.font='12px Segoe UI,Arial';ctx.textAlign='right';
  [0,20,40,60,80,100].forEach(v=>{{ctx.beginPath();ctx.moveTo(x0,y(v));ctx.lineTo(x1,y(v));ctx.stroke();ctx.fillText(v+'%',x0-8,y(v)+4);}});
  const ticks=Math.min(8,points.length); ctx.textAlign='center';
  for(let j=0;j<ticks;j++){{const i=Math.round(j*(points.length-1)/Math.max(1,ticks-1));ctx.fillStyle='#94a3b8';ctx.fillText(points[i].date,x(i),h-18);}}
  ctx.strokeStyle='#38bdf8';ctx.lineWidth=2.4;ctx.beginPath();
  points.forEach((pt,i)=>{{const xx=x(i),yy=y(Number(pt.value));if(i===0)ctx.moveTo(xx,yy);else ctx.lineTo(xx,yy);}});
  ctx.stroke();
  if(active!==null){{const pt=points[active],xx=x(active),yy=y(Number(pt.value));ctx.strokeStyle='rgba(248,250,252,.75)';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(xx,y0);ctx.lineTo(xx,y1);ctx.stroke();ctx.fillStyle='#38bdf8';ctx.beginPath();ctx.arc(xx,yy,4.5,0,Math.PI*2);ctx.fill();const label=pt.date+'  '+Number(pt.value).toFixed(1)+'%';ctx.font='13px Segoe UI,Arial';const tw=ctx.measureText(label).width+22;let bx=Math.min(Math.max(xx-tw/2,x0+4),x1-tw-4),by=yy-48;if(by<y0+4)by=yy+14;ctx.fillStyle='#020617';ctx.strokeStyle='#334155';ctx.beginPath();ctx.roundRect(bx,by,tw,34,6);ctx.fill();ctx.stroke();ctx.fillStyle='#f8fafc';ctx.fillText(label,bx+tw/2,by+22);}}
}}
canvas.addEventListener('mousemove',e=>{{const r=canvas.getBoundingClientRect(),mx=(e.clientX-r.left)*(canvas.width/r.width),x0=54,x1=canvas.width-18,t=Math.max(0,Math.min(1,(mx-x0)/(x1-x0)));active=Math.round(t*(points.length-1));draw();}});
canvas.addEventListener('mouseleave',()=>{{active=null;draw();}});
draw();
</script>
</body>
</html>"""

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html_doc)
    print(f"  Stockbee T2108 graph saved: {filepath}")
    return filepath

def save_and_open(html, report_date):
    filename = f"report_{report_date}.html"
    filepath = os.path.join(REPORT_FOLDER, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n  Report saved: {filepath}")
    webbrowser.open(f"file:///{filepath.replace(chr(92), '/')}")
    return filepath

# â”€â”€â”€ MAIN â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def main():
    if "--help" in sys.argv:
        print(__doc__)
        return

    report_date = datetime.now().strftime("%Y-%m-%d")
    print(f"\n{'='*55}")
    print(f"  Market Watcher Daily Agent â€” {report_date}")
    print(f"{'='*55}")

    # â”€â”€ FETCH â”€â”€
    print("\nFetching data...")
    dxy_rows   = safe_fetch("DXY (Yahoo Finance)",    lambda: fetch_yahoo(YAHOO_DXY))
    y10_rows   = safe_fetch("US 10Y (Yahoo Finance)", lambda: fetch_yahoo(YAHOO_10Y))
    hy_raw     = safe_fetch("HY Spread (FRED)",       fetch_hy_spread)
    spec_count = safe_fetch("Finviz Momentum",        fetch_finviz_momentum)
    breadth    = safe_fetch("Breadth (Stockbee)",     fetch_breadth)
    flow_curr  = safe_fetch("Flow Current (Sheet)",   fetch_flow_current)
    flow_hist  = safe_fetch("Flow History (Sheet)",   fetch_flow_history)

    # â”€â”€ BUILD INDICATOR OBJECTS â”€â”€
    dxy_obj = build_indicator_obj(dxy_rows) if dxy_rows else None
    y10_obj = build_indicator_obj(y10_rows) if y10_rows else None
    hy_obj  = hy_raw

    # â”€â”€ VELOCITY â”€â”€
    v_D = calc_velocity(dxy_obj["current"], dxy_obj["one_month_ago"], dxy_obj["three_months_ago"], dxy_obj["six_months_ago"], False) if dxy_obj else None
    v_Y = calc_velocity(y10_obj["current"], y10_obj["one_month_ago"], y10_obj["three_months_ago"], y10_obj["six_months_ago"], True)  if y10_obj else None
    v_H = calc_velocity(hy_obj["current"],  hy_obj["one_month_ago"],  hy_obj["three_months_ago"],  hy_obj["six_months_ago"],  True)  if hy_obj  else None

    # â”€â”€ THRESHOLDS (dynamic stdev-based) â”€â”€
    t_D = max(v_D["stdev"], 0.15) if v_D else DXY_THRESH
    t_Y = max(v_Y["stdev"], 2.0)  if v_Y else Y10_THRESH
    t_H = max(v_H["stdev"] / 100, 0.02) if v_H else HY_THRESH

    # â”€â”€ CHANGES â”€â”€
    dd1 = pct_chg(dxy_obj["current"], dxy_obj["one_month_ago"]) if dxy_obj else None
    yd1 = bps_diff(y10_obj["current"], y10_obj["one_month_ago"]) if y10_obj else None
    hd1 = abs_diff(hy_obj["current"], hy_obj["one_month_ago"])  if hy_obj  else None

    # â”€â”€ REGIME â”€â”€
    dxy_dir = classify_dxy(dd1, t_D)
    y10_dir = classify_y10(yd1, t_Y)
    regime  = determine_regime(dxy_dir, y10_dir)
    print(f"\n  Regime: {regime['name'] if regime else 'Insufficient data'}")

    # â”€â”€ BREADTH â”€â”€
    breadth_phase = determine_breadth_phase(breadth) if breadth else None
    b_phase_str   = breadth_phase["phase"] if breadth_phase else None
    save_stockbee_t2108_graph(breadth)

    # â”€â”€ CONVICTION â”€â”€
    conviction = calc_conviction(
        dd1, yd1, hd1, t_D, t_Y, t_H, v_D, v_Y, v_H,
        y10_obj["current"] if y10_obj else None,
        hy_obj["current"]  if hy_obj  else None,
        spec_count, b_phase_str
    )
    print(f"  Conviction: {conviction['score']:.0f}% ({conviction['direction']}, {conviction['align_label']})")

    # â”€â”€ STRESS VERDICT â”€â”€
    sv = stress_verdict(dd1, yd1, hd1, v_D, v_Y, v_H,
                        hy_obj["current"] if hy_obj else None, t_D, t_Y, t_H)

    # â”€â”€ SECTOR FLOW â”€â”€
    is_risk_on = regime["risk_on"] if regime else False
    active_flow = "Offense Flow Monthly"
    if flow_curr is not None:
        offense_col = next((c for c in flow_curr.columns if "offense" in c.lower() and "flow" in c.lower()), "Offense Flow Monthly")
        defense_col = next((c for c in flow_curr.columns if "defense" in c.lower() and "flow" in c.lower()), "Defense Flow Monthly")
        active_flow = offense_col if is_risk_on else defense_col
        # Detect if IMPORTHTML is blocked (all flow values are NaN)
        if not has_valid_flow(flow_curr, active_flow):
            print(f"  [WARNING] Current sheet has no valid flow data â€” Finviz IMPORTHTML may be blocked")
            fallback = flow_curr_from_history(flow_hist)
            if fallback is not None:
                flow_curr = fallback
                offense_col = "Offense Flow Monthly"
                defense_col = "Defense Flow Monthly"
                active_flow = offense_col if is_risk_on else defense_col
    print(f"  Active flow signal: {active_flow} ({'risk-on' if is_risk_on else 'risk-off/neutral'})")

    sectors = rank_sectors(flow_curr, flow_hist, active_flow) if flow_curr is not None else None

    # â”€â”€ NARRATIVES â”€â”€
    summary_html  = generate_executive_summary(regime, breadth_phase, conviction, sectors, dxy_obj, y10_obj, hy_obj, v_D, v_Y, spec_count)
    bull_pts, bear_pts = generate_risk_scenarios(regime, dxy_obj, y10_obj, hy_obj)

    # â”€â”€ SPEC ZONE â”€â”€
    sz = spec_zone(spec_count)
    spec_note = f"Finviz Momentum: {spec_count} stocks ({sz['zone']} â€” {sz['signal']}) Â· {sz['note']}" if spec_count is not None else "Finviz Momentum: unavailable"

    # â”€â”€ BUILD REPORT â”€â”€
    print("\nGenerating report...")
    sections = (
        section_header(report_date, regime, conviction, breadth_phase) +
        f'<div style="font-size:.72rem;color:var(--t3);margin-bottom:18px">{spec_note}</div>' +
        section_executive_summary(summary_html, regime) +
        section_macro_regime(dxy_obj, y10_obj, hy_obj, v_D, v_Y, v_H, regime, conviction, sv, spec_count) +
        section_breadth(breadth, breadth_phase) +
        section_sector_flow(sectors, active_flow) +
        section_extreme_signals(sectors, active_flow) +
        section_trade_framework(regime) +
        section_risk_scenarios(bull_pts, bear_pts) +
        section_full_table(sectors["full"] if sectors else None, active_flow)
    )
    html = wrap_html(sections, report_date)
    save_and_open(html, report_date)
    print("\n  Done.\n")

if __name__ == "__main__":
    main()

