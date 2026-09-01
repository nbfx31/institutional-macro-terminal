# 🏛️ Institutional Global Terminal & Macro Desk

> Ein professionelles, KI-gestütztes Finanz- und Makro-Terminal für institutionelle Trader und Analysten. Entwickelt mit Python, Streamlit, Plotly und Google Gemini (Gemini 3.6-Flash).

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Streamlit](https://img.shields.io/badge/Streamlit-App-ff4b4b)
![Gemini AI](https://img.shields.io/badge/Gemini-3.6--Flash-orange)
![License](https://img.shields.io/badge/License-MIT-green)

---

## 🚀 Key Features

* **Universal Asset Selector & Live Data:** Nahtlose Abfrage von US-Mega-Caps, DAX-Blue-Chips, Krypto, Rohstoffen und Forex via `yfinance`.
* **Institutionelles Makro Desk:** 
  * Live-Überwachung von VIX, DXY, Öl, Gold und EUR/USD.
  * **Länderspezifische Zinsstrukturkurven:** Automatische Erkennung (USA Treasuries vs. deutsche Bunds) inklusive interaktiver Plotly-Visualisierung.
  * **🧠 AI Macro Interpretation:** Vollautomatische, kontextuelle Deutung der Zinskurve durch Gemini 3.6-Flash zur Früherkennung von Rezessionssignalen.
* **Fundamental & Technical Analysis:** 
  * Interaktive Candlestick-Charts mit SMA (50/200) und RSI.
  * DCF-Fair-Value-Berechnung und historische Finanzdaten.
* **AI Red Flag Auditor & Peer Comparison:** Automatisierte Bilanzprüfung auf finanzielle Risiken und KI-gestütztes Peer-Ranking.
* **Portfolio & Risk Management:** Integrierter Portfolio-Tracker mit Asset-Allokation (Pie-Chart) und interaktivem **AI Stress Test** (Szenario-Simulation).

---

## 🛠️ Tech Stack

* **Frontend & UI:** Streamlit (Custom Dark-Mode CSS Terminal-Design)
* **Data Engine:** Yahoo Finance (`yfinance`), Pandas, NumPy
* **Visualization:** Plotly (Interactive Charts & Curves)
* **AI & LLM Integration:** Google Gemini via Phidata (`Gemini(id="gemini-3.6-flash")`)

---

## ⚙️ Installation & Setup

1. **Repository klonen:**
   ```bash
   git clone [https://github.com/DEIN-BENUTZERNAME/institutional-macro-terminal.git](https://github.com/DEIN-BENUTZERNAME/institutional-macro-terminal.git)
   cd institutional-macro-terminal
