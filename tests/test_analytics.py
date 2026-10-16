import math
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from backtest import simular_backtest
from main import MotorSimulador
from market_insights import buscar_historico_btc_brl_periodo
from models import Estado
from trading_analytics import (
    calcular_metricas_risco,
    calcular_pnl_realizado,
    gerar_csv_trades,
)


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.trades = [
            {
                "timestamp": self.start.isoformat(),
                "tipo": "COMPRA",
                "quantidade_btc": 1.0,
                "valor_brl": 100.0,
                "taxa_brl": 0.1,
                "motivo": "manual",
            },
            {
                "timestamp": (self.start + timedelta(hours=24)).isoformat(),
                "tipo": "VENDA",
                "quantidade_btc": 0.5,
                "valor_brl": 60.0,
                "taxa_brl": 0.06,
                "motivo": "take_profit",
            },
            {
                "timestamp": (self.start + timedelta(hours=48)).isoformat(),
                "tipo": "VENDA",
                "quantidade_btc": 0.5,
                "valor_brl": 40.0,
                "taxa_brl": 0.04,
                "motivo": "manual",
            },
        ]

    def test_fifo_realized_pnl_includes_transaction_fees(self):
        pnl = calcular_pnl_realizado(self.trades)
        self.assertIsNone(pnl[0])
        self.assertAlmostEqual(pnl[1], 9.94)
        self.assertAlmostEqual(pnl[2], -10.04)

    def test_risk_metrics_cover_drawdown_win_rate_profit_factor_and_holding(self):
        history = [
            {"timestamp": self.start.isoformat(), "nav": 1.0, "equity": 100, "btc": 1, "price": 100},
            {"timestamp": (self.start + timedelta(days=1)).isoformat(), "nav": 1.2, "equity": 120, "btc": 1, "price": 120},
            {"timestamp": (self.start + timedelta(days=2)).isoformat(), "nav": 0.9, "equity": 90, "btc": 1, "price": 90},
        ]
        metrics = calcular_metricas_risco(self.trades, history)
        self.assertAlmostEqual(metrics["max_drawdown_pct"], 25.0)
        self.assertEqual(metrics["win_rate_pct"], 50.0)
        self.assertAlmostEqual(metrics["profit_factor"], 9.94 / 10.04)
        self.assertEqual(metrics["average_holding_hours"], 36.0)
        self.assertEqual(metrics["average_exposure_pct"], 100.0)

    def test_csv_exports_sell_pnl_and_excel_delimiter(self):
        csv_text = gerar_csv_trades(self.trades)
        self.assertIn("tipo;motivo", csv_text)
        self.assertIn("9.94", csv_text)
        self.assertIn("-10.04", csv_text)

    def test_backtest_returns_strategy_and_buy_and_hold_series(self):
        history = [
            (self.start, 100.0),
            (self.start + timedelta(days=1), 98.0),
            (self.start + timedelta(days=2), 110.0),
        ]
        result = simular_backtest(history, 100_000)
        self.assertEqual(len(result["points"]), 3)
        self.assertEqual(result["trade_count"], 3)
        self.assertIn("strategy", result["points"][-1])
        self.assertIn("buy_hold", result["points"][-1])
        self.assertTrue(math.isfinite(result["strategy_return_pct"]))

    def test_backtest_rejects_zero_capital(self):
        with self.assertRaisesRegex(ValueError, "maior que zero"):
            simular_backtest([(self.start, 100), (self.start + timedelta(days=1), 101)], 0)

    def test_historical_fetch_uses_supported_range_parameters(self):
        response = Mock()
        response.json.return_value = {
            "prices": [
                [self.start.timestamp() * 1000, 100],
                [(self.start + timedelta(hours=1)).timestamp() * 1000, 101],
            ]
        }
        response.raise_for_status.return_value = None
        with patch("market_insights.requests.get", return_value=response) as get:
            history = buscar_historico_btc_brl_periodo(
                7, self.start + timedelta(days=7)
            )
        self.assertEqual(len(history), 2)
        self.assertEqual(get.call_args.kwargs["params"]["vs_currency"], "brl")
        self.assertEqual(get.call_args.kwargs["timeout"], 30)

    def test_historical_fetch_rejects_unsupported_period(self):
        with self.assertRaisesRegex(ValueError, "7, 30, 90 ou 365"):
            buscar_historico_btc_brl_periodo(14)

    def test_equity_history_adjusts_returns_for_deposits(self):
        motor = MotorSimulador.__new__(MotorSimulador)
        motor.estado = Estado(capital_brl=1_000)
        motor.equity_history = []
        motor._pending_equity_flow = 0.0
        motor._salvar_equity = lambda: None

        motor._registrar_amostra_equity(100, self.start, force_sample=True)
        motor.estado.capital_brl += 100
        motor._pending_equity_flow = 100
        motor._registrar_amostra_equity(
            110, self.start + timedelta(hours=1), force_sample=True
        )

        self.assertAlmostEqual(motor.equity_history[-1]["nav"], 1.0)
        self.assertAlmostEqual(motor.equity_history[-1]["benchmark"], 1_200)
        self.assertEqual(motor.equity_history[-1]["flow"], 100)


if __name__ == "__main__":
    unittest.main()
