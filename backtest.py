"""Historical simulation using the configured paper-trading strategy."""

from datetime import datetime
from math import isfinite

from config import CONFIG
from models import Estado
from simulator import executar_compra, executar_venda
from strategy import decidir_acao


def simular_backtest(
    historico: list[tuple[datetime, float]],
    capital_inicial_brl: float,
) -> dict:
    """Simulate from a 50/50 BTC/cash start and compare with buy-and-hold."""
    if not isfinite(capital_inicial_brl) or capital_inicial_brl <= 0:
        raise ValueError("O patrimônio inicial do backtest precisa ser maior que zero.")
    ordered = sorted(
        (
            (timestamp, float(price))
            for timestamp, price in historico
            if isfinite(float(price)) and float(price) > 0
        ),
        key=lambda item: item[0],
    )
    if len(ordered) < 2:
        raise ValueError("O backtest precisa de ao menos duas cotações válidas.")

    first_timestamp, first_price = ordered[0]
    estado = Estado(capital_brl=capital_inicial_brl)
    taxa = CONFIG["taxa_operacao_pct"] / 100
    compra_inicial = capital_inicial_brl * 0.5
    executar_compra(estado, first_price, compra_inicial, "aporte")
    estado.ultima_compra_ts = first_timestamp.isoformat()
    estado.ultimo_preco_compra_brl = first_price

    buy_hold_btc = capital_inicial_brl * (1 - taxa) / first_price
    points = [{
        "timestamp": first_timestamp.isoformat(),
        "strategy": estado.capital_brl + estado.capital_btc * first_price,
        "buy_hold": buy_hold_btc * first_price,
    }]
    total_trades = 1

    for timestamp, price in ordered[1:]:
        action = decidir_acao(estado, price)
        if action["acao"] == "COMPRA":
            executar_compra(estado, price, action["valor_brl"], action["motivo"])
            estado.ultima_compra_ts = timestamp.isoformat()
            total_trades += 1
        elif action["acao"] == "VENDA":
            executar_venda(
                estado, price, action["percentual"], action["motivo"]
            )
            estado.ultima_compra_ts = timestamp.isoformat()
            total_trades += 1
        points.append({
            "timestamp": timestamp.isoformat(),
            "strategy": estado.capital_brl + estado.capital_btc * price,
            "buy_hold": buy_hold_btc * price,
        })

    final = points[-1]
    return {
        "points": points,
        "final_strategy_brl": final["strategy"],
        "final_buy_hold_brl": final["buy_hold"],
        "strategy_return_pct": (final["strategy"] / capital_inicial_brl - 1) * 100,
        "buy_hold_return_pct": (final["buy_hold"] / capital_inicial_brl - 1) * 100,
        "trade_count": total_trades,
        "initial_capital_brl": capital_inicial_brl,
    }
