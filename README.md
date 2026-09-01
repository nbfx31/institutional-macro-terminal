# Institutional Terminal Pro

Ein Analyse-Terminal für Aktien, ETFs, Währungen, Rohstoffe und Krypto — gebaut mit Python und Streamlit, betrieben ausschließlich mit frei verfügbaren Datenquellen. Dreizehn Sektionen verdichten Bewertung, Technik, Derivatemarkt, Positionierung, Makro und Portfoliotheorie zu einem Gesamtbild.

> Keine Anlageberatung. Alle Analysen dienen der Information; Kursdaten können verzögert sein.

---

## Highlights

**Command Center** — 16+ Signale aus allen Fachbereichen (Trend, Momentum, Bewertung, Piotroski, Altman, Regime, Makro, Insider, Optionsmarkt, COT) als farbcodierte Matrix mit regelbasiertem Gesamtbild und optionaler KI-Gesamtanalyse inkl. Falsifizierungskriterium.

**Asset-Class Desk** — erkennt die Anlageklasse und wechselt das Instrumentarium:
- *FX*: Zinsdifferenzen US–Euroraum (live), Carry, Aktivitätsprofil nach Handelssession
- *Gold/Silber*: Realzins-Kompass (FRED-TIPS invertiert vs. Goldpreis), Gold/Silber-Ratio mit Perzentil, Gold in EUR
- *Rohstoffe*: Kupfer/Gold-Konjunktursignal, Sektor-Bestätigung, Dollar-Gegenwind
- *Krypto*: Halving-Zyklen-Vergleich (log-indexiert), 200-Wochen-Linie, Nasdaq-Korrelation, Dominanz-Proxy
- Für alle Nicht-Aktien: **CFTC-COT-Positionierung** der Large Speculators mit 3-Jahres-Perzentil

**Quant & Scores** — Faktor-Radar (Value/Qualität/Momentum/Stabilität), Piotroski F-Score mit Checkliste, Altman Z-Score, Markt-Regime-Analyse, Saisonalitäts-Heatmap, Renditeverteilung mit Schiefe/Kurtosis/CVaR.

**Bewertung** — interaktiver DCF mit Sensitivitäts-Heatmap und **Reverse DCF** (welches Wachstum preist der Kurs ein?).

**Options-Desk** — ATM-IV vs. realisierte Volatilität, eingepreiste Bewegung, Put/Call-Ratio, Max Pain, Open-Interest-Profil, Volatility Smile, IV-Term-Structure mit Event-Erkennung.

**Smart Money** — Insider-Transaktionen, Analysten-Up-/Downgrades, Earnings-Track-Record (Beat-Quote und Kursreaktion T+1).

**Strategy Lab** — Backtester ohne Look-Ahead-Bias (Buy & Hold, SMA-Crossover, RSI-Mean-Reversion, mit Transaktionskosten), Pairs-Monitor mit Z-Score, Sparplan-Simulator mit deutschem Netto-Steuerrechner (Abgeltungsteuer, Soli, Kirchensteuer, Teilfreistellung).

**Portfolio** — SQLite-persistenter Tracker, Pro-forma-Risikoanalyse, Korrelationsmatrix, Efficient Frontier (Markowitz) mit Max-Sharpe-Gewichten, Vier-Faktor-Modell (Markt/Zinsen/Dollar/Öl) mit Szenariorechner, Rebalancing-Assistent, Diversifikator-Finder.

**Risk & Stress Test** — Volatilität, Beta, Sharpe/Sortino, Max Drawdown, VaR/CVaR, Drawdown-Episoden, CAPM-Alpha mit rollierendem Beta, Krisen-Playbook (2008, Corona, Zinsschock 2022, Bankenkrise 2023), Monte-Carlo-Simulation, Position-Sizing (Stop-basiert, Vol-Targeting, Kelly, ATR-Chandelier), KI-Stresstest.

**Macro Desk** — echte Zinskurven (US via CBOE-Indizes, Euroraum via EZB Data API), Fisher-Zerlegung (Realzins + Breakeven via FRED), Risk-On/Off-Barometer, Sektor-Rotation, Cross-Asset-Momentum.

**Komfort** — Symbolsuche per Firmenname, Watchlist mit Signal-Scanner, persistente Kursalarme, Live-Modus (60-s-Auto-Refresh), teilbare Links (`?t=SAP.DE`), Markdown-Report-Export, Kennzahlen-Glossar, KI-Tagesbriefing.

