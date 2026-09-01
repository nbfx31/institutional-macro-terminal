# ============================================================================
#  INSTITUTIONAL TERMINAL PRO  —  v2.0 (Komplett-Überarbeitung)
# ----------------------------------------------------------------------------
#  Voraussetzungen:
#    pip install streamlit yfinance pandas numpy plotly requests
#    KI-Features (optional):  pip install agno google-genai
#    API-Key:                 Umgebungsvariable GOOGLE_API_KEY setzen
#  Start:  streamlit run terminal_pro.py
# ----------------------------------------------------------------------------
#  Designprinzipien dieser Version:
#    1. Keine erfundenen Daten. Jede Zahl ist live geladen oder klar als
#       nicht verfügbar ("–") gekennzeichnet. Datenquellen tragen Badges.
#    2. Keine beschönigenden Fallbacks. Schlägt eine KI-Analyse fehl, sagt
#       das Terminal das ehrlich — statt "Solide finanzielle Basis" zu raten.
#    3. Fehler werden geloggt statt verschluckt (kein nacktes `except: pass`).
#    4. Alles, was ein Anleger für eine echte Analyse braucht: erweiterte
#       Fundamentaldaten, interaktiver DCF mit Sensitivität, Risiko-Kennzahlen
#       (Vola, Beta, Sharpe, Sortino, Max Drawdown, VaR), Monte-Carlo-
#       Simulation, echte Zinskurven (US: Yahoo / EU: EZB-API).
# ============================================================================

import io
import os
import html
import sqlite3
import logging
from datetime import datetime
from email.utils import parsedate_to_datetime

import numpy as np
import pandas as pd
import requests
import streamlit as st
import yfinance as yf
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import xml.etree.ElementTree as ET

# --- Agent-Framework: agno (Nachfolger von phidata), Fallback auf altes phi ---
AGENT_BACKEND = None
try:
    from agno.agent import Agent
    from agno.models.google import Gemini
    try:
        from agno.tools.yfinance import YFinanceTools
    except ImportError:
        YFinanceTools = None
    AGENT_BACKEND = "agno"
except ImportError:
    try:
        from phi.agent import Agent
        from phi.model.google import Gemini
        try:
            from phi.tools.yfinance import YFinanceTools
        except ImportError:
            YFinanceTools = None
        AGENT_BACKEND = "phi"
    except ImportError:
        Agent = Gemini = YFinanceTools = None

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("terminal")

# ============================================================================
#  KONFIGURATION
# ============================================================================

st.set_page_config(page_title="Institutional Terminal Pro", layout="wide",
                   initial_sidebar_state="expanded")

# Modell-ID: gemini-3.6-flash (aktuelles Flash-Modell, Stand 07/2026).
GEMINI_MODEL_ID = os.getenv("GEMINI_MODEL_ID", "gemini-3.6-flash")
# ACHTUNG: Key im Quelltext ist nur für den privaten Gebrauch vertretbar.
# Datei niemals teilen, hochladen oder in ein Repository committen —
# sonst Key unter https://aistudio.google.com sofort rotieren.
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
os.environ.setdefault("GOOGLE_API_KEY", GOOGLE_API_KEY)

RISK_FREE_RATE = 0.04          # p.a., für Sharpe/Sortino (US-Geldmarkt-Näherung)
TRADING_DAYS = 252

CLR = {
    "up": "#00E58C", "down": "#FF5A5F", "amber": "#FFB454", "cyan": "#3DDCFF",
    "violet": "#C792EA", "gold": "#FFD700", "blue": "#4C9AFF", "muted": "#8A93A6",
    "card": "#161B26", "border": "#2A3242",
}

BENCH_EU, BENCH_US = "^GDAXI", "SPY"

ASSET_GROUPS = {
    "Leitindizes": [("SPY", "S&P 500 ETF"), ("QQQ", "Nasdaq 100 ETF"), ("^GDAXI", "DAX 40"), ("^STOXX50E", "Euro Stoxx 50")],
    "Deutsche Blue Chips (DAX)": [("SAP.DE", "SAP SE"), ("SIE.DE", "Siemens"), ("ALV.DE", "Allianz"), ("AIR.DE", "Airbus")],
    "US Mega-Caps & Tech": [("AAPL", "Apple"), ("MSFT", "Microsoft"), ("NVDA", "Nvidia"), ("TSLA", "Tesla")],
    "Kryptowährungen": [("BTC-USD", "Bitcoin"), ("ETH-USD", "Ethereum"), ("SOL-USD", "Solana")],
    "Rohstoffe & Energie": [("GC=F", "Gold"), ("CL=F", "WTI Crude Oil"), ("HG=F", "Kupfer")],
    "Forex / Währungen": [("EURUSD=X", "EUR/USD"), ("USDJPY=X", "USD/JPY"), ("GBPUSD=X", "GBP/USD")],
}

PEER_SUGGESTIONS = {
    "AAPL": ["MSFT", "GOOGL", "META"], "MSFT": ["AAPL", "GOOGL", "ORCL"],
    "NVDA": ["AMD", "AVGO", "TSM"], "TSLA": ["GM", "F", "RIVN"],
    "SAP.DE": ["ORCL", "CRM", "MSFT"], "SIE.DE": ["GE", "SU.PA", "ABBNY"],
    "ALV.DE": ["MUV2.DE", "ZURN.SW", "CS.PA"], "AIR.DE": ["BA", "RTX", "SAF.PA"],
    "BTC-USD": ["ETH-USD", "SOL-USD", "BNB-USD"],
}

OVERVIEW_TICKERS = [("^GDAXI", "DAX 40"), ("SPY", "S&P 500"), ("QQQ", "Nasdaq 100"),
                    ("^STOXX50E", "Euro Stoxx 50"), ("BTC-USD", "Bitcoin"), ("GC=F", "Gold")]

# ============================================================================
#  CSS  —  ein durchgängiges Terminal-Design
# ============================================================================

st.markdown(f"""
<style>
  .block-container {{ padding-top: 1.2rem; }}
  .terminal-card {{
      background: {CLR["card"]}; padding: 14px 16px; border-radius: 10px;
      border: 1px solid {CLR["border"]}; margin-bottom: 12px;
  }}
  .metric-title {{ color: {CLR["muted"]}; font-size: .78rem; letter-spacing: .4px; }}
  .metric-value {{ font-size: 1.45rem; font-weight: 700; color: #F2F5FA; line-height: 1.25; }}
  .metric-sub   {{ font-size: .85rem; font-weight: 600; margin-top: 2px; }}
  .badge {{
      display: inline-block; padding: 1px 9px; border-radius: 20px;
      font-size: .62rem; font-weight: 700; letter-spacing: 1px; vertical-align: middle;
  }}
  .badge-live   {{ background: rgba(0,229,140,.12); color: {CLR["up"]};   border: 1px solid rgba(0,229,140,.35); }}
  .badge-ki     {{ background: rgba(199,146,234,.12); color: {CLR["violet"]}; border: 1px solid rgba(199,146,234,.35); }}
  .badge-warn   {{ background: rgba(255,90,95,.12); color: {CLR["down"]}; border: 1px solid rgba(255,90,95,.35); }}
  .newsitem a {{ color: #E8ECF4; text-decoration: none; font-weight: 600; font-size: .9rem; }}
  .newsitem a:hover {{ color: {CLR["cyan"]}; }}
  .news-meta {{ color: {CLR["muted"]}; font-size: .72rem; }}
  .range-track {{ background: #2A3242; height: 6px; border-radius: 3px; position: relative; margin-top: 6px; }}
  .range-fill  {{ background: linear-gradient(90deg, {CLR["down"]}, {CLR["amber"]}, {CLR["up"]});
                  height: 6px; border-radius: 3px; }}
  .range-dot   {{ position: absolute; top: -3px; width: 12px; height: 12px; border-radius: 50%;
                  background: #fff; border: 2px solid {CLR["card"]}; transform: translateX(-50%); }}
  div[data-testid="stMetricValue"] {{ font-size: 1.35rem; }}
</style>
""", unsafe_allow_html=True)

# ============================================================================
#  HELPER
# ============================================================================

def esc(s) -> str:
    """HTML-Escaping für alles, was aus externen Quellen in Markup landet."""
    return html.escape(str(s)) if s is not None else ""

def is_num(x) -> bool:
    return isinstance(x, (int, float, np.floating, np.integer)) and not pd.isna(x)

def fmt(x, pattern="{:,.2f}", na="–") -> str:
    return pattern.format(x) if is_num(x) else na

def fmt_big(x, cur="") -> str:
    """1.234.567.890 -> '1,23 Mrd.' — lesbare Skalierung großer Beträge."""
    if not is_num(x):
        return "–"
    for t, s in [(1e12, " Bio."), (1e9, " Mrd."), (1e6, " Mio."), (1e3, " Tsd.")]:
        if abs(x) >= t:
            return f"{cur}{x / t:,.2f}{s}"
    return f"{cur}{x:,.2f}"

def updown(x) -> str:
    return CLR["up"] if is_num(x) and x >= 0 else CLR["down"]

def badge(text, kind="live") -> str:
    return f'<span class="badge badge-{kind}">{esc(text)}</span>'

def card(inner_html, accent=None) -> str:
    border = f"border-left: 4px solid {accent};" if accent else ""
    return f'<div class="terminal-card" style="{border}">{inner_html}</div>'

def metric_card(title, value, sub=None, sub_color=None) -> str:
    sub_html = f'<div class="metric-sub" style="color:{sub_color or CLR["muted"]}">{sub}</div>' if sub else ""
    return card(f'<div class="metric-title">{title}</div><div class="metric-value">{value}</div>{sub_html}')

def safe_last(series):
    try:
        s = series.dropna()
        return float(s.iloc[-1]) if len(s) else None
    except Exception:
        return None

# ============================================================================
#  LOKALE PERSISTENZ (SQLite) — Portfolio & Watchlist überleben den Neustart
# ============================================================================

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "terminal_data.db")

def _db():
    con = sqlite3.connect(DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS portfolio "
                "(ticker TEXT, datum TEXT, stueck REAL, preis REAL)")
    con.execute("CREATE TABLE IF NOT EXISTS watchlist (ticker TEXT PRIMARY KEY)")
    con.execute("CREATE TABLE IF NOT EXISTS alerts (ticker TEXT, op TEXT, level REAL)")
    return con

def db_load_portfolio() -> list:
    try:
        with _db() as con:
            rows = con.execute("SELECT ticker, datum, stueck, preis FROM portfolio").fetchall()
        return [{"Ticker": t, "Datum": d, "Stückzahl": s, "Kaufpreis": p}
                for t, d, s, p in rows]
    except Exception as e:
        log.warning("DB laden (Portfolio): %s", e)
        return []

def db_save_portfolio(trades: list):
    try:
        with _db() as con:
            con.execute("DELETE FROM portfolio")
            con.executemany("INSERT INTO portfolio VALUES (?,?,?,?)",
                            [(t["Ticker"], t["Datum"], t["Stückzahl"], t["Kaufpreis"])
                             for t in trades])
    except Exception as e:
        log.warning("DB speichern (Portfolio): %s", e)

def db_load_watchlist() -> list:
    try:
        with _db() as con:
            return [r[0] for r in con.execute(
                "SELECT ticker FROM watchlist ORDER BY ticker").fetchall()]
    except Exception as e:
        log.warning("DB laden (Watchlist): %s", e)
        return []

def db_save_watchlist(symbols: list):
    try:
        with _db() as con:
            con.execute("DELETE FROM watchlist")
            con.executemany("INSERT OR IGNORE INTO watchlist VALUES (?)",
                            [(s,) for s in symbols])
    except Exception as e:
        log.warning("DB speichern (Watchlist): %s", e)

def db_load_alerts() -> list:
    try:
        with _db() as con:
            return [{"Ticker": t, "Op": o, "Level": l} for t, o, l in
                    con.execute("SELECT ticker, op, level FROM alerts").fetchall()]
    except Exception as e:
        log.warning("DB laden (Alarme): %s", e)
        return []

def db_save_alerts(alerts: list):
    try:
        with _db() as con:
            con.execute("DELETE FROM alerts")
            con.executemany("INSERT INTO alerts VALUES (?,?,?)",
                            [(a["Ticker"], a["Op"], a["Level"]) for a in alerts])
    except Exception as e:
        log.warning("DB speichern (Alarme): %s", e)

@st.cache_data(ttl=3600, show_spinner=False)
def search_symbols(query: str) -> list:
    """Freitext -> Yahoo-Symbole ('allianz' findet ALV.DE), keyless."""
    try:
        r = requests.get("https://query1.finance.yahoo.com/v1/finance/search",
                         params={"q": query, "quotesCount": 8, "newsCount": 0},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
        if r.status_code != 200:
            return []
        out = []
        for q in r.json().get("quotes", []):
            sym = q.get("symbol")
            if sym:
                out.append({"symbol": sym,
                            "name": q.get("shortname") or q.get("longname") or sym,
                            "type": q.get("quoteType", ""),
                            "exch": q.get("exchDisp", "")})
        return out
    except Exception as e:
        log.warning("Symbolsuche '%s': %s", query, e)
        return []

# ============================================================================
#  SESSION STATE
# ============================================================================

def init_state():
    st.session_state.setdefault("active_ticker", "AAPL")
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("ai_results", {})   # (bereich, ticker) -> Text, überlebt Reruns
    st.session_state.setdefault("peer_df", None)
    st.session_state.setdefault("peer_norm", None)
    if not st.session_state.get("db_loaded"):       # einmal je Sitzung aus SQLite laden
        st.session_state["portfolio"] = db_load_portfolio()
        st.session_state["watchlist"] = db_load_watchlist()
        st.session_state["alerts"] = db_load_alerts()
        st.session_state["db_loaded"] = True
    if not isinstance(st.session_state.get("portfolio"), list):
        st.session_state["portfolio"] = []
    st.session_state.setdefault("watchlist", [])
    st.session_state.setdefault("alerts", [])

init_state()

if not st.session_state.get("url_ticker_applied"):
    try:
        url_t = st.query_params.get("t")
        if url_t:
            st.session_state.active_ticker = str(url_t).upper().strip()
    except Exception:
        pass
    st.session_state["url_ticker_applied"] = True

def set_ticker(new_ticker: str):
    t = (new_ticker or "").upper().strip()
    if t and t != st.session_state.active_ticker:
        st.session_state.active_ticker = t
        st.session_state.messages = []
        try:
            st.query_params["t"] = t          # teilbarer Link: ?t=SAP.DE
        except Exception:
            pass

def on_ticker_input():
    set_ticker(st.session_state.input_widget)

# ============================================================================
#  DATEN-LAYER  (alles gecacht, alles mit ehrlichem Fehlerverhalten)
# ============================================================================

@st.cache_data(ttl=600, show_spinner=False)
def load_info(ticker: str) -> dict:
    try:
        info = yf.Ticker(ticker).info or {}
        return info if isinstance(info, dict) else {}
    except Exception as e:
        log.warning("info(%s) fehlgeschlagen: %s", ticker, e)
        return {}

@st.cache_data(ttl=300, show_spinner=False)
def load_history(ticker: str, period: str = "max", interval: str = "1d") -> pd.DataFrame:
    try:
        return yf.Ticker(ticker).history(period=period, interval=interval)
    except Exception as e:
        log.warning("history(%s,%s,%s) fehlgeschlagen: %s", ticker, period, interval, e)
        return pd.DataFrame()

@st.cache_data(ttl=600, show_spinner=False)
def load_batch_close(tickers: tuple, period: str = "5d") -> pd.DataFrame:
    """Schlusskurse mehrerer Ticker in EINEM Request (statt N Einzel-Calls)."""
    try:
        df = yf.download(list(tickers), period=period, progress=False,
                         auto_adjust=True, group_by="column", threads=True)
        if df is None or df.empty:
            return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex):
            close = df["Close"]
        else:  # nur ein Ticker geliefert
            close = df[["Close"]].rename(columns={"Close": tickers[0]})
        return close.dropna(how="all")
    except Exception as e:
        log.warning("batch(%s) fehlgeschlagen: %s", tickers, e)
        return pd.DataFrame()

@st.cache_data(ttl=1800, show_spinner=False)
def load_financial_statements(ticker: str):
    """Income Statement, Cashflow, Bilanz — je leeres DF bei Fehler."""
    t = yf.Ticker(ticker)
    out = []
    for attr in ("income_stmt", "cashflow", "balance_sheet"):
        try:
            df = getattr(t, attr)
            out.append(df if isinstance(df, pd.DataFrame) else pd.DataFrame())
        except Exception as e:
            log.warning("%s(%s) fehlgeschlagen: %s", attr, ticker, e)
            out.append(pd.DataFrame())
    return tuple(out)

@st.cache_data(ttl=900, show_spinner=False)
def load_news(ticker: str) -> list:
    """Yahoo-RSS primär, yfinance-News als Fallback. Titel werden beim
    Rendern escaped (Injection-Schutz), Zeitstempel wenn verfügbar."""
    news = []
    try:
        url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}"
        r = requests.get(url, timeout=4)
        if r.status_code == 200:
            root = ET.fromstring(r.content)
            for item in root.findall(".//item")[:6]:
                title, link = item.find("title"), item.find("link")
                pub = item.find("pubDate")
                when = ""
                try:
                    if pub is not None and pub.text:
                        when = parsedate_to_datetime(pub.text).strftime("%d.%m. %H:%M")
                except Exception:
                    pass
                if title is not None and title.text and link is not None and link.text:
                    news.append({"title": title.text.strip(), "link": link.text.strip(),
                                 "publisher": "Yahoo Finance", "when": when})
    except Exception as e:
        log.warning("RSS(%s): %s", ticker, e)
    if news:
        return news
    try:
        for n in (yf.Ticker(ticker).news or [])[:6]:
            c = n.get("content") or {}
            title = n.get("title") or c.get("title")
            link = n.get("link") or (c.get("clickThroughUrl") or {}).get("url") or "#"
            if title:
                news.append({"title": title, "link": link, "publisher": "Yahoo Finance", "when": ""})
    except Exception as e:
        log.warning("yf.news(%s): %s", ticker, e)
    return news

# ---- Makro: nur echte Werte, None statt erfundener Zahlen ------------------

MACRO_TICKERS = {"VIX": "^VIX", "DXY": "DX-Y.NYB", "OIL": "CL=F", "GOLD": "GC=F", "EURUSD": "EURUSD=X"}

@st.cache_data(ttl=600, show_spinner=False)
def load_macro_quotes() -> dict:
    close = load_batch_close(tuple(MACRO_TICKERS.values()), period="5d")
    out = {}
    for name, sym in MACRO_TICKERS.items():
        out[name] = safe_last(close[sym]) if sym in getattr(close, "columns", []) else None
    if out.get("DXY") is None:  # Fallback-Symbol für den Dollar-Index
        h = load_history("DX=F", "5d", "1d")
        out["DXY"] = safe_last(h["Close"]) if not h.empty else None
    return out

# US-Zinsen: CBOE-Yield-Indizes (echte Daten, direkt in %). 2Y über Micro-Yield-Future.
US_YIELD_TICKERS = {"3M": "^IRX", "2Y": "2YY=F", "5Y": "^FVX", "10Y": "^TNX", "30Y": "^TYX"}

@st.cache_data(ttl=900, show_spinner=False)
def load_us_yields() -> dict:
    out, stamp = {}, None
    for mat, sym in US_YIELD_TICKERS.items():
        h = load_history(sym, "5d", "1d")
        v = safe_last(h["Close"]) if not h.empty else None
        if v is not None and 0 < v < 25:      # Plausibilitätscheck
            out[mat] = round(v, 2)
            stamp = h.index[-1].date()
    return {"yields": out, "date": stamp}

# EU-Zinsen: EZB Data API (AAA-Staatsanleihen Euroraum, Spot Rates) — echt & keyless.
ECB_SERIES = {"3M": "SR_3M", "2Y": "SR_2Y", "5Y": "SR_5Y", "10Y": "SR_10Y", "30Y": "SR_30Y"}

@st.cache_data(ttl=3600, show_spinner=False)
def load_ecb_yields() -> dict:
    out, stamp = {}, None
    for mat, code in ECB_SERIES.items():
        try:
            url = (f"https://data-api.ecb.europa.eu/service/data/YC/"
                   f"B.U2.EUR.4F.G_N_A.SV_C_YM.{code}?format=csvdata&lastNObservations=1")
            r = requests.get(url, timeout=6)
            if r.status_code != 200 or not r.text.strip():
                continue
            df = pd.read_csv(io.StringIO(r.text))
            if "OBS_VALUE" in df.columns and len(df):
                out[mat] = round(float(df["OBS_VALUE"].iloc[-1]), 2)
                if "TIME_PERIOD" in df.columns:
                    stamp = str(df["TIME_PERIOD"].iloc[-1])
        except Exception as e:
            log.warning("EZB %s: %s", code, e)
    return {"yields": out, "date": stamp}

@st.cache_data(ttl=3600, show_spinner=False)
def load_next_earnings(ticker: str):
    """Nächster Earnings-Termin als date | None (calendar-API, Fallback info)."""
    try:
        cal = yf.Ticker(ticker).calendar
        dates = None
        if isinstance(cal, dict):
            dates = cal.get("Earnings Date")
        elif isinstance(cal, pd.DataFrame) and "Earnings Date" in cal.index:
            dates = list(cal.loc["Earnings Date"])
        if dates:
            d = dates[0] if isinstance(dates, (list, tuple)) else dates
            return pd.Timestamp(d).date()
    except Exception as e:
        log.warning("calendar(%s): %s", ticker, e)
    ts = load_info(ticker).get("earningsTimestamp")
    try:
        return datetime.fromtimestamp(ts).date() if is_num(ts) else None
    except Exception:
        return None

@st.cache_data(ttl=86400, show_spinner=False)
def load_dividend_years(ticker: str) -> pd.Series:
    """Ausgeschüttete Dividende je Kalenderjahr (letzte 10 abgeschlossene + laufendes)."""
    try:
        div = yf.Ticker(ticker).dividends
        if div is None or div.empty:
            return pd.Series(dtype=float)
        yearly = div.groupby(div.index.year).sum()
        return yearly.tail(11)
    except Exception as e:
        log.warning("dividends(%s): %s", ticker, e)
        return pd.Series(dtype=float)

@st.cache_data(ttl=900, show_spinner=False)
def load_option_expiries(ticker: str) -> tuple:
    try:
        return tuple(yf.Ticker(ticker).options or ())
    except Exception as e:
        log.warning("options(%s): %s", ticker, e)
        return ()

@st.cache_data(ttl=900, show_spinner=False)
def load_option_chain(ticker: str, expiry: str):
    try:
        oc = yf.Ticker(ticker).option_chain(expiry)
        return oc.calls.copy(), oc.puts.copy()
    except Exception as e:
        log.warning("option_chain(%s,%s): %s", ticker, expiry, e)
        return pd.DataFrame(), pd.DataFrame()

@st.cache_data(ttl=3600, show_spinner=False)
def load_insider_transactions(ticker: str) -> pd.DataFrame:
    try:
        df = yf.Ticker(ticker).insider_transactions
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    except Exception as e:
        log.warning("insider(%s): %s", ticker, e)
        return pd.DataFrame()

@st.cache_data(ttl=3600, show_spinner=False)
def load_analyst_actions(ticker: str) -> pd.DataFrame:
    try:
        df = yf.Ticker(ticker).upgrades_downgrades
        return df.reset_index() if isinstance(df, pd.DataFrame) else pd.DataFrame()
    except Exception as e:
        log.warning("grades(%s): %s", ticker, e)
        return pd.DataFrame()

@st.cache_data(ttl=3600, show_spinner=False)
def load_earnings_history(ticker: str) -> pd.DataFrame:
    try:
        df = yf.Ticker(ticker).earnings_dates
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    except Exception as e:
        log.warning("earnings_dates(%s): %s", ticker, e)
        return pd.DataFrame()

# ---- FRED (St. Louis Fed): Realzinsen & Inflationserwartungen, keyless ----

def parse_fred_csv(text: str) -> pd.Series:
    """fredgraph.csv robust parsen (Datumsspalte + eine Wertspalte, '.' = NaN)."""
    df = pd.read_csv(io.StringIO(text))
    if df.shape[1] < 2:
        return pd.Series(dtype=float)
    date_col, val_col = df.columns[0], df.columns[1]
    s = pd.Series(pd.to_numeric(df[val_col], errors="coerce").values,
                  index=pd.to_datetime(df[date_col], errors="coerce"))
    return s.dropna()

@st.cache_data(ttl=21600, show_spinner=False)
def load_fred(series_id: str, years: int = 10) -> pd.Series:
    """z. B. DFII10 (10J-Realzins TIPS), T10YIE (10J-Breakeven-Inflation)."""
    try:
        start = (datetime.now() - pd.Timedelta(days=int(years * 365.25))).strftime("%Y-%m-%d")
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={start}"
        r = requests.get(url, timeout=8)
        if r.status_code == 200 and r.text.strip():
            return parse_fred_csv(r.text)
    except Exception as e:
        log.warning("FRED %s: %s", series_id, e)
    return pd.Series(dtype=float)

# ---- CFTC Commitment of Traders (Legacy Futures Only), keyless Socrata-API -

COT_MARKETS = {
    "GOLD": ["GOLD - COMMODITY EXCHANGE"],
    "SILBER": ["SILVER - COMMODITY EXCHANGE"],
    "EUR": ["EURO FX - CHICAGO MERCANTILE"],
    "JPY": ["JAPANESE YEN - CHICAGO MERCANTILE"],
    "GBP": ["BRITISH POUND - CHICAGO MERCANTILE",
            "BRITISH POUND STERLING - CHICAGO MERCANTILE"],
    "ÖL (WTI)": ["WTI-PHYSICAL", "CRUDE OIL, LIGHT SWEET"],
    "KUPFER": ["COPPER- #1", "COPPER - COMMODITY EXCHANGE"],
    "BITCOIN": ["BITCOIN - CHICAGO MERCANTILE"],
}

def parse_cot_rows(rows: list) -> pd.DataFrame:
    """Socrata-JSON zu Netto-Spekulanten-Serie (Long − Short, Non-Commercials)."""
    out = []
    for r in rows:
        try:
            out.append({
                "date": pd.Timestamp(r["report_date_as_yyyy_mm_dd"][:10]),
                "net": float(r["noncomm_positions_long_all"])
                       - float(r["noncomm_positions_short_all"]),
                "oi": float(r.get("open_interest_all", "nan")),
            })
        except Exception:
            continue
    if not out:
        return pd.DataFrame()
    return pd.DataFrame(out).set_index("date").sort_index()

@st.cache_data(ttl=21600, show_spinner=False)
def load_cot(market_key: str) -> pd.DataFrame:
    """Wöchentliche Positionierung der Large Speculators (letzte ~3 Jahre)."""
    for prefix in COT_MARKETS.get(market_key, []):
        try:
            url = ("https://publicreporting.cftc.gov/resource/6dca-aqww.json"
                   f"?$where=upper(market_and_exchange_names) like '{prefix.upper()}%'"
                   "&$order=report_date_as_yyyy_mm_dd DESC&$limit=160")
            r = requests.get(url, timeout=8)
            if r.status_code != 200:
                continue
            df = parse_cot_rows(r.json())
            if len(df) >= 30:
                return df
        except Exception as e:
            log.warning("COT %s (%s): %s", market_key, prefix, e)
    return pd.DataFrame()

def percentile_of_last(s: pd.Series):
    """Perzentil des aktuellsten Werts innerhalb der eigenen Historie (0-100)."""
    try:
        s = s.dropna()
        if len(s) < 20:
            return None
        return float((s < s.iloc[-1]).mean() * 100)
    except Exception:
        return None

# ============================================================================
#  BERECHNUNGEN  —  Technik, Performance, Risiko, Bewertung
# ============================================================================

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """SMA, Bollinger, RSI (Wilder-Glättung, statt der zu trägen SMA-Variante
    des alten Skripts), MACD."""
    d = df.copy()
    c = d["Close"]
    d["SMA_50"] = c.rolling(50).mean()
    d["SMA_200"] = c.rolling(200).mean()
    mid = c.rolling(20).mean()
    std = c.rolling(20).std()
    d["BB_UP"], d["BB_MID"], d["BB_LO"] = mid + 2 * std, mid, mid - 2 * std
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, min_periods=14).mean()
    d["RSI"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    ema12, ema26 = c.ewm(span=12, adjust=False).mean(), c.ewm(span=26, adjust=False).mean()
    d["MACD"] = ema12 - ema26
    d["MACD_SIG"] = d["MACD"].ewm(span=9, adjust=False).mean()
    d["MACD_HIST"] = d["MACD"] - d["MACD_SIG"]
    return d

def period_return(close: pd.Series, days: int = None, ytd: bool = False):
    try:
        close = close.dropna()
        if len(close) < 2:
            return None
        if ytd:
            start = pd.Timestamp(close.index[-1].year, 1, 1)
            if close.index.tz is not None:
                start = start.tz_localize(close.index.tz)
            sub = close[close.index >= start]
        else:
            sub = close[close.index >= close.index[-1] - pd.Timedelta(days=days)]
        if len(sub) < 2 or sub.iloc[0] == 0:
            return None
        return float(sub.iloc[-1] / sub.iloc[0] - 1)
    except Exception:
        return None

def performance_table(close: pd.Series) -> dict:
    return {
        "1W": period_return(close, 7), "1M": period_return(close, 30),
        "3M": period_return(close, 91), "6M": period_return(close, 182),
        "YTD": period_return(close, ytd=True), "1J": period_return(close, 365),
        "3J": period_return(close, 3 * 365), "5J": period_return(close, 5 * 365),
    }

def drawdown_series(close: pd.Series) -> pd.Series:
    return close / close.cummax() - 1

def risk_metrics(close: pd.Series, bench_close: pd.Series = None) -> dict:
    """Alle Kennzahlen auf Basis der letzten ~252 Handelstage; Max Drawdown
    auf der gesamten übergebenen Historie."""
    out = {k: None for k in ("vol", "sharpe", "sortino", "max_dd", "var95", "beta", "corr")}
    try:
        c = close.dropna()
        ret = c.pct_change().dropna().tail(TRADING_DAYS)
        if len(ret) < 30:
            return out
        vol = float(ret.std() * np.sqrt(TRADING_DAYS))
        mean_ann = float(ret.mean() * TRADING_DAYS)
        out["vol"] = vol
        out["sharpe"] = (mean_ann - RISK_FREE_RATE) / vol if vol > 0 else None
        downside = ret[ret < 0]
        ds = float(downside.std() * np.sqrt(TRADING_DAYS)) if len(downside) > 5 else None
        out["sortino"] = (mean_ann - RISK_FREE_RATE) / ds if ds else None
        out["max_dd"] = float(drawdown_series(c).min())
        out["var95"] = float(-np.percentile(ret, 5))
        if bench_close is not None and not bench_close.dropna().empty:
            b = bench_close.dropna().pct_change().dropna().tail(TRADING_DAYS)
            # Asset & Benchmark können in verschiedenen Zeitzonen notieren
            # (SAP.DE: Berlin, SPY: New York) — ohne Normalisierung auf naive
            # Tagesdaten fände der Inner-Join keine gemeinsamen Zeitstempel.
            ret_n, b_n = ret.copy(), b.copy()
            for s in (ret_n, b_n):
                if getattr(s.index, "tz", None) is not None:
                    s.index = s.index.tz_localize(None)
                s.index = s.index.normalize()
            j = pd.concat([ret_n, b_n], axis=1, join="inner").dropna()
            j.columns = ["a", "b"]
            if len(j) > 30 and j["b"].var() > 0:
                out["beta"] = float(j["a"].cov(j["b"]) / j["b"].var())
                out["corr"] = float(j["a"].corr(j["b"]))
    except Exception as e:
        log.warning("risk_metrics: %s", e)
    return out

def dcf_value(fcf_ps: float, growth: float, discount: float, terminal: float, years: int = 5):
    """5-Phasen-DCF je Aktie. Alle Raten als Dezimalzahlen. None, wenn
    discount <= terminal (Gordon-Growth sonst undefiniert)."""
    if not is_num(fcf_ps) or fcf_ps <= 0 or discount <= terminal:
        return None
    pv = sum(fcf_ps * (1 + growth) ** i / (1 + discount) ** i for i in range(1, years + 1))
    tv = fcf_ps * (1 + growth) ** years * (1 + terminal) / (discount - terminal)
    return pv + tv / (1 + discount) ** years

@st.cache_data(show_spinner=False, ttl=900)
def monte_carlo(close: pd.Series, days: int = TRADING_DAYS, n_paths: int = 500, seed: int = 42):
    """Geometrische Brownsche Bewegung auf Basis der letzten 2 Jahre.
    Liefert Perzentil-Pfade (P5/P25/P50/P75/P95) und die Endwerte."""
    try:
        c = close.dropna().tail(2 * TRADING_DAYS)
        logret = np.log(c / c.shift(1)).dropna()
        if len(logret) < 60:
            return None
        mu, sigma = float(logret.mean()), float(logret.std())
        rng = np.random.default_rng(seed)
        z = rng.standard_normal((days, n_paths))
        steps = (mu - 0.5 * sigma ** 2) + sigma * z
        paths = float(c.iloc[-1]) * np.exp(np.vstack([np.zeros(n_paths), np.cumsum(steps, axis=0)]))
        pct = np.percentile(paths, [5, 25, 50, 75, 95], axis=1)
        return {"pct": pct, "final": paths[-1], "start": float(c.iloc[-1])}
    except Exception as e:
        log.warning("monte_carlo: %s", e)
        return None


# ---- Quant-Engine: Scores, Saisonalität, Verteilungen, Regime --------------

def naive_daily(s: pd.Series) -> pd.Series:
    """Zeitzonen entfernen + auf Tagesanfang normalisieren, damit Serien
    verschiedener Börsen (Berlin/New York) sauber joinen."""
    s = s.dropna().copy()
    if getattr(s.index, "tz", None) is not None:
        s.index = s.index.tz_localize(None)
    s.index = s.index.normalize()
    return s

def cvar95(ret: pd.Series):
    """Expected Shortfall: mittlerer Verlust in den schlechtesten 5 % der Tage."""
    try:
        r = ret.dropna()
        if len(r) < 40:
            return None
        cut = np.percentile(r, 5)
        tail = r[r <= cut]
        return float(-tail.mean()) if len(tail) else None
    except Exception:
        return None

@st.cache_data(show_spinner=False, ttl=900)
def seasonality_matrix(close: pd.Series, max_years: int = 12):
    """Monatsrenditen als Matrix Jahr x Monat (in %), plus Durchschnittszeile."""
    try:
        c = naive_daily(close)
        m = c.resample("ME").last().pct_change().dropna() * 100
        if len(m) < 12:
            return None
        df = pd.DataFrame({"J": m.index.year, "M": m.index.month, "r": m.values})
        piv = df.pivot_table(index="J", columns="M", values="r").tail(max_years)
        piv.loc["Ø"] = piv.mean()
        piv.columns = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun",
                       "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"][:len(piv.columns)]             if list(piv.columns) == list(range(1, len(piv.columns) + 1)) else piv.columns
        return piv
    except Exception as e:
        log.warning("seasonality: %s", e)
        return None

def top_drawdowns(close: pd.Series, n: int = 5) -> list:
    """Die n tiefsten Drawdown-Episoden: Tiefe, Beginn, Tief, Dauer, Erholung."""
    try:
        c = naive_daily(close)
        dd = c / c.cummax() - 1
        episodes, start, trough, depth = [], None, None, 0.0
        for date, v in dd.items():
            if v < 0 and start is None:
                start, trough, depth = date, date, v
            elif v < 0 and v < depth:
                trough, depth = date, v
            elif v == 0 and start is not None:
                episodes.append({"Tiefe": depth, "Beginn": start, "Tief": trough,
                                 "Dauer": (date - start).days,
                                 "Erholt nach": (date - trough).days})
                start, trough, depth = None, None, 0.0
        if start is not None:  # laufender Drawdown
            episodes.append({"Tiefe": depth, "Beginn": start, "Tief": trough,
                             "Dauer": (dd.index[-1] - start).days, "Erholt nach": None})
        return sorted(episodes, key=lambda e: e["Tiefe"])[:n]
    except Exception as e:
        log.warning("top_drawdowns: %s", e)
        return []

def vol_regime(close: pd.Series):
    """Aktuelle 30-Tage-Vola relativ zum Langfrist-Median -> Regime-Label."""
    try:
        ret = naive_daily(close).pct_change().dropna()
        roll = ret.rolling(30).std() * np.sqrt(TRADING_DAYS)
        cur, med = safe_last(roll), float(roll.median())
        if cur is None or not med:
            return None, None
        ratio = cur / med
        label = "STRESS" if ratio > 1.5 else ("ERHÖHT" if ratio > 1.1 else "RUHIG")
        return ratio, label
    except Exception:
        return None, None

def rolling_corr(a: pd.Series, b: pd.Series, window: int = 60) -> pd.Series:
    try:
        ra, rb = naive_daily(a).pct_change(), naive_daily(b).pct_change()
        j = pd.concat([ra, rb], axis=1, join="inner").dropna()
        j.columns = ["a", "b"]
        return j["a"].rolling(window).corr(j["b"]).dropna()
    except Exception:
        return pd.Series(dtype=float)

def _latest_two(df: pd.DataFrame, row: str):
    """Neuester und Vorjahres-Wert einer Statement-Zeile (None, None wenn fehlt)."""
    try:
        if row not in df.index:
            return None, None
        s = df.loc[row].dropna()
        s = s[sorted(s.index, reverse=True)]
        t0 = float(s.iloc[0]) if len(s) > 0 else None
        t1 = float(s.iloc[1]) if len(s) > 1 else None
        return t0, t1
    except Exception:
        return None, None

def piotroski_f(inc: pd.DataFrame, cfs: pd.DataFrame, bal: pd.DataFrame):
    """Piotroski F-Score (0-9): akademischer Fundamental-Qualitätsfilter.
    Kriterien ohne Datenbasis werden als 'n. v.' gewertet, nicht als 0."""
    ni0, ni1 = _latest_two(inc, "Net Income")
    ta0, ta1 = _latest_two(bal, "Total Assets")
    ocf0, _ = _latest_two(cfs, "Operating Cash Flow")
    ltd0, ltd1 = _latest_two(bal, "Long Term Debt")
    ca0, ca1 = _latest_two(bal, "Current Assets")
    cl0, cl1 = _latest_two(bal, "Current Liabilities")
    gp0, gp1 = _latest_two(inc, "Gross Profit")
    rev0, rev1 = _latest_two(inc, "Total Revenue")
    sh0, sh1 = _latest_two(bal, "Ordinary Shares Number")
    if sh0 is None:
        sh0, sh1 = _latest_two(bal, "Share Issued")

    def ratio(a, b):
        return a / b if is_num(a) and is_num(b) and b else None

    roa0, roa1 = ratio(ni0, ta0), ratio(ni1, ta1)
    checks = [
        ("Nettogewinn positiv (ROA > 0)", roa0 > 0 if roa0 is not None else None),
        ("Operativer Cashflow positiv", ocf0 > 0 if ocf0 is not None else None),
        ("ROA gestiegen", roa0 > roa1 if None not in (roa0, roa1) else None),
        ("Cashflow > Gewinn (Accruals)", ocf0 > ni0 if None not in (ocf0, ni0) else None),
        ("Langfrist-Verschuldung gesunken",
         ratio(ltd0, ta0) < ratio(ltd1, ta1)
         if None not in (ratio(ltd0, ta0), ratio(ltd1, ta1)) else None),
        ("Current Ratio gestiegen",
         ratio(ca0, cl0) > ratio(ca1, cl1)
         if None not in (ratio(ca0, cl0), ratio(ca1, cl1)) else None),
        ("Keine Aktienverwässerung", sh0 <= sh1 if None not in (sh0, sh1) else None),
        ("Bruttomarge gestiegen",
         ratio(gp0, rev0) > ratio(gp1, rev1)
         if None not in (ratio(gp0, rev0), ratio(gp1, rev1)) else None),
        ("Kapitalumschlag gestiegen",
         ratio(rev0, ta0) > ratio(rev1, ta1)
         if None not in (ratio(rev0, ta0), ratio(rev1, ta1)) else None),
    ]
    avail = [c for _, c in checks if c is not None]
    score = sum(1 for c in avail if c)
    return (score if avail else None), checks, len(avail)

def altman_z(inc: pd.DataFrame, bal: pd.DataFrame, market_cap):
    """Altman Z-Score (Original 1968, Industrieunternehmen):
    Z = 1.2·WC/TA + 1.4·RE/TA + 3.3·EBIT/TA + 0.6·MVE/TL + 1.0·Umsatz/TA."""
    ta, _ = _latest_two(bal, "Total Assets")
    ca, _ = _latest_two(bal, "Current Assets")
    cl, _ = _latest_two(bal, "Current Liabilities")
    re_, _ = _latest_two(bal, "Retained Earnings")
    tl, _ = _latest_two(bal, "Total Liabilities Net Minority Interest")
    ebit, _ = _latest_two(inc, "EBIT")
    if ebit is None:
        ebit, _ = _latest_two(inc, "Operating Income")
    rev, _ = _latest_two(inc, "Total Revenue")
    if not (is_num(ta) and ta > 0 and is_num(tl) and tl > 0 and is_num(market_cap)):
        return None, None
    wc = (ca - cl) if None not in (ca, cl) else 0.0
    z = (1.2 * wc / ta + 1.4 * (re_ or 0.0) / ta + 3.3 * (ebit or 0.0) / ta
         + 0.6 * market_cap / tl + 1.0 * (rev or 0.0) / ta)
    zone = ("SICHER" if z > 2.99 else "GRAUZONE" if z >= 1.81 else "DISTRESS")
    return float(z), zone

def lin_score(x, worst, best):
    """Lineares 0-100-Mapping zwischen kalibrierten Schwellen (auch invers)."""
    if not is_num(x):
        return None
    return float(np.clip((x - worst) / (best - worst), 0, 1) * 100)

def factor_scores(info: dict, close: pd.Series, rm: dict, div_yield, fcf_yield):
    """Vier Faktor-Scores (0-100) aus kalibrierten Schwellen + Composite.
    Kein Ranking gegen ein Universum, sondern eine transparente Heuristik —
    die Schwellen stehen im Methodik-Expander des Quant-Tabs."""
    de = info.get("debtToEquity")
    sma200 = close.rolling(200).mean()
    dist200 = (safe_last(close) / safe_last(sma200) - 1) if safe_last(sma200) else None
    delta = close.diff()
    g = delta.clip(lower=0).ewm(alpha=1 / 14, min_periods=14).mean()
    l = (-delta.clip(upper=0)).ewm(alpha=1 / 14, min_periods=14).mean()
    rsi_now = safe_last(100 - 100 / (1 + g / l.replace(0, np.nan)))

    groups = {
        "Value": [lin_score(info.get("trailingPE"), 40, 8),
                  lin_score(info.get("priceToBook"), 8, 1),
                  lin_score(info.get("enterpriseToEbitda"), 25, 6),
                  lin_score(fcf_yield, 0.0, 0.08)],
        "Qualität": [lin_score(info.get("returnOnEquity"), 0.0, 0.25),
                     lin_score(info.get("operatingMargins"), 0.0, 0.30),
                     lin_score(de / 100 if is_num(de) else None, 2.5, 0.2),
                     lin_score(info.get("currentRatio"), 0.8, 2.5)],
        "Momentum": [lin_score(period_return(close, 182), -0.20, 0.30),
                     lin_score(period_return(close, 365), -0.25, 0.50),
                     lin_score(dist200, -0.15, 0.15),
                     lin_score(rsi_now, 35, 65)],
        "Stabilität": [lin_score(rm.get("vol"), 0.60, 0.15),
                       lin_score(rm.get("max_dd"), -0.60, -0.10),
                       lin_score(rm.get("beta"), 1.8, 0.6)],
    }
    out = {}
    for name, vals in groups.items():
        have = [v for v in vals if v is not None]
        out[name] = float(np.mean(have)) if have else None
    have_g = [v for v in out.values() if v is not None]
    out["Composite"] = float(np.mean(have_g)) if have_g else None
    return out

def compute_risk_gauge(close_1mo: pd.DataFrame):
    """Risk-On/Risk-Off-Barometer (-100..+100) aus vier Marktsignalen:
    VIX-Niveau, Kupfer/Gold-Ratio (Konjunktur), Aktien-Momentum, Dollar."""
    def col(sym):
        return close_1mo[sym].dropna() if sym in getattr(close_1mo, "columns", []) else pd.Series(dtype=float)
    def m_ret(s):
        return float(s.iloc[-1] / s.iloc[0] - 1) if len(s) > 1 and s.iloc[0] else None
    vix = safe_last(col("^VIX"))
    cu, au = col("HG=F"), col("GC=F")
    cg = None
    if len(cu) > 1 and len(au) > 1:
        r = pd.concat([cu, au], axis=1, join="inner").dropna()
        if len(r) > 1 and r.iloc[0, 1] and r.iloc[-1, 1]:
            cg = float((r.iloc[-1, 0] / r.iloc[-1, 1]) / (r.iloc[0, 0] / r.iloc[0, 1]) - 1)
    spy, dxy = m_ret(col("SPY")), m_ret(col("DX-Y.NYB"))
    comps = [
        ("VIX-Niveau", vix, "{:.1f}", np.clip((22 - vix) / 8, -1, 1) if is_num(vix) else None),
        ("Kupfer/Gold 1M", cg, "{:+.1%}", np.clip(cg / 0.05, -1, 1) if is_num(cg) else None),
        ("S&P 500 1M", spy, "{:+.1%}", np.clip(spy / 0.06, -1, 1) if is_num(spy) else None),
        ("Dollar-Index 1M (invers)", dxy, "{:+.1%}",
         np.clip(-dxy / 0.03, -1, 1) if is_num(dxy) else None),
    ]
    sigs = [s for *_, s in comps if s is not None]
    score = float(np.mean(sigs) * 100) if sigs else None
    return score, comps

# ---- Options-, Backtest- und Ereignis-Analytik -----------------------------

def max_pain_strike(calls: pd.DataFrame, puts: pd.DataFrame):
    """Strike, an dem der Gesamtwert aller offenen Optionen am Verfall minimal
    wäre — oft ein Gravitationspunkt für den Kurs zum Verfallstermin."""
    try:
        c = calls.groupby("strike")["openInterest"].sum().fillna(0)
        p = puts.groupby("strike")["openInterest"].sum().fillna(0)
        strikes = np.array(sorted(set(c.index).union(p.index)), dtype=float)
        if len(strikes) < 3:
            return None
        ck, cv = c.index.to_numpy(float), c.to_numpy(float)
        pk, pv = p.index.to_numpy(float), p.to_numpy(float)
        pain = [float((cv * np.clip(s - ck, 0, None)).sum()
                      + (pv * np.clip(pk - s, 0, None)).sum()) for s in strikes]
        return float(strikes[int(np.argmin(pain))])
    except Exception as e:
        log.warning("max_pain: %s", e)
        return None

def atm_implied_vol(calls: pd.DataFrame, puts: pd.DataFrame, spot: float):
    """Implizite Volatilität am Geld (Mittel aus Call und Put)."""
    vals = []
    for df in (calls, puts):
        try:
            d = df.dropna(subset=["impliedVolatility"])
            d = d[(d["impliedVolatility"] > 0.01) & (d["impliedVolatility"] < 5)]
            if len(d):
                row = d.iloc[(d["strike"] - spot).abs().argsort().iloc[0]]
                vals.append(float(row["impliedVolatility"]))
        except Exception:
            pass
    return float(np.mean(vals)) if vals else None

def put_call_ratio(calls: pd.DataFrame, puts: pd.DataFrame, field: str):
    try:
        c = float(calls[field].fillna(0).sum())
        p = float(puts[field].fillna(0).sum())
        return p / c if c > 0 else None
    except Exception:
        return None

@st.cache_data(show_spinner=False, ttl=900)
def run_backtest(close: pd.Series, strategy: str, cost_bps: float = 0.0,
                 fast: int = 50, slow: int = 200, rsi_lo: int = 30, rsi_hi: int = 70):
    """Vektorisierter Long/Flat-Backtest. Einstieg am Folgetag des Signals
    (kein Look-Ahead), optionale Kosten je Positionswechsel in Basispunkten."""
    c = naive_daily(close)
    ret = c.pct_change().fillna(0.0)
    if strategy == "Buy & Hold":
        pos = pd.Series(1.0, index=c.index)
    elif strategy == "SMA-Crossover":
        f, s = c.rolling(fast).mean(), c.rolling(slow).mean()
        pos = (f > s).astype(float)
    else:  # RSI-Mean-Reversion: kaufen unter rsi_lo, verkaufen über rsi_hi
        delta = c.diff()
        g = delta.clip(lower=0).ewm(alpha=1 / 14, min_periods=14).mean()
        l = (-delta.clip(upper=0)).ewm(alpha=1 / 14, min_periods=14).mean()
        rsi = 100 - 100 / (1 + g / l.replace(0, np.nan))
        sig = pd.Series(np.nan, index=c.index)
        sig[rsi < rsi_lo] = 1.0
        sig[rsi > rsi_hi] = 0.0
        pos = sig.ffill().fillna(0.0)
    turnover = pos.diff().abs().fillna(pos.iloc[0])
    strat_ret = pos.shift(1).fillna(0.0) * ret - turnover * cost_bps / 10000.0
    eq = (1 + strat_ret).cumprod()
    years = max(len(eq) / TRADING_DAYS, 1e-9)
    vol = float(strat_ret.std() * np.sqrt(TRADING_DAYS))
    active = strat_ret[pos.shift(1) > 0]
    return {
        "equity": eq,
        "total": float(eq.iloc[-1] - 1),
        "cagr": float(eq.iloc[-1] ** (1 / years) - 1) if eq.iloc[-1] > 0 else None,
        "vol": vol if vol > 0 else None,
        "sharpe": float((strat_ret.mean() * TRADING_DAYS - RISK_FREE_RATE) / vol)
                  if vol > 0 else None,
        "max_dd": float((eq / eq.cummax() - 1).min()),
        "trades": int(round(float(turnover.sum()))),   # einzelne Umschichtungen
        "exposure": float(pos.mean()),
        "hit": float((active > 0).mean()) if len(active) > 20 else None,
    }

CRISIS_WINDOWS = [
    ("Finanzkrise 2008/09", "2007-10-09", "2009-03-09"),
    ("Corona-Crash 2020", "2020-02-19", "2020-03-23"),
    ("Zinsschock 2022", "2022-01-03", "2022-10-12"),
    ("US-Bankenkrise 2023", "2023-03-06", "2023-05-04"),
]

def crisis_playbook(close: pd.Series, bench_close: pd.Series) -> list:
    """Realisierte Performance in definierten historischen Stressfenstern."""
    out = []
    a = naive_daily(close)
    b = naive_daily(bench_close) if bench_close is not None else pd.Series(dtype=float)
    for name, start, end in CRISIS_WINDOWS:
        s, e = pd.Timestamp(start), pd.Timestamp(end)
        wa = a[(a.index >= s) & (a.index <= e)]
        if len(wa) < 10 or a.index[0] > s:
            continue
        row = {"Phase": name,
               "Asset": float(wa.iloc[-1] / wa.iloc[0] - 1),
               "Max DD": float((wa / wa.cummax() - 1).min()),
               "Benchmark": None}
        wb = b[(b.index >= s) & (b.index <= e)]
        if len(wb) > 10:
            row["Benchmark"] = float(wb.iloc[-1] / wb.iloc[0] - 1)
        out.append(row)
    return out

def earnings_reactions(earn_df: pd.DataFrame, close: pd.Series) -> pd.DataFrame:
    """Vergangene Quartalszahlen: EPS-Überraschung + Kursreaktion zum
    nächsten Handelsschluss (T+1)."""
    try:
        if earn_df.empty or "Reported EPS" not in earn_df.columns:
            return pd.DataFrame()
        past = earn_df[earn_df["Reported EPS"].notna()].copy()
        if past.empty:
            return pd.DataFrame()
        c = naive_daily(close)
        rows = []
        for ts, r in past.head(8).iterrows():
            d = pd.Timestamp(ts).tz_localize(None).normalize()
            i = c.index.searchsorted(d, side="right") - 1
            if 0 <= i < len(c) - 1:
                rows.append({"Datum": d, "Surprise": r.get("Surprise(%)"),
                             "Reaktion T+1": float(c.iloc[i + 1] / c.iloc[i] - 1)})
        return pd.DataFrame(rows)
    except Exception as e:
        log.warning("earnings_reactions: %s", e)
        return pd.DataFrame()

@st.cache_data(show_spinner=False, ttl=900)
def savings_plan(close: pd.Series, monthly: float, years: int):
    """Sparplan-Backtest: monatliche Rate zum Monatsersten vs. Einmalanlage."""
    m = naive_daily(close).resample("MS").first().dropna().tail(years * 12)
    if len(m) < 12:
        return None
    shares = (monthly / m).cumsum()
    value = shares * m
    invested = pd.Series(monthly * np.arange(1, len(m) + 1), index=m.index)
    lump = invested.iloc[-1] / m.iloc[0] * m.iloc[-1]
    return {"value": value, "invested": invested, "final": float(value.iloc[-1]),
            "paid": float(invested.iloc[-1]), "lump_final": float(lump),
            "avg_price": float(invested.iloc[-1] / shares.iloc[-1]),
            "start_price": float(m.iloc[0]), "n_months": len(m)}

def scan_watchlist(close_df: pd.DataFrame, symbols: list) -> pd.DataFrame:
    """Signal-Scanner: RSI-Extreme, frische Golden/Death-Crosses, 52W-Hoch-Nähe."""
    rows = []
    for sym in symbols:
        if sym not in getattr(close_df, "columns", []):
            continue
        c = close_df[sym].dropna()
        if len(c) < 60:
            continue
        delta = c.diff()
        g = delta.clip(lower=0).ewm(alpha=1 / 14, min_periods=14).mean()
        l = (-delta.clip(upper=0)).ewm(alpha=1 / 14, min_periods=14).mean()
        rsi = safe_last(100 - 100 / (1 + g / l.replace(0, np.nan)))
        sigs = []
        if len(c) >= 210:
            s50, s200 = c.rolling(50).mean(), c.rolling(200).mean()
            above, above_prev = s50.iloc[-1] > s200.iloc[-1], s50.iloc[-11] > s200.iloc[-11]
            if above and not above_prev:
                sigs.append("Golden Cross")
            if not above and above_prev:
                sigs.append("Death Cross")
        hi = float(c.tail(TRADING_DAYS).max())
        if hi and c.iloc[-1] >= hi * 0.98:
            sigs.append("nahe 52W-Hoch")
        if is_num(rsi) and rsi < 30:
            sigs.append("RSI überverkauft")
        if is_num(rsi) and rsi > 70:
            sigs.append("RSI überkauft")
        d1 = float(c.iloc[-1] / c.iloc[-2] - 1) if len(c) > 1 else None
        rows.append({"Ticker": sym, "Kurs": float(c.iloc[-1]), "1T %": d1,
                     "RSI": rsi, "Signale": " · ".join(sigs) if sigs else "—"})
    return pd.DataFrame(rows)

# ---- Portfolio-Optimierung, Regime, Bewertung, Steuern ---------------------

@st.cache_data(show_spinner=False, ttl=900)
def efficient_frontier(rets: pd.DataFrame, n_samples: int = 4000, seed: int = 11):
    """Markowitz per Zufalls-Sampling (kein Optimierer nötig): Rendite-,
    Vola- und Sharpe-Werte für n zufällige Long-only-Gewichtungen."""
    R = rets.dropna()
    if R.shape[1] < 2 or len(R) < 60:
        return None
    mu = R.mean().to_numpy() * TRADING_DAYS
    cov = R.cov().to_numpy() * TRADING_DAYS
    rng = np.random.default_rng(seed)
    w = rng.random((n_samples, R.shape[1]))
    w = w / w.sum(axis=1, keepdims=True)
    pr = w @ mu
    pv = np.sqrt(np.einsum("ij,jk,ik->i", w, cov, w))
    with np.errstate(divide="ignore", invalid="ignore"):
        sh = np.where(pv > 0, (pr - RISK_FREE_RATE) / pv, np.nan)
    return {"vol": pv, "ret": pr, "sharpe": sh, "w": w, "cols": list(R.columns),
            "mu": mu, "cov": cov,
            "i_maxsh": int(np.nanargmax(sh)), "i_minv": int(np.argmin(pv))}

def portfolio_point(weights: np.ndarray, mu: np.ndarray, cov: np.ndarray):
    r = float(weights @ mu)
    v = float(np.sqrt(weights @ cov @ weights))
    return v, r

def implied_fcf_growth(price: float, fcf_ps: float, discount: float,
                       terminal: float, years: int = 5):
    """Reverse DCF: welches jährliche FCF-Wachstum rechtfertigt den aktuellen
    Kurs? Bisektion über g — der Kern des Expectations Investing."""
    if not (is_num(price) and is_num(fcf_ps)) or fcf_ps <= 0 or discount <= terminal:
        return None
    lo, hi = -0.50, 0.80
    f = lambda g: dcf_value(fcf_ps, g, discount, terminal, years) - price
    try:
        if f(lo) > 0:      # selbst bei -50 % Wachstum wäre die Aktie günstig
            return lo
        if f(hi) < 0:
            return None    # außerhalb des sinnvoll lösbaren Bereichs
        for _ in range(60):
            mid = (lo + hi) / 2
            if f(mid) < 0:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2
    except Exception:
        return None

def market_regime(close: pd.Series):
    """Regime-Klassifikation je Handelstag: Trend (Kurs vs. SMA200) x
    Volatilität (30T vs. Langfrist-Median). Liefert aktuelles Regime plus
    historische Kennzahlen je Regime."""
    c = naive_daily(close)
    if len(c) < 320:
        return None
    ret = c.pct_change()
    sma = c.rolling(200).mean()
    vol30 = ret.rolling(30).std() * np.sqrt(TRADING_DAYS)
    vmed = float(vol30.median())
    lab = np.where(c < sma, "BÄRENMARKT",
                   np.where(vol30 <= vmed * 1.3, "BULLENMARKT (ruhig)",
                            "BULLENMARKT (volatil)"))
    reg = pd.Series(lab, index=c.index)[sma.notna()]
    ret = ret[reg.index]
    rows = []
    for name in ("BULLENMARKT (ruhig)", "BULLENMARKT (volatil)", "BÄRENMARKT"):
        m = reg == name
        if m.sum() < 20:
            continue
        rows.append({"Regime": name, "Zeitanteil": float(m.mean()),
                     "Rendite p.a.": float(ret[m].mean() * TRADING_DAYS),
                     "Vola p.a.": float(ret[m].std() * np.sqrt(TRADING_DAYS))})
    return {"current": str(reg.iloc[-1]), "table": rows}

