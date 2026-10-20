# BTC Paper Trader — Versão Android (Kotlin + Jetpack Compose)

Aplicativo Android nativo para simulação (*paper trading*) de Bitcoin, totalmente reescrito em **Kotlin** com **Jetpack Compose**, **Room Database** e **Retrofit**.

---

## 🎨 Estética Cyberpunk

O aplicativo preserva 100% da identidade visual original do projeto Python (`theme.py`):
- **Fundo Dark / Terminal Matrix:** `#0A0E0A`, `#121A12`, `#1A241A`
- **Verde Matrix & Neon:** `#00FF41`, `#39FF14`
- **Pastéis de Acento:** `#A8E6A3`, `#F5B8D0`, `#A8C8F0`, `#F5E6A3`
- **Tipografia:** Fonte Monospaced (Terminal / JetBrains Mono)

---

## 🏗️ Arquitetura do App (MVVM + Clean Architecture)

```
com.misa.btcpapertrader
│
├── 📁 data
│   ├── 📁 local (Room Database - SQLite)
│   │   ├── AppDatabase.kt
│   │   ├── 📁 entity (TradeEntity, CashFlowEntity, AppStateEntity)
│   │   └── 📁 dao (TradeDao, CashFlowDao, AppStateDao)
│   ├── 📁 remote (Retrofit HTTP API)
│   │   ├── ApiService.kt (CoinGecko & Binance)
│   │   └── 📁 model (ApiResponses.kt)
│   └── 📁 repository
│       ├── PriceRepository.kt (Cascata: CoinGecko -> Binance -> Cache)
│       └── TradeRepository.kt (Orquestrador de persistência e simulação)
│
├── 📁 domain
│   ├── 📁 model (TradeModel, CashFlowModel, EstadoModel, CotacaoModel)
│   ├── 📁 usecase
│   │   └── TradingEngine.kt (Tradução 1:1 de strategy.py e simulator.py)
│   └── 📁 util
│       ├── Formatter.kt (Formatação BRL, BTC e datas)
│       └── TradingAnalytics.kt (Cálculo FIFO de P&L, Win Rate, Profit Factor)
│
├── 📁 ui
│   ├── 📁 theme (Color.kt, Type.kt, Theme.kt)
│   ├── 📁 components (CyberpunkCards.kt)
│   ├── 📁 screens (MainScreen.kt)
│   └── 📁 viewmodel (MainViewModel.kt)
│
└── MainActivity.kt
```

---

## 🔄 Mapeamento do Código Python $\rightarrow$ Kotlin

| Módulo Python Original | Implementação Kotlin Android |
| :--- | :--- |
| **`models.py`** | `TradeEntity`, `CashFlowEntity`, `AppStateEntity` + Domain Models |
| **`strategy.py`** | `TradingEngine.decidirAcao` (Função pura) |
| **`simulator.py`** | `TradingEngine.executarCompra`, `executarVenda`, `registrarDeposito` |
| **`fetcher.py`** | `PriceRepository.buscarCotacao` (CoinGecko $\rightarrow$ Binance $\rightarrow$ Cache) |
| **`trading_analytics.py`**| `TradingAnalytics.calcularPnlRealizadoFifo` e `calcularMetricas` |
| **`formatting.py`** | `Formatter.formatarReais`, `formatarBtc`, `formatarMoeda` |
| **`theme.py`** | `CyberpunkColors` em `Color.kt`, `Type.kt` e `Theme.kt` |
| **`gui.py`** | `MainScreen.kt` + `CyberpunkCards.kt` em **Jetpack Compose** |
| **`data/*.json`** | **Room SQLite Database** (`btc_paper_trader.db`) |

---

## 🚀 Como Abrir e Rodar no Android Studio

1. **Abrir o Projeto:**
   - Abra o **Android Studio**.
   - Selecione **Open** e navegue até a pasta: `E:\btc-paper-trader\android`.
   - Aguarde o Gradle sincronizar as dependências automaticamente.

2. **Executar no Celular Físico:**
   - Ative as **Opções do Desenvolvedor** e a **Depuração USB** no seu smartphone Android.
   - Conecte o dispositivo via cabo USB ao computador.
   - Selecione seu celular na lista de dispositivos do Android Studio e clique em **Run (Shift + F10)**.

---

## 🛡️ Destaques de Engenharia & Persistência

1. **Substituição dos JSONs por Room SQLite:**
   - Transações atômicas garantidas pelo bancoSQLite local. Se o celular reiniciar ou fechar o app, nada é corrompido ou perdido.
2. **Cascata de Preços Resiliente:**
   - Tenta buscar cotação spot em tempo real na CoinGecko em BRL.
   - Se falhar, tenta o par BTC/BRL da Binance.
   - Se ambos falharem, recupera o último preço do cache em memória e bloqueia operações até que a conexão retorne.
3. **Estratégia Automática:**
   - Reavalia a estratégia a cada atualização de preço em segundo plano.
   - Dispara compra extra em quedas de 2%, take-profit em altas de 3% e stop-loss de proteção em quedas de 10%.
