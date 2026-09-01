from phi.agent import Agent
from phi.model.google import Gemini
from phi.tools.yfinance import YFinanceTools

# 1. Unseren Finanz-Agenten konfigurieren
finance_agent = Agent(
    model=Gemini(id="gemini-3.6-flash"), # Wir nutzen Googles schnelle KI
    tools=[YFinanceTools(stock_price=True, company_info=True)], # Er darf auf echte Aktiendaten zugreifen
    description="Du bist ein erfahrener Finanz-Analyst.",
    instructions=["Nutze immer Tabellen, um Finanzdaten übersichtlich darzustellen. Antworte auf Deutsch."],
    show_tool_calls=True,
    markdown=True
)

# 2. Dem Agenten einen Auftrag geben
print("Startschuss! Der Agent denkt nach und holt sich die Daten...\n")
finance_agent.print_response(
    "Wie ist der aktuelle Aktienkurs von Apple (AAPL) und was ist das Kerngeschäft des Unternehmens?", 
    stream=True
)