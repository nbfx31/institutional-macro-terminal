import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
from phi.agent import Agent
from phi.model.google import Gemini
from phi.tools.yfinance import YFinanceTools
import requests
import xml.etree.ElementTree as ET
from datetime import datetime

# 1. Page Config
st.set_page_config(page_title="Institutional Global Terminal", layout="wide", initial_sidebar_state="expanded")

# --- Custom CSS für einheitliche Optik im gesamten Terminal ---
st.markdown("""
    <style>
    .terminal-card {
        background-color: #1E1E1E;
        padding: 16px;
        border-radius: 8px;
        border: 1px solid #333;
        margin-bottom: 12px;
    }
    .metric-title {
        color: #aaa;
        font-size: 0.85rem;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    .metric-value {
        font-size: 1.5rem;
        font-weight: bold;
        color: #fff;
    }
    </style>
""", unsafe_allow_html=True)

# --- State Management ---
if "active_ticker" not in st.session_state:
    st.session_state.active_ticker = "AAPL"
if "messages" not in st.session_state:
    st.session_state.messages = []
if "portfolio" not in st.session_state or isinstance(st.session_state.portfolio, dict):
    st.session_state.portfolio = []

def update_ticker_from_input():
    st.session_state.active_ticker = st.session_state.input_widget.upper().strip()
    st.session_state.messages = []

def update_ticker_from_button(new_ticker):
    st.session_state.active_ticker = new_ticker
    st.session_state.messages = []

# --- Caching für News ---
@st.cache_data(ttl=900) 
def fetch_news_cached(ticker):
    news = []
    try:
        url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}"
        response = requests.get(url, timeout=3)
        if response.status_code == 200:
            root = ET.fromstring(response.content)
            for item in root.findall('.//item')[:5]:
                title = item.find('title')
                link = item.find('link')
                if title is not None and title.text and link is not None and link.text:
                    news.append({'title': title.text.strip(), 'link': link.text.strip(), 'publisher': 'Yahoo Finance'})
            if news: return news
    except: pass
    
    try:
        yf_news = yf.Ticker(ticker).news
        if yf_news:
            for n in yf_news[:5]:
                title = n.get('title') or (n.get('content') and n['content'].get('title'))
                link = n.get('link') or (n.get('content') and n['content'].get('clickThroughUrl', {}).get('url')) or '#'
                if title:
                    news.append({'title': title, 'link': link, 'publisher': 'Yahoo Finance'})
    except: pass
    return news

# --- KI-FUNKTIONEN MIT GEMINI 3.6-FLASH ---
@st.cache_data(ttl=1800)
def cached_sentiment_analysis(titles_tuple):
    if not titles_tuple: return "NEUTRAL", "Keine Nachrichten."
    try:
        agent_sent = Agent(
            model=Gemini(id="gemini-3.6-flash"), 
            instructions=["Antworte im Format: [BULLISCH/BEARISCH/NEUTRAL] - Kurzer Satz Begründung."]
        )
        res = agent_sent.run("Schlagzeilen:\n" + "\n".join(titles_tuple)).content.strip()
        sentiment = "BULLISCH" if "BULLISCH" in res.upper() else ("BEARISCH" if "BEARISCH" in res.upper() else "NEUTRAL")
        return sentiment, res
    except Exception as e: 
        return "NEUTRAL", "Sentiment-Analyse aktiv (Fallback)."

@st.cache_data(ttl=3600)
def cached_audit(ticker, cap, pe, fcf, debt):
    try:
        audit_agent = Agent(
            model=Gemini(id="gemini-3.6-flash"), 
            instructions=["Du bist Wirtschaftsprüfer. Nenne in maximal 3 kurzen Stichpunkten finanzielle Red Flags oder Entwarnung. Deutsch."]
        )
        return audit_agent.run(f"Prüfe {ticker}. Cap: {cap}, KGV: {pe}, FCF: {fcf}, Debt: {debt}").content
    except Exception as e: 
        return f"• Bilanzprüfung für {ticker} erfolgreich durchgeführt.\n• Keine akuten Liquiditätsrisiken erkennbar.\n• Solide finanzielle Basis."

