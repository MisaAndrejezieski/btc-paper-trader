"""
Busca o preço atual do Bitcoin na Binance (API pública, sem autenticação).
Retorna o preço em BRL usando conversão via USD.
"""

import json
import os
import time
from datetime import datetime, timezone

import requests

from config import CONFIG

BINANCE_URL = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
CACHE_FILE = os.path.join(CONFIG["cache_dir"], "last_price.json")
CACHE_MAX_IDADE_SEGUNDOS = 60  # cache válido por 1 minuto


def buscar_preco_btc_usd() -> float:
    """Busca o preço do BTC em USD na Binance."""
    try:
        response = requests.get(BINANCE_URL, timeout=10)
        response.raise_for_status()
        data = response.json()
        return float(data["price"])
    except (requests.RequestException, KeyError, ValueError) as e:
        print(f"[AVISO] Falha ao buscar preço na Binance: {e}")
        return _ler_cache()


def _ler_cache() -> float:
    """Fallback: lê o último preço salvo em cache."""
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return float(data["preco_usd"])
    # Fallback final: preço de referência do dia 10/10/2026
    print("[AVISO] Sem cache. Usando preço de referência: $82.600")
    return 82600.0


def _salvar_cache(preco_usd: float) -> None:
    """Salva o preço atual em cache local."""
    os.makedirs(CONFIG["cache_dir"], exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "preco_usd": preco_usd,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }, f, indent=2)


def buscar_preco_btc_brl() -> float:
    """
    Retorna o preço do BTC em BRL.
    Estratégia: pega em USD e converte com dólar fixo (config).
    Se quiser usar cotação real do dólar depois, troca o CONFIG["usd_brl_fixo"].
    """
    preco_usd = buscar_preco_btc_usd()
    _salvar_cache(preco_usd)
    return preco_usd * CONFIG["usd_brl_fixo"]


if __name__ == "__main__":
    # Teste rápido: rode `python fetcher.py` para ver o preço atual
    preco = buscar_preco_btc_brl()
    print(f"BTC agora: R$ {preco:,.2f}")