"""
Busca o preço atual do Bitcoin.
Fonte primária: CoinGecko (preço em BRL direto, sem bloqueio no Brasil).
Fallback 1: Binance (par BTC/BRL).
Fallback 2: cache local em data/last_price.json.
"""

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

from config import CONFIG

# --- Fontes de dados ---
COINGECKO_URL = (
    "https://api.coingecko.com/api/v3/simple/price"
    "?ids=bitcoin&vs_currencies=brl,usd"
)
BINANCE_URL = "https://api.binance.com/api/v3/ticker/price?symbol=BTCBRL"

CACHE_FILE = os.path.join(CONFIG["cache_dir"], "last_price.json")


@dataclass(frozen=True)
class Cotacao:
    preco_brl: float
    fonte: str
    timestamp: datetime
    atual: bool


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
        if not math.isfinite(preco_brl) or preco_brl <= 0:
            raise ValueError("CoinGecko retornou um preço inválido.")
        return preco_brl
    except (requests.RequestException, KeyError, TypeError, ValueError) as e:
        print(f"[AVISO] CoinGecko falhou: {e}")
        return None


# ---------------------------------------------------------------------------
# Fonte 2: Binance (fallback)
# ---------------------------------------------------------------------------
def buscar_binance() -> float | None:
    """Retorna o preço do BTC em BRL diretamente do par BTCBRL da Binance."""
    try:
        response = requests.get(BINANCE_URL, timeout=10)
        response.raise_for_status()
        data = response.json()
        preco_brl = float(data["price"])
        if not math.isfinite(preco_brl) or preco_brl <= 0:
            raise ValueError("Binance retornou um preço inválido.")
        return preco_brl
    except (requests.RequestException, KeyError, TypeError, ValueError) as e:
        print(f"[AVISO] Binance falhou: {e}")
        return None


# ---------------------------------------------------------------------------
# Fonte 3: cache local
# ---------------------------------------------------------------------------
def ler_cache() -> Cotacao | None:
    """Lê o último preço salvo em cache, identificando-o como desatualizado."""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                timestamp_text = data.get("timestamp")
                timestamp = (
                    datetime.fromisoformat(timestamp_text)
                    if timestamp_text
                    else datetime.fromtimestamp(os.path.getmtime(CACHE_FILE), timezone.utc)
                )
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                preco_brl = float(data["preco_brl"])
                if not math.isfinite(preco_brl) or preco_brl <= 0:
                    return None
                return Cotacao(
                    preco_brl=preco_brl,
                    fonte=f"cache ({data.get('fonte', 'desconhecida')})",
                    timestamp=timestamp,
                    atual=False,
                )
        except (json.JSONDecodeError, KeyError, OSError, TypeError, ValueError):
            return None
    return None


def salvar_cache(preco_brl: float, fonte: str, timestamp: datetime) -> None:
    """Salva o preço atual em cache local."""
    os.makedirs(CONFIG["cache_dir"], exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "preco_brl": preco_brl,
            "fonte": fonte,
            "timestamp": timestamp.isoformat(),
        }, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Função principal: cascata de fallbacks
# ---------------------------------------------------------------------------
def buscar_cotacao_btc_brl() -> Cotacao:
    """
    Retorna preço, fonte, horário e se a cotação é atual.
    A cotação em cache é somente informativa e não deve disparar operações.
    """
    # 1. CoinGecko
    preco = buscar_coingecko()
    if preco is not None:
        agora = datetime.now(timezone.utc)
        salvar_cache(preco, "CoinGecko", agora)
        return Cotacao(preco, "CoinGecko", agora, True)

    # 2. Binance
    preco = buscar_binance()
    if preco is not None:
        agora = datetime.now(timezone.utc)
        salvar_cache(preco, "Binance BTC/BRL", agora)
        return Cotacao(preco, "Binance BTC/BRL", agora, True)

    # 3. Cache
    cotacao = ler_cache()
    if cotacao is not None:
        print("[AVISO] Exibindo preço do cache; operações automáticas e manuais bloqueadas.")
        return cotacao

    raise RuntimeError("Não foi possível obter cotação atual nem encontrar preço em cache.")


def buscar_preco_btc_brl() -> float:
    """Compatibilidade com chamadas que precisam somente do valor em BRL."""
    return buscar_cotacao_btc_brl().preco_brl


if __name__ == "__main__":
    # Teste rápido: `python fetcher.py`
    cotacao = buscar_cotacao_btc_brl()
    status = "atual" if cotacao.atual else "CACHE DESATUALIZADO"
    preco_formatado = (
        f"{cotacao.preco_brl:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    )
    print(
        f"BTC {status} ({cotacao.fonte}, "
        f"{cotacao.timestamp.astimezone(ZoneInfo('America/Sao_Paulo')).strftime('%d/%m/%Y %H:%M:%S')}): "
        f"R$ {preco_formatado}"
    )