"""Trade P&L, risk statistics, and CSV export helpers."""

import csv
import io
import math
from datetime import datetime, timezone
from statistics import fmean, median, stdev


def _timestamp(value: object) -> datetime | None:
    """Interpreta timestamp ISO 8601 e normaliza o resultado para UTC.

    Args:
        value: Valor que deve conter uma string ISO 8601.

    Returns:
        datetime | None: Instante timezone-aware em UTC ou ``None`` se inválido.

    Raises:
        Nenhuma exceção de parsing é propagada.

    Side Effects:
        Nenhum.
    """
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def calcular_pnl_realizado(trades: list[dict]) -> list[float | None]:
    """Calcula P&L realizado por trade usando lotes FIFO e taxas de venda.

    Args:
        trades: Operações em ordem cronológica, com tipo, quantidade BTC,
            valor BRL bruto, taxa BRL e timestamp ISO 8601.

    Returns:
        list[float | None]: Resultado alinhado à lista de entrada; compras e
        vendas sem lote suficiente têm ``None``. Vendas calculáveis retornam
        P&L líquido em BRL.

    Raises:
        Nenhuma exceção de conversão de campos é propagada; registros inválidos
        recebem ``None``.

    Side Effects:
        Nenhum; os lotes são mantidos apenas em estruturas locais.
    """
    lots: list[list[object]] = []
    realized: list[float | None] = []

    for trade in trades:
        tipo = str(trade.get("tipo", "")).upper()
        try:
            quantity = float(trade.get("quantidade_btc", 0))
            gross = float(trade.get("valor_brl", 0))
            fee = float(trade.get("taxa_brl", 0))
        except (TypeError, ValueError):
            realized.append(None)
            continue
        if not all(math.isfinite(value) for value in (quantity, gross, fee)):
            realized.append(None)
            continue

        if tipo == "COMPRA":
            if quantity > 0 and gross >= 0:
                lots.append(
                    [quantity, gross / quantity, _timestamp(trade.get("timestamp"))]
                )
            realized.append(None)
            continue

        if tipo != "VENDA" or quantity <= 0:
            realized.append(None)
            continue

        proceeds_per_btc = (gross - fee) / quantity
        remaining = quantity
        pnl = 0.0
        for lot in lots:
            lot_quantity = float(lot[0])
            if remaining <= 1e-12:
                break
            matched = min(remaining, lot_quantity)
            pnl += matched * (proceeds_per_btc - float(lot[1]))
            lot[0] = lot_quantity - matched
            remaining -= matched
        realized.append(pnl if remaining <= 1e-9 else None)
        lots = [lot for lot in lots if float(lot[0]) > 1e-12]

    return realized


