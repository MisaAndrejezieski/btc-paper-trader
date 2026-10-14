"""
A estratégia em si: decide se deve comprar, vender ou não fazer nada.
Função pura: recebe estado + preço, devolve a ação.
Isso facilita testes e troca de estratégia depois.
"""

from config import CONFIG
from models import Estado


def decidir_acao(estado: Estado, preco_brl: float) -> dict:
    """
    Retorna um dict com a ação a executar:
      {"acao": "COMPRA", "valor_brl": 300.0, "motivo": "compra_extra"}
      {"acao": "VENDA", "percentual": 30, "motivo": "take_profit"}
      {"acao": "VENDA", "percentual": 50, "motivo": "stop_loss"}
      {"acao": "NADA"}
    """
    variacao = (
        (preco_brl - estado.preco_medio_brl) / estado.preco_medio_brl
        if estado.preco_medio_brl > 0 else 0.0
    )

    # Evita repetir a proteção ou recomprar enquanto a queda que a disparou persiste.
    if (
        CONFIG["stop_loss_ativo"]
        and estado.stop_loss_executado
        and variacao <= -(CONFIG["stop_loss_pct"] / 100)
    ):
        return {"acao": "NADA"}

    # --- Verifica stop-loss PRIMEIRO (proteção é prioridade) ---
    if (
        CONFIG["stop_loss_ativo"]
        and not estado.stop_loss_executado
        and estado.preco_medio_brl > 0
    ):
        if variacao <= -(CONFIG["stop_loss_pct"] / 100):
            return {
                "acao": "VENDA",
                "percentual": CONFIG["percentual_venda_stop_loss"],
                "motivo": "stop_loss",
            }

    # --- Verifica take-profit ---
    if (
        estado.preco_medio_brl > 0
        and estado.capital_btc > 0
        and not estado.take_profit_executado
    ):
        if variacao >= (CONFIG["take_profit_pct"] / 100):
            return {
                "acao": "VENDA",
                "percentual": CONFIG["percentual_venda_take_profit"],
                "motivo": "take_profit",
            }

    # --- Verifica compra extra em queda forte ---
    if estado.ultima_compra_ts and estado.capital_brl > 0:
        ultima_compra_preco = (
            estado.ultimo_preco_compra_brl or estado.preco_medio_brl
        )
        if ultima_compra_preco > 0:
            variacao = (preco_brl - ultima_compra_preco) / ultima_compra_preco
            if variacao <= -(CONFIG["queda_para_compra_extra_pct"] / 100):
                valor = CONFIG["valor_compra_base_brl"] * CONFIG["multiplicador_compra_extra"]
                if estado.capital_brl >= valor:
                    return {
                        "acao": "COMPRA",
                        "valor_brl": valor,
                        "motivo": "compra_extra",
                    }

    return {"acao": "NADA"}