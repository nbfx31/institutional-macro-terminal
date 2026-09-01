#!/bin/bash

# Prüfen, ob der API-Key bereits gesetzt ist
if [ -z "$GOOGLE_API_KEY" ]; then
    echo "=================================================="
    echo " 🔑 Kein Google API-Key gefunden!"
    echo "=================================================="
    read -p "Bitte gib jetzt deinen Gemini API-Key ein und drücke Enter: " user_key
    
    if [ -n "$user_key" ]; then
        # Key direkt für diese Session setzen
        export GOOGLE_API_KEY="$user_key"
        
        # Key dauerhaft in die .bashrc schreiben, damit er für immer da ist
        echo "export GOOGLE_API_KEY=\"$user_key\"" >> ~/.bashrc
        echo "✅ API-Key erfolgreich gespeichert und hinterlegt!"
    else
        echo "❌ Kein Key eingegeben. Die KI-Funktionen werden eingeschränkt sein."
    fi
else
    echo "🔑 Google API-Key ist bereits aktiv."
fi

echo "=================================================="
echo " 🚀 Starte das Multi-Asset AI Terminal..."
echo "=================================================="

# Streamlit App starten
streamlit run app.py