def calcular_metricas_risco(
    trades: list[dict],
    equity_history: list[dict],
) -> dict[str, float | None]:
    """Calcula drawdown, Sharpe, resultados de trades e exposição.

    Args:
        trades: Histórico de operações com quantidades BTC, valores BRL,
            taxas e timestamps ISO 8601.
        equity_history: Amostras contendo NAV, patrimônio, saldo BTC, preço
            BRL/BTC e timestamp.

    Returns:
        dict[str, float | None]: Métricas ``max_drawdown_pct``,
        ``sharpe_ratio``, ``win_rate_pct``, ``profit_factor``,
        ``average_holding_hours`` e ``average_exposure_pct``. Métricas sem
        amostras suficientes são ``None``; profit factor pode ser infinito.

    Raises:
        Nenhuma exceção para campos ausentes ou valores não convertíveis é
        propagada; tais registros são ignorados nos cálculos aplicáveis.

    Side Effects:
        Nenhum; listas de entrada não são modificadas.
    """
    navs = [
        float(point["nav"])
        for point in equity_history
        if isinstance(point.get("nav"), (int, float))
        and math.isfinite(float(point["nav"]))
        and float(point["nav"]) > 0
    ]
    drawdown = 0.0
    peak = 0.0
    for nav in navs:
        peak = max(peak, nav)
        drawdown = max(drawdown, (peak - nav) / peak if peak else 0.0)

    daily_nav: dict[object, tuple[datetime, float]] = {}
    for point in equity_history:
        timestamp = _timestamp(point.get("timestamp"))
        try:
            nav = float(point["nav"])
        except (KeyError, TypeError, ValueError):
            continue
        if timestamp is not None and math.isfinite(nav) and nav > 0:
            daily_nav[timestamp.date()] = (timestamp, nav)
    ordered_daily = [
        item[1][1] for item in sorted(daily_nav.items(), key=lambda item: item[1][0])
    ]
    daily_returns = [
        current / previous - 1
        for previous, current in zip(ordered_daily, ordered_daily[1:], strict=False)
        if previous > 0
    ]
    sharpe = None
    if len(daily_returns) > 1:
        deviation = stdev(daily_returns)
        if deviation > 0:
            sharpe = fmean(daily_returns) / deviation * math.sqrt(365)

    pnl_by_trade = calcular_pnl_realizado(trades)
    sell_pnls = [
        pnl
        for trade, pnl in zip(trades, pnl_by_trade, strict=False)
        if str(trade.get("tipo", "")).upper() == "VENDA" and pnl is not None
    ]
    wins = [pnl for pnl in sell_pnls if pnl > 0]
    losses = [pnl for pnl in sell_pnls if pnl < 0]
    profit_factor = (
        sum(wins) / abs(sum(losses)) if losses else (math.inf if wins else None)
    )
    win_rate = len(wins) / len(sell_pnls) * 100 if sell_pnls else None

    exposure_points = []
    for point in equity_history:
        try:
            equity = float(point["equity"])
            btc_value = float(point["btc"]) * float(point["price"])
        except (KeyError, TypeError, ValueError):
            continue
        timestamp = _timestamp(point.get("timestamp"))
        if (
            timestamp is not None
            and math.isfinite(equity)
            and equity > 0
            and math.isfinite(btc_value)
        ):
            exposure_points.append(
                (timestamp, min(100.0, max(0.0, btc_value / equity * 100)))
            )
    exposure_points.sort(key=lambda item: item[0])
    exposure_durations = [
        max(0.0, (right[0] - left[0]).total_seconds())
        for left, right in zip(exposure_points, exposure_points[1:], strict=False)
    ]
    positive_durations = [duration for duration in exposure_durations if duration > 0]
    last_duration = median(positive_durations) if positive_durations else 1.0
    if exposure_points:
        weights = exposure_durations + [last_duration]
        total_weight = sum(weights)
        average_exposure = (
            sum(
                point[1] * weight
                for point, weight in zip(exposure_points, weights, strict=False)
            )
            / total_weight
            if total_weight > 0
            else fmean(point[1] for point in exposure_points)
        )
    else:
        average_exposure = None

    durations = []
    lots: list[list[object]] = []
    for trade in trades:
        try:
            quantity = float(trade.get("quantidade_btc", 0))
            gross = float(trade.get("valor_brl", 0))
        except (TypeError, ValueError):
            continue
        timestamp = _timestamp(trade.get("timestamp"))
        if timestamp is None or quantity <= 0 or not math.isfinite(gross):
            continue
        if str(trade.get("tipo", "")).upper() == "COMPRA":
            lots.append([quantity, timestamp])
        elif str(trade.get("tipo", "")).upper() == "VENDA":
            remaining = quantity
            for lot in lots:
                matched = min(remaining, float(lot[0]))
                if matched > 0:
                    held = (timestamp - lot[1]).total_seconds() / 3600
                    durations.append((held, matched))
                    lot[0] = float(lot[0]) - matched
                    remaining -= matched
                if remaining <= 1e-12:
                    break
            lots = [lot for lot in lots if float(lot[0]) > 1e-12]
    duration_hours = (
        sum(hours * quantity for hours, quantity in durations)
        / sum(quantity for _, quantity in durations)
        if durations
        else None
    )

    return {
        "max_drawdown_pct": drawdown * 100 if navs else None,
        "sharpe_ratio": sharpe,
        "win_rate_pct": win_rate,
        "profit_factor": profit_factor,
        "average_holding_hours": duration_hours,
        "average_exposure_pct": average_exposure,
    }


def gerar_csv_trades(trades: list[dict]) -> str:
    """Serializa trades e P&L realizado em CSV delimitado por ponto-e-vírgula.

    Args:
        trades: Operações em ordem cronológica, com os campos usados pelo
            histórico e cálculo FIFO.

    Returns:
        str: CSV UTF-8 textual (sem BOM), com cabeçalho e delimitador ``;``.

    Raises:
        Nenhuma exceção por campo ausente é propagada; campos opcionais ficam
        vazios.

    Side Effects:
        Nenhum; não cria arquivo. A gravação é responsabilidade do chamador.
    """
    pnl_by_trade = calcular_pnl_realizado(trades)
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";")
    writer.writerow(
        [
            "timestamp",
            "tipo",
            "motivo",
            "preco_brl",
            "quantidade_btc",
            "valor_brl",
            "taxa_brl",
            "pnl_realizado_brl",
        ]
    )
    for trade, pnl in zip(trades, pnl_by_trade, strict=False):
        writer.writerow(
            [
                trade.get("timestamp", ""),
                trade.get("tipo", ""),
                trade.get("motivo", ""),
                trade.get("preco_brl", ""),
                trade.get("quantidade_btc", ""),
                trade.get("valor_brl", ""),
                trade.get("taxa_brl", ""),
                "" if pnl is None else round(pnl, 8),
            ]
        )
    return output.getvalue()