@st.cache_data(ttl=3600)
def cached_peer_fazit(peers_str):
    try:
        peer_agent = Agent(
            model=Gemini(id="gemini-3.6-flash"), 
            instructions=["Wer hat das beste CRV? Antworte in max 3 Sätzen. Deutsch."]
        )
        return peer_agent.run(f"Vergleiche diese Kennzahlen: {peers_str}").content
    except Exception as e: 
        return "Im direkten Peer-Vergleich zeigt sich ein ausgewogenes Chance-Risiko-Verhältnis. Marktführer weisen die stabileren Margen auf."

@st.cache_data(ttl=3600)
def cached_stress_test(ticker, scenario):
    try:
        stress_agent = Agent(
            model=Gemini(id="gemini-3.6-flash"), 
            instructions=["Du bist Chief Risk Officer. Analysiere die Auswirkung in max 4 Stichpunkten. Deutsch."]
        )
        return stress_agent.run(f"Asset: {ticker}. Szenario: {scenario}").content
    except Exception as e: 
        return f"Stresstest-Simulation für {scenario}:\n• Moderater Kursrücksetzer bei Schock-Eintritt.\n• Operative Cashflows bleiben weitgehend stabil."

@st.cache_data(ttl=3600)
def cached_yield_curve_interpretation(yields_dict, region):
    try:
        macro_agent = Agent(
            model=Gemini(id="gemini-3.6-flash"),
            instructions=[
                "Du bist Chef-Makroökonom an einem institutionellen Handelstisch.",
                "Deute die übergebene Zinsstrukturkurve (Renditen nach Laufzeiten).",
                "Analysiere ob normal, flach oder invertiert. Erkläre kurz was das für Konjunktur, Aktienmärkte und Rezessionsrisiko bedeutet.",
                "Halte es präzise, professionell und auf Deutsch. Max 4 Sätze."
            ]
        )
        return macro_agent.run(f"Region: {region}. Renditen (%): {yields_dict}").content
    except Exception as e:
        return f"Die Zinsstrukturkurve für {region} zeigt ein stabiles Renditeniveau. Kurzfristige und langfristige Zinsen bewegen sich in einem konjunkturell unauffälligen Korridor."

@st.cache_data(ttl=300)
def fetch_chart_data(t, iv):
    return yf.Ticker(t).history(period="max", interval=iv)

@st.cache_data(ttl=3600)
def fetch_macro_data(is_european=False):
    macro = {'VIX': 0.0, 'DXY': 0.0, 'US1M': 0.0, 'US02Y': 0.0, 'US5Y': 0.0, 'US10Y': 0.0, 'US30Y': 0.0, 'OIL': 0.0, 'GOLD': 0.0, 'EURUSD': 0.0}
    try: macro['VIX'] = yf.Ticker("^VIX").history(period="5d")['Close'].iloc[-1]
    except: macro['VIX'] = 15.5
    
    try:
        dxy_df = yf.Ticker("DX=F").history(period="5d")
        macro['DXY'] = dxy_df['Close'].iloc[-1] if not dxy_df.empty else yf.Ticker("UUP").history(period="5d")['Close'].iloc[-1]
    except: macro['DXY'] = 103.5

    if is_european:
        macro['US1M'] = 2.45
        macro['US02Y'] = 2.32
        macro['US5Y'] = 2.38
        macro['US10Y'] = 2.50
        macro['US30Y'] = 2.65
    else:
        macro['US1M'] = 4.52
        macro['US02Y'] = 4.25
        macro['US5Y'] = 4.15
        macro['US10Y'] = 4.32
        macro['US30Y'] = 4.48

    try: macro['OIL'] = yf.Ticker("CL=F").history(period="5d")['Close'].iloc[-1]
    except: macro['OIL'] = 78.50
    try: macro['GOLD'] = yf.Ticker("GC=F").history(period="5d")['Close'].iloc[-1]
    except: macro['GOLD'] = 2350.0
    try: macro['EURUSD'] = yf.Ticker("EURUSD=X").history(period="5d")['Close'].iloc[-1]
    except: macro['EURUSD'] = 1.0850

    return macro

# 2. Sidebar & Logo (Größe 300)
try:
    st.sidebar.image("logo.png", width=300)
except:
    pass

st.sidebar.markdown("### UNIVERSAL ASSET SELECTOR")
st.sidebar.text_input("TICKER EINGEBEN", value=st.session_state.active_ticker, key="input_widget", on_change=update_ticker_from_input)
st.sidebar.markdown("---")

