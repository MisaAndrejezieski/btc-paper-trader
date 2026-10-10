"""
Busca o preço atual do Bitcoin.
Fonte primária: CoinGecko (preço em BRL direto, sem bloqueio no Brasil).
Fallback 1: Binance (USD, converte com dólar fixo).
Fallback 2: cache local em data/last_price.json.
Fallback 3: preço de referência hardcoded.
"""

import json
import os
from datetime import datetime, timezone

import requests

from config import CONFIG

# --- Fontes de dados ---
COINGECKO_URL = (
    "https://api.coingecko.com/api/v3/simple/price"
    "?ids=bitcoin&vs_currencies=brl,usd"
)
BINANCE_URL = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"

CACHE_FILE = os.path.join(CONFIG["cache_dir"], "last_price.json")

# Preço de referência final (10/10/2026)
PRECO_REFERENCIA_USD = 82600.0


# ---------------------------------------------------------------------------
# Fonte 1: CoinGecko (preferida)
# ---------------------------------------------------------------------------
def buscar_coingecko() -> float | None:
    """Retorna o preço do BTC em BRL via CoinGecko, ou None se falhar."""
    try:
        response = requests.get(COINGECKO_URL, timeout=10)
        response.raise_for_status()
        data = response.json()
        preco_brl = float(data["bitcoin"]["brl"])
        return preco_brl
    except (requests.RequestException, KeyError, ValueError) as e:
        print(f"[AVISO] CoinGecko falhou: {e}")
        return None


# ---------------------------------------------------------------------------
# Fonte 2: Binance (fallback)
# ---------------------------------------------------------------------------
def buscar_binance() -> float | None:
    """Retorna o preço do BTC em BRL via Binance (USD * câmbio fixo), ou None."""
    try:
        response = requests.get(BINANCE_URL, timeout=10)
        response.raise_for_status()
        data = response.json()
        preco_usd = float(data["price"])
        return preco_usd * CONFIG["usd_brl_fixo"]
    except (requests.RequestException, KeyError, ValueError) as e:
        print(f"[AVISO] Binance falhou: {e}")
        return None


# ---------------------------------------------------------------------------
# Fonte 3: cache local
# ---------------------------------------------------------------------------
def ler_cache() -> float | None:
    """Lê o último preço salvo em cache, ou None se não existir."""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return float(data["preco_brl"])
        except (json.JSONDecodeError, KeyError, ValueError):
            return None
    return None


def salvar_cache(preco_brl: float, fonte: str) -> None:
    """Salva o preço atual em cache local."""
    os.makedirs(CONFIG["cache_dir"], exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "preco_brl": preco_brl,
            "fonte": fonte,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Função principal: cascata de fallbacks
# ---------------------------------------------------------------------------
def buscar_preco_btc_brl() -> float:
    """
    Retorna o preço do BTC em BRL.
    Tenta CoinGecko, depois Binance, depois cache, depois referência.
    """
    # 1. CoinGecko
    preco = buscar_coingecko()
    if preco is not None:
        salvar_cache(preco, "coingecko")
        return preco

    # 2. Binance
    preco = buscar_binance()
    if preco is not None:
        salvar_cache(preco, "binance")
        return preco

    # 3. Cache
    preco = ler_cache()
    if preco is not None:
        print("[AVISO] Usando preço do cache local.")
        return preco

    # 4. Referência hardcoded
    print("[AVISO] Sem cache. Usando preço de referência.")
    return PRECO_REFERENCIA_USD * CONFIG["usd_brl_fixo"]


if __name__ == "__main__":
    # Teste rápido: `python fetcher.py`
    preco = buscar_preco_btc_brl()
    print(f"BTC agora: R$ {preco:,.2f}")