"""
Motor de simulação: aplica as ações da estratégia no estado.
Não decide nada — só executa o que a estratégia manda.
"""

from datetime import datetime, timezone

from config import CONFIG
from models import Estado, Trade


def executar_compra(estado: Estado, preco_brl: float, valor_brl: float, motivo: str) -> Trade:
    """Executa uma compra simulada e atualiza o estado."""
    if valor_brl > estado.capital_brl:
        valor_brl = estado.capital_brl  # não deixa estourar

    taxa = valor_brl * (CONFIG["taxa_operacao_pct"] / 100)
    valor_efetivo = valor_brl - taxa
    quantidade = valor_efetivo / preco_brl

    # Atualiza preço médio (média ponderada)
    novo_total_btc = estado.capital_btc + quantidade
    if novo_total_btc > 0:
        novo_preco_medio = (
            (estado.preco_medio_brl * estado.capital_btc) + (preco_brl * quantidade)
        ) / novo_total_btc
    else:
        novo_preco_medio = preco_brl

    estado.capital_brl -= valor_brl
    estado.capital_btc = novo_total_btc
    estado.preco_medio_brl = novo_preco_medio
    estado.ultima_compra_ts = datetime.now(timezone.utc).isoformat()
    if motivo == "aporte":
        estado.ultimo_aporte_ts = estado.ultima_compra_ts
    estado.total_compras += 1
    estado.total_taxas_brl += taxa

    return Trade(
        timestamp=estado.ultima_compra_ts,
        tipo="COMPRA",
        preco_brl=preco_brl,
        quantidade_btc=quantidade,
        valor_brl=valor_brl,
        taxa_brl=taxa,
        motivo=motivo,
    )


def executar_venda(estado: Estado, preco_brl: float, percentual: float, motivo: str) -> Trade:
    """Executa uma venda simulada e atualiza o estado."""
    quantidade = estado.capital_btc * (percentual / 100)
    if quantidade <= 0:
        return None

    valor_bruto = quantidade * preco_brl
    taxa = valor_bruto * (CONFIG["taxa_operacao_pct"] / 100)
    valor_liquido = valor_bruto - taxa

    estado.capital_brl += valor_liquido
    estado.capital_btc -= quantidade
    estado.total_vendas += 1
    estado.total_taxas_brl += taxa

    if estado.capital_btc <= 1e-12:  # zera se for residual
        estado.capital_btc = 0.0
        estado.preco_medio_brl = 0.0

    return Trade(
        timestamp=datetime.now(timezone.utc).isoformat(),
        tipo="VENDA",
        preco_brl=preco_brl,
        quantidade_btc=quantidade,
        valor_brl=valor_bruto,
        taxa_brl=taxa,
        motivo=motivo,
    )