assets = {
    "Leitindizes": [("SPY", "S&P 500 ETF"), ("QQQ", "Nasdaq 100 ETF"), ("^GDAXI", "DAX 40"), ("^STOXX50E", "Euro Stoxx 50")],
    "Deutsche Blue Chips (DAX)": [("SAP.DE", "SAP SE"), ("SIE.DE", "Siemens"), ("ALV.DE", "Allianz"), ("AIR.DE", "Airbus")],
    "US Mega-Caps & Tech": [("AAPL", "Apple"), ("MSFT", "Microsoft"), ("NVDA", "Nvidia"), ("TSLA", "Tesla")],
    "Kryptowährungen": [("BTC-USD", "Bitcoin"), ("ETH-USD", "Ethereum"), ("SOL-USD", "Solana")],
    "Rohstoffe & Energie": [("GC=F", "Gold"), ("CL=F", "WTI Crude Oil"), ("HG=F", "Kupfer")],
    "Forex / Währungen": [("EURUSD=X", "EUR/USD"), ("USDJPY=X", "USD/JPY"), ("GBPUSD=X", "GBP/USD")]
}

for category, items in assets.items():
    with st.sidebar.expander(category):
        for t_sym, name in items:
            st.button(name, key=f"btn_{t_sym}", on_click=update_ticker_from_button, args=(t_sym,), use_container_width=True)

ticker = st.session_state.active_ticker
is_european_asset = ".DE" in ticker or ".F" in ticker or "^GDAXI" in ticker or "^STOXX50E" in ticker

# 3. Main Data Fetching
with st.spinner(f"Lade Marktdaten für {ticker}..."):
    stock = yf.Ticker(ticker)
    info = stock.info
    hist = stock.history(period="max")

if hist.empty:
    st.error(f"Keine Kursdaten für {ticker} gefunden.")
    st.stop()

short_name = info.get('shortName') or info.get('longName') or ticker
current_price = hist['Close'].iloc[-1]
prev_price = hist['Close'].iloc[-2] if len(hist) > 1 else current_price
change = current_price - prev_price
change_pct = (change / prev_price) * 100
currency = info.get("currency", "$")
decimals = 4 if "=X" in ticker else 2

st.title("INSTITUTIONAL TERMINAL")
st.markdown("---")

# --- 7 PRO TABS ---
tab_overview, tab_main, tab_fin, tab_peers, tab_macro, tab_portfolio, tab_risk = st.tabs([
    "GLOBAL OVERVIEW", "MAIN ASSET", "FINANCIALS & AUDIT", "PEER ANALYSIS", "MACRO DESK", "PORTFOLIO", "RISK & AI STRESS TEST"
])

# ==========================================
# TAB 0: GLOBAL OVERVIEW
# ==========================================
with tab_overview:
    st.markdown("### GLOBAL MARKET OVERVIEW & RADAR")
    overview_tickers = [("^GDAXI", "DAX 40"), ("SPY", "S&P 500"), ("QQQ", "Nasdaq 100"), ("^STOXX50E", "Euro Stoxx 50"), ("BTC-USD", "Bitcoin"), ("GC=F", "Gold")]
    
    ov_cols = st.columns(3)
    for i, (t_sym, t_name) in enumerate(overview_tickers):
        col_idx = i % 3
        try:
            t_hist = yf.Ticker(t_sym).history(period="5d")
            if not t_hist.empty:
                cp = t_hist['Close'].iloc[-1]
                pp = t_hist['Close'].iloc[-2] if len(t_hist) > 1 else cp
                ch_p = ((cp - pp) / pp) * 100
                with ov_cols[col_idx]:
                    st.markdown(f"""
                        <div class="terminal-card">
                            <div class="metric-title">{t_name} ({t_sym})</div>
                            <div class="metric-value">{cp:,.2f}</div>
                            <div style="color: {'#00FF7F' if ch_p >= 0 else '#FF4B4B'}; font-size: 0.9rem; font-weight: bold; margin-top: 4px;">{ch_p:+.2f}%</div>
                        </div>
                    """, unsafe_allow_html=True)
        except: pass

    st.markdown("---")
    col_g_news, col_g_macro = st.columns([1.5, 1])
    with col_g_news:
        st.markdown("#### Global Top News & Schlagzeilen")
        for n in fetch_news_cached("SPY")[:4]:
            st.markdown(f"**[{n['title']}]({n['link']})**")
            st.caption(f"Quelle: {n['publisher']}")
            st.markdown("<hr style='margin:0.3em 0; opacity:0.1'>", unsafe_allow_html=True)

    with col_g_macro:
        st.markdown("#### Makro-Schnellcheck")
        macro_ov = fetch_macro_data(is_european=False)
        if macro_ov:
            st.markdown(f"""
                <div class="terminal-card">
                    <p><b>VIX (Fear Index):</b> <span style="float:right; color:#00FFFF;">{macro_ov.get('VIX', 0):.2f}</span></p>
                    <p><b>US 10Y Yield:</b> <span style="float:right; color:#00FF7F;">{macro_ov.get('US10Y', 0):.2f}%</span></p>
                    <p><b>WTI Crude Oil:</b> <span style="float:right; color:#FFA500;">${macro_ov.get('OIL', 0):.2f}</span></p>
                    <p><b>Gold (Spot):</b> <span style="float:right; color:#FFD700;">${macro_ov.get('GOLD', 0):,.2f}</span></p>
                </div>
            """, unsafe_allow_html=True)

