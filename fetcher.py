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
    "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=brl,usd"
)
BINANCE_URL = "https://api.binance.com/api/v3/ticker/price?symbol=BTCBRL"

CACHE_FILE = os.path.join(CONFIG["cache_dir"], "last_price.json")


@dataclass(frozen=True)
class Cotacao:
    """Cotação de BTC em reais e metadados de origem/atualidade.

    Attributes:
        preco_brl: Preço do BTC em BRL/BTC.
        fonte: Nome da fonte online ou indicação do cache.
        timestamp: Instante da cotação como datetime com fuso.
        atual: ``True`` somente para resposta obtida online nesta consulta.
    """

    preco_brl: float
    fonte: str
    timestamp: datetime
    atual: bool


# ---------------------------------------------------------------------------
# Fonte 1: CoinGecko (preferida)
# ---------------------------------------------------------------------------
def buscar_coingecko() -> float | None:
    """Obtém o preço spot BTC/BRL na CoinGecko.

    Returns:
        float | None: Preço positivo em BRL/BTC ou ``None`` se a consulta,
        resposta ou valor retornado for inválido.

    Raises:
        Nenhuma exceção de rede ou parsing é propagada; falhas previstas são
        reportadas no console e convertidas em ``None``.

    Side Effects:
        Faz uma requisição HTTP com timeout de 10 segundos e pode imprimir aviso.
    """
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
    """Obtém o preço spot do par BTC/BRL na Binance.

    Returns:
        float | None: Preço positivo em BRL/BTC ou ``None`` em falha prevista.

    Raises:
        Nenhuma exceção de rede ou parsing é propagada; falhas previstas são
        reportadas no console e convertidas em ``None``.

    Side Effects:
        Faz uma requisição HTTP com timeout de 10 segundos e pode imprimir aviso.
    """
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
    """Lê o cache local e marca a cotação como desatualizada.

    Returns:
        Cotacao | None: Valor válido do cache com timestamp salvo (ou mtime),
        ``atual=False``; retorna ``None`` se ausente ou inválido.

    Raises:
        Nenhuma exceção de leitura ou parsing do JSON é propagada.

    Side Effects:
        Lê ``data/last_price.json``; não altera arquivos.
    """
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                data = json.load(f)
                timestamp_text = data.get("timestamp")
                timestamp = (
                    datetime.fromisoformat(timestamp_text)
                    if timestamp_text
                    else datetime.fromtimestamp(
                        os.path.getmtime(CACHE_FILE), timezone.utc
                    )
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
    """Grava a cotação online mais recente no cache JSON.

    Args:
        preco_brl: Preço em BRL/BTC.
        fonte: Nome da fonte que forneceu o preço.
        timestamp: Instante da cotação, preferencialmente timezone-aware.

    Returns:
        None.

    Raises:
        OSError: Se não for possível criar o diretório ou escrever o arquivo.
        TypeError: Se algum valor não puder ser serializado em JSON.

    Side Effects:
        Cria o diretório de cache se necessário e sobrescreve ``last_price.json``.
    """
    os.makedirs(CONFIG["cache_dir"], exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "preco_brl": preco_brl,
                "fonte": fonte,
                "timestamp": timestamp.isoformat(),
            },
            f,
            indent=2,
            ensure_ascii=False,
        )


# ---------------------------------------------------------------------------
# Função principal: cascata de fallbacks
# ---------------------------------------------------------------------------
def buscar_cotacao_btc_brl() -> Cotacao:
    """
    Busca cotação em CoinGecko, Binance e, por último, no cache local.

    Returns:
        Cotacao: Preço, origem, timestamp UTC e indicador de atualidade.

    Raises:
        RuntimeError: Se as duas fontes online falharem e não houver cache válido.
        OSError: Se a gravação do cache de uma cotação online falhar.
        TypeError: Se dados válidos para cotação não puderem ser serializados.

    Side Effects:
        Faz até duas requisições HTTP; uma cotação online atualiza
        ``data/last_price.json``. Uma cotação do cache é apenas informativa e
        gera aviso no console.
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
        print(
            "[AVISO] Exibindo preço do cache; operações automáticas "
            "e manuais bloqueadas."
        )
        return cotacao

    raise RuntimeError(
        "Não foi possível obter cotação atual nem encontrar preço em cache."
    )


def buscar_preco_btc_brl() -> float:
    """Obtém somente o preço BTC/BRL, preservando a API legada.

    Returns:
        float: Preço em BRL/BTC.

    Raises:
        RuntimeError: Se nenhuma fonte online nem cache válido estiver disponível.
        OSError: Se a gravação do cache de preço online falhar.

    Side Effects:
        Herda as requisições e possíveis atualizações de cache de
        :func:`buscar_cotacao_btc_brl`.
    """
    return buscar_cotacao_btc_brl().preco_brl


if __name__ == "__main__":
    # Teste rápido: `python fetcher.py`
    cotacao = buscar_cotacao_btc_brl()
    status = "atual" if cotacao.atual else "CACHE DESATUALIZADO"
    preco_formatado = (
        f"{cotacao.preco_brl:,.2f}".replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )
    print(
        f"BTC {status} ({cotacao.fonte}, "
        f"{cotacao.timestamp.astimezone(ZoneInfo('America/Sao_Paulo')).strftime('%d/%m/%Y %H:%M:%S')}"
        f"): "
        f"R$ {preco_formatado}"
    )
