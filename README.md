# BTC Paper Trader

Simulador de paper trading de Bitcoin em Python. 
Acompanha o preço real do BTC e simula uma estratégia de 
DCA + take-profit parcial + stop-loss, com capital fictício.

## Estratégia

- **DCA:** aporte fixo a cada 24h
- **Compra extra:** dobra o aporte se o preço cair 5%
- **Take-profit:** vende 30% se o preço subir 8% sobre o preço médio
- **Stop-loss:** vende 50% se o preço cair 25% sobre o preço médio
- **Nunca vende tudo:** sempre fica uma parte exposta

## Setup

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt