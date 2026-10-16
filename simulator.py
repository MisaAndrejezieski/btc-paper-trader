"""
Motor de simulação: aplica as ações da estratégia no estado.
Não decide nada — só executa o que a estratégia manda.
"""

from datetime import datetime, timezone
from math import isfinite

from config import CONFIG
from models import CashFlow, Estado, Trade


def registrar_deposito(estado: Estado, valor_brl: float) -> CashFlow:
    """Adiciona um depósito fictício ao caixa sem registrar trade.

    Args:
        estado: Estado a atualizar in-place.
        valor_brl: Valor do depósito em reais; deve ser finito e positivo.

    Returns:
        CashFlow: Evento ``DEPOSITO`` com timestamp UTC.

    Raises:
        ValueError: Se o valor for não finito ou menor/igual a zero.

    Side Effects:
        Incrementa ``estado.capital_brl``. Não cobra taxa nem altera contadores
        de trades.
    """
    if not isfinite(valor_brl) or valor_brl <= 0:
        raise ValueError("O valor do depósito precisa ser maior que zero.")

    estado.capital_brl += valor_brl
    return CashFlow(
        timestamp=datetime.now(timezone.utc).isoformat(),
        tipo="DEPOSITO",
        valor_brl=valor_brl,
    )


def registrar_saque(estado: Estado, valor_brl: float) -> CashFlow:
    """Registra um saque em reais sem executar venda de BTC.

    Args:
        estado: Estado a atualizar in-place.
        valor_brl: Valor do saque em reais; deve ser finito, positivo e disponível.

    Returns:
        CashFlow: Evento ``SAQUE`` com timestamp UTC.

    Raises:
        ValueError: Se o valor não for positivo/finito ou exceder o caixa.

    Side Effects:
        Decrementa ``estado.capital_brl``. Não cobra taxa nem altera contadores
        de trades.
    """
    if not isfinite(valor_brl) or valor_brl <= 0:
        raise ValueError("O valor do saque precisa ser maior que zero.")
    if valor_brl > estado.capital_brl:
        raise ValueError("O valor do saque excede o saldo disponível em reais.")

    estado.capital_brl -= valor_brl
    return CashFlow(
        timestamp=datetime.now(timezone.utc).isoformat(),
        tipo="SAQUE",
        valor_brl=valor_brl,
    )


def executar_compra(
    estado: Estado, preco_brl: float, valor_brl: float, motivo: str
) -> Trade:
    """Executa uma compra simulada, incluindo taxa e preço médio ponderado.

    Args:
        estado: Estado a alterar in-place.
        preco_brl: Preço unitário do BTC em BRL/BTC.
        valor_brl: Valor bruto da compra em BRL; limitado ao caixa disponível.
        motivo: Código descritivo da origem da compra.

    Returns:
        Trade: Compra executada, com quantidade líquida de taxa e valores BRL.

    Raises:
        ValueError: Se preço ou valor forem não finitos/não positivos, ou não
            houver saldo disponível.

    Side Effects:
        Altera caixa, BTC, custo médio, timestamps, flags estratégicas,
        contador de compras e total de taxas. A persistência cabe ao chamador.
    """
    if not isfinite(preco_brl) or preco_brl <= 0:
        raise ValueError("O preço precisa ser maior que zero.")
    if not isfinite(valor_brl) or valor_brl <= 0:
        raise ValueError("O valor da compra precisa ser maior que zero.")
    if valor_brl > estado.capital_brl:
        valor_brl = estado.capital_brl  # não deixa estourar
    if valor_brl <= 0:
        raise ValueError("Não há saldo em reais para comprar.")

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
    estado.ultimo_preco_compra_brl = preco_brl
    estado.take_profit_executado = False
    estado.stop_loss_executado = False
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


def executar_venda(
    estado: Estado, preco_brl: float, percentual: float, motivo: str
) -> Trade | None:
    """Vende uma porcentagem da posição BTC atual.

    Args:
        estado: Estado a alterar in-place.
        preco_brl: Preço unitário do BTC em BRL/BTC.
        percentual: Fração percentual da posição, no intervalo ``(0, 100]``.
        motivo: Código descritivo da origem da venda.

    Returns:
        Trade | None: Trade produzido pela venda; ``None`` se não houver BTC.

    Raises:
        ValueError: Se o percentual for inválido ou os dados da venda não forem válidos.

    Side Effects:
        Delega a alteração do estado a :func:`executar_venda_quantidade`.
    """
    if not isfinite(percentual) or percentual <= 0 or percentual > 100:
        raise ValueError("O percentual da venda precisa estar entre 0 e 100%.")
    quantidade = estado.capital_btc * (percentual / 100)
    if quantidade <= 0:
        return None
    return executar_venda_quantidade(estado, preco_brl, quantidade, motivo)


def executar_venda_quantidade(
    estado: Estado, preco_brl: float, quantidade: float, motivo: str
) -> Trade:
    """Vende uma quantidade exata de BTC, descontando taxa de operação.

    Args:
        estado: Estado a alterar in-place.
        preco_brl: Preço unitário do BTC em BRL/BTC.
        quantidade: Quantidade a vender em BTC.
        motivo: Código descritivo da origem da venda.

    Returns:
        Trade: Registro da venda com valor bruto e taxa em BRL.

    Raises:
        ValueError: Se preço/quantidade forem não finitos ou não positivos, ou
            se a quantidade exceder o saldo BTC.

    Side Effects:
        Atualiza caixa, saldo BTC, flags estratégicas, contadores e taxas.
        Remove resíduos de BTC abaixo do limite numérico; não persiste em disco.
    """
    if not isfinite(preco_brl) or preco_brl <= 0:
        raise ValueError("O preço precisa ser maior que zero.")
    if not isfinite(quantidade) or quantidade <= 0:
        raise ValueError("A quantidade de BTC precisa ser maior que zero.")
    if quantidade > estado.capital_btc:
        raise ValueError("A quantidade informada excede o saldo disponível em BTC.")

    valor_bruto = quantidade * preco_brl
    taxa = valor_bruto * (CONFIG["taxa_operacao_pct"] / 100)
    valor_liquido = valor_bruto - taxa

    estado.capital_brl += valor_liquido
    estado.capital_btc -= quantidade
    if motivo == "take_profit":
        estado.take_profit_executado = True
    elif motivo == "stop_loss":
        estado.stop_loss_executado = True
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