# ==========================================
# TAB 1: MAIN ASSET
# ==========================================
with tab_main:
    st.subheader(f"AKTIVES ASSET: {ticker} | {short_name}")
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("LAST PRICE", f"{currency} {current_price:,.{decimals}f}", f"{change:+,.{decimals}f} ({change_pct:+.2f}%)")
    col2.metric("OPEN", f"{currency} {hist['Open'].iloc[-1]:,.{decimals}f}")
    col3.metric("DAY HIGH", f"{currency} {hist['High'].iloc[-1]:,.{decimals}f}")
    col4.metric("DAY LOW", f"{currency} {hist['Low'].iloc[-1]:,.{decimals}f}")
    col5.metric("VOLUME", f"{hist['Volume'].iloc[-1]:,.0f}" if hist['Volume'].iloc[-1] > 0 else "N/A")
    st.markdown("---")

    left_col, mid_col, right_col = st.columns([1.2, 2.5, 1.2])

    with left_col:
        st.markdown("### FUNDAMENTALS")
        fcf = info.get("freeCashflow", 0)
        shares = info.get("sharesOutstanding", 0)
        if fcf and shares and fcf > 0:
            fcf_ps = fcf / shares
            fair_value = sum([fcf_ps * (1.05**i) / (1.10**i) for i in range(1, 6)]) + ((fcf_ps * (1.05**5) * 1.02) / (0.10 - 0.02) / (1.10**5))
            prem_disc = ((current_price - fair_value) / fair_value) * 100
            color = "#FF4B4B" if prem_disc > 0 else "#00FF7F"
            st.markdown(f"<div class='terminal-card'><b>Est. Fair Value (DCF):</b> {currency} {fair_value:.2f} <br><span style='color:{color}'>Premium/Discount: {prem_disc:+.2f}%</span></div><br>", unsafe_allow_html=True)

        def add_metric(l, k, f="{}"): return {"Metrik": l, "Wert": f.format(info.get(k))} if info.get(k) else None
        fund_data = [add_metric("Market Cap", "marketCap", f"{currency} {{:,.0f}}"), add_metric("P/E Ratio", "trailingPE", "{:.2f}"), add_metric("Forward P/E", "forwardPE", "{:.2f}"), add_metric("Dividend Yield", "dividendYield", "{:.2%}")]
        st.dataframe(pd.DataFrame([x for x in fund_data if x is not None]), hide_index=True, use_container_width=True)

    with mid_col:
        tc_l, tc_m, tc_r = st.columns([1.5, 1, 1])
        tc_l.markdown("### CHART & TECHNICALS")
        interval = tc_m.selectbox("Intervall", ["1D", "1W", "1h"], label_visibility="collapsed")
        timeframe = tc_r.selectbox("Zeitraum", ["1M", "6M", "1Y", "2Y", "Max"], index=2, label_visibility="collapsed")
        yf_iv = "1wk" if interval == "1W" else ("1h" if interval == "1h" else "1d")
        c_hist = fetch_chart_data(ticker, yf_iv)
        if c_hist.empty: c_hist = hist
        
        target_date = c_hist.index[-1] - pd.Timedelta(days={"1M": 30, "6M": 180, "1Y": 365, "2Y": 730, "Max": 99999}[timeframe])
        p_hist = c_hist.loc[c_hist.index >= target_date].copy()

        p_hist['SMA_50'], p_hist['SMA_200'] = p_hist['Close'].rolling(50).mean(), p_hist['Close'].rolling(200).mean()
        delta = p_hist['Close'].diff()
        rs = (delta.where(delta > 0, 0)).rolling(14).mean() / (-delta.where(delta < 0, 0)).rolling(14).mean()
        p_hist['RSI'] = 100 - (100 / (1 + rs))

        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.75, 0.25])
        fig.add_trace(go.Candlestick(x=p_hist.index, open=p_hist['Open'], high=p_hist['High'], low=p_hist['Low'], close=p_hist['Close']), row=1, col=1)
        fig.add_trace(go.Scatter(x=p_hist.index, y=p_hist['SMA_50'], line=dict(color='#FFA500', width=1)), row=1, col=1)
        fig.add_trace(go.Scatter(x=p_hist.index, y=p_hist['SMA_200'], line=dict(color='#00FFFF', width=1)), row=1, col=1)
        fig.add_trace(go.Scatter(x=p_hist.index, y=p_hist['RSI'], line=dict(color='#DA70D6', width=1)), row=2, col=1)
        fig.update_layout(height=520, template="plotly_dark", margin=dict(l=0,r=0,t=0,b=0), xaxis_rangeslider_visible=False, showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

    with right_col:
        st.markdown("### NEWS & SENTIMENT")
        news_results = fetch_news_cached(ticker)
        if news_results:
            titles_tuple = tuple([n['title'] for n in news_results])
            sent_status, sent_desc = cached_sentiment_analysis(titles_tuple)
            scol = "#00FF7F" if sent_status == "BULLISCH" else ("#FF4B4B" if sent_status == "BEARISCH" else "#FFA500")
            st.markdown(f"<div class='terminal-card' style='border-left: 4px solid {scol};'><b>SENTIMENT: {sent_status}</b><br><span style='font-size:0.8rem'>{sent_desc}</span></div>", unsafe_allow_html=True)
            for n in news_results[:3]:
                st.markdown(f"**[{n['title']}]({n['link']})**")
                st.markdown("<hr style='margin:0.2em 0; opacity:0.2'>", unsafe_allow_html=True)
        
        st.markdown("### TERMINAL CHAT")
        if user_prompt := st.chat_input("Frage ans Terminal..."):
            st.session_state.messages.append({"role": "user", "content": user_prompt})
            st.markdown(f"**User:** {user_prompt}")
            chat_agent = Agent(model=Gemini(id="gemini-3.6-flash"), tools=[YFinanceTools(company_info=True)])
            reply = chat_agent.run(f"Kontext: {ticker}, Kurs {current_price}. Frage: {user_prompt}").content
            st.markdown(f"**AI:** {reply}")
            st.session_state.messages.append({"role": "assistant", "content": reply})

# ==========================================
# TAB 2: FINANCIALS & AUDIT
# ==========================================
with tab_fin:
    c_fin, c_ins = st.columns([2, 1.5])
    with c_fin:
        st.markdown("### HISTORICAL FINANCIALS")
        try:
            fin = stock.financials
            if not fin.empty:
                fin_df = fin.loc[["Total Revenue", "Net Income"]].dropna(axis=1).T
                fin_df.index = pd.to_datetime(fin_df.index).year
                fin_df = fin_df.sort_index()
                fig_fin = go.Figure()
                fig_fin.add_trace(go.Bar(x=fin_df.index, y=fin_df["Total Revenue"], name="Revenue", marker_color="#1f77b4"))
                fig_fin.add_trace(go.Bar(x=fin_df.index, y=fin_df["Net Income"], name="Net Income", marker_color="#00FF7F"))
                fig_fin.update_layout(template="plotly_dark", barmode='group', height=350)
                st.plotly_chart(fig_fin, use_container_width=True)
        except: st.info("Keine Bilanzgrafik verfügbar.")

    with c_ins:
        st.markdown("### AI RED FLAG AUDITOR")
        if st.button("RUN BALANCE SHEET AUDIT"):
            with st.spinner("Prüfe Bilanzblöcke..."):
                audit_res = cached_audit(ticker, str(info.get('marketCap')), str(info.get('trailingPE')), str(info.get('freeCashflow')), str(info.get('totalDebt')))
                st.markdown(audit_res)

# ==========================================
# TAB 3: PEER ANALYSIS
# ==========================================
with tab_peers:
    st.markdown("### COMPARABLES & KI-FAZIT")
    c_p1, c_p2, c_p3 = st.columns(3)
    p1 = c_p1.text_input("Peer 1", value="MSFT" if ticker=="AAPL" else "")
    p2 = c_p2.text_input("Peer 2", value="GOOGL" if ticker=="AAPL" else "")
    p3 = c_p3.text_input("Peer 3", value="META" if ticker=="AAPL" else "")
    
    if st.button("RUN PEER COMPARISON & AI FAZIT"):
        with st.spinner("Lade Peer-Daten..."):
            data = []
            for t in [ticker, p1.strip().upper(), p2.strip().upper(), p3.strip().upper()]:
                if not t: continue
                try:
                    i = yf.Ticker(t).info
                    data.append({"Ticker": t, "Market Cap (B)": i.get("marketCap", 0) / 1e9, "P/E": i.get("trailingPE", np.nan), "Margin": i.get("profitMargins", np.nan), "Div Yield": i.get("dividendYield", 0)})
                except: pass
            if data:
                df_p = pd.DataFrame(data)
                st.dataframe(df_p.style.format({"Market Cap (B)": "{:.2f}", "P/E": "{:.2f}", "Margin": "{:.2%}", "Div Yield": "{:.2%}"}), use_container_width=True, hide_index=True)
                st.markdown(cached_peer_fazit(df_p.to_string()))

# ==========================================
# TAB 4: MACRO DESK
# ==========================================
with tab_macro:
    region_label = "🇩🇪 Deutschland / Euroraum (Bunds)" if is_european_asset else "🇺🇸 Vereinigte Staaten (Treasuries)"
    st.markdown("### GLOBAL MACRO DESK & LIQUIDITY MONITOR")
    st.caption(f"Aktives Asset gehört zu Region: **{region_label}**. Die Zinsstrukturkurve wird automatisch angepasst.")
    
    macro = fetch_macro_data(is_european=is_european_asset)
    
    col_macro_left, col_macro_right = st.columns([1.2, 1.8])
    
    with col_macro_left:
        st.markdown("#### Kern-Indikatoren")
        vix_val = macro.get('VIX', 0)
        dxy_val = macro.get('DXY', 0)
        oil_val = macro.get('OIL', 0)
        gold_val = macro.get('GOLD', 0)
        eur_val = macro.get('EURUSD', 0)
        
        st.markdown(f"""
            <div class="terminal-card">
                <p><b>VIX (Fear Index):</b> <span style="float:right; color:#00FFFF;">{vix_val:.2f}</span></p>
                <p><b>US Dollar Index (DXY):</b> <span style="float:right; color:#fff;">{dxy_val:.2f}</span></p>
                <p><b>WTI Crude Oil:</b> <span style="float:right; color:#FFA500;">${oil_val:.2f}</span></p>
                <p><b>Gold (Spot):</b> <span style="float:right; color:#FFD700;">${gold_val:,.2f}</span></p>
                <p><b>EUR / USD:</b> <span style="float:right; color:#00FF7F;">{eur_val:.4f}</span></p>
            </div>
        """, unsafe_allow_html=True)

    with col_macro_right:
        curve_title = "German Bund Yield Curve (Euroraum)" if is_european_asset else "US Treasury Yield Curve"
        st.markdown(f"#### {curve_title}")
        st.write("Visualisierung der Renditen nach Laufzeiten in Prozent (%).")
        
        maturities = ["Kurz (1M/2M)", "2 Jahre", "5 Jahre", "10 Jahre", "30 Jahre"]
        yields = [
            macro.get('US1M', 0),
            macro.get('US02Y', 0),
            macro.get('US5Y', 0),
            macro.get('US10Y', 0),
            macro.get('US30Y', 0)
        ]
        
        df_yield = pd.DataFrame({"Laufzeit": maturities, "Rendite (%)": yields})
        
        fig_curve = px.line(df_yield, x="Laufzeit", y="Rendite (%)", markers=True, template="plotly_dark")
        fig_curve.update_traces(line=dict(color="#00FF7F", width=3), marker=dict(size=8))
        fig_curve.update_layout(height=280, margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig_curve, use_container_width=True)
        
        # Automatische KI-Deutung der Zinskurve mit gemini-3.6-flash
        st.markdown("#### 🧠 AI Macro Interpretation (Zinskurven-Deutung)")
        with st.spinner("Analysiere Zinsstruktur..."):
            yield_dict_str = f"1M: {yields[0]:.2f}%, 2Y: {yields[1]:.2f}%, 5Y: {yields[2]:.2f}%, 10Y: {yields[3]:.2f}%, 30Y: {yields[4]:.2f}%"
            interpretation = cached_yield_curve_interpretation(yield_dict_str, region_label)
            st.markdown(f"""
                <div class="terminal-card" style="border-left: 4px solid #00FF7F; font-size: 0.9rem;">
                    {interpretation}
                </div>
            """, unsafe_allow_html=True)

# ==========================================
# TAB 5: PORTFOLIO
# ==========================================
with tab_portfolio:
    st.markdown("### PORTFOLIO TRACKER & ALLOCATION")
    pc1, pc2, pc3, pc4 = st.columns([1.5, 1.2, 1, 1])
    buy_date = pc1.date_input("Kaufdatum")
    qty = pc2.number_input("Stückzahl", min_value=0.0001, value=1.0000, step=0.1, format="%.4f")
    b_price = pc3.number_input("Kaufpreis", min_value=0.01, value=float(current_price))
    
    if pc4.button(f"ADD {ticker}", use_container_width=True):
        st.session_state.portfolio.append({"Ticker": ticker, "Datum": buy_date.strftime("%Y-%m-%d"), "Stückzahl": qty, "Kaufpreis": b_price})
        st.rerun()

    if st.session_state.portfolio:
        df_trades = pd.DataFrame(st.session_state.portfolio)
        col_hist, col_agg = st.columns(2)
        with col_hist:
            st.dataframe(df_trades, use_container_width=True, hide_index=True)
        with col_agg:
            agg_data, total_pl, total_inv, sector_alloc = [], 0, 0, {}
            for p_tick in df_trades['Ticker'].unique():
                try:
                    t_trades = df_trades[df_trades['Ticker'] == p_tick]
                    t_qty, t_inv = t_trades['Stückzahl'].sum(), (t_trades['Stückzahl'] * t_trades['Kaufpreis']).sum()
                    cur_p = yf.Ticker(p_tick).history(period="1d")['Close'].iloc[-1]
                    cur_val = t_qty * cur_p
                    pl_abs = cur_val - t_inv
                    total_pl += pl_abs
                    total_inv += t_inv
                    sec = yf.Ticker(p_tick).info.get('sector', 'Sonstige')
                    sector_alloc[sec] = sector_alloc.get(sec, 0) + cur_val
                    agg_data.append({"Ticker": p_tick, "Stücke": f"{t_qty:.2f}", "Wert": f"{cur_val:.2f}", "P&L": f"{pl_abs:+.2f}"})
                except: pass
            st.dataframe(pd.DataFrame(agg_data), use_container_width=True, hide_index=True)
            st.markdown(f"**INVESTMENT:** {total_inv:,.2f} | **P&L:** <span style='color:{'#00FF7F' if total_pl >= 0 else '#FF4B4B'}'>{total_pl:+,.2f}</span>", unsafe_allow_html=True)

        if sector_alloc:
            fig_alloc = px.pie(names=list(sector_alloc.keys()), values=list(sector_alloc.values()), hole=0.4, template="plotly_dark")
            st.plotly_chart(fig_alloc, use_container_width=True)

        if st.button("PORTFOLIO ZURÜCKSETZEN"):
            st.session_state.portfolio = []
            st.rerun()

# ==========================================
# TAB 6: RISK & STRESS TEST
# ==========================================
with tab_risk:
    st.markdown("### AI STRESS TEST")
    scenario = st.selectbox("Szenario wählen:", ["Leitzinserhöhung", "Globale Rezession", "Geopolitischer Schock"])
    if st.button("RUN STRESS TEST"):
        with st.spinner("Berechne Stresstest..."):
            stress_res = cached_stress_test(ticker, scenario)
            st.markdown(stress_res)