---

## Installation

Voraussetzung: Python ≥ 3.10

```bash
git clone <dein-repository>
cd <dein-repository>
pip install -r requirements.txt
streamlit run terminal_pro.py
```

Die App läuft anschließend unter `http://localhost:8501`. Portfolio, Watchlist und Alarme werden lokal in `terminal_data.db` (SQLite) gespeichert und überleben Neustarts.

### KI-Features (optional)

Sentiment, Bilanz-Audit, Peer-Fazit, Investment-Memo, Stresstest, Tagesbriefing und der Terminal-Chat nutzen Google Gemini:

```bash
pip install agno google-genai
export GOOGLE_API_KEY="dein-key"        # Windows: setx GOOGLE_API_KEY "dein-key"
```

Ohne Key läuft das Terminal vollständig — die KI-Bereiche zeigen dann einen ehrlichen Hinweis statt Ergebnisse.

### Konfiguration über Umgebungsvariablen

| Variable | Zweck | Default |
|---|---|---|
| `GOOGLE_API_KEY` | Aktiviert alle KI-Features | – |
| `GEMINI_MODEL_ID` | Gemini-Modell | `gemini-3.6-flash` |

---

## Sicherheit: API-Key vor dem Veröffentlichen entfernen

**Wichtig vor dem ersten Push:** Wenn im Konstantenblock von `terminal_pro.py` ein API-Key als Default eingetragen ist (Zeile `GOOGLE_API_KEY = os.getenv(...)`), ersetze ihn durch

```python
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
```

und setze den Key ausschließlich als Umgebungsvariable. Ein bereits committeter Key gilt als kompromittiert — in dem Fall unter https://aistudio.google.com rotieren. Die beiliegende `.gitignore` hält `terminal_data.db` und lokale Umgebungsdateien aus dem Repository.

---

## Datenquellen

| Quelle | Daten | Zugang |
|---|---|---|
| Yahoo Finance (yfinance) | Kurse, Fundamentaldaten, Optionsketten, Insider, News | frei, ggf. verzögert |
| EZB Data API | AAA-Zinskurve Euroraum | frei, keyless |
| FRED (St. Louis Fed) | Realzins 10J (DFII10), Breakeven-Inflation (T10YIE) | frei, keyless |
| CFTC (Socrata API) | Commitment-of-Traders-Reports, wöchentlich | frei, keyless |
| Google Gemini | Alle KI-Analysen | API-Key erforderlich |

Fällt eine Quelle aus, zeigt die betroffene Sektion eine klare Fehlermeldung — der Rest der App bleibt nutzbar (Fehler-Isolation je Sektion). Der Status aller Quellen ist in der Sidebar prüfbar.

---

## Architektur in Kürze

Eine Datei, bewusst: `terminal_pro.py` (~4.300 Zeilen) mit klarer Schichtung — Konfiguration, Persistenz (SQLite), gecachter Daten-Layer, Berechnungs-Engine, KI-Layer, 13 isolierte Render-Sektionen mit Navigations-Dispatch. Es wird nur die aktive Sektion pro Rerun ausgeführt; schwere Berechnungen (Monte-Carlo, Markowitz, Backtests) sind zusätzlich gecacht. Grundprinzip: **keine erfundenen Daten** — jede Zahl ist live geladen oder klar als nicht verfügbar gekennzeichnet, fehlgeschlagene KI-Aufrufe liefern eine ehrliche Meldung statt Platzhalter-Analysen.

## Grenzen

Kursdaten sind teils börsenverzögert; Optionsketten, Insider- und Rating-Daten sind bei US-Werten am vollständigsten. Scores und Signale sind transparente Heuristiken, kein Ranking gegen ein Aktienuniversum und kein Prognosemodell. Der Steuerrechner bildet Vorabpauschale und Verlustverrechnung nicht ab. Backtests rechnen ohne Steuern und Slippage.

## Lizenz

Privates Lern- und Analyseprojekt. Vor einer Veröffentlichung Lizenz ergänzen (z. B. MIT) und die Nutzungsbedingungen der Datenanbieter beachten — insbesondere erlaubt Yahoo Finance keine kommerzielle Weiterverbreitung der Daten.