def atr_series(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Average True Range (Wilder) — die Standardeinheit für Stop-Abstände."""
    h, l, c1 = df["High"], df["Low"], df["Close"].shift(1)
    tr = pd.concat([h - l, (h - c1).abs(), (l - c1).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, min_periods=n).mean()

def kelly_from_monthly(close: pd.Series):
    """Kelly-Anteil aus Monatsrenditen: f* = W − (1−W)/R."""
    m = naive_daily(close).resample("ME").last().pct_change().dropna()
    if len(m) < 24:
        return None
    wins, losses = m[m > 0], m[m < 0]
    if len(wins) < 5 or len(losses) < 5 or losses.mean() == 0:
        return None
    W = float((m > 0).mean())
    R = float(wins.mean() / -losses.mean())
    return {"W": W, "R": R, "kelly": W - (1 - W) / R, "n": len(m)}

def net_after_tax_de(gain: float, allowance: float, church_rate: float,
                     equity_fund: bool):
    """Deutsche Kapitalertragsbesteuerung: Abgeltungsteuer nach §32d
    (bei Kirchensteuer ermäßigt: ESt = Bemessung / (4 + Kirchensteuersatz)),
    plus Soli 5,5 % und Kirchensteuer; optional 30 % Teilfreistellung
    für Aktienfonds. Vorabpauschale/Verlustverrechnung nicht abgebildet."""
    if gain <= 0:
        return {"steuer": 0.0, "netto": gain, "eff": 0.0}
    taxable = max(gain * (0.70 if equity_fund else 1.0) - max(allowance, 0.0), 0.0)
    est = taxable / (4 + church_rate) if church_rate else taxable * 0.25
    soli = est * 0.055
    kist = est * church_rate
    total = est + soli + kist
    return {"steuer": total, "netto": gain - total,
            "eff": total / gain, "bemessung": taxable}

DIVERSIFIER_CANDIDATES = {"GC=F": "Gold", "TLT": "US-Langläufer (TLT)",
                          "BTC-USD": "Bitcoin", "^STOXX50E": "Euro Stoxx 50",
                          "EEM": "Emerging Markets", "CL=F": "Öl (WTI)",
                          "USDJPY=X": "USD/JPY"}

# ---- Synthese & Faktor-Risiko ----------------------------------------------

def aggregate_signals(rows: list):
    """Signal-Matrix zu einem Gesamtbild verdichten: Anteil positiver minus
    negativer Signale, gleichgewichtet — bewusst simpel und nachvollziehbar."""
    dirs = [r["dir"] for r in rows if r.get("dir") is not None]
    if not dirs:
        return None
    pos = sum(1 for d in dirs if d > 0)
    neg = sum(1 for d in dirs if d < 0)
    score = (pos - neg) / len(dirs)
    label = ("KONSTRUKTIV" if score >= 0.25 else
             "DEFENSIV" if score <= -0.25 else "NEUTRAL")
    return {"score": score, "label": label, "pos": pos, "neg": neg,
            "neutral": len(dirs) - pos - neg, "n": len(dirs)}

FACTOR_SYMS = {"Markt (SPY)": "SPY", "Zinsen (TLT)": "TLT",
               "Dollar (DXY)": "DX-Y.NYB", "Öl (WTI)": "CL=F"}

def factor_regression(port_ret: pd.Series, factor_rets: pd.DataFrame):
    """OLS-Mehrfaktormodell: Depot-Renditen ~ Markt + Zinsen + Dollar + Öl.
    Liefert annualisiertes Alpha, Faktor-Betas und R²."""
    try:
        j = pd.concat([port_ret, factor_rets], axis=1, join="inner").dropna()
        if len(j) < 60:
            return None
        y = j.iloc[:, 0].to_numpy()
        X = np.column_stack([np.ones(len(j)), j.iloc[:, 1:].to_numpy()])
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        resid = y - X @ beta
        ss_res = float((resid ** 2).sum())
        ss_tot = float(((y - y.mean()) ** 2).sum())
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else None
        return {"alpha_ann": float(beta[0] * TRADING_DAYS),
                "betas": dict(zip(factor_rets.columns, beta[1:].astype(float))),
                "r2": r2, "n": len(j)}
    except Exception as e:
        log.warning("factor_regression: %s", e)
        return None

def capm_alpha_beta(asset_close: pd.Series, bench_close: pd.Series):
    """Jensen-Alpha und Beta (CAPM, 1 Jahr) plus rollierendes 60T-Beta."""
    try:
        ra = naive_daily(asset_close).pct_change()
        rb = naive_daily(bench_close).pct_change()
        j = pd.concat([ra, rb], axis=1, join="inner").dropna()
        j.columns = ["a", "b"]
        if len(j) < 90:
            return None
        rf_d = RISK_FREE_RATE / TRADING_DAYS
        y = (j["a"] - rf_d).tail(TRADING_DAYS)
        x = (j["b"] - rf_d).tail(TRADING_DAYS)
        beta = float(x.cov(y) / x.var()) if x.var() > 0 else None
        alpha_ann = float((y.mean() - beta * x.mean()) * TRADING_DAYS) \
            if beta is not None else None
        roll_cov = j["a"].rolling(60).cov(j["b"])
        roll_var = j["b"].rolling(60).var()
        roll_beta = (roll_cov / roll_var).dropna()
        return {"alpha_ann": alpha_ann, "beta": beta, "roll_beta": roll_beta}
    except Exception as e:
        log.warning("capm: %s", e)
        return None

GLOSSAR = {
    "KGV": "Kurs geteilt durch Jahresgewinn je Aktie — wie viele Jahresgewinne der Markt für die Aktie bezahlt.",
    "EV/EBITDA": "Unternehmenswert (inkl. Schulden) zum operativen Ergebnis — kapitalstruktur-neutraler als das KGV.",
    "FCF-Rendite": "Freier Cashflow im Verhältnis zum Börsenwert — die 'echte' Verzinsung aus dem Geschäft.",
    "Beta": "Marktsensitivität: 1,5 heißt, das Asset bewegt sich tendenziell 1,5-mal so stark wie der Markt.",
    "Sharpe Ratio": "Überrendite je Einheit Schwankung — die Standardwährung für risikoadjustierte Performance.",
    "Sortino Ratio": "Wie Sharpe, bestraft aber nur Abwärtsschwankung — Aufwärtsvolatilität ist ja kein Risiko.",
    "Max Drawdown": "Größter Verlust vom Hoch zum Tief — die Kennzahl, die man im Crash wirklich spürt.",
    "VaR 95 %": "Tagesverlust, der an 95 % der Tage nicht überschritten wurde — sagt nichts über die restlichen 5 %.",
    "CVaR 95 %": "Durchschnittsverlust der schlechtesten 5 % der Tage — die ehrlichere Schwester des VaR.",
    "Piotroski F-Score": "Neun Bilanz-Checks (0-9) zu Profitabilität, Verschuldung und Effizienz — Klassiker der Fundamentalanalyse.",
    "Altman Z-Score": "Insolvenz-Frühwarnindikator aus fünf Bilanzkennzahlen; über 2,99 gilt als sicher.",
    "RSI": "Relative Stärke der letzten 14 Tage (0-100); über 70 überkauft, unter 30 überverkauft.",
    "MACD": "Differenz zweier gleitender Durchschnitte — macht Trendwechsel im Momentum sichtbar.",
    "Implied Volatility": "Vom Optionsmarkt eingepreiste künftige Schwankung — die Erwartung, nicht die Vergangenheit.",
    "Put/Call-Ratio": "Verhältnis von Put- zu Call-Positionen; hohe Werte zeigen Absicherungs- oder Wettbedarf nach unten.",
    "Max Pain": "Strike, bei dem am Verfallstag die wenigsten Optionen im Geld wären — oft ein Kurs-Magnet.",
    "Reverse DCF": "Rechnet rückwärts: Welches Wachstum muss eintreten, damit der heutige Kurs fair ist?",
    "Kelly-Kriterium": "Mathematisch optimale Einsatzgröße aus Trefferquote und Gewinn/Verlust-Verhältnis.",
    "ATR": "Durchschnittliche Tagesspanne — die natürliche Maßeinheit für Stop-Abstände.",
    "Alpha (Jensen)": "Rendite über das hinaus, was das Marktrisiko (Beta) erklärt — das, wofür Manager bezahlt werden.",
    "Efficient Frontier": "Alle Portfolios mit maximaler Rendite je Risikoniveau — Kern der Portfoliotheorie (Markowitz).",
    "HHI / Effektive Positionen": "Konzentrationsmaß: 1/HHI zeigt, wie vielen gleichgewichteten Positionen das Depot entspricht.",
    "Zinsstruktur-Inversion": "Kurzfristzinsen über Langfristzinsen — historisch eines der besten Rezessionssignale.",
    "Teilfreistellung": "Bei Aktienfonds bleiben 30 % der Erträge steuerfrei — Ausgleich für Steuern auf Fondsebene.",
}

# ---- Asset-Klassen-Analytik: FX, Gold, Rohstoffe, Krypto -------------------

def asset_class(t: str, info: dict) -> str:
    qt = str(info.get("quoteType", "")).upper()
    if qt == "CRYPTOCURRENCY" or t.endswith("-USD") and t.split("-")[0] in (
            "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE"):
        return "KRYPTO"
    if qt == "CURRENCY" or "=X" in t:
        return "FX"
    if t in ("GC=F", "GLD", "XAUUSD=X") or "GOLD" in str(info.get("shortName", "")).upper():
        return "GOLD"
    if t in ("SI=F", "SLV"):
        return "SILBER"
    if qt == "FUTURE" or t.endswith("=F"):
        return "ROHSTOFF"
    return "AKTIE"

COT_KEY_FOR_TICKER = {"GC=F": "GOLD", "GLD": "GOLD", "SI=F": "SILBER", "SLV": "SILBER",
                      "EURUSD=X": "EUR", "USDJPY=X": "JPY", "GBPUSD=X": "GBP",
                      "CL=F": "ÖL (WTI)", "HG=F": "KUPFER",
                      "BTC-USD": "BITCOIN"}

BTC_HALVINGS = ["2012-11-28", "2016-07-09", "2020-05-11", "2024-04-19"]

@st.cache_data(show_spinner=False, ttl=900)
def halving_cycles(btc_close: pd.Series, max_days: int = 1460) -> dict:
    """BTC-Kurs je Halving-Zyklus auf 100 indexiert, x = Tage seit Halving."""
    c = naive_daily(btc_close)
    out = {}
    for h in BTC_HALVINGS:
        h_ts = pd.Timestamp(h)
        if c.index[0] > h_ts + pd.Timedelta(days=3):
            continue          # Historie beginnt erst nach diesem Halving
        seg = c[c.index >= h_ts]
        if len(seg) < 30:
            continue
        seg = seg.iloc[:max_days]
        days = (seg.index - h_ts).days
        out[f"Zyklus ab {h_ts.year}"] = pd.Series(
            (seg / seg.iloc[0] * 100).values, index=days)
    return out

def hourly_activity(hist_1h: pd.DataFrame, tz: str = "Europe/Berlin") -> pd.Series:
    """Durchschnittliche absolute Stundenbewegung je Tageszeit — wann lebt
    der Markt? (Sessions: Asien / London / New York)."""
    try:
        c = hist_1h["Close"].dropna()
        if len(c) < 200 or c.index.tz is None:
            return pd.Series(dtype=float)
        ret = c.pct_change().abs()
        hours = ret.index.tz_convert(tz).hour
        return (ret.groupby(hours).mean() * 100).reindex(range(24))
    except Exception as e:
        log.warning("hourly_activity: %s", e)
        return pd.Series(dtype=float)

def weekday_stats(close: pd.Series) -> pd.DataFrame:
    """Durchschnittsrendite und Trefferquote je Wochentag (5J)."""
    ret = naive_daily(close).pct_change().dropna().tail(5 * TRADING_DAYS)
    if len(ret) < 200:
        return pd.DataFrame()
    names = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag",
             "Samstag", "Sonntag"]
    grp = ret.groupby(ret.index.dayofweek)
    return pd.DataFrame({
        "Tag": [names[d] for d in grp.mean().index],
        "Ø Rendite": grp.mean().values,
        "Trefferquote": grp.apply(lambda x: float((x > 0).mean())).values,
        "n": grp.size().values,
    })

# ============================================================================
#  KI-LAYER  —  ehrlich: liefert (ok, text), nie erfundene "Analysen"
# ============================================================================

def ai_status():
    if AGENT_BACKEND is None:
        return False, "Agent-Framework fehlt — `pip install agno google-genai`."
    if not GOOGLE_API_KEY:
        return False, "Kein Google-API-Key hinterlegt."
    return True, f"{AGENT_BACKEND} · {GEMINI_MODEL_ID}"

@st.cache_data(ttl=1800, show_spinner=False)
def _run_ai_cached(instructions: tuple, prompt: str) -> str:
    """Nur erfolgreiche Antworten landen im Cache — Exceptions werden von
    st.cache_data nicht gespeichert, ein 429 blockiert also nicht 30 Minuten."""
    agent = Agent(model=Gemini(id=GEMINI_MODEL_ID, api_key=GOOGLE_API_KEY),
                  instructions=list(instructions), markdown=True)
    return agent.run(prompt).content

def run_ai(instructions: tuple, prompt: str):
    ok, why = ai_status()
    if not ok:
        return False, f"KI-Analyse nicht verfügbar: {why}"
    try:
        return True, _run_ai_cached(instructions, prompt)
    except Exception as e:
        name = type(e).__name__
        if "ResourceExhausted" in name or "429" in str(e):
            return False, ("KI-Kontingent erschöpft (Rate-Limit 429): Der API-Key funktioniert, "
                           "aber das Anfragelimit des Tarifs ist aktuell aufgebraucht. "
                           "Ein bis zwei Minuten warten und erneut ausführen — oder in "
                           "AI Studio Billing aktivieren bzw. per Env-Var GEMINI_MODEL_ID "
                           "ein Lite-Modell mit höherem Freikontingent wählen.")
        log.exception("KI-Call fehlgeschlagen")
        return False, (f"KI-Analyse fehlgeschlagen ({name}). "
                       f"Modell-ID `{GEMINI_MODEL_ID}` und API-Key prüfen.")

def render_ai_result(ok: bool, text: str, accent=None):
    if ok:
        st.markdown(card(text, accent or CLR["violet"]), unsafe_allow_html=True)
    else:
        st.warning(text)

def ai_chat_reply(prompt: str, context: str) -> str:
    ok, why = ai_status()
    if not ok:
        return f"Hinweis: {why}"
    try:
        tools = []
        if YFinanceTools is not None:
            try:
                tools = [YFinanceTools(stock_price=True, company_info=True)]
            except Exception:
                tools = []
        agent = Agent(model=Gemini(id=GEMINI_MODEL_ID, api_key=GOOGLE_API_KEY),
                      tools=tools, markdown=True,
                      instructions=["Du bist ein Analyst an einem Marktdaten-Terminal.",
                                    "Antworte präzise, faktenbasiert und auf Deutsch.",
                                    "Keine Anlageberatung — Einschätzungen immer mit Begründung und Unsicherheit."])
        return agent.run(f"Kontext zum aktiven Asset: {context}\n\nFrage: {prompt}").content
    except Exception as e:
        log.exception("Chat fehlgeschlagen")
        return f"Anfrage fehlgeschlagen ({type(e).__name__})."

# ============================================================================
#  SIDEBAR
# ============================================================================

try:
    st.sidebar.image("logo.png", width=280)
except Exception:
    pass

st.sidebar.markdown("### ASSET-AUSWAHL")
search_q = st.sidebar.text_input("Suche nach Name (z. B. 'allianz', 'nvidia')",
                                 key="sym_search",
                                 help="Findet das Yahoo-Symbol zum Firmennamen.")
if search_q.strip():
    hits = search_symbols(search_q.strip())
    if hits:
        for h in hits[:6]:
            st.sidebar.button(
                f"{h['symbol']} — {h['name'][:26]} ({h['exch']})",
                key=f"srch_{h['symbol']}", on_click=set_ticker,
                args=(h["symbol"],), use_container_width=True)
    else:
        st.sidebar.caption("Keine Treffer — Suche kurz halten (Firmenname).")
st.sidebar.text_input("Ticker eingeben", value=st.session_state.active_ticker,
                      key="input_widget", on_change=on_ticker_input,
                      help="Yahoo-Finance-Symbole, z. B. AAPL, SAP.DE, BTC-USD, EURUSD=X")

for category, items in ASSET_GROUPS.items():
    with st.sidebar.expander(category):
        for t_sym, name in items:
            st.button(name, key=f"btn_{t_sym}", on_click=set_ticker, args=(t_sym,),
                      use_container_width=True)

with st.sidebar.expander("WATCHLIST", expanded=False):
    if st.button("Aktives Asset aufnehmen", use_container_width=True):
        if st.session_state.active_ticker not in st.session_state.watchlist:
            st.session_state.watchlist.append(st.session_state.active_ticker)
            st.session_state.watchlist.sort()
            db_save_watchlist(st.session_state.watchlist)
            st.rerun()
    for w_sym in list(st.session_state.watchlist):
        wc1, wc2 = st.columns([3, 1])
        wc1.button(w_sym, key=f"wl_{w_sym}", on_click=set_ticker, args=(w_sym,),
                   use_container_width=True)
        if wc2.button("x", key=f"wlx_{w_sym}", use_container_width=True):
            st.session_state.watchlist.remove(w_sym)
            db_save_watchlist(st.session_state.watchlist)
            st.rerun()
    if not st.session_state.watchlist:
        st.caption("Noch leer — der Signal-Scanner im Overview nutzt diese Liste.")

with st.sidebar.expander("KURSALARME", expanded=False):
    al1, al2 = st.columns([1, 1.2])
    al_op = al1.selectbox("Richtung", ["über", "unter"], label_visibility="collapsed")
    al_lvl = al2.number_input("Level", min_value=0.0001, value=100.0,
                              label_visibility="collapsed", format="%.4f")
    if st.button("Alarm für aktives Asset setzen", use_container_width=True):
        st.session_state.alerts.append({"Ticker": st.session_state.active_ticker,
                                        "Op": ">=" if al_op == "über" else "<=",
                                        "Level": float(al_lvl)})
        db_save_alerts(st.session_state.alerts)
        st.rerun()
    for i, a in enumerate(list(st.session_state.alerts)):
        ac1, ac2 = st.columns([3, 1])
        ac1.caption(f"{a['Ticker']} {'≥' if a['Op'] == '>=' else '≤'} {a['Level']:g}")
        if ac2.button("x", key=f"alx_{i}", use_container_width=True):
            st.session_state.alerts.pop(i)
            db_save_alerts(st.session_state.alerts)
            st.rerun()
    if not st.session_state.alerts:
        st.caption("Alarme werden bei jedem App-Lauf geprüft (kein Push).")

st.sidebar.markdown("---")
live_mode = st.sidebar.toggle("Live-Modus (60 s Auto-Refresh der Kurszeile)",
                              value=False)
_ai_ok, _ai_msg = ai_status()
st.sidebar.markdown(
    f"**KI-Status:** {'AKTIV' if _ai_ok else 'INAKTIV'} — {esc(_ai_msg)}",
    help="Sentiment, Audit, Peer-Fazit, Stresstest und Chat benötigen einen Google-API-Key.")
with st.sidebar.expander("DATENQUELLEN-STATUS"):
    if st.button("Status prüfen", use_container_width=True):
        checks = [
            ("Yahoo Finance (Kurse)", not load_history("SPY", "5d", "1d").empty),
            ("EZB Data API (EU-Zinsen)", bool(load_ecb_yields()["yields"])),
            ("FRED (Realzins/Inflation)", not load_fred("T10YIE", 1).empty),
            ("CFTC (COT-Reports)", not load_cot("GOLD").empty),
            ("Gemini-KI", ai_status()[0]),
        ]
        for name, ok_src in checks:
            clr = CLR["up"] if ok_src else CLR["down"]
            word = "OK" if ok_src else "GESTÖRT"
            st.markdown(f"<span style='color:{clr};font-weight:700'>{word}</span> "
                        f"&nbsp;{name}", unsafe_allow_html=True)
        st.caption("Alle Antworten sind gecacht — der Check kostet praktisch "
                   "keine zusätzlichen Anfragen.")

if st.sidebar.button("Alle Daten neu laden", use_container_width=True):
    st.cache_data.clear()
    st.rerun()
st.sidebar.caption("Kurse: Yahoo Finance (ggf. verzögert) · Zinsen EU: EZB · "
                   "Keine Anlageberatung.")

@st.cache_data(ttl=50, show_spinner=False)
def load_live_close(sym: str):
    """Frischer Kurs für den Live-Header (Minutenkerzen, kurzer Cache)."""
    try:
        h = yf.Ticker(sym).history(period="1d", interval="1m")
        return h if not h.empty else yf.Ticker(sym).history(period="5d")
    except Exception:
        return pd.DataFrame()

# ============================================================================
#  HAUPTDATEN DES AKTIVEN ASSETS
# ============================================================================

ticker = st.session_state.active_ticker
is_european_asset = any(x in ticker for x in (".DE", ".F", "^GDAXI", "^STOXX50E"))
is_fx = "=X" in ticker
decimals = 4 if is_fx else 2

with st.spinner(f"Lade Marktdaten für {ticker} …"):
    info = load_info(ticker)
    hist = load_history(ticker, "max", "1d")

if hist.empty or "Close" not in hist:
    st.error(f"Keine Kursdaten für **{ticker}** gefunden. Symbol prüfen — "
             f"Yahoo-Notation verwenden (z. B. `SAP.DE`, `BTC-USD`, `EURUSD=X`).")
    st.stop()

short_name = info.get("shortName") or info.get("longName") or ticker
currency = info.get("currency") or ""
current_price = float(hist["Close"].iloc[-1])
prev_price = float(hist["Close"].iloc[-2]) if len(hist) > 1 else current_price
change = current_price - prev_price
change_pct = (change / prev_price * 100) if prev_price else 0.0
last_ts = hist.index[-1]

# Dividendenrendite selbst rechnen (dividendRate / Kurs): das yfinance-Feld
# `dividendYield` wechselte zwischen Dezimal- und Prozentformat — so ist es eindeutig.
div_rate = info.get("dividendRate")
div_yield = (div_rate / current_price) if is_num(div_rate) and current_price else None
fcf_yield = (info.get("freeCashflow") / info.get("marketCap")
             if is_num(info.get("freeCashflow")) and is_num(info.get("marketCap"))
             and info.get("marketCap") else None)

head_l, head_r = st.columns([3, 1])
with head_l:
    st.markdown(f"## {esc(short_name)} &nbsp; <span style='color:{CLR['muted']};font-size:1.1rem'>"
                f"{esc(ticker)}</span>", unsafe_allow_html=True)
    meta = " · ".join(x for x in (info.get("sector"), info.get("industry"),
                                  info.get("exchange")) if x)
    if meta:
        st.caption(esc(meta))
@st.fragment(run_every="60s" if live_mode else None)
def live_quote_fragment():
    h_live = load_live_close(ticker) if live_mode else pd.DataFrame()
    if not h_live.empty and "Close" in h_live:
        px = float(h_live["Close"].dropna().iloc[-1])
        ts = h_live.index[-1]
    else:
        px, ts = current_price, last_ts
    dlt = (px / prev_price - 1) * 100 if prev_price else 0.0
    mode_txt = "Auto-Refresh aktiv" if live_mode else f"Stand {ts.strftime('%d.%m.%Y %H:%M')}"
    st.markdown(
        f"<div style='text-align:right;padding-top:8px'>{badge('LIVE', 'live')}<br>"
        f"<span style='font-size:1.3rem;font-weight:700'>{currency} "
        f"{px:,.{decimals}f}</span> "
        f"<span style='color:{updown(dlt)};font-weight:600'>{dlt:+.2f} %</span><br>"
        f"<span class='news-meta'>{esc(mode_txt)}</span></div>",
        unsafe_allow_html=True)

with head_r:
    live_quote_fragment()

# Kernkennzahlen einmal zentral berechnen — mehrere Tabs greifen darauf zu.
bench_sym = BENCH_EU if is_european_asset else BENCH_US
bench_hist = load_history(bench_sym, "2y", "1d")
rm = risk_metrics(hist["Close"].tail(2 * TRADING_DAYS),
                  bench_hist["Close"] if not bench_hist.empty else None)
fs = factor_scores(info, hist["Close"], rm, div_yield, fcf_yield)

a_class = asset_class(ticker, info)

# Navigation statt st.tabs: Streamlit führt sonst bei JEDEM Rerun alle
# Bereiche aus — mit 13 Sektionen der größte einzelne Performance-Hebel.
NAV_PAGES = ["COMMAND CENTER", "GLOBAL OVERVIEW", "MAIN ASSET", "QUANT & SCORES",
             "ASSET-CLASS DESK", "OPTIONS-DESK", "SMART MONEY", "STRATEGY LAB",
             "FINANCIALS & AUDIT", "PEER ANALYSIS", "MACRO DESK", "PORTFOLIO",
             "RISK & STRESS TEST"]
nav = st.radio("Navigation", NAV_PAGES, horizontal=True, key="nav",
               label_visibility="collapsed")
st.markdown("<hr style='margin:.2em 0 1em;opacity:.2'>", unsafe_allow_html=True)

# ============================================================================
#  TAB — COMMAND CENTER: alle Signale des Terminals in einem Bild
# ============================================================================

def render_command_center():
    st.markdown(f"### COMMAND CENTER — {esc(short_name)}")
    st.caption("Jede Zeile ist ein Signal aus einem Fachbereich des Terminals, "
               "gleichgewichtet zu einem Gesamtbild verdichtet. Regelbasierte "
               "Heuristik zur Orientierung — kein Prognosemodell, keine Empfehlung.")

    sig_rows = []
    def add_sig(kat, name, wert, direction, grund):
        sig_rows.append({"Kategorie": kat, "Signal": name, "Wert": wert,
                         "dir": direction, "Einordnung": grund})

    # Trend & Momentum
    sma200_cc = safe_last(hist["Close"].rolling(200).mean())
    if sma200_cc:
        d200 = current_price / sma200_cc - 1
        add_sig("Trend", "Kurs vs. SMA 200", f"{d200:+.1%}",
                1 if d200 > 0.02 else (-1 if d200 < -0.02 else 0),
                "über der 200-Tage-Linie = intakter Aufwärtstrend")
    sma50_cc = safe_last(hist["Close"].rolling(50).mean())
    if sma50_cc and sma200_cc:
        gc = sma50_cc > sma200_cc
        add_sig("Trend", "SMA 50 vs. SMA 200", "50 über 200" if gc else "50 unter 200",
                1 if gc else -1, "Golden-Cross-Konstellation" if gc
                else "Death-Cross-Konstellation")
    p6 = period_return(hist["Close"], 182)
    if p6 is not None:
        add_sig("Momentum", "6-Monats-Rendite", f"{p6:+.1%}",
                1 if p6 > 0.08 else (-1 if p6 < -0.08 else 0),
                "Momentum gehört zu den robustesten Faktoren")
    rsi_cc = None
    if "Momentum" in fs or True:
        _d = hist["Close"].diff()
        _g = _d.clip(lower=0).ewm(alpha=1 / 14, min_periods=14).mean()
        _l = (-_d.clip(upper=0)).ewm(alpha=1 / 14, min_periods=14).mean()
        rsi_cc = safe_last(100 - 100 / (1 + _g / _l.replace(0, np.nan)))
    if is_num(rsi_cc):
        add_sig("Momentum", "RSI (14)", f"{rsi_cc:.0f}",
                1 if rsi_cc < 30 else (-1 if rsi_cc > 70 else 0),
                "unter 30 überverkauft, über 70 überhitzt")

    # Bewertung
    if fs.get("Value") is not None:
        add_sig("Bewertung", "Value-Faktorscore", f"{fs['Value']:.0f}/100",
                1 if fs["Value"] > 60 else (-1 if fs["Value"] < 40 else 0),
                "Multiples relativ zu kalibrierten Schwellen")
    tgt_cc = info.get("targetMeanPrice")
    if is_num(tgt_cc) and current_price:
        ups = tgt_cc / current_price - 1
        add_sig("Bewertung", "Analysten-Upside", f"{ups:+.1%}",
                1 if ups > 0.10 else (-1 if ups < -0.05 else 0),
                "Konsens-Kursziel vs. aktueller Kurs")
    fcf_cc, sh_cc = info.get("freeCashflow"), info.get("sharesOutstanding")
    if is_num(fcf_cc) and is_num(sh_cc) and sh_cc:
        ig_cc = implied_fcf_growth(current_price, fcf_cc / sh_cc, 0.10, 0.02)
        rg_cc = info.get("revenueGrowth")
        if ig_cc is not None and is_num(rg_cc):
            add_sig("Bewertung", "Eingepreistes vs. gemeldetes Wachstum",
                    f"{ig_cc:+.1%} vs. {rg_cc:+.1%}",
                    1 if ig_cc < rg_cc - 0.02 else (-1 if ig_cc > rg_cc + 0.05 else 0),
                    "Reverse DCF: niedrige Erwartungen sind leichter zu schlagen")

    # Qualität
    inc_cc, cfs_cc, bal_cc = load_financial_statements(ticker)
    if not (inc_cc.empty and bal_cc.empty):
        p_sc, _, p_av = piotroski_f(inc_cc, cfs_cc, bal_cc)
        if p_sc is not None and p_av >= 5:
            add_sig("Qualität", "Piotroski F-Score", f"{p_sc}/{p_av}",
                    1 if p_sc >= 7 else (-1 if p_sc <= 3 else 0),
                    "Bilanzqualität in neun Einzelchecks")
        z_cc, zone_cc = altman_z(inc_cc, bal_cc, info.get("marketCap"))
        if z_cc is not None:
            add_sig("Qualität", "Altman Z-Score", f"{z_cc:.2f} ({zone_cc})",
                    1 if zone_cc == "SICHER" else (-1 if zone_cc == "DISTRESS" else 0),
                    "Insolvenzrisiko-Frühwarnung")

    # Risiko & Marktumfeld
    _, vr_lab_cc = vol_regime(hist["Close"])
    if vr_lab_cc:
        add_sig("Risiko", "Volatilitäts-Regime", vr_lab_cc,
                1 if vr_lab_cc == "RUHIG" else (-1 if vr_lab_cc == "STRESS" else 0),
                "aktuelle Schwankung vs. Normalniveau")
    mr_cc = market_regime(hist["Close"])
    if mr_cc:
        cur_r = mr_cc["current"]
        add_sig("Risiko", "Markt-Regime des Assets", cur_r,
                -1 if "BÄREN" in cur_r else (1 if "ruhig" in cur_r else 0),
                "Trend- und Volatilitätslage kombiniert")
    g_sc_cc, _ = compute_risk_gauge(
        load_batch_close(("^VIX", "HG=F", "GC=F", "SPY", "DX-Y.NYB"), "1mo"))
    if g_sc_cc is not None:
        add_sig("Markt", "Risk-On/Off-Barometer", f"{g_sc_cc:+.0f}",
                1 if g_sc_cc > 33 else (-1 if g_sc_cc < -33 else 0),
                "VIX, Kupfer/Gold, Aktien-Momentum, Dollar")
    us_cc = load_us_yields()["yields"]
    if is_num(us_cc.get("10Y")) and is_num(us_cc.get("3M")):
        sp_cc = us_cc["10Y"] - us_cc["3M"]
        add_sig("Markt", "US-Zinskurve (10J−3M)", f"{sp_cc:+.2f} PP",
                -1 if sp_cc < 0 else 0,
                "Inversion ist ein historisch starkes Rezessionssignal")

    # Smart Money & Derivatemarkt
    ins_cc = load_insider_transactions(ticker)
    if not ins_cc.empty and "Value" in ins_cc.columns:
        t_col = next((c for c in ("Text", "Transaction") if c in ins_cc.columns), None)
        if t_col:
            tl = ins_cc[t_col].astype(str).str.lower()
            b_cc = float(ins_cc.loc[tl.str.contains("purchase|buy"), "Value"].fillna(0).sum())
            s_cc = float(ins_cc.loc[tl.str.contains("sale|sell"), "Value"].fillna(0).sum())
            if b_cc + s_cc > 0:
                add_sig("Smart Money", "Insider-Transaktionen",
                        f"Käufe {fmt_big(b_cc, '$')} / Verkäufe {fmt_big(s_cc, '$')}",
                        1 if b_cc > s_cc * 1.5 else
                        (-1 if s_cc > max(b_cc * 3, 1e6) else 0),
                        "Insiderkäufe gelten als starkes Signal")
    spf_cc = info.get("shortPercentOfFloat")
    if is_num(spf_cc):
        add_sig("Smart Money", "Short-Quote", f"{spf_cc:.1%}",
                -1 if spf_cc > 0.10 else 0,
                "hoher Anteil professioneller Skeptiker")
    exp_cc = load_option_expiries(ticker)
    if exp_cc:
        c_cc, p_cc = load_option_chain(ticker, exp_cc[0])
        pcr_cc = put_call_ratio(c_cc, p_cc, "openInterest")
        if is_num(pcr_cc):
            add_sig("Derivatemarkt", "Put/Call-Ratio (OI)", f"{pcr_cc:.2f}",
                    1 if pcr_cc < 0.7 else (-1 if pcr_cc > 1.2 else 0),
                    "Positionierung am Optionsmarkt")

    # Assetklassen-Signale: COT-Extreme, Realzins (Gold), 200W-SMA (Krypto)
    cot_key_cc = COT_KEY_FOR_TICKER.get(ticker)
    if cot_key_cc:
        cot_cc = load_cot(cot_key_cc)
        if not cot_cc.empty:
            p_cc = percentile_of_last(cot_cc["net"])
            if is_num(p_cc):
                add_sig("Positionierung", f"COT Large Specs ({cot_key_cc})",
                        f"{p_cc:.0f}. Perzentil",
                        1 if p_cc < 15 else (-1 if p_cc > 85 else 0),
                        "Extreme Einseitigkeit wirkt kontrarisch")
    if a_class in ("GOLD", "SILBER"):
        r10_cc = load_fred("DFII10", years=3)
        if len(r10_cc) > 70:
            tr_cc = float(r10_cc.iloc[-1] - r10_cc.iloc[-64])
            add_sig("Makro-Treiber", "US-Realzins, 3M-Trend", f"{tr_cc:+.2f} PP",
                    1 if tr_cc < -0.10 else (-1 if tr_cc > 0.10 else 0),
                    "fallende Realzinsen stützen zinslose Assets")
    if a_class == "KRYPTO":
        w200_cc = safe_last(naive_daily(hist["Close"]).resample("W").last()
                            .rolling(200).mean())
        if is_num(w200_cc) and current_price:
            d_cc = current_price / w200_cc - 1
            add_sig("Zyklus", "Abstand zur 200-Wochen-Linie", f"{d_cc:+.0%}",
                    1 if d_cc > 0 else -1,
                    "historisch die Trennlinie der Krypto-Zyklen")
    if a_class == "FX" and ticker == "EURUSD=X":
        us_cc2 = load_us_yields()["yields"]
        eu_cc2 = load_ecb_yields()["yields"]
        if is_num(us_cc2.get("2Y")) and is_num(eu_cc2.get("2Y")):
            dif_cc = us_cc2["2Y"] - eu_cc2["2Y"]
            add_sig("Makro-Treiber", "Zinsdifferenz 2J (US − EU)",
                    f"{dif_cc:+.2f} PP", 0,
                    "Niveau der Differenz — Richtung entscheidet der Trend, "
                    "der hier ohne Historie nicht messbar ist")

    agg = aggregate_signals(sig_rows)
    if agg is None:
        st.info("Für dieses Instrument sind zu wenige Signale berechenbar.")
    else:
        agg_clr = (CLR["up"] if agg["label"] == "KONSTRUKTIV" else
                   CLR["down"] if agg["label"] == "DEFENSIV" else CLR["amber"])
        vc1, vc2 = st.columns([1, 2.4])
        vc1.markdown(metric_card(
            "TERMINAL-GESAMTBILD", agg["label"],
            f"{agg['pos']} positiv · {agg['neutral']} neutral · {agg['neg']} negativ "
            f"({agg['n']} Signale, gleichgewichtet)", agg_clr),
            unsafe_allow_html=True)
        with vc2:
            df_sig = pd.DataFrame([{
                "Kategorie": r["Kategorie"], "Signal": r["Signal"], "Wert": r["Wert"],
                "Tendenz": ("POSITIV" if r["dir"] and r["dir"] > 0 else
                            "NEGATIV" if r["dir"] and r["dir"] < 0 else "NEUTRAL"),
                "Einordnung": r["Einordnung"],
            } for r in sig_rows])
            sty = df_sig.style.map(
                lambda v: (f"color:{CLR['up']};font-weight:700" if v == "POSITIV" else
                           f"color:{CLR['down']};font-weight:700" if v == "NEGATIV" else
                           f"color:{CLR['amber']}"), subset=["Tendenz"])
            st.dataframe(sty, hide_index=True, use_container_width=True,
                         height=min(38 * len(df_sig) + 40, 560))

        st.markdown(f"#### KI-GESAMTANALYSE {badge('KI', 'ki')}", unsafe_allow_html=True)
        if st.button("GESAMTANALYSE ERSTELLEN"):
            matrix_txt = "\n".join(
                f"[{r['Kategorie']}] {r['Signal']}: {r['Wert']} -> "
                f"{'positiv' if r['dir'] and r['dir'] > 0 else 'negativ' if r['dir'] and r['dir'] < 0 else 'neutral'}"
                for r in sig_rows)
            with st.spinner("Verdichte alle Signale …"):
                ok_cc, res_cc = run_ai(
                    ("Du bist Chief Investment Officer. Dir liegt die komplette "
                     "Signal-Matrix eines Analyse-Terminals vor. Nutze NUR diese Daten.",
                     "Struktur exakt: 'GESAMTURTEIL:' (KONSTRUKTIV/NEUTRAL/DEFENSIV mit "
                     "Konfidenz niedrig/mittel/hoch, 1 Satz), 'TREIBER:' (die 3 "
                     "wichtigsten Signale mit Begründung), 'RISIKEN:' (die 2 größten "
                     "Gegenargumente), 'FALSIFIZIERUNG:' (1 Satz: welche Entwicklung "
                     "würde dieses Bild kippen). Deutsch, präzise, keine Emojis."),
                    f"Asset: {ticker} ({short_name}), Sektor {info.get('sector', '–')}."
                    f"\nSignal-Matrix:\n{matrix_txt}")
            st.session_state.ai_results[("command", ticker)] = (ok_cc, res_cc)
        if ("command", ticker) in st.session_state.ai_results:
            render_ai_result(*st.session_state.ai_results[("command", ticker)])

# ============================================================================
#  TAB — GLOBAL OVERVIEW
# ============================================================================

def render_overview():
    if st.session_state.alerts:
        al_syms = tuple(sorted({a["Ticker"] for a in st.session_state.alerts}))
        al_close = load_batch_close(al_syms, period="5d")
        fired = []
        for a in st.session_state.alerts:
            cur = (safe_last(al_close[a["Ticker"]])
                   if a["Ticker"] in getattr(al_close, "columns", []) else None)
            if cur is None:
                continue
            if (a["Op"] == ">=" and cur >= a["Level"]) or \
               (a["Op"] == "<=" and cur <= a["Level"]):
                fired.append((a, cur))
        if fired:
            for a, cur in fired:
                st.warning(f"ALARM: {a['Ticker']} notiert bei {cur:,.4g} — "
                           f"Schwelle {'≥' if a['Op'] == '>=' else '≤'} {a['Level']:g} erreicht.")
            if st.button("Ausgelöste Alarme quittieren"):
                st.session_state.alerts = [a for a in st.session_state.alerts
                                           if a not in [f[0] for f in fired]]
                db_save_alerts(st.session_state.alerts)
                st.rerun()

    st.markdown("### GLOBALER MARKTÜBERBLICK")
    ov_syms = tuple(s for s, _ in OVERVIEW_TICKERS)
    ov_close = load_batch_close(ov_syms, period="5d")
    cols = st.columns(3)
    for i, (sym, name) in enumerate(OVERVIEW_TICKERS):
        with cols[i % 3]:
            s = ov_close[sym].dropna() if sym in getattr(ov_close, "columns", []) else pd.Series(dtype=float)
            if len(s) >= 2:
                cp, pp = float(s.iloc[-1]), float(s.iloc[-2])
                ch = (cp / pp - 1) * 100
                st.markdown(metric_card(f"{esc(name)} <span style='color:{CLR['muted']}'>({esc(sym)})</span>",
                                        f"{cp:,.2f}", f"{ch:+.2f} %", updown(ch)),
                            unsafe_allow_html=True)
            else:
                st.markdown(metric_card(esc(name), "–", "keine Daten", CLR["muted"]),
                            unsafe_allow_html=True)

    st.markdown("#### Relative Performance (6 Monate, indexiert = 100)")
    ov6 = load_batch_close(ov_syms, period="6mo")
    if not ov6.empty:
        norm = ov6.dropna(how="all").apply(lambda col: col / col.dropna().iloc[0] * 100
                                           if col.dropna().size else col)
        fig_rel = go.Figure()
        palette = [CLR["cyan"], CLR["up"], CLR["amber"], CLR["violet"], CLR["gold"], CLR["blue"]]
        for j, (sym, name) in enumerate(OVERVIEW_TICKERS):
            if sym in norm.columns and norm[sym].dropna().size:
                fig_rel.add_trace(go.Scatter(x=norm.index, y=norm[sym], name=name,
                                             line=dict(width=2, color=palette[j % len(palette)])))
        fig_rel.update_layout(template="plotly_dark", height=330,
                              margin=dict(l=0, r=0, t=10, b=0),
                              legend=dict(orientation="h", y=1.12),
                              paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig_rel, use_container_width=True)
    else:
        st.info("Vergleichsdaten aktuell nicht verfügbar.")

    col_news, col_macro = st.columns([1.5, 1])
    with col_news:
        st.markdown("#### Top-Schlagzeilen (Markt)")
        for n in load_news("SPY")[:4]:
            when = f" · {esc(n['when'])}" if n.get("when") else ""
            st.markdown(f"<div class='newsitem'><a href='{esc(n['link'])}' target='_blank'>"
                        f"{esc(n['title'])}</a><br><span class='news-meta'>"
                        f"{esc(n['publisher'])}{when}</span></div>"
                        f"<hr style='margin:.4em 0;opacity:.12'>", unsafe_allow_html=True)
    with col_macro:
        st.markdown("#### Makro-Schnellcheck")
        mq = load_macro_quotes()
        us_y = load_us_yields()["yields"]
        rows = [
            ("VIX (Fear Index)", fmt(mq.get("VIX")), CLR["cyan"]),
            ("US 10Y Yield", fmt(us_y.get("10Y"), "{:.2f} %"), CLR["up"]),
            ("WTI Crude Oil", fmt(mq.get("OIL"), "${:,.2f}"), CLR["amber"]),
            ("Gold (Spot)", fmt(mq.get("GOLD"), "${:,.2f}"), CLR["gold"]),
        ]
        inner = "".join(f"<p style='margin:.35em 0'><b>{lbl}:</b>"
                        f"<span style='float:right;color:{c}'>{val}</span></p>"
                        for lbl, val, c in rows)
        st.markdown(card(inner), unsafe_allow_html=True)

    st.markdown("#### WATCHLIST-SIGNAL-SCANNER")
    if not st.session_state.watchlist:
        st.info("Watchlist ist leer — in der Sidebar Assets aufnehmen, dann scannt "
                "das Terminal hier automatisch auf Golden/Death-Cross, RSI-Extreme "
                "und 52-Wochen-Hoch-Nähe.")
    else:
        wl_close = load_batch_close(tuple(st.session_state.watchlist), period="2y")
        scan = scan_watchlist(wl_close, st.session_state.watchlist)
        if scan.empty:
            st.info("Keine Kursdaten für die Watchlist abrufbar.")
        else:
            st.dataframe(pd.DataFrame({
                "Ticker": scan["Ticker"],
                "Kurs": scan["Kurs"].map(lambda v: fmt(v)),
                "1T %": scan["1T %"].map(lambda v: fmt(v, "{:+.2%}")),
                "RSI (14)": scan["RSI"].map(lambda v: fmt(v, "{:.0f}")),
                "Signale": scan["Signale"],
            }), hide_index=True, use_container_width=True)
            hits = scan[scan["Signale"] != "—"]
            if len(hits):
                st.caption(f"{len(hits)} von {len(scan)} Werten mit aktivem Signal.")
            st.markdown(f"#### TAGES-BRIEFING {badge('KI', 'ki')}", unsafe_allow_html=True)
            if st.button("BRIEFING GENERIEREN"):
                us_b = load_us_yields()["yields"]
                sp_b = (us_b.get("10Y") - us_b.get("3M")
                        if is_num(us_b.get("10Y")) and is_num(us_b.get("3M")) else None)
                g_sc, _ = compute_risk_gauge(
                    load_batch_close(("^VIX", "HG=F", "GC=F", "SPY", "DX-Y.NYB"), "1mo"))
                mq_b = load_macro_quotes()
                scan_txt = "\n".join(
                    f"{r['Ticker']}: {fmt(r['1T %'], '{:+.2%}')} Tag, RSI "
                    f"{fmt(r['RSI'], '{:.0f}')}, Signale: {r['Signale']}"
                    for _, r in scan.iterrows())
                news_txt = "; ".join(n["title"] for n in load_news("SPY")[:4])
                with st.spinner("Erstelle Briefing …"):
                    ok_b, brief = run_ai(
                        ("Du bist Morning-Desk-Analyst. Nutze NUR die übergebenen Daten.",
                         "Struktur exakt: 'MARKTLAGE:' (2 Sätze), 'WATCHLIST:' "
                         "(je Auffälligkeit 1 Zeile, Unauffälliges weglassen), "
                         "'FOKUS HEUTE:' (1 Satz). Deutsch, nüchtern, keine Emojis."),
                        f"Makro: VIX {fmt(mq_b.get('VIX'))}, Risk-Gauge "
                        f"{fmt(g_sc, '{:.0f}')} (-100..+100), US 10J-3M-Spread "
                        f"{fmt(sp_b, '{:+.2f}')} PP.\nWatchlist-Scan:\n{scan_txt}\n"
                        f"Markt-Schlagzeilen: {news_txt}")
                st.session_state.ai_results[("briefing", "global")] = (ok_b, brief)
            if ("briefing", "global") in st.session_state.ai_results:
                render_ai_result(*st.session_state.ai_results[("briefing", "global")])

# ============================================================================
#  TAB — MAIN ASSET
# ============================================================================

def render_main():
    # --- Kopfmetriken + 52-Wochen-Einordnung --------------------------------
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("LAST PRICE", f"{currency} {current_price:,.{decimals}f}",
              f"{change:+,.{decimals}f} ({change_pct:+.2f} %)")
    m2.metric("OPEN", f"{currency} {hist['Open'].iloc[-1]:,.{decimals}f}")
    m3.metric("TAGESHOCH", f"{currency} {hist['High'].iloc[-1]:,.{decimals}f}")
    m4.metric("TAGESTIEF", f"{currency} {hist['Low'].iloc[-1]:,.{decimals}f}")
    vol_last = hist["Volume"].iloc[-1] if "Volume" in hist else None
    m5.metric("VOLUMEN", f"{vol_last:,.0f}" if is_num(vol_last) and vol_last > 0 else "–")

    w52 = hist["Close"].tail(TRADING_DAYS)
    lo52, hi52 = float(w52.min()), float(w52.max())
    if hi52 > lo52:
        pos = (current_price - lo52) / (hi52 - lo52) * 100
        off_high = current_price / hi52 - 1
        st.markdown(card(
            f"<div class='metric-title'>52-WOCHEN-SPANNE &nbsp; "
            f"<span style='color:{CLR['muted']}'>Abstand zum Hoch: "
            f"<b style='color:{updown(off_high)}'>{off_high:+.1%}</b></span></div>"
            f"<div class='range-track'><div class='range-fill' style='width:100%'></div>"
            f"<div class='range-dot' style='left:{pos:.1f}%'></div></div>"
            f"<div style='display:flex;justify-content:space-between' class='news-meta'>"
            f"<span>{lo52:,.{decimals}f}</span><span>{hi52:,.{decimals}f}</span></div>"),
            unsafe_allow_html=True)

    ecol1, ecol2 = st.columns(2)
    with ecol1:
        nxt = load_next_earnings(ticker)
        if nxt:
            delta_d = (nxt - datetime.now().date()).days
            sub = f"in {delta_d} Tagen" if delta_d >= 0 else f"vor {-delta_d} Tagen"
            st.markdown(metric_card("NÄCHSTE QUARTALSZAHLEN", nxt.strftime("%d.%m.%Y"),
                                    sub, CLR["amber"] if 0 <= delta_d <= 14 else None),
                        unsafe_allow_html=True)
    with ecol2:
        inst, insd = info.get("heldPercentInstitutions"), info.get("heldPercentInsiders")
        spf, srat = info.get("shortPercentOfFloat"), info.get("shortRatio")
        if any(is_num(v) for v in (inst, insd, spf, srat)):
            hot = is_num(spf) and spf > 0.10
            main_val = (f"Short-Quote {fmt(spf, '{:.1%}')}" if is_num(spf)
                        else f"Institutionen {fmt(inst, '{:.0%}')}")
            sub = (f"Institutionen {fmt(inst, '{:.0%}')} · Insider {fmt(insd, '{:.1%}')} · "
                   f"Eindeckung {fmt(srat, '{:.1f}')} Tage"
                   + (" — hoher Short-Anteil (Squeeze-Risiko)" if hot else ""))
            st.markdown(metric_card("OWNERSHIP & SHORT INTEREST", main_val, sub,
                                    CLR["amber"] if hot else None), unsafe_allow_html=True)

    # --- Chart mit wählbaren Indikator-Panels -------------------------------
    st.markdown("### CHART & TECHNISCHE ANALYSE")
    cc1, cc2, cc3 = st.columns([1, 1, 2.5])
    interval = cc1.selectbox("Intervall", ["1D", "1W", "1h"], index=0)
    timeframe = cc2.selectbox("Zeitraum", ["1M", "3M", "6M", "1J", "2J", "5J", "Max"], index=3)
    inds = cc3.multiselect("Indikatoren",
                           ["SMA 50", "SMA 200", "Bollinger (20,2)", "Levels (52W)",
                            "Volumen", "RSI (14)", "MACD"],
                           default=["SMA 50", "SMA 200", "Volumen", "RSI (14)"])

    if interval == "1h":
        c_hist = load_history(ticker, "700d", "1h")   # Yahoo-Limit für Stundendaten
    elif interval == "1W":
        c_hist = load_history(ticker, "max", "1wk")
    else:
        c_hist = hist
    if c_hist.empty:
        c_hist = hist

    span_days = {"1M": 31, "3M": 92, "6M": 183, "1J": 366, "2J": 731, "5J": 1827, "Max": 99999}[timeframe]
    plot_df = c_hist[c_hist.index >= c_hist.index[-1] - pd.Timedelta(days=span_days)].copy()
    plot_df = add_indicators(plot_df)

    panes = [p for p in ["Volumen", "RSI (14)", "MACD"] if p in inds]
    rows = 1 + len(panes)
    heights = [0.56] + [0.44 / len(panes)] * len(panes) if panes else [1.0]
    row_of = {p: i + 2 for i, p in enumerate(panes)}

    fig = make_subplots(rows=rows, cols=1, shared_xaxes=True,
                        vertical_spacing=0.03, row_heights=heights)
    fig.add_trace(go.Candlestick(x=plot_df.index, open=plot_df["Open"], high=plot_df["High"],
                                 low=plot_df["Low"], close=plot_df["Close"], name="Kurs",
                                 increasing_line_color=CLR["up"], decreasing_line_color=CLR["down"]),
                  row=1, col=1)
    if "Bollinger (20,2)" in inds:
        fig.add_trace(go.Scatter(x=plot_df.index, y=plot_df["BB_UP"], name="BB oben",
                                 line=dict(color="rgba(61,220,255,.5)", width=1)), row=1, col=1)
        fig.add_trace(go.Scatter(x=plot_df.index, y=plot_df["BB_LO"], name="BB unten",
                                 line=dict(color="rgba(61,220,255,.5)", width=1),
                                 fill="tonexty", fillcolor="rgba(61,220,255,.06)"), row=1, col=1)
    if "SMA 50" in inds:
        fig.add_trace(go.Scatter(x=plot_df.index, y=plot_df["SMA_50"], name="SMA 50",
                                 line=dict(color=CLR["amber"], width=1.4)), row=1, col=1)
    if "SMA 200" in inds:
        fig.add_trace(go.Scatter(x=plot_df.index, y=plot_df["SMA_200"], name="SMA 200",
                                 line=dict(color=CLR["cyan"], width=1.4)), row=1, col=1)
    if "Levels (52W)" in inds and hi52 > lo52:
        for lvl, lbl in ((hi52, "52W-Hoch"), (lo52, "52W-Tief")):
            fig.add_hline(y=lvl, line_dash="dash", line_color=CLR["muted"], opacity=.55,
                          annotation_text=lbl, annotation_font_size=10, row=1, col=1)
    if "Volumen" in panes and "Volume" in plot_df:
        vcol = np.where(plot_df["Close"] >= plot_df["Open"],
                        "rgba(0,229,140,.55)", "rgba(255,90,95,.55)")
        fig.add_trace(go.Bar(x=plot_df.index, y=plot_df["Volume"], name="Volumen",
                             marker_color=vcol), row=row_of["Volumen"], col=1)
    if "RSI (14)" in panes:
        r = row_of["RSI (14)"]
        fig.add_trace(go.Scatter(x=plot_df.index, y=plot_df["RSI"], name="RSI",
                                 line=dict(color=CLR["violet"], width=1.4)), row=r, col=1)
        fig.add_hline(y=70, line_dash="dot", line_color=CLR["down"], opacity=.5, row=r, col=1)
        fig.add_hline(y=30, line_dash="dot", line_color=CLR["up"], opacity=.5, row=r, col=1)
        fig.update_yaxes(range=[0, 100], row=r, col=1)
    if "MACD" in panes:
        r = row_of["MACD"]
        hcol = np.where(plot_df["MACD_HIST"] >= 0, "rgba(0,229,140,.6)", "rgba(255,90,95,.6)")
        fig.add_trace(go.Bar(x=plot_df.index, y=plot_df["MACD_HIST"], name="Histogramm",
                             marker_color=hcol), row=r, col=1)
        fig.add_trace(go.Scatter(x=plot_df.index, y=plot_df["MACD"], name="MACD",
                                 line=dict(color=CLR["blue"], width=1.2)), row=r, col=1)
        fig.add_trace(go.Scatter(x=plot_df.index, y=plot_df["MACD_SIG"], name="Signal",
                                 line=dict(color=CLR["amber"], width=1.2)), row=r, col=1)
    fig.update_layout(template="plotly_dark", height=340 + 120 * len(panes),
                      margin=dict(l=0, r=0, t=10, b=0), xaxis_rangeslider_visible=False,
                      showlegend=True, legend=dict(orientation="h", y=1.06),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig, use_container_width=True)

    # --- Performance über Zeiträume -----------------------------------------
    st.markdown("#### PERFORMANCE")
    perf = performance_table(hist["Close"])
    pcols = st.columns(len(perf))
    for col, (lbl, val) in zip(pcols, perf.items()):
        val_html = (f"<span style='color:{updown(val)}'>{val:+.1%}</span>"
                    if is_num(val) else "–")
        col.markdown(metric_card(lbl, val_html), unsafe_allow_html=True)

    st.markdown("---")
    col_fund, col_val, col_news = st.columns([1.15, 1.15, 1.1])

    # --- Fundamentaldaten (erweitert, 0-Werte werden korrekt angezeigt) -----
    with col_fund:
        st.markdown("### FUNDAMENTALS")
        de_ratio = info.get("debtToEquity")
        rows_val = [
            ("Market Cap", fmt_big(info.get("marketCap"), f"{currency} ")),
            ("Enterprise Value", fmt_big(info.get("enterpriseValue"), f"{currency} ")),
            ("KGV (trailing)", fmt(info.get("trailingPE"))),
            ("KGV (forward)", fmt(info.get("forwardPE"))),
            ("PEG-Ratio", fmt(info.get("trailingPegRatio") or info.get("pegRatio"))),
            ("KUV (P/S)", fmt(info.get("priceToSalesTrailing12Months"))),
            ("KBV (P/B)", fmt(info.get("priceToBook"))),
            ("EV/EBITDA", fmt(info.get("enterpriseToEbitda"))),
        ]
        rows_q = [
            ("Bruttomarge", fmt(info.get("grossMargins"), "{:.1%}")),
            ("Operative Marge", fmt(info.get("operatingMargins"), "{:.1%}")),
            ("Nettomarge", fmt(info.get("profitMargins"), "{:.1%}")),
            ("ROE", fmt(info.get("returnOnEquity"), "{:.1%}")),
            ("Debt/Equity", fmt(de_ratio / 100 if is_num(de_ratio) else None, "{:.2f}x")),
            ("Current Ratio", fmt(info.get("currentRatio"))),
            ("FCF-Rendite", fmt(fcf_yield, "{:.1%}")),
            ("Dividendenrendite", fmt(div_yield, "{:.2%}")),
            ("Ausschüttungsquote", fmt(info.get("payoutRatio"), "{:.0%}")),
            ("Beta (Yahoo)", fmt(info.get("beta"))),
        ]
        st.caption("Bewertung")
        st.dataframe(pd.DataFrame(rows_val, columns=["Kennzahl", "Wert"]),
                     hide_index=True, use_container_width=True)
        st.caption("Qualität & Ausschüttung")
        st.dataframe(pd.DataFrame(rows_q, columns=["Kennzahl", "Wert"]),
                     hide_index=True, use_container_width=True)
        div_years = load_dividend_years(ticker)
        if len(div_years) >= 2:
            with st.expander("Dividendenhistorie"):
                fig_dv = go.Figure(go.Bar(x=[str(y) for y in div_years.index],
                                          y=div_years.values, marker_color=CLR["gold"],
                                          name="Dividende p. a."))
                fig_dv.update_layout(template="plotly_dark", height=190,
                                     margin=dict(l=0, r=0, t=8, b=0),
                                     yaxis_title=currency,
                                     paper_bgcolor="rgba(0,0,0,0)",
                                     plot_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig_dv, use_container_width=True)
                full = (div_years.iloc[:-1]
                        if div_years.index[-1] == datetime.now().year else div_years)
                if len(full) >= 6 and full.iloc[-6] > 0:
                    cagr5 = (full.iloc[-1] / full.iloc[-6]) ** (1 / 5) - 1
                    st.caption(f"Dividendenwachstum (CAGR, 5 abgeschlossene Jahre): {cagr5:+.1%}")

    # --- Bewertung: Analystenziele + interaktiver DCF -----------------------
    with col_val:
        st.markdown("### BEWERTUNG")
        tgt = info.get("targetMeanPrice")
        if is_num(tgt) and current_price:
            upside = tgt / current_price - 1
            n_an = info.get("numberOfAnalystOpinions")
            reco = {"strong_buy": "Strong Buy", "buy": "Kaufen", "hold": "Halten",
                    "sell": "Verkaufen", "strong_sell": "Strong Sell"}.get(
                        str(info.get("recommendationKey")), info.get("recommendationKey") or "–")
            st.markdown(card(
                f"<div class='metric-title'>ANALYSTEN-KONSENS"
                f"{' · ' + str(int(n_an)) + ' Schätzungen' if is_num(n_an) else ''}</div>"
                f"<div class='metric-value'>{currency} {tgt:,.2f} "
                f"<span style='font-size:.95rem;color:{updown(upside)}'>({upside:+.1%})</span></div>"
                f"<div class='metric-sub'>Votum: <b>{esc(reco)}</b> · Spanne "
                f"{fmt(info.get('targetLowPrice'))} – {fmt(info.get('targetHighPrice'))}</div>",
                CLR["blue"]), unsafe_allow_html=True)

        fcf, shares = info.get("freeCashflow"), info.get("sharesOutstanding")
        fcf_ps_default = (fcf / shares) if is_num(fcf) and is_num(shares) and shares else 0.0
        with st.expander("Fair-Value-Rechner (DCF)", expanded=fcf_ps_default > 0):
            fcf_ps = st.number_input(f"Free Cashflow je Aktie ({currency})",
                                     min_value=0.0, value=round(max(fcf_ps_default, 0.0), 2),
                                     step=0.1, help="Vorbelegt aus Yahoo-Daten, frei anpassbar.")
            s1, s2, s3 = st.columns(3)
            g = s1.slider("Wachstum p. a. (%)", 0.0, 25.0, 8.0, 0.5) / 100
            r = s2.slider("Diskontsatz (%)", 6.0, 16.0, 10.0, 0.5) / 100
            tg = s3.slider("Terminal-Wachstum (%)", 0.0, 4.0, 2.0, 0.25) / 100
            fv = dcf_value(fcf_ps, g, r, tg)
            if fv is None:
                st.info("DCF nicht berechenbar — FCF je Aktie > 0 und Diskontsatz > "
                        "Terminal-Wachstum erforderlich.")
            else:
                prem = current_price / fv - 1
                verdict = "unterbewertet" if prem < 0 else "überbewertet"
                st.markdown(card(
                    f"<div class='metric-title'>FAIRER WERT (5J-DCF)</div>"
                    f"<div class='metric-value'>{currency} {fv:,.2f}</div>"
                    f"<div class='metric-sub' style='color:{updown(-prem)}'>"
                    f"Kurs {prem:+.1%} → rechnerisch {verdict}</div>", updown(-prem)),
                    unsafe_allow_html=True)
                # Sensitivität: Fair Value über Diskontsatz × Wachstum
                r_axis = [round(r + d, 3) for d in (-0.02, -0.01, 0, 0.01, 0.02)]
                g_axis = [round(g + d, 3) for d in (-0.02, -0.01, 0, 0.01, 0.02)]
                z = [[dcf_value(fcf_ps, gg, rr, tg) if rr > tg else None
                      for gg in g_axis] for rr in r_axis]
                fig_s = go.Figure(go.Heatmap(
                    z=z, x=[f"g {gg:.0%}" for gg in g_axis], y=[f"r {rr:.0%}" for rr in r_axis],
                    colorscale="RdYlGn", zmid=current_price, texttemplate="%{z:,.0f}",
                    colorbar=dict(title="FV")))
                fig_s.update_layout(template="plotly_dark", height=260,
                                    margin=dict(l=0, r=0, t=24, b=0),
                                    title=dict(text="Sensitivität (grün = FV über aktuellem Kurs)",
                                               font=dict(size=12)),
                                    paper_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig_s, use_container_width=True)
            ig = implied_fcf_growth(current_price, fcf_ps, r, tg)
            if ig is not None:
                hist_g = info.get("revenueGrowth")
                st.markdown(card(
                    f"<div class='metric-title'>REVERSE DCF — EINGEPREISTES WACHSTUM</div>"
                    f"<div class='metric-value'>{ig:+.1%} p. a.</div>"
                    f"<div class='metric-sub'>FCF-Wachstum, das den aktuellen Kurs bei "
                    f"r = {r:.0%} rechtfertigt"
                    + (f" · zuletzt gemeldetes Umsatzwachstum: {hist_g:+.1%}"
                       if is_num(hist_g) else "") + "</div>", CLR["cyan"]),
                    unsafe_allow_html=True)
                st.caption("Expectations Investing: Statt den fairen Wert zu raten, "
                           "prüfst du, ob die im Kurs steckende Erwartung erreichbar ist.")
            st.caption("Vereinfachtes Modell (konstante Raten, 5 Jahre + Terminal Value). "
                       "Keine Anlageberatung.")

    # --- News + KI-Sentiment -------------------------------------------------
    with col_news:
        st.markdown(f"### NEWS & SENTIMENT {badge('KI', 'ki')}", unsafe_allow_html=True)
        news_items = load_news(ticker)
        if news_items:
            if st.button("SENTIMENT ANALYSIEREN"):
                titles = tuple(n["title"] for n in news_items)
                with st.spinner("Bewerte Schlagzeilen …"):
                    ok_s, sent = run_ai(
                        ("Du bist Marktanalyst. Bewerte die Schlagzeilen als Ganzes.",
                         "Antwortformat exakt: [BULLISCH/BEARISCH/NEUTRAL] — ein Satz "
                         "Begründung. Deutsch, keine Emojis."),
                        "Schlagzeilen:\n" + "\n".join(titles))
                st.session_state.ai_results[("sentiment", ticker)] = (ok_s, sent)
            if ("sentiment", ticker) in st.session_state.ai_results:
                ok_s, sent = st.session_state.ai_results[("sentiment", ticker)]
                if ok_s:
                    s_up = sent.upper()
                    lab = ("BULLISCH" if "BULLISCH" in s_up else
                           "BEARISCH" if "BEARISCH" in s_up else "NEUTRAL")
                    s_clr = {"BULLISCH": CLR["up"], "BEARISCH": CLR["down"],
                             "NEUTRAL": CLR["amber"]}[lab]
                    st.markdown(card(f"<b style='color:{s_clr}'>SENTIMENT: {lab}</b><br>"
                                     f"<span style='font-size:.85rem'>{esc(sent)}</span>",
                                     s_clr), unsafe_allow_html=True)
                else:
                    st.warning(sent)
            for n in news_items[:4]:
                when = f" · {esc(n['when'])}" if n.get("when") else ""
                st.markdown(f"<div class='newsitem'><a href='{esc(n['link'])}' target='_blank'>"
                            f"{esc(n['title'])}</a><br><span class='news-meta'>"
                            f"{esc(n['publisher'])}{when}</span></div>"
                            f"<hr style='margin:.35em 0;opacity:.12'>", unsafe_allow_html=True)
        else:
            st.info("Aktuell keine Nachrichten zu diesem Symbol abrufbar.")

    # --- Investment-Memo: die Gesamtthese aus allen Terminaldaten -----------
    st.markdown(f"### INVESTMENT-MEMO {badge('KI', 'ki')}", unsafe_allow_html=True)
    st.caption("Verdichtet Bewertung, Faktor-Scores, Risiko und Makro zu einer "
               "strukturierten These — auf Basis der geladenen Terminaldaten.")
    if st.button("MEMO GENERIEREN"):
        us_spread = load_us_yields()["yields"]
        sp = (us_spread.get("10Y") - us_spread.get("3M")
              if is_num(us_spread.get("10Y")) and is_num(us_spread.get("3M")) else None)
        tgt_m = info.get("targetMeanPrice")
        facts_memo = (
            f"Asset: {ticker} ({short_name}), Sektor {info.get('sector', '–')}, "
            f"Kurs {currency} {current_price:,.2f}.\n"
            f"Bewertung: KGV {fmt(info.get('trailingPE'))}, EV/EBITDA "
            f"{fmt(info.get('enterpriseToEbitda'))}, FCF-Rendite {fmt(fcf_yield, '{:.1%}')}, "
            f"Analystenziel {fmt(tgt_m)} "
            f"({fmt(tgt_m / current_price - 1 if is_num(tgt_m) else None, '{:+.1%}')}).\n"
            f"Faktor-Scores (0-100): " + ", ".join(
                f"{k} {fmt(v, '{:.0f}')}" for k, v in fs.items()) + ".\n"
            f"Risiko: Vola {fmt(rm['vol'], '{:.0%}')}, Beta {fmt(rm['beta'])}, "
            f"Max Drawdown {fmt(rm['max_dd'], '{:.0%}')}.\n"
            f"Makro: US 10J-3M-Spread {fmt(sp, '{:+.2f}')} PP, "
            f"Performance 6M {fmt(period_return(hist['Close'], 182), '{:+.1%}')}.")
        with st.spinner("Erstelle Memo …"):
            ok_memo, memo = run_ai(
                ("Du bist Portfoliomanager eines Hedgefonds und schreibst ein internes Memo.",
                 "Struktur exakt: 'THESE:' (2 Saetze), 'BULL-CASE:' (3 Punkte), "
                 "'BEAR-CASE:' (3 Punkte), 'KATALYSATOREN:' (2 Punkte), "
                 "'RISIKOMANAGEMENT:' (1 Satz). Nur die uebergebenen Daten verwenden, "
                 "fehlende Daten benennen. Deutsch, praezise, keine Emojis."),
                facts_memo)
        st.session_state.ai_results[("memo", ticker)] = (ok_memo, memo)
    if ("memo", ticker) in st.session_state.ai_results:
        render_ai_result(*st.session_state.ai_results[("memo", ticker)])

    # --- Analyse-Snapshot als Markdown-Report -------------------------------
    rep_lines = [
        f"# Analyse-Report: {short_name} ({ticker})",
        f"Erstellt: {datetime.now().strftime('%d.%m.%Y %H:%M')} · "
        f"Kurs: {currency} {current_price:,.{decimals}f} ({change_pct:+.2f} %)", "",
        "## Performance",
        " | ".join(f"{k}: {fmt(v, '{:+.1%}')}" for k, v in perf.items()), "",
        "## Bewertung & Qualität",
        f"Market Cap {fmt_big(info.get('marketCap'))} · KGV {fmt(info.get('trailingPE'))} · "
        f"EV/EBITDA {fmt(info.get('enterpriseToEbitda'))} · KBV {fmt(info.get('priceToBook'))}",
        f"Nettomarge {fmt(info.get('profitMargins'), '{:.1%}')} · "
        f"ROE {fmt(info.get('returnOnEquity'), '{:.1%}')} · "
        f"FCF-Rendite {fmt(fcf_yield, '{:.1%}')} · "
        f"Dividendenrendite {fmt(div_yield, '{:.2%}')}", "",
        "## Faktor-Scores (0-100)",
        " | ".join(f"{k}: {fmt(v, '{:.0f}')}" for k, v in fs.items()), "",
        "## Risiko",
        f"Volatilität {fmt(rm['vol'], '{:.1%}')} · Beta vs. {bench_sym} {fmt(rm['beta'])} · "
        f"Sharpe {fmt(rm['sharpe'])} · Max Drawdown {fmt(rm['max_dd'], '{:.1%}')} · "
        f"VaR 95 % {fmt(rm['var95'], '{:.2%}')}", "",
        "---",
        "Quelle: Yahoo Finance (ggf. verzögert). Automatisch erstellt mit "
        "Institutional Terminal Pro. Keine Anlageberatung.",
    ]
    st.download_button("ANALYSE ALS REPORT EXPORTIEREN (.md)",
                       "\n".join(rep_lines).encode("utf-8"),
                       file_name=f"analyse_{ticker.replace('=', '_')}.md",
                       mime="text/markdown")

    # --- Terminal-Chat (mit Verlauf — der alte Code verlor die Historie) ----
    st.markdown(f"### TERMINAL-CHAT {badge('KI', 'ki')}", unsafe_allow_html=True)
    chat_box = st.container(height=300)
    with chat_box:
        if not st.session_state.messages:
            st.caption("Frag z. B.: „Wie haben sich die Margen entwickelt?“ oder "
                       "„Was spricht aktuell gegen einen Einstieg?“")
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
    if user_prompt := st.chat_input(f"Frage zu {ticker} …"):
        st.session_state.messages.append({"role": "user", "content": user_prompt})
        ctx = (f"{ticker} ({short_name}), Kurs {currency} {current_price:,.2f}, "
               f"Sektor {info.get('sector', '–')}, KGV {fmt(info.get('trailingPE'))}, "
               f"Marktkapitalisierung {fmt_big(info.get('marketCap'))}")
        with st.spinner("Denke nach …"):
            reply = ai_chat_reply(user_prompt, ctx)
        st.session_state.messages.append({"role": "assistant", "content": reply})
        st.rerun()

# ============================================================================
#  TAB — QUANT & SCORES
# ============================================================================

def render_quant():
    st.markdown("### FAKTOR-PROFIL")
    axes = ["Value", "Qualität", "Momentum", "Stabilität"]
    have_axes = [(a, fs[a]) for a in axes if fs.get(a) is not None]
    qc1, qc2 = st.columns([1.3, 1])
    with qc1:
        if have_axes:
            names = [a for a, _ in have_axes] + [have_axes[0][0]]
            vals = [v for _, v in have_axes] + [have_axes[0][1]]
            fig_rad = go.Figure(go.Scatterpolar(
                r=vals, theta=names, fill="toself",
                line=dict(color=CLR["cyan"], width=2),
                fillcolor="rgba(61,220,255,.15)"))
            fig_rad.update_layout(template="plotly_dark", height=340, showlegend=False,
                                  polar=dict(radialaxis=dict(range=[0, 100])),
                                  margin=dict(l=50, r=50, t=26, b=26),
                                  paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig_rad, use_container_width=True)
        else:
            st.info("Für dieses Instrument sind keine Faktor-Scores berechenbar "
                    "(bei Indizes, FX und Krypto fehlen Fundamentaldaten).")
    with qc2:
        comp = fs.get("Composite")
        if comp is not None:
            verdict = ("STARKES PROFIL" if comp >= 70 else
                       "SOLIDES PROFIL" if comp >= 50 else "SCHWACHES PROFIL")
            comp_clr = (CLR["up"] if comp >= 70 else
                        CLR["amber"] if comp >= 50 else CLR["down"])
            st.markdown(metric_card("COMPOSITE-SCORE", f"{comp:.0f} / 100",
                                    verdict, comp_clr), unsafe_allow_html=True)
        bars = ""
        for a in axes:
            v = fs.get(a)
            b_clr = (CLR["up"] if is_num(v) and v >= 70 else
                     CLR["amber"] if is_num(v) and v >= 50 else CLR["down"])
            bars += (f"<p style='margin:.45em 0 .15em'>{a}"
                     f"<span style='float:right;color:{b_clr if is_num(v) else CLR['muted']}'>"
                     f"{fmt(v, '{:.0f}')}</span></p>"
                     f"<div class='range-track'><div style='width:{v if is_num(v) else 0:.0f}%;"
                     f"height:6px;border-radius:3px;background:{b_clr}'></div></div>")
        st.markdown(card(bars), unsafe_allow_html=True)
        with st.expander("Methodik"):
            st.caption("Jede Kennzahl wird linear zwischen kalibrierten Schwellen auf 0-100 "
                       "skaliert (z. B. KGV 40 → 8, ROE 0 % → 25 %, 6M-Rendite −20 % → +30 %, "
                       "Volatilität 60 % → 15 %); der Faktor-Score ist das Mittel seiner "
                       "verfügbaren Kennzahlen, der Composite das Mittel der Faktoren. "
                       "Transparente Heuristik zur Einordnung — kein Perzentil-Ranking "
                       "gegen ein Aktienuniversum.")

    st.markdown("### MARKT-REGIME")
    mr = market_regime(hist["Close"])
    if mr is None:
        st.info("Zu wenig Historie für eine Regime-Analyse.")
    else:
        cur_reg = mr["current"]
        reg_clr = (CLR["down"] if "BÄREN" in cur_reg else
                   CLR["amber"] if "volatil" in cur_reg else CLR["up"])
        mr1, mr2 = st.columns([1, 2])
        mr1.markdown(metric_card("AKTUELLES REGIME", cur_reg,
                                 "Trend (SMA 200) × Volatilität (30 T)", reg_clr),
                     unsafe_allow_html=True)
        with mr2:
            st.dataframe(pd.DataFrame([{
                "Regime": e["Regime"],
                "Zeitanteil": fmt(e["Zeitanteil"], "{:.0%}"),
                "Rendite p.a.": fmt(e["Rendite p.a."], "{:+.1%}"),
                "Vola p.a.": fmt(e["Vola p.a."], "{:.1%}"),
            } for e in mr["table"]]), hide_index=True, use_container_width=True)
        st.caption("Historische Kennzahlen dieses Assets je Regime — die meisten "
                   "Strategien funktionieren nur in bestimmten Regimen; erst das "
                   "Regime prüfen, dann die Strategie wählen.")

    st.markdown("### BILANZ-SCORES")
    inc_q, cfs_q, bal_q = load_financial_statements(ticker)
    if inc_q.empty and bal_q.empty:
        st.info("Bilanz-Scores sind nur für Aktien mit veröffentlichten Abschlüssen verfügbar.")
    else:
        bs1, bs2 = st.columns([1.3, 1])
        with bs1:
            p_score, p_checks, p_avail = piotroski_f(inc_q, cfs_q, bal_q)
            if p_score is None:
                st.info("Piotroski F-Score: Datenbasis unvollständig.")
            else:
                p_clr = (CLR["up"] if p_score >= 7 else
                         CLR["amber"] if p_score >= 4 else CLR["down"])
                p_txt = ("fundamental stark" if p_score >= 7 else
                         "durchschnittlich" if p_score >= 4 else "fundamental schwach")
                st.markdown(metric_card("PIOTROSKI F-SCORE", f"{p_score} / {p_avail}",
                                        f"{p_txt} · {p_avail} von 9 Kriterien prüfbar",
                                        p_clr), unsafe_allow_html=True)
                chk = pd.DataFrame(
                    [(n, "erfüllt" if c else ("verfehlt" if c is False else "n. v."))
                     for n, c in p_checks], columns=["Kriterium", "Status"])
                st.dataframe(chk, hide_index=True, use_container_width=True)
        with bs2:
            z, zone = altman_z(inc_q, bal_q, info.get("marketCap"))
            if z is None:
                st.info("Altman Z-Score: Datenbasis unvollständig.")
            else:
                z_clr = {"SICHER": CLR["up"], "GRAUZONE": CLR["amber"],
                         "DISTRESS": CLR["down"]}[zone]
                st.markdown(metric_card("ALTMAN Z-SCORE (Insolvenzrisiko)", f"{z:.2f}",
                                        f"Zone: {zone} · sicher > 2.99, Distress < 1.81",
                                        z_clr), unsafe_allow_html=True)
                st.caption("Z = 1.2·WC/TA + 1.4·RE/TA + 3.3·EBIT/TA + 0.6·MVE/TL + Umsatz/TA.")
                if info.get("sector") == "Financial Services":
                    st.caption("Hinweis: Für Banken und Versicherer ist der Z-Score "
                               "konstruktionsbedingt wenig aussagekräftig.")

    st.markdown("### SAISONALITÄT — Monatsrenditen in %")
    sea = seasonality_matrix(hist["Close"])
    if sea is None:
        st.info("Zu wenig Historie für eine Saisonalitätsanalyse.")
    else:
        sea_r = sea.iloc[::-1]  # Ø-Zeile unten, neueste Jahre darüber
        fig_sea = go.Figure(go.Heatmap(
            z=sea_r.values, x=list(sea_r.columns), y=[str(i) for i in sea_r.index],
            colorscale="RdYlGn", zmid=0, texttemplate="%{z:.1f}",
            textfont=dict(size=10), colorbar=dict(title="%")))
        fig_sea.update_layout(template="plotly_dark",
                              height=max(320, 26 * len(sea_r) + 70),
                              margin=dict(l=0, r=0, t=10, b=0),
                              paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig_sea, use_container_width=True)
        st.caption("Ø = Durchschnitt über die angezeigten Jahre. Saisonmuster sind "
                   "Tendenzen aus der Vergangenheit, keine Gesetzmäßigkeiten.")

    st.markdown("### RENDITEVERTEILUNG — Tagesrenditen, 2 Jahre")
    ret2 = naive_daily(hist["Close"].tail(2 * TRADING_DAYS)).pct_change().dropna()
    if len(ret2) < 60:
        st.info("Zu wenig Historie für eine Verteilungsanalyse.")
    else:
        dv1, dv2 = st.columns([1.6, 1])
        with dv1:
            mu_d, sd_d = float(ret2.mean()), float(ret2.std())
            xs = np.linspace(float(ret2.min()), float(ret2.max()), 140)
            pdf = np.exp(-0.5 * ((xs - mu_d) / sd_d) ** 2) / (sd_d * np.sqrt(2 * np.pi))
            nbins = 60
            binw = (float(ret2.max()) - float(ret2.min())) / nbins
            fig_h = go.Figure()
            fig_h.add_trace(go.Histogram(x=ret2, nbinsx=nbins, name="Beobachtet",
                                         marker_color="rgba(76,154,255,.55)"))
            fig_h.add_trace(go.Scatter(x=xs, y=pdf * len(ret2) * binw,
                                       name="Normalverteilung",
                                       line=dict(color=CLR["amber"], width=2)))
            fig_h.update_layout(template="plotly_dark", height=300, barmode="overlay",
                                margin=dict(l=0, r=0, t=10, b=0),
                                legend=dict(orientation="h", y=1.12),
                                xaxis_tickformat=".1%",
                                paper_bgcolor="rgba(0,0,0,0)",
                                plot_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig_h, use_container_width=True)
        with dv2:
            skew, kurt = float(ret2.skew()), float(ret2.kurt())
            cv = cvar95(ret2)
            st.markdown(metric_card("SCHIEFE",
                                    f"{skew:+.2f}",
                                    "linksschief — Ausreißer nach unten" if skew < -0.2
                                    else "rechtsschief" if skew > 0.2 else "annähernd symmetrisch"),
                        unsafe_allow_html=True)
            st.markdown(metric_card("EXZESS-KURTOSIS", f"{kurt:+.2f}",
                                    "Fat Tails — Extremtage häufiger als Normalverteilung"
                                    if kurt > 1 else "nahe Normalverteilung",
                                    CLR["amber"] if kurt > 1 else None),
                        unsafe_allow_html=True)
            st.markdown(metric_card("CVaR 95 % (1 Tag)", fmt(cv, "{:.2%}"),
                                    "Ø-Verlust der schlechtesten 5 % der Tage",
                                    CLR["down"]), unsafe_allow_html=True)

# ============================================================================
#  TAB — ASSET-CLASS DESK: das passende Instrumentarium je Anlageklasse
# ============================================================================

def render_asset_class():
    st.markdown(f"### ASSET-CLASS DESK — erkannt: {a_class}")

    # --- COT-Positionierung (für alles außer Aktien) ------------------------
    cot_key = COT_KEY_FOR_TICKER.get(ticker)
    if cot_key:
        st.markdown(f"#### COT-REPORT: {cot_key} {badge('LIVE', 'live')} — "
                    f"Positionierung der Large Speculators (CFTC, wöchentlich)",
                    unsafe_allow_html=True)
        cot = load_cot(cot_key)
        if cot.empty:
            st.info("CFTC-Daten aktuell nicht abrufbar — später erneut laden.")
        else:
            net_now = float(cot["net"].iloc[-1])
            pctl = percentile_of_last(cot["net"])
            cq1, cq2 = st.columns([1, 2.2])
            with cq1:
                p_clr = (CLR["down"] if is_num(pctl) and (pctl > 85 or pctl < 15)
                         else None)
                st.markdown(metric_card(
                    "NETTO-SPEKULANTEN-POSITION", f"{net_now:+,.0f} Kontrakte",
                    f"{fmt(pctl, '{:.0f}')}. Perzentil der letzten 3 Jahre"
                    + (" — Extremzone, kontrarisch relevant"
                       if is_num(pctl) and (pctl > 85 or pctl < 15) else ""),
                    p_clr), unsafe_allow_html=True)
                st.caption("Extrem einseitige Positionierung (>85. / <15. "
                           "Perzentil) markiert oft überlaufene Trades — der "
                           "Treibstoff für Gegenbewegungen fehlt dann.")
            with cq2:
                fig_cot = go.Figure(go.Scatter(
                    x=cot.index, y=cot["net"], fill="tozeroy", name="Netto-Position",
                    line=dict(color=CLR["cyan"], width=1.8),
                    fillcolor="rgba(61,220,255,.10)"))
                fig_cot.add_hline(y=0, line_color=CLR["muted"], opacity=.5)
                fig_cot.update_layout(template="plotly_dark", height=260,
                                      yaxis_title="Kontrakte (Long − Short)",
                                      margin=dict(l=0, r=0, t=8, b=0),
                                      paper_bgcolor="rgba(0,0,0,0)",
                                      plot_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig_cot, use_container_width=True)

    # --- FX-Desk ------------------------------------------------------------
    if a_class == "FX":
        st.markdown("#### ZINSDIFFERENZ & CARRY")
        if ticker == "EURUSD=X":
            us_fx = load_us_yields()["yields"]
            eu_fx = load_ecb_yields()["yields"]
            pairs = [(m, us_fx.get(m), eu_fx.get(m)) for m in ("3M", "2Y", "10Y")]
            fx_cols = st.columns(3)
            for col, (m, u, e) in zip(fx_cols, pairs):
                d = (u - e) if is_num(u) and is_num(e) else None
                col.markdown(metric_card(
                    f"ZINSDIFFERENZ {m} (US − EU)", fmt(d, "{:+.2f} PP"),
                    f"US {fmt(u, '{:.2f}')} % · EU {fmt(e, '{:.2f}')} %",
                    CLR["cyan"]), unsafe_allow_html=True)
            d3m = (us_fx.get("3M") - eu_fx.get("3M")
                   if is_num(us_fx.get("3M")) and is_num(eu_fx.get("3M")) else None)
            if is_num(d3m):
                carry_side = "Short EUR/USD (Long USD)" if d3m > 0 else "Long EUR/USD"
                st.markdown(metric_card(
                    "CARRY", f"≈ {abs(d3m):.2f} % p.a.",
                    f"Zinsvorteil auf Seite: {carry_side} — vor Kursbewegung",
                    CLR["gold"]), unsafe_allow_html=True)
            st.caption("Zinsdifferenzen sind der fundamentale Treiber von "
                       "Währungspaaren: Kapital fließt zur höheren realen "
                       "Verzinsung. Quellen: CBOE/Yahoo (US), EZB (Euroraum).")
        else:
            st.info("Das volle Zinsdifferenz-Modul ist für EUR/USD verfügbar "
                    "(US- und EZB-Kurve live). Für andere Paare fehlen freie "
                    "Zinsquellen — COT und Aktivitätsprofil unten gelten "
                    "trotzdem.")
        st.markdown("#### AKTIVITÄTSPROFIL — wann bewegt sich dieses Paar?")
        h1_fx = load_history(ticker, "60d", "1h")
        act = hourly_activity(h1_fx)
        if act.dropna().empty:
            st.info("Keine Stundendaten verfügbar.")
        else:
            fig_act = go.Figure(go.Bar(
                x=[f"{h:02d}h" for h in act.index], y=act.values,
                marker_color=[CLR["amber"] if 8 <= h <= 17 else CLR["blue"]
                              for h in act.index]))
            fig_act.update_layout(template="plotly_dark", height=240,
                                  yaxis_title="Ø |Stundenbewegung| (%)",
                                  margin=dict(l=0, r=0, t=8, b=0),
                                  paper_bgcolor="rgba(0,0,0,0)",
                                  plot_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig_act, use_container_width=True)
            st.caption("Zeiten in deutscher Zeit; die Überlappung London/New York "
                       "(14–18 Uhr) bringt typischerweise Bewegung und Liquidität — "
                       "orange = europäische Handelszeit.")

    # --- Gold/Silber-Desk ---------------------------------------------------
    if a_class in ("GOLD", "SILBER"):
        st.markdown("#### REALZINS-KOMPASS — der wichtigste Gold-Treiber")
        real10 = load_fred("DFII10", years=10)
        if real10.empty:
            st.info("FRED-Realzinsdaten aktuell nicht abrufbar.")
        else:
            gold_h = naive_daily(load_history("GC=F", "10y", "1d")["Close"])
            j_rg = pd.concat([gold_h, real10], axis=1, join="inner").dropna()
            j_rg.columns = ["Gold", "Realzins"]
            r_now = float(real10.iloc[-1])
            r_3m = (float(real10.iloc[-64]) if len(real10) > 64 else None)
            rz1, rz2 = st.columns([1, 2.2])
            with rz1:
                trend = (r_now - r_3m) if is_num(r_3m) else None
                st.markdown(metric_card(
                    "US-REALZINS 10J (TIPS)", f"{r_now:.2f} %",
                    f"3M-Trend {fmt(trend, '{:+.2f} PP')} — "
                    + ("fallend stützt Gold" if is_num(trend) and trend < 0
                       else "steigend belastet Gold" if is_num(trend) else ""),
                    CLR["up"] if is_num(trend) and trend < 0 else CLR["down"]
                    if is_num(trend) else None), unsafe_allow_html=True)
                if len(j_rg) > 120:
                    corr_rg = float(j_rg["Gold"].pct_change()
                                    .corr(j_rg["Realzins"].diff()))
                    st.markdown(metric_card("KORRELATION GOLD ↔ REALZINS",
                                            fmt(corr_rg),
                                            "typisch negativ — Gold zahlt keinen Zins"),
                                unsafe_allow_html=True)
            with rz2:
                fig_rg = make_subplots(specs=[[{"secondary_y": True}]])
                fig_rg.add_trace(go.Scatter(x=j_rg.index, y=j_rg["Gold"],
                                            name="Gold ($)",
                                            line=dict(color=CLR["gold"], width=1.8)))
                fig_rg.add_trace(go.Scatter(x=j_rg.index, y=j_rg["Realzins"],
                                            name="Realzins 10J (%)",
                                            line=dict(color=CLR["cyan"], width=1.6)),
                                 secondary_y=True)
                fig_rg.update_yaxes(autorange="reversed", secondary_y=True,
                                    title_text="Realzins % (invertiert)")
                fig_rg.update_layout(template="plotly_dark", height=300,
                                     margin=dict(l=0, r=0, t=8, b=0),
                                     legend=dict(orientation="h", y=1.12),
                                     paper_bgcolor="rgba(0,0,0,0)",
                                     plot_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig_rg, use_container_width=True)
                st.caption("Realzins-Achse invertiert: laufen beide Linien "
                           "zusammen, treibt der Realzins den Goldpreis — "
                           "Quelle: FRED (DFII10).")
        st.markdown("#### RELATIVE BEWERTUNG")
        gs_close = load_batch_close(("GC=F", "SI=F", "EURUSD=X"), period="10y")
        if all(s in getattr(gs_close, "columns", []) for s in ("GC=F", "SI=F")):
            gsr = (gs_close["GC=F"] / gs_close["SI=F"]).dropna()
            gs_pctl = percentile_of_last(gsr)
            gb1, gb2, gb3 = st.columns(3)
            gb1.markdown(metric_card(
                "GOLD/SILBER-RATIO", fmt(safe_last(gsr), "{:.0f}"),
                f"{fmt(gs_pctl, '{:.0f}')}. Perzentil (10J) — hoch = Silber "
                f"relativ billig"), unsafe_allow_html=True)
            if "EURUSD=X" in gs_close.columns:
                g_eur = (gs_close["GC=F"] / gs_close["EURUSD=X"]).dropna()
                g_eur_1y = (float(g_eur.iloc[-1] / g_eur.iloc[-252] - 1)
                            if len(g_eur) > 252 else None)
                gb2.markdown(metric_card("GOLD IN EUR",
                                         fmt(safe_last(g_eur), "€ {:,.0f}"),
                                         f"1J: {fmt(g_eur_1y, '{:+.1%}')} — die "
                                         f"Sicht des Euro-Anlegers"),
                             unsafe_allow_html=True)
            dxy_1m = load_macro_quotes().get("DXY")
            gb3.markdown(metric_card("DOLLAR-INDEX", fmt(dxy_1m),
                                     "starker Dollar bremst Gold (Preis in $)"),
                         unsafe_allow_html=True)

    # --- Rohstoff-Desk (Energie/Industriemetalle) ---------------------------
    if a_class == "ROHSTOFF" and ticker not in ("GC=F", "SI=F"):
        st.markdown("#### ROHSTOFF-KONTEXT")
        rc_close = load_batch_close((ticker, "HG=F", "GC=F", "XLE", "DX-Y.NYB"),
                                    period="1y")
        rk1, rk2, rk3 = st.columns(3)
        def _r1m(sym):
            s = (rc_close[sym].dropna()
                 if sym in getattr(rc_close, "columns", []) else pd.Series(dtype=float))
            return float(s.iloc[-1] / s.iloc[-22] - 1) if len(s) > 22 else None
        cg_r = None
        if all(s in getattr(rc_close, "columns", []) for s in ("HG=F", "GC=F")):
            ratio = (rc_close["HG=F"] / rc_close["GC=F"]).dropna()
            cg_r = percentile_of_last(ratio)
        rk1.markdown(metric_card("KUPFER/GOLD-PERZENTIL (1J)",
                                 fmt(cg_r, "{:.0f}"),
                                 "hoch = Konjunkturoptimismus"),
                     unsafe_allow_html=True)
        rk2.markdown(metric_card("ENERGIE-SEKTOR (XLE) 1M", fmt(_r1m("XLE"), "{:+.1%}"),
                                 "Aktienmarkt-Bestätigung des Rohstofftrends"),
                     unsafe_allow_html=True)
        rk3.markdown(metric_card("DOLLAR 1M", fmt(_r1m("DX-Y.NYB"), "{:+.1%}"),
                                 "Dollar-Stärke drückt Dollar-Rohstoffe"),
                     unsafe_allow_html=True)
        st.caption("Saisonalität und Verteilungsanalyse dieses Kontrakts findest "
                   "du im QUANT-Tab, die spekulative Positionierung oben im "
                   "COT-Report.")

    # --- Krypto-Desk --------------------------------------------------------
    if a_class == "KRYPTO":
        btc_hist = (hist if ticker == "BTC-USD"
                    else load_history("BTC-USD", "max", "1d"))
        st.markdown("#### HALVING-ZYKLEN — wo im Zyklus stehen wir?")
        if not btc_hist.empty:
            cyc = halving_cycles(btc_hist["Close"])
            if cyc:
                fig_cy = go.Figure()
                pal = [CLR["muted"], CLR["blue"], CLR["violet"], CLR["cyan"]]
                for i_c, (name, s) in enumerate(cyc.items()):
                    fig_cy.add_trace(go.Scatter(
                        x=s.index, y=s, name=name,
                        line=dict(color=pal[i_c % 4],
                                  width=2.4 if i_c == len(cyc) - 1 else 1.3)))
                fig_cy.update_layout(template="plotly_dark", height=320,
                                     yaxis_type="log",
                                     xaxis_title="Tage seit Halving",
                                     yaxis_title="indexiert = 100 (log)",
                                     margin=dict(l=0, r=0, t=8, b=0),
                                     legend=dict(orientation="h", y=1.1),
                                     paper_bgcolor="rgba(0,0,0,0)",
                                     plot_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig_cy, use_container_width=True)
                days_since = (pd.Timestamp.now()
                              - pd.Timestamp(BTC_HALVINGS[-1])).days
                st.caption(f"Aktueller Zyklus (dick): Tag {days_since} seit dem "
                           f"Halving {BTC_HALVINGS[-1]}. Drei Zyklen sind eine "
                           f"winzige Stichprobe — Muster, kein Gesetz.")
        st.markdown("#### KRYPTO-KOMPASS")
        kb1, kb2, kb3 = st.columns(3)
        w200 = naive_daily(btc_hist["Close"]).resample("W").last().rolling(200).mean()
        w200_now = safe_last(w200)
        btc_now = safe_last(btc_hist["Close"])
        if is_num(w200_now) and is_num(btc_now):
            d200w = btc_now / w200_now - 1
            kb1.markdown(metric_card("BTC vs. 200-WOCHEN-SMA", f"{d200w:+.0%}",
                                     f"200W bei ${w200_now:,.0f} — historisch der "
                                     f"Zyklus-Boden", updown(d200w)),
                         unsafe_allow_html=True)
        qq_corr = rolling_corr(hist["Close"].tail(2 * TRADING_DAYS),
                               load_history("QQQ", "2y", "1d")["Close"])
        kb2.markdown(metric_card("KORRELATION zu NASDAQ (60T)",
                                 fmt(safe_last(qq_corr)),
                                 "hoch = handelt als Risk-Asset, nicht als Hedge"),
                     unsafe_allow_html=True)
        mc_syms = ("BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD")
        mcaps = {s: load_info(s).get("marketCap") for s in mc_syms}
        if all(is_num(v) for v in mcaps.values()):
            dom = mcaps["BTC-USD"] / sum(mcaps.values())
            kb3.markdown(metric_card("BTC-DOMINANZ (Top-4-Proxy)", f"{dom:.0%}",
                                     "steigend = Kapital flieht in die Leitwährung"),
                         unsafe_allow_html=True)
        wd = weekday_stats(hist["Close"])
        if not wd.empty:
            st.markdown("#### WOCHENTAGS-MUSTER (5 Jahre)")
            st.dataframe(pd.DataFrame({
                "Tag": wd["Tag"],
                "Ø Rendite": wd["Ø Rendite"].map(lambda v: fmt(v, "{:+.3%}")),
                "Trefferquote": wd["Trefferquote"].map(lambda v: fmt(v, "{:.0%}")),
                "Beobachtungen": wd["n"],
            }), hide_index=True, use_container_width=True)
            st.caption("Krypto handelt 24/7 — Wochenenden sind dünn und "
                       "sprunganfällig. Statistische Muster dieser Art sind "
                       "schwach und können jederzeit verschwinden.")

    if a_class == "AKTIE":
        st.info("Für Aktien und ETFs liegen die Spezialwerkzeuge in den Tabs "
                "FINANCIALS, OPTIONS-DESK und SMART MONEY. Dieser Desk "
                "aktiviert seine Module bei Währungen, Gold/Silber, Rohstoffen "
                "und Krypto — wähle z. B. EURUSD=X, GC=F oder BTC-USD.")

# ============================================================================
#  TAB — OPTIONS-DESK
# ============================================================================

def render_options():
    st.markdown("### OPTIONS-DESK — der Blick des Derivatemarkts")
    expiries = load_option_expiries(ticker)
    if not expiries:
        st.info("Für dieses Symbol sind bei Yahoo keine Optionsketten verfügbar "
                "(üblich bei deutschen Aktien, Indizes, FX und Krypto). "
                "US-Werte wie AAPL, MSFT oder SPY liefern volle Daten.")
    else:
        oc1, oc2 = st.columns([1.2, 2.8])
        expiry = oc1.selectbox("Verfallstermin", expiries[:12])
        calls, puts = load_option_chain(ticker, expiry)
        if calls.empty and puts.empty:
            st.warning("Optionskette für diesen Termin nicht abrufbar.")
        else:
            days_to_exp = max((pd.Timestamp(expiry) - pd.Timestamp.now()).days, 1)
            iv = atm_implied_vol(calls, puts, current_price)
            exp_move = (current_price * iv * np.sqrt(days_to_exp / 365)
                        if iv is not None else None)
            pcr_oi = put_call_ratio(calls, puts, "openInterest")
            pcr_vol = put_call_ratio(calls, puts, "volume")
            mp = max_pain_strike(calls, puts)
            rv = rm.get("vol")
            iv_prem = (iv / rv - 1) if (iv is not None and rv) else None

            ok1, ok2, ok3, ok4, ok5 = st.columns(5)
            ok1.markdown(metric_card("ATM-IMPLIED VOL", fmt(iv, "{:.1%}"),
                                     f"vs. realisiert {fmt(rv, '{:.1%}')}"
                                     + (f" ({iv_prem:+.0%})" if iv_prem is not None else ""),
                                     CLR["amber"] if is_num(iv_prem) and iv_prem > 0.25
                                     else None), unsafe_allow_html=True)
            ok2.markdown(metric_card("EINGEPREISTE BEWEGUNG",
                                     fmt(exp_move / current_price if exp_move else None,
                                         "±{:.1%}"),
                                     f"bis {expiry} ({days_to_exp} Tage)"),
                         unsafe_allow_html=True)
            pcr_clr = (CLR["down"] if is_num(pcr_oi) and pcr_oi > 1.2 else
                       CLR["up"] if is_num(pcr_oi) and pcr_oi < 0.7 else None)
            ok3.markdown(metric_card("PUT/CALL-RATIO (OI)", fmt(pcr_oi),
                                     "> 1 defensiv positioniert, < 0.7 offensiv",
                                     pcr_clr), unsafe_allow_html=True)
            ok4.markdown(metric_card("PUT/CALL-RATIO (VOLUMEN)", fmt(pcr_vol),
                                     "heutiger Handel"), unsafe_allow_html=True)
            ok5.markdown(metric_card("MAX PAIN", fmt(mp),
                                     f"Kurs-Distanz {fmt(mp / current_price - 1 if is_num(mp) else None, '{:+.1%}')}"),
                         unsafe_allow_html=True)

            lo_b, hi_b = current_price * 0.7, current_price * 1.3
            c_f = calls[(calls["strike"] >= lo_b) & (calls["strike"] <= hi_b)]
            p_f = puts[(puts["strike"] >= lo_b) & (puts["strike"] <= hi_b)]
            g1, g2 = st.columns(2)
            with g1:
                st.markdown("#### Open Interest nach Strike")
                fig_oi = go.Figure()
                fig_oi.add_trace(go.Bar(x=c_f["strike"], y=c_f["openInterest"].fillna(0),
                                        name="Calls", marker_color="rgba(0,229,140,.65)"))
                fig_oi.add_trace(go.Bar(x=p_f["strike"], y=-p_f["openInterest"].fillna(0),
                                        name="Puts", marker_color="rgba(255,90,95,.65)"))
                fig_oi.add_vline(x=current_price, line_color="#F2F5FA", line_width=1.5,
                                 annotation_text="Kurs", annotation_font_size=10)
                if is_num(mp):
                    fig_oi.add_vline(x=mp, line_dash="dot", line_color=CLR["amber"],
                                     annotation_text="Max Pain", annotation_font_size=10)
                fig_oi.update_layout(template="plotly_dark", barmode="relative", height=320,
                                     margin=dict(l=0, r=0, t=10, b=0),
                                     legend=dict(orientation="h", y=1.12),
                                     paper_bgcolor="rgba(0,0,0,0)",
                                     plot_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig_oi, use_container_width=True)
                st.caption("Große OI-Cluster wirken als Unterstützung/Widerstand — "
                           "der Kurs tendiert zum Verfall oft Richtung Max Pain.")
            with g2:
                st.markdown("#### Volatility Smile (IV je Strike)")
                fig_sm = go.Figure()
                for df_o, name, clr in ((c_f, "Calls", CLR["up"]), (p_f, "Puts", CLR["down"])):
                    d = df_o.dropna(subset=["impliedVolatility"])
                    d = d[(d["impliedVolatility"] > 0.01) & (d["impliedVolatility"] < 5)]
                    if len(d):
                        fig_sm.add_trace(go.Scatter(x=d["strike"],
                                                    y=d["impliedVolatility"] * 100,
                                                    name=name, mode="lines+markers",
                                                    marker=dict(size=4),
                                                    line=dict(color=clr, width=1.6)))
                fig_sm.add_vline(x=current_price, line_color="#F2F5FA", line_width=1.5)
                fig_sm.update_layout(template="plotly_dark", height=320,
                                     yaxis_title="IV (%)",
                                     margin=dict(l=0, r=0, t=10, b=0),
                                     legend=dict(orientation="h", y=1.12),
                                     paper_bgcolor="rgba(0,0,0,0)",
                                     plot_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig_sm, use_container_width=True)
                st.caption("Ein steiler Put-Skew (links erhöht) zeigt teure "
                           "Absicherungsnachfrage — der Markt bezahlt für Crash-Schutz.")

            st.markdown("#### IV-Term-Structure — welche Termine sind teuer?")
            with st.spinner("Lade Terminkurve der Volatilität …"):
                ts_rows = []
                for ex in expiries[:6]:
                    cx, px_ = load_option_chain(ticker, ex)
                    ivx = atm_implied_vol(cx, px_, current_price)
                    if ivx is not None:
                        ts_rows.append((ex, ivx))
            if len(ts_rows) >= 2:
                fig_ts = go.Figure(go.Scatter(
                    x=[t for t, _ in ts_rows], y=[v * 100 for _, v in ts_rows],
                    mode="lines+markers", name="ATM-IV",
                    line=dict(color=CLR["violet"], width=2), marker=dict(size=8)))
                if rm.get("vol"):
                    fig_ts.add_hline(y=rm["vol"] * 100, line_dash="dot",
                                     line_color=CLR["muted"],
                                     annotation_text="realisierte Vola (1J)",
                                     annotation_font_size=10)
                fig_ts.update_layout(template="plotly_dark", height=260,
                                     yaxis_title="ATM-IV (%)",
                                     margin=dict(l=0, r=0, t=8, b=0),
                                     paper_bgcolor="rgba(0,0,0,0)",
                                     plot_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig_ts, use_container_width=True)
                if ts_rows[0][1] > ts_rows[1][1] * 1.15:
                    st.caption("Der vordere Termin ist deutlich teurer als der "
                               "nächste — der Markt preist ein nahes Ereignis ein "
                               "(typisch: Quartalszahlen). Nach dem Event fällt "
                               "diese IV oft schlagartig (Vol-Crush).")
                else:
                    st.caption("Flache bis leicht steigende Terminkurve — kein "
                               "einzelnes Ereignis dominiert die Preisbildung.")

# ============================================================================
#  TAB — SMART MONEY
# ============================================================================

def render_smart_money():
    st.markdown("### SMART MONEY — wer handelt, bevor es alle sehen")
    sm1, sm2 = st.columns(2)

    with sm1:
        st.markdown("#### Insider-Transaktionen")
        ins = load_insider_transactions(ticker)
        if ins.empty:
            st.info("Keine Insider-Daten verfügbar (US-Werte liefern am meisten).")
        else:
            txt_col = next((c for c in ("Text", "Transaction") if c in ins.columns), None)
            if txt_col and "Value" in ins.columns:
                t_lower = ins[txt_col].astype(str).str.lower()
                buys = float(ins.loc[t_lower.str.contains("purchase|buy"), "Value"]
                             .fillna(0).sum())
                sells = float(ins.loc[t_lower.str.contains("sale|sell"), "Value"]
                              .fillna(0).sum())
                ic1, ic2 = st.columns(2)
                ic1.markdown(metric_card("KÄUFE (gemeldet)", fmt_big(buys, "$"),
                                         sub_color=CLR["up"]), unsafe_allow_html=True)
                ic2.markdown(metric_card("VERKÄUFE (gemeldet)", fmt_big(sells, "$"),
                                         sub_color=CLR["down"]), unsafe_allow_html=True)
            show_cols = [c for c in ("Start Date", "Insider", "Position", txt_col,
                                     "Shares", "Value") if c and c in ins.columns]
            st.dataframe(ins[show_cols].head(10), hide_index=True,
                         use_container_width=True)
            st.caption("Insiderkäufe gelten als starkes Signal — verkauft wird aus "
                       "vielen Gründen, gekauft fast nur aus einem.")

    with sm2:
        st.markdown("#### Analysten: Up- & Downgrades")
        gr = load_analyst_actions(ticker)
        if gr.empty:
            st.info("Keine Rating-Änderungen verfügbar.")
        else:
            date_col = next((c for c in ("GradeDate", "index", "Date")
                             if c in gr.columns), None)
            if date_col and "Action" in gr.columns:
                gr[date_col] = pd.to_datetime(gr[date_col], errors="coerce")
                recent = gr[gr[date_col] >= pd.Timestamp.now() - pd.Timedelta(days=90)]
                ups = int((recent["Action"] == "up").sum())
                downs = int((recent["Action"] == "down").sum())
                gc1, gc2 = st.columns(2)
                gc1.markdown(metric_card("UPGRADES (90 T)", str(ups),
                                         sub_color=CLR["up"]), unsafe_allow_html=True)
                gc2.markdown(metric_card("DOWNGRADES (90 T)", str(downs),
                                         sub_color=CLR["down"]), unsafe_allow_html=True)
            show_cols = [c for c in (date_col, "Firm", "FromGrade", "ToGrade", "Action")
                         if c and c in gr.columns]
            st.dataframe(gr[show_cols].head(10), hide_index=True,
                         use_container_width=True)

    st.markdown("#### Earnings-Track-Record — Überraschung vs. Kursreaktion")
    er = earnings_reactions(load_earnings_history(ticker), hist["Close"])
    if er.empty:
        st.info("Keine auswertbare Earnings-Historie verfügbar.")
    else:
        beats = er["Surprise"].dropna()
        beat_q = float((beats > 0).mean()) if len(beats) else None
        eb1, eb2, eb3 = st.columns(3)
        eb1.markdown(metric_card("BEAT-QUOTE", fmt(beat_q, "{:.0%}"),
                                 f"letzte {len(er)} Quartale"), unsafe_allow_html=True)
        eb2.markdown(metric_card("Ø EPS-ÜBERRASCHUNG",
                                 fmt(beats.mean() / 100 if len(beats) else None, "{:+.1%}")),
                     unsafe_allow_html=True)
        eb3.markdown(metric_card("Ø KURSREAKTION T+1",
                                 fmt(er["Reaktion T+1"].mean(), "{:+.1%}"),
                                 "Schluss zu Schluss"), unsafe_allow_html=True)
        fig_er = make_subplots(specs=[[{"secondary_y": True}]])
        x_lab = er["Datum"].dt.strftime("%m/%y")
        fig_er.add_trace(go.Bar(x=x_lab, y=er["Surprise"], name="EPS-Surprise (%)",
                                marker_color=[updown(v) for v in er["Surprise"].fillna(0)]))
        fig_er.add_trace(go.Scatter(x=x_lab, y=er["Reaktion T+1"] * 100,
                                    name="Kursreaktion T+1 (%)", mode="lines+markers",
                                    line=dict(color=CLR["cyan"], width=2)),
                         secondary_y=True)
        fig_er.update_layout(template="plotly_dark", height=300,
                             margin=dict(l=0, r=0, t=10, b=0),
                             legend=dict(orientation="h", y=1.14),
                             paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig_er, use_container_width=True)
        st.caption("Spannend sind die Brüche: Ein Beat mit negativer Reaktion heißt, "
                   "der Markt hatte mehr erwartet als die offizielle Schätzung.")

# ============================================================================
#  TAB — STRATEGY LAB
# ============================================================================

def render_strategy_lab():
    st.markdown("### STRATEGY LAB — Ideen testen statt glauben")
    bt1, bt2, bt3, bt4 = st.columns([1, 1, 1, 1])
    bt_years = bt1.selectbox("Zeitraum", ["3 Jahre", "5 Jahre", "10 Jahre", "Max"], index=1)
    bt_cost = bt2.slider("Kosten je Umschichtung (BP)", 0, 25, 5,
                         help="Spread + Gebühren in Basispunkten je Positionswechsel.")
    sma_f = bt3.selectbox("SMA schnell", [10, 20, 50, 100], index=2)
    sma_s = bt4.selectbox("SMA langsam", [100, 150, 200], index=2)
    rs1, rs2 = st.columns(2)
    rsi_lo = rs1.slider("RSI-Kaufschwelle", 15, 40, 30)
    rsi_hi = rs2.slider("RSI-Verkaufsschwelle", 55, 85, 70)

    span = {"3 Jahre": 3, "5 Jahre": 5, "10 Jahre": 10, "Max": 100}[bt_years]
    bt_close = hist["Close"].tail(span * TRADING_DAYS)
    if sma_f >= sma_s:
        st.warning("Der schnelle SMA muss kürzer sein als der langsame.")
    elif len(bt_close) < sma_s + 60:
        st.info("Zu wenig Historie für diesen Zeitraum/Parameter.")
    else:
        results = {
            "Buy & Hold": run_backtest(bt_close, "Buy & Hold", bt_cost),
            f"SMA {sma_f}/{sma_s}": run_backtest(bt_close, "SMA-Crossover", bt_cost,
                                                 fast=sma_f, slow=sma_s),
            f"RSI {rsi_lo}/{rsi_hi}": run_backtest(bt_close, "RSI-Mean-Reversion", bt_cost,
                                                   rsi_lo=rsi_lo, rsi_hi=rsi_hi),
        }
        fig_bt = go.Figure()
        for (name, res), clr in zip(results.items(),
                                    (CLR["muted"], CLR["cyan"], CLR["violet"])):
            fig_bt.add_trace(go.Scatter(x=res["equity"].index,
                                        y=res["equity"] * 100, name=name,
                                        line=dict(color=clr, width=2)))
        fig_bt.update_layout(template="plotly_dark", height=360,
                             yaxis_title="Depotwert (Start = 100)",
                             margin=dict(l=0, r=0, t=10, b=0),
                             legend=dict(orientation="h", y=1.1),
                             paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig_bt, use_container_width=True)
        st.dataframe(pd.DataFrame([{
            "Strategie": name,
            "Gesamt": fmt(res["total"], "{:+.1%}"),
            "CAGR": fmt(res["cagr"], "{:+.1%}"),
            "Volatilität": fmt(res["vol"], "{:.1%}"),
            "Sharpe": fmt(res["sharpe"]),
            "Max DD": fmt(res["max_dd"], "{:.1%}"),
            "Transaktionen": res["trades"],
            "Investiert": fmt(res["exposure"], "{:.0%}"),
            "Trefferquote": fmt(res["hit"], "{:.0%}"),
        } for name, res in results.items()]), hide_index=True, use_container_width=True)
        st.caption("Ohne Steuern und Slippage; Einstieg jeweils am Folgetag des Signals "
                   "(kein Look-Ahead). Achtung Overfitting: Parameter, die in der "
                   "Vergangenheit glänzen, sind keine Garantie — Robustheit prüfen, "
                   "indem man sie leicht variiert.")

    st.markdown("---")
    st.markdown("### PAIRS-MONITOR — relative Bewertung zweier Werte")
    pm1, pm2 = st.columns(2)
    pair_a = pm1.text_input("Long-Kandidat A", value=ticker)
    pair_b = pm2.text_input("Gegenpart B",
                            value=PEER_SUGGESTIONS.get(ticker, ["MSFT"])[0] or "MSFT")
    if pair_a.strip() and pair_b.strip():
        pa, pb = pair_a.strip().upper(), pair_b.strip().upper()
        pair_close = load_batch_close((pa, pb), period="2y")
        if all(s in getattr(pair_close, "columns", []) for s in (pa, pb)):
            j = pair_close[[pa, pb]].dropna()
            if len(j) > 120:
                ratio = np.log(j[pa] / j[pb])
                z = (ratio - ratio.rolling(60).mean()) / ratio.rolling(60).std()
                z_now = safe_last(z)
                corr_ab = float(j[pa].pct_change().corr(j[pb].pct_change()))
                zc1, zc2 = st.columns([1, 2.6])
                with zc1:
                    z_clr = (CLR["down"] if is_num(z_now) and abs(z_now) > 2 else
                             CLR["amber"] if is_num(z_now) and abs(z_now) > 1 else CLR["up"])
                    deut = ("A relativ teuer zu B" if is_num(z_now) and z_now > 1 else
                            "A relativ günstig zu B" if is_num(z_now) and z_now < -1 else
                            "im normalen Band")
                    st.markdown(metric_card(f"Z-SCORE {pa}/{pb} (60 T)",
                                            fmt(z_now, "{:+.2f}"), deut, z_clr),
                                unsafe_allow_html=True)
                    st.markdown(metric_card("KORRELATION (2 J)", fmt(corr_ab),
                                            "je höher, desto tauglicher als Paar"),
                                unsafe_allow_html=True)
                with zc2:
                    fig_z = go.Figure(go.Scatter(x=z.index, y=z, name="Z-Score",
                                                 line=dict(color=CLR["cyan"], width=1.6)))
                    for lvl in (2, -2):
                        fig_z.add_hline(y=lvl, line_dash="dash",
                                        line_color=CLR["down"], opacity=.5)
                    fig_z.add_hline(y=0, line_dash="dot", line_color=CLR["muted"],
                                    opacity=.5)
                    fig_z.update_layout(template="plotly_dark", height=270,
                                        margin=dict(l=0, r=0, t=8, b=0),
                                        paper_bgcolor="rgba(0,0,0,0)",
                                        plot_bgcolor="rgba(0,0,0,0)")
                    st.plotly_chart(fig_z, use_container_width=True)
                st.caption("Klassische Pairs-Logik: |Z| > 2 markiert eine Dehnung des "
                           "Verhältnisses — Rückkehr zum Mittel ist die Wette, aber "
                           "nicht garantiert (Kointegration hier nicht getestet).")
            else:
                st.info("Zu wenig überlappende Historie für dieses Paar.")
        else:
            st.warning("Für mindestens eines der Symbole gibt es keine Kursdaten.")

    st.markdown("---")
    st.markdown("### SPARPLAN-SIMULATOR — Cost Averaging historisch geprüft")
    sp1, sp2 = st.columns(2)
    sp_rate = sp1.number_input("Monatliche Rate", min_value=25.0, value=150.0, step=25.0)
    max_years = max(int(len(hist) / TRADING_DAYS), 2)
    sp_years = sp2.slider("Zeitraum (Jahre)", 2, min(max_years, 25),
                          min(10, min(max_years, 25)))
    sp = savings_plan(hist["Close"], sp_rate, sp_years)
    if sp is None:
        st.info("Zu wenig Historie für diesen Zeitraum.")
    else:
        gain = sp["final"] / sp["paid"] - 1
        lump_gain = sp["lump_final"] / sp["paid"] - 1
        sk1, sk2, sk3, sk4 = st.columns(4)
        sk1.markdown(metric_card("ENDWERT SPARPLAN", f"{currency} {sp['final']:,.0f}",
                                 f"{gain:+.1%} auf {currency} {sp['paid']:,.0f} Einzahlung",
                                 updown(gain)), unsafe_allow_html=True)
        sk2.markdown(metric_card("ENDWERT EINMALANLAGE",
                                 f"{currency} {sp['lump_final']:,.0f}",
                                 f"{lump_gain:+.1%} — gleiche Summe zum Start",
                                 updown(lump_gain)), unsafe_allow_html=True)
        sk3.markdown(metric_card("Ø KAUFKURS", f"{currency} {sp['avg_price']:,.2f}",
                                 f"Startkurs {currency} {sp['start_price']:,.2f}"),
                     unsafe_allow_html=True)
        sk4.markdown(metric_card("RATEN", str(sp["n_months"]),
                                 "monatlich zum Monatsersten"), unsafe_allow_html=True)
        fig_sp = go.Figure()
        fig_sp.add_trace(go.Scatter(x=sp["value"].index, y=sp["value"],
                                    name="Depotwert", fill="tozeroy",
                                    line=dict(color=CLR["up"], width=2),
                                    fillcolor="rgba(0,229,140,.10)"))
        fig_sp.add_trace(go.Scatter(x=sp["invested"].index, y=sp["invested"],
                                    name="Eingezahlt", line=dict(color=CLR["muted"],
                                                                 width=1.6, dash="dot")))
        fig_sp.update_layout(template="plotly_dark", height=300,
                             margin=dict(l=0, r=0, t=10, b=0),
                             legend=dict(orientation="h", y=1.12),
                             paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig_sp, use_container_width=True)
        st.caption("Rückrechnung auf Basis historischer Monatsanfangskurse, ohne Kosten "
                   "und Steuern. In stetig steigenden Märkten gewinnt meist die "
                   "Einmalanlage — der Sparplan glättet dafür das Timing-Risiko.")

        with st.expander("NETTO NACH STEUERN (Deutschland)"):
            tx1, tx2, tx3, tx4 = st.columns(4)
            tx_gain = tx1.number_input("Kursgewinn (brutto)", min_value=0.0,
                                       value=float(max(round(sp["final"] - sp["paid"], 2),
                                                       0.0)), step=100.0)
            tx_allow = tx2.number_input("Freier Sparerpauschbetrag", 0.0, 1000.0,
                                        1000.0, 50.0)
            tx_church = tx3.selectbox("Kirchensteuer", ["keine", "8 %", "9 %"])
            tx_fund = tx4.checkbox("Aktienfonds/ETF (30 % Teilfreistellung)",
                                   value=False)
            ch_rate = {"keine": 0.0, "8 %": 0.08, "9 %": 0.09}[tx_church]
            tax = net_after_tax_de(tx_gain, tx_allow, ch_rate, tx_fund)
            nt1, nt2, nt3 = st.columns(3)
            nt1.markdown(metric_card("STEUERLAST", f"{tax['steuer']:,.2f}",
                                     f"effektiv {tax['eff']:.1%} des Gewinns",
                                     CLR["down"]), unsafe_allow_html=True)
            nt2.markdown(metric_card("NETTO-GEWINN", f"{tax['netto']:,.2f}",
                                     sub_color=CLR["up"]), unsafe_allow_html=True)
            nt3.markdown(metric_card("BEMESSUNGSGRUNDLAGE",
                                     f"{tax.get('bemessung', 0):,.2f}",
                                     "nach Teilfreistellung und Pauschbetrag"),
                         unsafe_allow_html=True)
            st.caption("Abgeltungsteuer nach §32d EStG (bei Kirchensteuer ermäßigt), "
                       "Soli 5,5 % auf die Steuer. Vereinfachung: Vorabpauschale, "
                       "Verlustverrechnungstöpfe und Günstigerprüfung sind nicht "
                       "abgebildet — keine Steuerberatung.")

# ============================================================================
#  TAB — FINANCIALS & AUDIT
# ============================================================================

def render_financials():
    inc, cfs, bal = load_financial_statements(ticker)

    def year_frame(df: pd.DataFrame, rows: list) -> pd.DataFrame:
        """Vorhandene Zeilen als Jahres-DF (alte Version warf KeyError bei
        fehlenden Positionen)."""
        have = [r for r in rows if r in df.index]
        if df.empty or not have:
            return pd.DataFrame()
        out = df.loc[have].dropna(axis=1, how="all").T
        out.index = pd.to_datetime(out.index).year
        return out.sort_index()

    c_fin, c_ins = st.columns([2, 1.4])
    with c_fin:
        st.markdown("### GEWINN- & VERLUSTENTWICKLUNG")
        gv = year_frame(inc, ["Total Revenue", "Net Income"])
        if not gv.empty and "Total Revenue" in gv:
            fig_gv = make_subplots(specs=[[{"secondary_y": True}]])
            fig_gv.add_trace(go.Bar(x=gv.index, y=gv["Total Revenue"], name="Umsatz",
                                    marker_color=CLR["blue"]))
            if "Net Income" in gv:
                fig_gv.add_trace(go.Bar(x=gv.index, y=gv["Net Income"], name="Nettogewinn",
                                        marker_color=CLR["up"]))
                marge = gv["Net Income"] / gv["Total Revenue"].replace(0, np.nan) * 100
                fig_gv.add_trace(go.Scatter(x=gv.index, y=marge, name="Nettomarge (%)",
                                            line=dict(color=CLR["amber"], width=2)),
                                 secondary_y=True)
            fig_gv.update_layout(template="plotly_dark", barmode="group", height=330,
                                 margin=dict(l=0, r=0, t=10, b=0),
                                 legend=dict(orientation="h", y=1.12),
                                 paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
            fig_gv.update_yaxes(title_text="%", secondary_y=True)
            st.plotly_chart(fig_gv, use_container_width=True)
        else:
            st.info("Keine GuV-Daten verfügbar (bei ETFs, Indizes, FX und Krypto normal).")

        st.markdown("### CASHFLOW & VERSCHULDUNG")
        cf = year_frame(cfs, ["Operating Cash Flow", "Free Cash Flow"])
        bl = year_frame(bal, ["Total Debt", "Cash And Cash Equivalents"])
        cf_col, bl_col = st.columns(2)
        with cf_col:
            if not cf.empty:
                fig_cf = go.Figure()
                for name, col_key, clr in [("Operativer CF", "Operating Cash Flow", CLR["cyan"]),
                                           ("Free Cash Flow", "Free Cash Flow", CLR["up"])]:
                    if col_key in cf:
                        fig_cf.add_trace(go.Bar(x=cf.index, y=cf[col_key], name=name,
                                                marker_color=clr))
                fig_cf.update_layout(template="plotly_dark", barmode="group", height=260,
                                     margin=dict(l=0, r=0, t=26, b=0), title="Cashflows",
                                     legend=dict(orientation="h", y=1.2),
                                     paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig_cf, use_container_width=True)
            else:
                st.info("Keine Cashflow-Daten verfügbar.")
        with bl_col:
            if not bl.empty:
                fig_bl = go.Figure()
                for name, col_key, clr in [("Gesamtschulden", "Total Debt", CLR["down"]),
                                           ("Liquide Mittel", "Cash And Cash Equivalents", CLR["up"])]:
                    if col_key in bl:
                        fig_bl.add_trace(go.Bar(x=bl.index, y=bl[col_key], name=name,
                                                marker_color=clr))
                fig_bl.update_layout(template="plotly_dark", barmode="group", height=260,
                                     margin=dict(l=0, r=0, t=26, b=0), title="Bilanz",
                                     legend=dict(orientation="h", y=1.2),
                                     paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig_bl, use_container_width=True)
            else:
                st.info("Keine Bilanzdaten verfügbar.")

    with c_ins:
        st.markdown(f"### RED-FLAG-AUDIT {badge('KI', 'ki')}", unsafe_allow_html=True)
        st.caption("Bewertet ausschließlich die geladenen Zahlen — kein Ergebnis ohne Datenbasis.")
        if st.button("BILANZ-AUDIT AUSFÜHREN", use_container_width=True):
            gv_txt = year_frame(inc, ["Total Revenue", "Net Income"]).tail(4).to_string()
            facts = (f"Ticker {ticker} ({short_name}) | MarketCap {fmt_big(info.get('marketCap'))} | "
                     f"KGV {fmt(info.get('trailingPE'))} | Debt/Equity "
                     f"{fmt(info.get('debtToEquity'))} | Current Ratio {fmt(info.get('currentRatio'))} | "
                     f"FCF {fmt_big(info.get('freeCashflow'))} | TotalDebt {fmt_big(info.get('totalDebt'))}\n"
                     f"GuV (Jahre):\n{gv_txt}")
            with st.spinner("Analysiere Kennzahlen …"):
                ok_a, res = run_ai(
                    ("Du bist Wirtschaftsprüfer. Bewerte NUR die übergebenen Zahlen — keine Annahmen.",
                     "Struktur: 'RED FLAGS:' (max. 3 Punkte), 'POSITIV:' (max. 3 Punkte), "
                     "'Fazit:' (1 Satz). Fehlende Daten explizit benennen. Deutsch, knapp."),
                    facts)
            st.session_state.ai_results[("audit", ticker)] = (ok_a, res)
        if ("audit", ticker) in st.session_state.ai_results:
            render_ai_result(*st.session_state.ai_results[("audit", ticker)])

# ============================================================================
#  TAB — PEER ANALYSIS
# ============================================================================

def render_peers():
    st.markdown("### PEER-VERGLEICH")
    defaults = PEER_SUGGESTIONS.get(ticker, ["", "", ""])
    pc = st.columns(3)
    peers_in = [pc[i].text_input(f"Peer {i + 1}", value=defaults[i] if i < len(defaults) else "",
                                 key=f"peer_{i}") for i in range(3)]

    if st.button("VERGLEICH & KI-FAZIT AUSFÜHREN", use_container_width=True):
        symbols = [ticker] + [p.strip().upper() for p in peers_in if p.strip()]
        rows, failed = [], []
        with st.spinner("Lade Peer-Daten …"):
            hist_1y = load_batch_close(tuple(symbols), period="1y")
            for sym in symbols:
                pi = load_info(sym)
                if not pi:
                    failed.append(sym)
                    continue
                perf_1y = (period_return(hist_1y[sym], days=365)
                           if sym in getattr(hist_1y, "columns", []) else None)
                p_price = pi.get("currentPrice") or pi.get("regularMarketPrice")
                p_dy = (pi.get("dividendRate") / p_price
                        if is_num(pi.get("dividendRate")) and is_num(p_price) and p_price else None)
                rows.append({
                    "Ticker": sym, "Name": (pi.get("shortName") or sym)[:22],
                    "MCap": pi.get("marketCap"), "KGV": pi.get("trailingPE"),
                    "EV/EBITDA": pi.get("enterpriseToEbitda"),
                    "Nettomarge": pi.get("profitMargins"), "ROE": pi.get("returnOnEquity"),
                    "Div.": p_dy, "1J-Perf.": perf_1y,
                })
        if failed:
            st.warning(f"Keine Daten für: {', '.join(failed)} — Symbol prüfen.")
        st.session_state.peer_df = pd.DataFrame(rows) if rows else None
        st.session_state.peer_norm = (
            hist_1y.apply(lambda c: c / c.dropna().iloc[0] * 100 if c.dropna().size else c)
            if not hist_1y.empty else None)
        if rows:
            with st.spinner("KI-Fazit …"):
                ok_p, fazit = run_ai(
                    ("Du bist Aktienanalyst. Nutze NUR die übergebene Tabelle.",
                     "Je Titel 1 Stärke + 1 Schwäche (stichpunktartig), dann Ranking nach "
                     "Chance-Risiko-Verhältnis mit je 1 Satz Begründung. Deutsch, kompakt."),
                    st.session_state.peer_df.to_string(index=False))
            st.session_state.ai_results[("peers", ticker)] = (ok_p, fazit)

    if st.session_state.peer_df is not None and len(st.session_state.peer_df):
        dfp = st.session_state.peer_df.copy()
        show = pd.DataFrame({
            "Ticker": dfp["Ticker"], "Name": dfp["Name"],
            "Market Cap": dfp["MCap"].map(fmt_big),
            "KGV": dfp["KGV"].map(lambda v: fmt(v)),
            "EV/EBITDA": dfp["EV/EBITDA"].map(lambda v: fmt(v)),
            "Nettomarge": dfp["Nettomarge"].map(lambda v: fmt(v, "{:.1%}")),
            "ROE": dfp["ROE"].map(lambda v: fmt(v, "{:.1%}")),
            "Div.-Rendite": dfp["Div."].map(lambda v: fmt(v, "{:.2%}")),
            "1J-Perf.": dfp["1J-Perf."].map(lambda v: fmt(v, "{:+.1%}")),
        })
        st.dataframe(show, use_container_width=True, hide_index=True)
        if st.session_state.peer_norm is not None:
            st.markdown("#### Kursentwicklung 1 Jahr (indexiert = 100)")
            fig_pn = go.Figure()
            palette = [CLR["cyan"], CLR["up"], CLR["amber"], CLR["violet"]]
            for j, sym in enumerate(dfp["Ticker"]):
                if sym in st.session_state.peer_norm.columns:
                    fig_pn.add_trace(go.Scatter(x=st.session_state.peer_norm.index,
                                                y=st.session_state.peer_norm[sym], name=sym,
                                                line=dict(width=2, color=palette[j % 4])))
            fig_pn.update_layout(template="plotly_dark", height=320,
                                 margin=dict(l=0, r=0, t=10, b=0),
                                 legend=dict(orientation="h", y=1.12),
                                 paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig_pn, use_container_width=True)
        if ("peers", ticker) in st.session_state.ai_results:
            st.markdown(f"#### KI-Fazit {badge('KI', 'ki')}", unsafe_allow_html=True)
            render_ai_result(*st.session_state.ai_results[("peers", ticker)])

# ============================================================================
#  TAB — MACRO DESK  (echte Kurven: Yahoo/US + EZB/Euroraum)
# ============================================================================

def render_macro():
    st.markdown("### GLOBAL MACRO DESK")
    us = load_us_yields()
    eu = load_ecb_yields()
    mq = load_macro_quotes()

    spread = None
    if is_num(us["yields"].get("10Y")) and is_num(us["yields"].get("3M")):
        spread = us["yields"]["10Y"] - us["yields"]["3M"]

    k1, k2, k3, k4, k5, k6 = st.columns(6)
    vix = mq.get("VIX")
    vix_note = ("ruhig" if is_num(vix) and vix < 15 else
                "erhöht" if is_num(vix) and vix > 25 else "normal")
    k1.markdown(metric_card("VIX", fmt(vix), vix_note,
                            CLR["down"] if vix_note == "erhöht" else CLR["muted"]),
                unsafe_allow_html=True)
    k2.markdown(metric_card("DOLLAR-INDEX", fmt(mq.get("DXY"))), unsafe_allow_html=True)
    k3.markdown(metric_card("WTI ÖL", fmt(mq.get("OIL"), "${:,.2f}")), unsafe_allow_html=True)
    k4.markdown(metric_card("GOLD", fmt(mq.get("GOLD"), "${:,.0f}")), unsafe_allow_html=True)
    k5.markdown(metric_card("EUR/USD", fmt(mq.get("EURUSD"), "{:.4f}")), unsafe_allow_html=True)
    if spread is not None:
        k6.markdown(metric_card("10J–3M SPREAD (US)", f"{spread:+.2f} PP",
                                "invertiert — Rezessionssignal" if spread < 0 else "normal",
                                CLR["down"] if spread < 0 else CLR["up"]),
                    unsafe_allow_html=True)
    else:
        k6.markdown(metric_card("10J–3M SPREAD (US)", "–"), unsafe_allow_html=True)

    st.markdown("#### RISK-ON / RISK-OFF-BAROMETER")
    g_close = load_batch_close(("^VIX", "HG=F", "GC=F", "SPY", "DX-Y.NYB"), period="1mo")
    g_score, g_comps = compute_risk_gauge(g_close)
    gb1, gb2 = st.columns([1, 1.25])
    with gb1:
        if g_score is None:
            st.info("Barometer-Daten aktuell nicht verfügbar.")
        else:
            fig_g = go.Figure(go.Indicator(
                mode="gauge+number", value=g_score,
                number=dict(font=dict(size=34), suffix=""),
                gauge=dict(axis=dict(range=[-100, 100], tickwidth=1),
                           bar=dict(color="#F2F5FA", thickness=.24),
                           borderwidth=0,
                           steps=[dict(range=[-100, -33], color="rgba(255,90,95,.45)"),
                                  dict(range=[-33, 33], color="rgba(255,180,84,.30)"),
                                  dict(range=[33, 100], color="rgba(0,229,140,.40)")])))
            fig_g.update_layout(template="plotly_dark", height=230,
                                margin=dict(l=24, r=24, t=18, b=0),
                                paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig_g, use_container_width=True)
            g_lab = ("RISK-ON" if g_score > 33 else
                     "RISK-OFF" if g_score < -33 else "NEUTRAL")
            g_clr = (CLR["up"] if g_score > 33 else
                     CLR["down"] if g_score < -33 else CLR["amber"])
            st.markdown(f"<div style='text-align:center;font-weight:700;"
                        f"letter-spacing:2px;color:{g_clr}'>{g_lab}</div>",
                        unsafe_allow_html=True)
    with gb2:
        if g_comps:
            rows_g = ""
            for name, raw, pattern, sig in g_comps:
                sig_txt = (f"<span style='float:right;color:{updown(sig)}'>{sig:+.2f}</span>"
                           if sig is not None else
                           f"<span style='float:right;color:{CLR['muted']}'>n. v.</span>")
                rows_g += (f"<p style='margin:.4em 0'>{name}: "
                           f"<b>{fmt(raw, pattern)}</b>{sig_txt}</p>")
            st.markdown(card(rows_g), unsafe_allow_html=True)
            st.caption("Signal je Komponente von −1 (Risk-Off) bis +1 (Risk-On); Score = "
                       "Mittelwert × 100. VIX invers, Kupfer/Gold als Konjunkturindikator, "
                       "Dollar-Stärke invers.")

    st.markdown("#### CROSS-ASSET-MOMENTUM (1 Monat)")
    ca_map = {"SPY": "S&P 500", "QQQ": "Nasdaq 100", "^GDAXI": "DAX", "EEM": "Emerging Mkts",
              "TLT": "US-Langläufer", "GC=F": "Gold", "CL=F": "Öl (WTI)", "HG=F": "Kupfer",
              "BTC-USD": "Bitcoin", "DX-Y.NYB": "Dollar-Index", "EURUSD=X": "EUR/USD"}
    ca_close = load_batch_close(tuple(ca_map), period="1mo")
    ca_rows = []
    for sym, name in ca_map.items():
        s = ca_close[sym].dropna() if sym in getattr(ca_close, "columns", []) else pd.Series(dtype=float)
        if len(s) > 1 and s.iloc[0]:
            ca_rows.append((name, float(s.iloc[-1] / s.iloc[0] - 1)))
    if ca_rows:
        ca_rows.sort(key=lambda x: x[1])
        fig_ca = go.Figure(go.Bar(
            x=[v * 100 for _, v in ca_rows], y=[n for n, _ in ca_rows],
            orientation="h", marker_color=[updown(v) for _, v in ca_rows],
            text=[f"{v:+.1%}" for _, v in ca_rows], textposition="outside"))
        fig_ca.update_layout(template="plotly_dark", height=30 * len(ca_rows) + 60,
                             xaxis_title="1M-Rendite (%)",
                             margin=dict(l=0, r=40, t=10, b=0),
                             paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig_ca, use_container_width=True)

    st.markdown("#### INFLATIONS- & REALZINS-MONITOR (FRED)")
    be10 = load_fred("T10YIE", years=5)
    rr10 = load_fred("DFII10", years=5)
    if be10.empty and rr10.empty:
        st.info("FRED-Daten aktuell nicht abrufbar.")
    else:
        im1, im2, im3 = st.columns(3)
        if not be10.empty:
            be_tr = (float(be10.iloc[-1] - be10.iloc[-64])
                     if len(be10) > 64 else None)
            im1.markdown(metric_card(
                "INFLATIONSERWARTUNG 10J", f"{float(be10.iloc[-1]):.2f} %",
                f"Breakeven, 3M-Trend {fmt(be_tr, '{:+.2f} PP')}",
                CLR["amber"] if is_num(be_tr) and be_tr > 0.15 else None),
                unsafe_allow_html=True)
        if not rr10.empty:
            rr_tr = (float(rr10.iloc[-1] - rr10.iloc[-64])
                     if len(rr10) > 64 else None)
            im2.markdown(metric_card(
                "REALZINS 10J (TIPS)", f"{float(rr10.iloc[-1]):.2f} %",
                f"3M-Trend {fmt(rr_tr, '{:+.2f} PP')} — der Gegenspieler von "
                f"Gold und Growth", None), unsafe_allow_html=True)
        if not be10.empty and not rr10.empty:
            nom = float(be10.iloc[-1]) + float(rr10.iloc[-1])
            im3.markdown(metric_card(
                "IMPLIZITER NOMINALZINS", f"{nom:.2f} %",
                "Realzins + Inflationserwartung (Fisher-Zerlegung)"),
                unsafe_allow_html=True)
        st.caption("Die Fisher-Zerlegung zeigt, WAS die Zinsen bewegt: steigende "
                   "Breakevens = Inflationssorge, steigende Realzinsen = "
                   "straffere Finanzbedingungen. Quelle: FRED, täglich.")

    st.markdown("#### SEKTOR-ROTATION (US, SPDR-Sektoren)")
    SECTOR_ETFS = {"XLK": "Technologie", "XLF": "Finanzen", "XLE": "Energie",
                   "XLV": "Gesundheit", "XLI": "Industrie", "XLY": "Zykl. Konsum",
                   "XLP": "Basiskonsum", "XLU": "Versorger", "XLB": "Materialien",
                   "XLRE": "Immobilien", "XLC": "Kommunikation"}
    sec_close = load_batch_close(tuple(SECTOR_ETFS), period="3mo")
    sec_rows = []
    for sym, name in SECTOR_ETFS.items():
        s = sec_close[sym].dropna() if sym in getattr(sec_close, "columns", []) else pd.Series(dtype=float)
        if len(s) > 22:
            r1m = float(s.iloc[-1] / s.iloc[-22] - 1)
            r3m = float(s.iloc[-1] / s.iloc[0] - 1)
            sec_rows.append({"sym": sym, "name": name, "1M": r1m, "3M": r3m})
    if sec_rows:
        sec_rows.sort(key=lambda x: x["1M"], reverse=True)
        cyc = [r["1M"] for r in sec_rows if r["sym"] in ("XLY", "XLK", "XLF", "XLI")]
        dfn = [r["1M"] for r in sec_rows if r["sym"] in ("XLP", "XLU", "XLV")]
        rot = (float(np.mean(cyc)) - float(np.mean(dfn))) if cyc and dfn else None
        sr1, sr2 = st.columns([1, 2.4])
        with sr1:
            st.markdown(metric_card(
                "ZYKLIK − DEFENSIVE (1M)", fmt(rot, "{:+.1%}"),
                "positiv = Risikoappetit, negativ = Flucht in Sicherheit",
                updown(rot) if is_num(rot) else None), unsafe_allow_html=True)
            leader, laggard = sec_rows[0], sec_rows[-1]
            st.markdown(metric_card(
                "FÜHRUNG / SCHLUSSLICHT (1M)",
                f"{leader['name']} {leader['1M']:+.1%}",
                f"Schlusslicht: {laggard['name']} {laggard['1M']:+.1%}"),
                unsafe_allow_html=True)
        with sr2:
            fig_sec_rot = go.Figure()
            fig_sec_rot.add_trace(go.Bar(
                x=[r["1M"] * 100 for r in sec_rows],
                y=[r["name"] for r in sec_rows], orientation="h", name="1M",
                marker_color=[updown(r["1M"]) for r in sec_rows],
                text=[f"{r['1M']:+.1%}" for r in sec_rows], textposition="outside"))
            fig_sec_rot.add_trace(go.Scatter(
                x=[r["3M"] * 100 for r in sec_rows],
                y=[r["name"] for r in sec_rows], name="3M", mode="markers",
                marker=dict(color="#F2F5FA", size=7, symbol="diamond")))
            fig_sec_rot.update_layout(template="plotly_dark",
                                      height=30 * len(sec_rows) + 70,
                                      xaxis_title="Rendite (%)",
                                      margin=dict(l=0, r=48, t=10, b=0),
                                      legend=dict(orientation="h", y=1.08),
                                      paper_bgcolor="rgba(0,0,0,0)",
                                      plot_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig_sec_rot, use_container_width=True)
        st.caption("Sektor-Führung verrät die Markterzählung: Energie/Finanzen vorn = "
                   "Reflation, Versorger/Basiskonsum vorn = Defensive. Rauten = 3M.")
    else:
        st.info("Sektor-Daten aktuell nicht abrufbar.")

    st.markdown(f"#### ZINSSTRUKTURKURVEN {badge('LIVE', 'live')}", unsafe_allow_html=True)
    order = ["3M", "2Y", "5Y", "10Y", "30Y"]
    fig_yc = go.Figure()
    if us["yields"]:
        xs = [m for m in order if m in us["yields"]]
        fig_yc.add_trace(go.Scatter(x=xs, y=[us["yields"][m] for m in xs],
                                    name=f"US Treasuries ({us['date'] or 'aktuell'})",
                                    mode="lines+markers",
                                    line=dict(color=CLR["up"], width=3), marker=dict(size=8)))
    if eu["yields"]:
        xs = [m for m in order if m in eu["yields"]]
        fig_yc.add_trace(go.Scatter(x=xs, y=[eu["yields"][m] for m in xs],
                                    name=f"Euroraum AAA / EZB ({eu['date'] or 'aktuell'})",
                                    mode="lines+markers",
                                    line=dict(color=CLR["cyan"], width=3), marker=dict(size=8)))
    if fig_yc.data:
        fig_yc.update_layout(template="plotly_dark", height=340,
                             yaxis_title="Rendite (%)", margin=dict(l=0, r=0, t=10, b=0),
                             legend=dict(orientation="h", y=1.12),
                             paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig_yc, use_container_width=True)
        srcs = []
        if us["yields"]:
            srcs.append("US: CBOE-Yield-Indizes via Yahoo")
        if eu["yields"]:
            srcs.append("Euroraum: EZB Data API (AAA-Staatsanleihen)")
        st.caption("Quellen — " + " · ".join(srcs))
    else:
        st.warning("Zinsdaten aktuell von keiner Quelle abrufbar — bitte später erneut laden.")
    if not eu["yields"]:
        st.caption("Hinweis: EZB-Kurve derzeit nicht erreichbar; es wird nur die US-Kurve gezeigt.")

    st.markdown(f"#### MAKRO-INTERPRETATION {badge('KI', 'ki')}", unsafe_allow_html=True)
    if st.button("ZINSKURVEN DEUTEN", use_container_width=False):
        payload = (f"US-Kurve: {us['yields'] or 'nicht verfügbar'} | "
                   f"Euroraum-Kurve (EZB): {eu['yields'] or 'nicht verfügbar'} | "
                   f"US 10J–3M-Spread: {f'{spread:+.2f} PP' if spread is not None else 'n/v'} | "
                   f"VIX: {fmt(vix)}")
        with st.spinner("Analysiere Zinsstruktur …"):
            ok_m, res_m = run_ai(
                ("Du bist Chef-Makroökonom an einem institutionellen Handelstisch.",
                 "Deute die Kurvenform(en) (normal/flach/invertiert), den transatlantischen "
                 "Unterschied und das Rezessionsrisiko. Nur übergebene Daten nutzen. "
                 "Deutsch, max. 5 Sätze."),
                payload)
        st.session_state.ai_results[("macro", "global")] = (ok_m, res_m)
    if ("macro", "global") in st.session_state.ai_results:
        render_ai_result(*st.session_state.ai_results[("macro", "global")], accent=CLR["cyan"])

# ============================================================================
#  TAB — PORTFOLIO
# ============================================================================

def render_portfolio():
    st.markdown("### PORTFOLIO-TRACKER")
    st.caption("Positionen werden lokal in `terminal_data.db` gespeichert und überleben "
               "Neustarts. CSV-Export/-Import unten dient als Backup und Austauschformat.")

    f1, f2, f3, f4, f5 = st.columns([1.2, 1.3, 1, 1, 1])
    in_ticker = f1.text_input("Ticker", value=ticker, key="pf_ticker").upper().strip()
    buy_date = f2.date_input("Kaufdatum", key="pf_date")
    qty = f3.number_input("Stückzahl", min_value=0.0001, value=1.0, step=0.1,
                          format="%.4f", key="pf_qty")
    b_price = f4.number_input("Kaufpreis", min_value=0.0001,
                              value=float(round(current_price, decimals)), key="pf_price")
    f5.markdown("<div style='height:1.75rem'></div>", unsafe_allow_html=True)
    if f5.button("HINZUFÜGEN", use_container_width=True):
        check = load_history(in_ticker, "5d", "1d")
        if check.empty:
            st.error(f"'{in_ticker}' liefert keine Kursdaten — Position nicht angelegt.")
        else:
            st.session_state.portfolio.append({
                "Ticker": in_ticker, "Datum": buy_date.strftime("%Y-%m-%d"),
                "Stückzahl": float(qty), "Kaufpreis": float(b_price)})
            db_save_portfolio(st.session_state.portfolio)
            st.rerun()

    if not st.session_state.portfolio:
        st.info("Noch keine Positionen. Links Ticker wählen oder oben direkt erfassen.")
    else:
        trades = pd.DataFrame(st.session_state.portfolio)
        pf_syms = tuple(sorted(trades["Ticker"].unique()))
        close5 = load_batch_close(pf_syms, period="5d")

        agg, sector_alloc, pos_alloc = [], {}, {}
        total_val = total_inv = total_day = 0.0
        for sym in pf_syms:
            t_tr = trades[trades["Ticker"] == sym]
            t_qty = float(t_tr["Stückzahl"].sum())
            t_inv = float((t_tr["Stückzahl"] * t_tr["Kaufpreis"]).sum())
            s = close5[sym].dropna() if sym in getattr(close5, "columns", []) else pd.Series(dtype=float)
            if len(s) == 0:
                agg.append({"Ticker": sym, "Stück": t_qty, "Ø Kauf": t_inv / t_qty,
                            "Kurs": None, "Wert": None, "P&L": None, "P&L %": None,
                            "Tag %": None, "Gewicht": None})
                continue
            cur = float(s.iloc[-1])
            prev = float(s.iloc[-2]) if len(s) > 1 else cur
            val = t_qty * cur
            pl = val - t_inv
            total_val += val
            total_inv += t_inv
            total_day += t_qty * (cur - prev)
            sec = load_info(sym).get("sector") or "Sonstige/ETF"
            sector_alloc[sec] = sector_alloc.get(sec, 0.0) + val
            pos_alloc[sym] = val
            agg.append({"Ticker": sym, "Stück": t_qty, "Ø Kauf": t_inv / t_qty, "Kurs": cur,
                        "Wert": val, "P&L": pl, "P&L %": pl / t_inv if t_inv else None,
                        "Tag %": cur / prev - 1 if prev else None, "Gewicht": None})
        for a in agg:  # Gewichte erst nach Gesamtwert berechenbar
            a["Gewicht"] = (a["Wert"] / total_val) if is_num(a["Wert"]) and total_val else None

        s1, s2, s3, s4 = st.columns(4)
        total_pl = total_val - total_inv
        s1.markdown(metric_card("GESAMTWERT", fmt_big(total_val)), unsafe_allow_html=True)
        s2.markdown(metric_card("INVESTIERT", fmt_big(total_inv)), unsafe_allow_html=True)
        s3.markdown(metric_card("P&L GESAMT",
                                f"<span style='color:{updown(total_pl)}'>{total_pl:+,.2f}</span>",
                                fmt(total_pl / total_inv if total_inv else None, "{:+.1%}"),
                                updown(total_pl)), unsafe_allow_html=True)
        s4.markdown(metric_card("HEUTE",
                                f"<span style='color:{updown(total_day)}'>{total_day:+,.2f}</span>"),
                    unsafe_allow_html=True)
        st.caption("Hinweis: Beträge werden in der jeweiligen Handelswährung summiert — "
                   "gemischte Währungen (z. B. USD + EUR) sind hier nicht umgerechnet.")

        df_agg = pd.DataFrame(agg)
        st.dataframe(pd.DataFrame({
            "Ticker": df_agg["Ticker"],
            "Stück": df_agg["Stück"].map(lambda v: fmt(v, "{:,.4g}")),
            "Ø Kaufpreis": df_agg["Ø Kauf"].map(lambda v: fmt(v)),
            "Kurs": df_agg["Kurs"].map(lambda v: fmt(v)),
            "Wert": df_agg["Wert"].map(lambda v: fmt(v)),
            "P&L": df_agg["P&L"].map(lambda v: fmt(v, "{:+,.2f}")),
            "P&L %": df_agg["P&L %"].map(lambda v: fmt(v, "{:+.1%}")),
            "Heute": df_agg["Tag %"].map(lambda v: fmt(v, "{:+.2%}")),
            "Gewicht": df_agg["Gewicht"].map(lambda v: fmt(v, "{:.1%}")),
        }), use_container_width=True, hide_index=True)

        pie1, pie2 = st.columns(2)
        with pie1:
            if sector_alloc:
                fig_sec = px.pie(names=list(sector_alloc), values=list(sector_alloc.values()),
                                 hole=.45, template="plotly_dark", title="Sektor-Allokation")
                fig_sec.update_layout(height=300, margin=dict(l=0, r=0, t=40, b=0),
                                      paper_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig_sec, use_container_width=True)
        with pie2:
            if pos_alloc:
                fig_pos = px.pie(names=list(pos_alloc), values=list(pos_alloc.values()),
                                 hole=.45, template="plotly_dark", title="Positions-Gewichtung")
                fig_pos.update_layout(height=300, margin=dict(l=0, r=0, t=40, b=0),
                                      paper_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig_pos, use_container_width=True)

        val_syms = [s for s in pf_syms if is_num(pos_alloc.get(s))]
        if val_syms:
            st.markdown("#### PORTFOLIO-RISIKO (PRO-FORMA, 1 JAHR)")
            st.caption("Heutige Gewichte konstant über 1 Jahr zurückgerechnet — Analyse "
                       "der aktuellen Struktur, keine echte Depothistorie.")
            factor_pool = tuple(dict.fromkeys(
                list(val_syms) + [BENCH_US] + list(FACTOR_SYMS.values())))
            c1y = load_batch_close(factor_pool, period="1y")
            rets = pd.DataFrame({s: naive_daily(c1y[s]).pct_change()
                                 for s in factor_pool
                                 if s in getattr(c1y, "columns", [])})
            have = [s for s in val_syms if s in rets.columns
                    and rets[s].dropna().size > 30]
            if have:
                w = pd.Series({s: pos_alloc[s] for s in have})
                w = w / w.sum()
                pr = (rets[have] * w).sum(axis=1, min_count=len(have)).dropna()
                cum = (1 + pr).cumprod()
                p_vol = float(pr.std() * np.sqrt(TRADING_DAYS)) if len(pr) > 30 else None
                p_mdd = float((cum / cum.cummax() - 1).min()) if len(cum) else None
                hhi = float((w ** 2).sum())
                eff_n = 1 / hhi if hhi else None
                ind_vol = {s: float(rets[s].std() * np.sqrt(TRADING_DAYS)) for s in have}
                w_avg_vol = float(sum(w[s] * ind_vol[s] for s in have))
                div_q = (w_avg_vol / p_vol) if p_vol else None
                pk1, pk2, pk3, pk4 = st.columns(4)
                pk1.markdown(metric_card("PORTFOLIO-VOLA p.a.", fmt(p_vol, "{:.1%}"),
                                         "Schwankung der Gesamtstruktur"),
                             unsafe_allow_html=True)
                pk2.markdown(metric_card("MAX DRAWDOWN (1J)", fmt(p_mdd, "{:.1%}"),
                                         "pro-forma", CLR["down"]), unsafe_allow_html=True)
                pk3.markdown(metric_card("EFFEKTIVE POSITIONEN", fmt(eff_n, "{:.1f}"),
                                         "1/HHI — Konzentrationsmaß"),
                             unsafe_allow_html=True)
                pk4.markdown(metric_card("DIVERSIFIKATIONSQUOTE", fmt(div_q, "{:.2f}"),
                                         "> 1 = Streuung senkt das Risiko",
                                         CLR["up"] if is_num(div_q) and div_q > 1.1
                                         else None), unsafe_allow_html=True)
                pr1, pr2 = st.columns([1.4, 1.2])
                with pr1:
                    fig_pf = go.Figure()
                    fig_pf.add_trace(go.Scatter(x=cum.index, y=cum / cum.iloc[0] * 100,
                                                name="Portfolio (pro-forma)",
                                                line=dict(color=CLR["cyan"], width=2.2)))
                    if BENCH_US in rets.columns:
                        bcum = (1 + rets[BENCH_US].reindex(pr.index)).cumprod()
                        fig_pf.add_trace(go.Scatter(x=bcum.index,
                                                    y=bcum / bcum.iloc[0] * 100,
                                                    name=BENCH_US,
                                                    line=dict(color=CLR["muted"],
                                                              width=1.6, dash="dot")))
                    fig_pf.update_layout(template="plotly_dark", height=280,
                                         margin=dict(l=0, r=0, t=8, b=0),
                                         legend=dict(orientation="h", y=1.14),
                                         paper_bgcolor="rgba(0,0,0,0)",
                                         plot_bgcolor="rgba(0,0,0,0)")
                    st.plotly_chart(fig_pf, use_container_width=True)
                with pr2:
                    if len(have) >= 2:
                        cm = rets[have].corr()
                        fig_cm = go.Figure(go.Heatmap(
                            z=cm.values, x=cm.columns, y=cm.index,
                            colorscale="RdYlGn", reversescale=True,
                            zmin=-1, zmax=1, texttemplate="%{z:.2f}",
                            textfont=dict(size=10)))
                        fig_cm.update_layout(template="plotly_dark", height=280,
                                             title=dict(text="Korrelationen (rot = Klumpenrisiko)",
                                                        font=dict(size=12)),
                                             margin=dict(l=0, r=0, t=30, b=0),
                                             paper_bgcolor="rgba(0,0,0,0)")
                        st.plotly_chart(fig_cm, use_container_width=True)
                    else:
                        st.info("Korrelationsmatrix ab 2 Positionen.")

                # --- Markowitz: Wo steht das Depot relativ zur Effizienzgrenze? --
                if len(have) >= 2:
                    st.markdown("#### EFFIZIENZ-CHECK (Markowitz)")
                    ef = efficient_frontier(rets[have])
                    if ef is not None:
                        cur_v, cur_r = portfolio_point(
                            w[have].to_numpy(), ef["mu"], ef["cov"])
                        fig_ef = go.Figure()
                        fig_ef.add_trace(go.Scatter(
                            x=ef["vol"] * 100, y=ef["ret"] * 100, mode="markers",
                            marker=dict(size=4, color=ef["sharpe"],
                                        colorscale="Viridis", showscale=True,
                                        colorbar=dict(title="Sharpe")),
                            name="mögliche Gewichtungen", hoverinfo="skip"))
                        fig_ef.add_trace(go.Scatter(
                            x=[cur_v * 100], y=[cur_r * 100], mode="markers+text",
                            marker=dict(size=14, color="#F2F5FA", symbol="x"),
                            text=["Dein Depot"], textposition="top center",
                            name="aktuell"))
                        ms = ef["i_maxsh"]
                        fig_ef.add_trace(go.Scatter(
                            x=[ef["vol"][ms] * 100], y=[ef["ret"][ms] * 100],
                            mode="markers+text",
                            marker=dict(size=14, color=CLR["up"], symbol="star"),
                            text=["Max Sharpe"], textposition="bottom center",
                            name="Max Sharpe"))
                        fig_ef.update_layout(template="plotly_dark", height=340,
                                             xaxis_title="Volatilität p.a. (%)",
                                             yaxis_title="erwartete Rendite p.a. (%)",
                                             margin=dict(l=0, r=0, t=10, b=0),
                                             legend=dict(orientation="h", y=1.1),
                                             paper_bgcolor="rgba(0,0,0,0)",
                                             plot_bgcolor="rgba(0,0,0,0)")
                        st.plotly_chart(fig_ef, use_container_width=True)
                        opt_w = pd.Series(ef["w"][ms], index=ef["cols"])
                        st.dataframe(pd.DataFrame({
                            "Position": ef["cols"],
                            "Aktuelles Gewicht": [fmt(w.get(s), "{:.1%}")
                                                  for s in ef["cols"]],
                            "Max-Sharpe-Gewicht": [fmt(opt_w[s], "{:.1%}")
                                                   for s in ef["cols"]],
                        }), hide_index=True, use_container_width=True)
                        st.caption("Klassische Portfoliotheorie auf 1J-Daten — die "
                                   "Schätzungen sind notorisch instabil (kleine "
                                   "Datenänderung, ganz andere 'optimale' Gewichte). "
                                   "Als Richtungssignal lesen, nicht als Befehl.")

                # --- Faktormodell: Woraus besteht das Depot-Risiko wirklich? ----
                st.markdown("#### FAKTOR-EXPOSURE & SZENARIO-RECHNER")
                fac_cols = {name: sym for name, sym in FACTOR_SYMS.items()
                            if sym in rets.columns}
                freg = (factor_regression(
                    pr, rets[list(fac_cols.values())].rename(
                        columns={v: k for k, v in fac_cols.items()}))
                    if len(fac_cols) >= 2 else None)
                if freg is None:
                    st.info("Faktordaten aktuell nicht ausreichend verfügbar.")
                else:
                    fx1, fx2 = st.columns([1.1, 1.6])
                    with fx1:
                        beta_html = "".join(
                            f"<p style='margin:.35em 0'>{esc(k)}: "
                            f"<b style='color:{updown(v)}'>{v:+.2f}</b></p>"
                            for k, v in freg["betas"].items())
                        st.markdown(card(
                            f"<div class='metric-title'>FAKTOR-BETAS "
                            f"(R² = {fmt(freg['r2'], '{:.0%}')})</div>" + beta_html
                            + f"<p style='margin:.35em 0'>Alpha p.a.: "
                              f"<b style='color:{updown(freg['alpha_ann'])}'>"
                              f"{freg['alpha_ann']:+.1%}</b></p>"),
                            unsafe_allow_html=True)
                        if is_num(freg["r2"]) and freg["r2"] < 0.3:
                            st.caption("Niedriges R²: Die vier Faktoren erklären "
                                       "wenig — Einzeltitelrisiken dominieren.")
                    with fx2:
                        sc_cols = st.columns(len(freg["betas"]))
                        moves, defaults = {}, {"Markt (SPY)": -20.0,
                                               "Zinsen (TLT)": 5.0,
                                               "Dollar (DXY)": 3.0, "Öl (WTI)": 15.0}
                        for i_f, name in enumerate(freg["betas"]):
                            moves[name] = sc_cols[i_f].slider(
                                name.split(" (")[0] + " (%)", -40.0, 40.0,
                                defaults.get(name, 0.0), 1.0, key=f"fm_{name}")
                        impact = sum(freg["betas"][n] * moves[n] / 100
                                     for n in freg["betas"])
                        st.markdown(metric_card(
                            "GESCHÄTZTE DEPOT-REAKTION",
                            f"{impact:+.1%}  ·  {impact * total_val:+,.0f}",
                            "lineare Beta-Schätzung auf Basis von 1J-Daten",
                            updown(impact)), unsafe_allow_html=True)
                        st.caption("Vorsicht: Betas sind historisch geschätzt und "
                                   "in Krisen instabil — Korrelationen springen "
                                   "genau dann, wenn es zählt. Als Größenordnung "
                                   "lesen, nicht als Punktprognose.")

                # --- Was würde dieses Depot am besten diversifizieren? ----------
                st.markdown("#### DIVERSIFIKATOR-FINDER")
                cand = dict(DIVERSIFIER_CANDIDATES)
                for wsym in st.session_state.watchlist:
                    if wsym not in cand and wsym not in have:
                        cand[wsym] = wsym
                cand = {s: n for s, n in cand.items() if s not in have}
                if cand:
                    cd_close = load_batch_close(tuple(cand), period="1y")
                    div_rows = []
                    for sym, name in cand.items():
                        if sym in getattr(cd_close, "columns", []):
                            cr = naive_daily(cd_close[sym]).pct_change()
                            jj = pd.concat([pr, cr], axis=1, join="inner").dropna()
                            if len(jj) > 60:
                                div_rows.append({"Kandidat": name,
                                                 "Korrelation zum Depot":
                                                     float(jj.iloc[:, 0].corr(jj.iloc[:, 1]))})
                    if div_rows:
                        div_rows.sort(key=lambda x: x["Korrelation zum Depot"])
                        st.dataframe(pd.DataFrame([{
                            "Kandidat": d["Kandidat"],
                            "Korrelation zum Depot": fmt(d["Korrelation zum Depot"]),
                        } for d in div_rows[:8]]), hide_index=True,
                            use_container_width=True)
                        st.caption("Je niedriger (oder negativer) die Korrelation, "
                                   "desto stärker senkt eine Beimischung die "
                                   "Depotschwankung. Kandidaten: Klassiker plus "
                                   "deine Watchlist.")

                # --- Rebalancing: Zielgewichte -> konkrete Orders ---------------
                with st.expander("REBALANCING-ASSISTENT — Zielgewichte umsetzen"):
                    tgt_cols = st.columns(min(len(have), 5))
                    targets = {}
                    for i_s, sym in enumerate(have):
                        targets[sym] = tgt_cols[i_s % len(tgt_cols)].number_input(
                            f"{sym} (%)", 0.0, 100.0,
                            float(round(w[sym] * 100, 1)), 1.0, key=f"tw_{sym}")
                    tsum = sum(targets.values())
                    thresh = st.slider("Handeln ab Abweichung von (%)", 0.5, 10.0,
                                       2.0, 0.5)
                    if tsum <= 0:
                        st.info("Zielgewichte vergeben (Summe > 0).")
                    else:
                        if abs(tsum - 100) > 0.5:
                            st.caption(f"Summe {tsum:.1f} % — wird auf 100 % normiert.")
                        orders = []
                        for sym in have:
                            tgt_share = targets[sym] / tsum
                            cur_val = pos_alloc.get(sym, 0.0)
                            delta = tgt_share * total_val - cur_val
                            px = None
                            s5 = (close5[sym].dropna()
                                  if sym in getattr(close5, "columns", [])
                                  else pd.Series(dtype=float))
                            if len(s5):
                                px = float(s5.iloc[-1])
                            if abs(delta) / total_val * 100 >= thresh and px:
                                orders.append({
                                    "Position": sym,
                                    "Aktion": "KAUFEN" if delta > 0 else "VERKAUFEN",
                                    "Betrag": f"{abs(delta):,.2f}",
                                    "≈ Stück": f"{abs(delta) / px:,.2f}",
                                    "Ist → Ziel": f"{cur_val / total_val:.1%} → "
                                                  f"{tgt_share:.1%}",
                                })
                        if orders:
                            st.dataframe(pd.DataFrame(orders), hide_index=True,
                                         use_container_width=True)
                        else:
                            st.success("Alle Positionen innerhalb der Toleranz — "
                                       "kein Handlungsbedarf.")
                        st.caption("Ohne Kosten/Steuern gerechnet; bei kleinen Depots "
                                   "fressen Mindestgebühren enge Toleranzen auf.")

        with st.expander("Einzelne Käufe / Verwaltung"):
            show_tr = trades.copy()
            show_tr.index = [f"#{i + 1}" for i in range(len(show_tr))]
            st.dataframe(show_tr, use_container_width=True)
            d1, d2, d3 = st.columns([1.4, 1, 1])
            pick = d1.selectbox("Kauf entfernen",
                                [f"#{i + 1} — {r['Ticker']} ({r['Datum']})"
                                 for i, r in trades.iterrows()])
            if d2.button("ENTFERNEN", use_container_width=True):
                idx = int(pick.split("—")[0].strip().lstrip("#")) - 1
                st.session_state.portfolio.pop(idx)
                db_save_portfolio(st.session_state.portfolio)
                st.rerun()
            if d3.button("ALLES LÖSCHEN", use_container_width=True):
                st.session_state.portfolio = []
                db_save_portfolio([])
                st.rerun()

        e1, e2 = st.columns(2)
        e1.download_button("Portfolio als CSV exportieren",
                           trades.to_csv(index=False).encode("utf-8-sig"),
                           file_name="portfolio.csv", mime="text/csv",
                           use_container_width=True)
        up = e2.file_uploader("CSV importieren (ersetzt aktuelles Portfolio)", type="csv",
                              label_visibility="collapsed")
        if up is not None:
            try:
                imp = pd.read_csv(up)
                need = {"Ticker", "Datum", "Stückzahl", "Kaufpreis"}
                if need.issubset(imp.columns):
                    st.session_state.portfolio = imp[list(need)].to_dict("records")
                    db_save_portfolio(st.session_state.portfolio)
                    st.success("Portfolio importiert.")
                    st.rerun()
                else:
                    st.error(f"CSV braucht die Spalten: {', '.join(sorted(need))}.")
            except Exception as e:
                st.error(f"Import fehlgeschlagen: {e}")

# ============================================================================
#  TAB — RISK & STRESS TEST
# ============================================================================

def render_risk():
    st.markdown("### QUANTITATIVE RISIKOANALYSE")

    r1, r2, r3, r4, r5, r6 = st.columns(6)
    r1.markdown(metric_card("VOLATILITÄT p.a.", fmt(rm["vol"], "{:.1%}"),
                            "Schwankungsbreite, 1J"), unsafe_allow_html=True)
    r2.markdown(metric_card(f"BETA vs. {bench_sym}", fmt(rm["beta"]),
                            "Marktsensitivität"), unsafe_allow_html=True)
    r3.markdown(metric_card("SHARPE RATIO", fmt(rm["sharpe"]),
                            f"Rendite je Risiko (rf {RISK_FREE_RATE:.0%})"), unsafe_allow_html=True)
    r4.markdown(metric_card("SORTINO RATIO", fmt(rm["sortino"]),
                            "nur Abwärtsrisiko"), unsafe_allow_html=True)
    r5.markdown(metric_card("MAX DRAWDOWN", fmt(rm["max_dd"], "{:.1%}"),
                            "größter Rückgang, 2J", CLR["down"]), unsafe_allow_html=True)
    r6.markdown(metric_card("VaR 95 % (1 Tag)", fmt(rm["var95"], "{:.2%}"),
                            "historische Simulation", CLR["down"]), unsafe_allow_html=True)

    ret_2y = naive_daily(hist["Close"].tail(2 * TRADING_DAYS)).pct_change().dropna()
    cv95 = cvar95(ret_2y)
    vr_ratio, vr_label = vol_regime(hist["Close"])
    rc_series = (rolling_corr(hist["Close"].tail(2 * TRADING_DAYS), bench_hist["Close"])
                 if not bench_hist.empty else pd.Series(dtype=float))
    rc_now = safe_last(rc_series)
    q1, q2, q3 = st.columns(3)
    q1.markdown(metric_card("CVaR 95 % (1 Tag)", fmt(cv95, "{:.2%}"),
                            "Ø-Verlust der schlechtesten 5 % der Tage", CLR["down"]),
                unsafe_allow_html=True)
    vr_clr = {"RUHIG": CLR["up"], "ERHÖHT": CLR["amber"], "STRESS": CLR["down"]}.get(vr_label)
    q2.markdown(metric_card("VOLATILITÄTS-REGIME", vr_label or "–",
                            f"30T-Vola bei {fmt(vr_ratio, '{:.1f}')}× des Normalniveaus"
                            if vr_ratio else "keine Daten", vr_clr),
                unsafe_allow_html=True)
    q3.markdown(metric_card(f"KORRELATION 60T vs. {bench_sym}", fmt(rc_now),
                            "aktueller Gleichlauf mit dem Markt"), unsafe_allow_html=True)

    dd = drawdown_series(hist["Close"].tail(2 * TRADING_DAYS))
    fig_dd = go.Figure(go.Scatter(x=dd.index, y=dd * 100, fill="tozeroy",
                                  line=dict(color=CLR["down"], width=1.2),
                                  fillcolor="rgba(255,90,95,.18)", name="Drawdown"))
    fig_dd.update_layout(template="plotly_dark", height=240, yaxis_title="Drawdown (%)",
                         margin=dict(l=0, r=0, t=26, b=0), title="Drawdown-Verlauf (2 Jahre)",
                         paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig_dd, use_container_width=True)

    rc_col, dd_col = st.columns([1.4, 1.3])
    with rc_col:
        st.markdown(f"#### ROLLENDE KORRELATION (60 T) vs. {bench_sym}")
        if len(rc_series) > 10:
            fig_rc = go.Figure(go.Scatter(x=rc_series.index, y=rc_series,
                                          line=dict(color=CLR["violet"], width=1.6)))
            fig_rc.add_hline(y=0, line_dash="dot", line_color=CLR["muted"], opacity=.5)
            fig_rc.update_layout(template="plotly_dark", height=260,
                                 yaxis=dict(range=[-1, 1]),
                                 margin=dict(l=0, r=0, t=6, b=0),
                                 paper_bgcolor="rgba(0,0,0,0)",
                                 plot_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig_rc, use_container_width=True)
            st.caption("Sinkende Korrelation = wachsender Diversifikationsnutzen "
                       "gegenüber dem Markt — steigende = Gleichlauf.")
        else:
            st.info("Keine Benchmark-Daten für die Korrelationsanalyse.")
    with dd_col:
        st.markdown("#### GRÖSSTE DRAWDOWNS (10 Jahre)")
        eps = top_drawdowns(hist["Close"].tail(10 * TRADING_DAYS))
        if eps:
            st.dataframe(pd.DataFrame([{
                "Tiefe": f"{e['Tiefe']:.1%}",
                "Beginn": e["Beginn"].strftime("%m/%Y"),
                "Tief": e["Tief"].strftime("%m/%Y"),
                "Dauer": f"{e['Dauer']} T",
                "Erholung": f"{e['Erholt nach']} T" if e["Erholt nach"] is not None else "läuft",
            } for e in eps]), hide_index=True, use_container_width=True)
            st.caption("Erholung = Tage vom Tief zurück zum alten Hoch.")
        else:
            st.info("Keine Drawdown-Episoden ermittelbar.")

    if not bench_hist.empty:
        capm = capm_alpha_beta(hist["Close"].tail(2 * TRADING_DAYS),
                               bench_hist["Close"])
        if capm:
            st.markdown(f"### CAPM-CHECK vs. {bench_sym} — Alpha oder nur Beta?")
            ca1, ca2 = st.columns([1, 2])
            with ca1:
                a_ann = capm["alpha_ann"]
                st.markdown(metric_card(
                    "JENSEN-ALPHA p.a. (1J)", fmt(a_ann, "{:+.1%}"),
                    "Rendite über das Marktrisiko hinaus",
                    updown(a_ann) if is_num(a_ann) else None),
                    unsafe_allow_html=True)
                st.markdown(metric_card("CAPM-BETA (1J)", fmt(capm["beta"]),
                                        "Basis der Alpha-Zerlegung"),
                            unsafe_allow_html=True)
            with ca2:
                rb = capm["roll_beta"]
                if len(rb) > 20:
                    fig_rb = go.Figure(go.Scatter(
                        x=rb.index, y=rb, name="rollierendes Beta (60 T)",
                        line=dict(color=CLR["blue"], width=1.8)))
                    fig_rb.add_hline(y=1, line_dash="dot", line_color=CLR["muted"],
                                     opacity=.6)
                    fig_rb.update_layout(template="plotly_dark", height=240,
                                         margin=dict(l=0, r=0, t=8, b=0),
                                         paper_bgcolor="rgba(0,0,0,0)",
                                         plot_bgcolor="rgba(0,0,0,0)")
                    st.plotly_chart(fig_rb, use_container_width=True)
            st.caption("Alpha auf ein Jahr ist statistisch wackelig — interessant ist "
                       "vor allem das Vorzeichen über Zeit und ob das Beta stabil "
                       "bleibt oder in Stressphasen springt.")

    st.markdown("### KRISEN-PLAYBOOK — realisiertes Verhalten in Stressphasen")
    cp = crisis_playbook(hist["Close"], bench_hist["Close"] if not bench_hist.empty else None)
    if cp:
        st.dataframe(pd.DataFrame([{
            "Phase": e["Phase"],
            "Asset": fmt(e["Asset"], "{:+.1%}"),
            "Max DD in der Phase": fmt(e["Max DD"], "{:.1%}"),
            f"Benchmark ({bench_sym})": fmt(e["Benchmark"], "{:+.1%}"),
        } for e in cp]), hide_index=True, use_container_width=True)
        st.caption("Harte historische Daten statt Hypothesen — deckt die Fenster ab, "
                   "die die Kurshistorie hergibt. Ergänzt den KI-Stresstest unten.")
    else:
        st.info("Die Kurshistorie reicht für keines der definierten Krisenfenster.")

    with st.expander("POSITION-SIZING-RECHNER — wie groß darf die Position sein?"):
        ps1, ps2, ps3 = st.columns(3)
        depot = ps1.number_input("Depotgröße", min_value=500.0, value=10000.0, step=500.0)
        risk_pct = ps2.slider("Risikobudget je Trade (%)", 0.25, 5.0, 1.0, 0.25)
        stop_default = float(np.clip(round((rm["var95"] or 0.02) * 300, 1), 2.0, 25.0))
        stop_pct = ps3.slider("Stop-Loss-Abstand (%)", 1.0, 30.0, stop_default, 0.5,
                              help="Vorbelegt mit ca. 3× Tages-VaR — ein Stop, den "
                                   "normales Rauschen selten reißt.")
        risk_eur = depot * risk_pct / 100
        pos_eur = risk_eur / (stop_pct / 100)
        n_shares = pos_eur / current_price if current_price else None
        vt_weight = (0.10 / rm["vol"]) if rm.get("vol") else None
        pz1, pz2 = st.columns(2)
        pz1.markdown(metric_card(
            "STOP-BASIERTE GRÖSSE", f"{currency} {pos_eur:,.0f}",
            f"= {fmt(n_shares, '{:,.2f}')} Stück · riskiert {currency} {risk_eur:,.0f} "
            f"bei {stop_pct:.1f} % Stop"), unsafe_allow_html=True)
        pz2.markdown(metric_card(
            "VOL-TARGETING (Ziel 10 % p. a.)",
            fmt(min(vt_weight, 1.0) if is_num(vt_weight) else None, "{:.0%} des Depots"),
            f"bei {fmt(rm['vol'], '{:.0%}')} Asset-Volatilität"
            + (" — Gewicht auf 100 % gekappt" if is_num(vt_weight) and vt_weight > 1 else "")),
            unsafe_allow_html=True)
        st.caption("Zwei Profi-Ansätze: Risiko fixieren (Stop-basiert) oder Schwankung "
                   "fixieren (Vol-Targeting). Beide begrenzen Schaden, keiner "
                   "garantiert ihn — Gaps können Stops überspringen.")
        kl = kelly_from_monthly(hist["Close"])
        atr = safe_last(atr_series(hist.tail(3 * TRADING_DAYS)))
        kz1, kz2 = st.columns(2)
        if kl and kl["kelly"] is not None:
            k_cap = float(np.clip(kl["kelly"], -1, 1))
            kz1.markdown(metric_card(
                "KELLY-KRITERIUM (Monatsbasis)",
                fmt(k_cap, "{:+.0%}") + " · Half-Kelly " + fmt(k_cap / 2, "{:+.0%}"),
                f"Trefferquote {kl['W']:.0%}, Gewinn/Verlust-Ratio {kl['R']:.2f} "
                f"({kl['n']} Monate) — Profis nutzen meist Half-Kelly",
                CLR["down"] if k_cap <= 0 else None), unsafe_allow_html=True)
        else:
            kz1.markdown(metric_card("KELLY-KRITERIUM", "–",
                                     "zu wenig Monatshistorie"), unsafe_allow_html=True)
        if is_num(atr) and current_price:
            ch_stop = float(hist["High"].tail(22).max() - 3 * atr)
            kz2.markdown(metric_card(
                "ATR-TRAILING-STOP (Chandelier)",
                f"{currency} {ch_stop:,.{decimals}f}",
                f"22T-Hoch − 3×ATR(14) · ATR = {atr:,.{decimals}f} "
                f"({atr / current_price:.1%} vom Kurs)",
                CLR["amber"] if ch_stop >= current_price else None),
                unsafe_allow_html=True)
        st.caption("Kelly maximiert langfristiges Kapitalwachstum, ist aber "
                   "schätzfehler-empfindlich — negativ heißt: statistisch kein "
                   "Vorteil, Finger weg. Der Chandelier-Stop läuft dem Trend nach, "
                   "statt starr zu bleiben.")

    st.markdown("### MONTE-CARLO-SIMULATION (12 Monate, 500 Pfade)")
    mc = monte_carlo(hist["Close"])
    if mc is None:
        st.info("Zu wenig Kurshistorie für eine Simulation.")
    else:
        x = list(range(mc["pct"].shape[1]))
        p5, p25, p50, p75, p95 = mc["pct"]
        fig_mc = go.Figure()
        fig_mc.add_trace(go.Scatter(x=x, y=p95, line=dict(width=0), showlegend=False,
                                    hoverinfo="skip"))
        fig_mc.add_trace(go.Scatter(x=x, y=p5, fill="tonexty", name="P5–P95",
                                    line=dict(width=0), fillcolor="rgba(76,154,255,.12)"))
        fig_mc.add_trace(go.Scatter(x=x, y=p75, line=dict(width=0), showlegend=False,
                                    hoverinfo="skip"))
        fig_mc.add_trace(go.Scatter(x=x, y=p25, fill="tonexty", name="P25–P75",
                                    line=dict(width=0), fillcolor="rgba(76,154,255,.25)"))
        fig_mc.add_trace(go.Scatter(x=x, y=p50, name="Median",
                                    line=dict(color=CLR["cyan"], width=2.5)))
        fig_mc.add_hline(y=mc["start"], line_dash="dot", line_color=CLR["muted"],
                         annotation_text="heute")
        fig_mc.update_layout(template="plotly_dark", height=340,
                             xaxis_title="Handelstage", yaxis_title=f"Kurs ({currency})",
                             margin=dict(l=0, r=0, t=10, b=0),
                             legend=dict(orientation="h", y=1.1),
                             paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig_mc, use_container_width=True)
        mc1, mc2, mc3 = st.columns(3)
        med, lo, hi = (float(np.median(mc["final"])), float(np.percentile(mc["final"], 5)),
                       float(np.percentile(mc["final"], 95)))
        mc1.markdown(metric_card("MEDIAN in 12M", f"{currency} {med:,.2f}",
                                 fmt(med / mc["start"] - 1, "{:+.1%}"),
                                 updown(med / mc["start"] - 1)), unsafe_allow_html=True)
        mc2.markdown(metric_card("PESSIMISTISCH (P5)", f"{currency} {lo:,.2f}",
                                 fmt(lo / mc["start"] - 1, "{:+.1%}"), CLR["down"]),
                     unsafe_allow_html=True)
        mc3.markdown(metric_card("OPTIMISTISCH (P95)", f"{currency} {hi:,.2f}",
                                 fmt(hi / mc["start"] - 1, "{:+.1%}"), CLR["up"]),
                     unsafe_allow_html=True)
        st.caption("Modell: Geometrische Brownsche Bewegung auf Basis der letzten 2 Jahre. "
                   "Vergangenheit ist keine Garantie — die Simulation zeigt Bandbreiten, "
                   "keine Prognosen.")

    st.markdown(f"### SZENARIO-STRESSTEST {badge('KI', 'ki')}", unsafe_allow_html=True)
    sc1, sc2 = st.columns([1.4, 1.6])
    scenario = sc1.selectbox("Szenario", ["Leitzinserhöhung um 100 BP", "Globale Rezession",
                                          "Geopolitischer Schock", "Eigenes Szenario …"])
    custom = sc2.text_input("Eigenes Szenario",
                            placeholder="z. B. Ölpreis verdoppelt sich binnen 6 Monaten",
                            disabled=scenario != "Eigenes Szenario …")
    chosen = custom.strip() if scenario == "Eigenes Szenario …" and custom.strip() else scenario
    if st.button("STRESSTEST AUSFÜHREN"):
        payload = (f"Asset: {ticker} ({short_name}), Sektor {info.get('sector', '–')}. "
                   f"Kennzahlen: Beta {fmt(rm['beta'])}, Vola {fmt(rm['vol'], '{:.0%}')}, "
                   f"Max Drawdown 2J {fmt(rm['max_dd'], '{:.0%}')}, "
                   f"Verschuldung (D/E) {fmt(info.get('debtToEquity'))}. Szenario: {chosen}")
        with st.spinner("Berechne Stresstest …"):
            ok_st, res_st = run_ai(
                ("Du bist Chief Risk Officer. Stütze dich auf die übergebenen Kennzahlen.",
                 "Struktur: Wirkungskanäle (max. 3 Punkte), grobe Kursreaktion als Spanne "
                 "in % mit Herleitung aus Beta/Vola, 1 Absicherungsidee. "
                 "Deutsch, kompakt, Unsicherheit benennen."),
                payload)
        st.session_state.ai_results[("stress", ticker + chosen)] = (ok_st, res_st)
    if ("stress", ticker + chosen) in st.session_state.ai_results:
        render_ai_result(*st.session_state.ai_results[("stress", ticker + chosen)],
                         accent=CLR["amber"])

PAGES = {
    "COMMAND CENTER": render_command_center, "GLOBAL OVERVIEW": render_overview,
    "MAIN ASSET": render_main, "QUANT & SCORES": render_quant,
    "ASSET-CLASS DESK": render_asset_class, "OPTIONS-DESK": render_options,
    "SMART MONEY": render_smart_money, "STRATEGY LAB": render_strategy_lab,
    "FINANCIALS & AUDIT": render_financials, "PEER ANALYSIS": render_peers,
    "MACRO DESK": render_macro, "PORTFOLIO": render_portfolio,
    "RISK & STRESS TEST": render_risk,
}
try:
    PAGES[nav]()
except Exception as _e:
    log.exception("Sektion '%s' fehlgeschlagen", nav)
    st.error(f"Die Sektion »{nav}« konnte nicht geladen werden "
             f"({type(_e).__name__}). Meist ist eine externe Datenquelle "
             f"kurzzeitig gestört — »Alle Daten neu laden« in der Sidebar "
             f"behebt das in der Regel. Alle anderen Sektionen bleiben nutzbar.")

st.markdown("---")
with st.expander("KENNZAHLEN-GLOSSAR — jedes Instrument in einem Satz"):
    gl1, gl2 = st.columns(2)
    items = list(GLOSSAR.items())
    half = (len(items) + 1) // 2
    for col, chunk in ((gl1, items[:half]), (gl2, items[half:])):
        with col:
            for term, expl in chunk:
                st.markdown(f"**{term}** — {expl}")
st.caption("Institutional Terminal Pro · Daten: Yahoo Finance (ggf. verzögert), EZB Data API · "
           "Alle Analysen dienen der Information und sind keine Anlageberatung.")
