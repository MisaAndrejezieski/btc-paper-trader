import math
import threading
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from io import StringIO
from unittest.mock import patch

from fetcher import Cotacao, buscar_cotacao_btc_brl
from main import (
    AppBTC,
    MotorSimulador,
    formatar_reais,
)
from models import Estado
from market_insights import analisar_feed_analistas, calcular_previsao
from report import imprimir_snapshot
from simulator import executar_compra, executar_venda_quantidade


class SimulatorTests(unittest.TestCase):
    def setUp(self):
        self.estado = Estado(capital_brl=1000, patrimonio_inicial_brl=1000)

    def test_buy_and_manual_quantity_sale_apply_fees(self):
        compra = executar_compra(self.estado, 200_000, 100, "manual")
        self.assertAlmostEqual(compra.quantidade_btc, 0.0004995)
        self.assertAlmostEqual(self.estado.capital_brl, 900)

        venda = executar_venda_quantidade(
            self.estado, 220_000, 0.00025, "manual"
        )
        self.assertAlmostEqual(venda.valor_brl, 55)
        self.assertAlmostEqual(venda.taxa_brl, 0.055)
        self.assertAlmostEqual(self.estado.capital_brl, 954.945)
        self.assertAlmostEqual(self.estado.total_taxas_brl, 0.155)

    def test_manual_sale_cannot_exceed_btc_balance(self):
        with self.assertRaisesRegex(ValueError, "excede"):
            executar_venda_quantidade(self.estado, 200_000, 0.001, "manual")
        self.assertEqual(self.estado.capital_brl, 1000)
        self.assertEqual(self.estado.total_vendas, 0)

    def test_manual_order_requires_live_quote_and_persists_trade(self):
        motor = MotorSimulador.__new__(MotorSimulador)
        motor.estado = self.estado
        motor.historico = []
        motor.preco_atual = 200_000
        motor.preco_atualizado = True
        motor.lock = threading.Lock()
        motor._salvar_estado = lambda: None
        motor._salvar_historico = lambda: None

        trade = motor.comprar_manual(100)
        self.assertEqual(trade.motivo, "manual")
        self.assertEqual(len(motor.historico), 1)

        motor.preco_atualizado = False
        with self.assertRaisesRegex(RuntimeError, "cotação não está atualizada"):
            motor.comprar_manual(100)
        self.assertEqual(len(motor.historico), 1)

    def test_cached_quote_is_not_marked_as_current(self):
        cached = Cotacao(
            400_000, "cache (CoinGecko)",
            datetime(2026, 10, 13, tzinfo=timezone.utc), False,
        )
        with (
            patch("fetcher.buscar_coingecko", return_value=None),
            patch("fetcher.buscar_binance", return_value=None),
            patch("fetcher.ler_cache", return_value=cached),
        ):
            quote = buscar_cotacao_btc_brl()
        self.assertFalse(quote.atual)
        self.assertEqual(quote.preco_brl, 400_000)

    def test_local_time_and_brazilian_currency_format(self):
        local = AppBTC._para_horario_local("2026-10-13T08:00:00+00:00")
        self.assertEqual(local.strftime("%H:%M"), "05:00")
        self.assertEqual(formatar_reais(1234.5), "1.234,50")
        self.assertEqual(formatar_reais(-12.5, sinal=True), "-12,50")

    def test_terminal_buy_and_hold_needs_an_initial_price(self):
        output = StringIO()
        with redirect_stdout(output):
            imprimir_snapshot(self.estado, 200_000)
        self.assertIn("indisponível (informe o preço inicial)", output.getvalue())

        output = StringIO()
        with redirect_stdout(output):
            imprimir_snapshot(self.estado, 200_000, preco_inicial_brl=100_000)
        self.assertIn("+100.00%", output.getvalue())

    def test_analyst_feed_uses_recent_directional_btc_articles(self):
        now = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
        xml = f"""<?xml version="1.0"?>
        <rss><channel>
          <item>
            <title>Bitcoin analyst expects a rally</title>
            <link>https://example.com/bitcoin-outlook</link>
            <description><![CDATA[Analyst sees upside for Bitcoin.]]></description>
            <pubDate>{format_datetime(now)}</pubDate>
            <dc:creator xmlns:dc="http://purl.org/dc/elements/1.1/">Jane Analyst</dc:creator>
          </item>
          <item>
            <title>Bitcoin price update</title>
            <link>https://example.com/neutral</link>
            <description>No analyst outlook here.</description>
            <pubDate>{format_datetime(now)}</pubDate>
          </item>
        </channel></rss>"""

        articles = analisar_feed_analistas(xml, "Test Feed", now)
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].author, "Jane Analyst")
        self.assertEqual(articles[0].sentiment, 1)
        self.assertEqual(articles[0].url, "https://example.com/bitcoin-outlook")

    def test_general_bitcoin_outlook_is_not_mistaken_for_analyst_consensus(self):
        now = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
        xml = f"""<rss><channel>
          <item>
            <title>Bitcoin forecast: rally may continue</title>
            <link>https://example.com/forecast</link>
            <description>Bitcoin could rise further.</description>
            <pubDate>{format_datetime(now)}</pubDate>
          </item>
        </channel></rss>"""

        self.assertEqual(analisar_feed_analistas(xml, "Test Feed", now), [])

    def test_forecast_is_bounded_and_analyst_signal_is_not_dominant(self):
        now = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
        history = [
            (now - timedelta(hours=24 - hour), 400_000 * math.exp(
                hour * 0.0005 + 0.004 * math.sin(hour)
            ))
            for hour in range(25)
        ]
        positive = calcular_previsao(history, 405_000, 1, now)
        negative = calcular_previsao(history, 405_000, -1, now)

        self.assertEqual(len(positive), 25)
        self.assertGreater(positive[-1].expected_brl, negative[-1].expected_brl)
        self.assertLess(positive[-1].low_brl, positive[-1].expected_brl)
        self.assertGreater(positive[-1].high_brl, positive[-1].expected_brl)
        self.assertLess(positive[-1].expected_brl, 405_000 * 1.1)


if __name__ == "__main__":
    unittest.main()
