import json
import math
import os
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from io import StringIO
from unittest.mock import Mock, patch

from fetcher import Cotacao, buscar_cotacao_btc_brl
from main import (
    AppBTC,
    MotorSimulador,
    formatar_reais,
)
from models import Estado
from market_insights import analisar_feed_analistas, calcular_previsao
from report import imprimir_snapshot
from simulator import (
    executar_compra,
    executar_venda,
    executar_venda_quantidade,
    registrar_deposito,
    registrar_saque,
)
from strategy import decidir_acao


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

    def test_deposit_and_withdrawal_are_cash_flow_not_trades(self):
        deposito = registrar_deposito(self.estado, 250)
        self.assertEqual(deposito.tipo, "DEPOSITO")
        self.assertEqual(deposito.valor_brl, 250)
        self.assertEqual(self.estado.capital_brl, 1250)
        self.assertEqual(self.estado.total_compras, 0)
        self.assertEqual(self.estado.total_vendas, 0)

        saque = registrar_saque(self.estado, 100)
        self.assertEqual(saque.tipo, "SAQUE")
        self.assertEqual(saque.valor_brl, 100)
        self.assertEqual(self.estado.capital_brl, 1150)
        self.assertEqual(self.estado.total_taxas_brl, 0)

    def test_motor_records_deposits_and_withdrawals_separately(self):
        motor = MotorSimulador.__new__(MotorSimulador)
        motor.estado = self.estado
        motor.historico = []
        motor.fluxo_caixa = []
        motor.lock = threading.Lock()
        motor._salvar_estado = lambda: None
        motor._salvar_fluxo_caixa = lambda: None

        motor.depositar_manual(250)
        motor.sacar_manual(100)

        self.assertEqual([item["tipo"] for item in motor.fluxo_caixa], ["DEPOSITO", "SAQUE"])
        self.assertEqual(motor.historico, [])

    def test_cash_flow_rejects_invalid_amounts_without_changing_balance(self):
        for operacao in (registrar_deposito, registrar_saque):
            for valor in (0, -1, math.inf, math.nan):
                with self.subTest(operacao=operacao.__name__, valor=valor):
                    with self.assertRaises(ValueError):
                        operacao(self.estado, valor)
                    self.assertEqual(self.estado.capital_brl, 1000)

    def test_withdrawal_cannot_exceed_available_brl(self):
        with self.assertRaisesRegex(ValueError, "excede"):
            registrar_saque(self.estado, 1000.01)
        self.assertEqual(self.estado.capital_brl, 1000)

    def test_old_saved_state_uses_defaults_for_new_strategy_fields(self):
        estado = Estado.from_dict({"capital_brl": 1000, "capital_btc": 0.01})

        self.assertEqual(estado.ultimo_preco_compra_brl, 0)
        self.assertFalse(estado.take_profit_executado)
        self.assertFalse(estado.stop_loss_executado)

    def test_strategy_does_not_buy_periodically_from_existing_balance(self):
        self.estado.capital_btc = 0.01
        self.estado.ultima_compra_ts = datetime.now(timezone.utc).isoformat()
        self.estado.ultimo_aporte_ts = "2000-01-01T00:00:00+00:00"
        self.assertEqual(decidir_acao(self.estado, 200_000), {"acao": "NADA"})

    def test_extra_buy_uses_price_of_last_purchase_not_average(self):
        self.estado.capital_brl = 10_000
        self.estado.capital_btc = 0.01
        self.estado.preco_medio_brl = 290_000
        self.estado.ultimo_preco_compra_brl = 300_000
        self.estado.ultima_compra_ts = datetime.now(timezone.utc).isoformat()

        acao = decidir_acao(self.estado, 280_000)

        self.assertEqual(acao["acao"], "COMPRA")
        self.assertEqual(acao["valor_brl"], 5_000)
        self.assertEqual(acao["motivo"], "compra_extra")

    def test_extra_buy_waits_for_sufficient_cash(self):
        self.estado.capital_brl = 4_999
        self.estado.ultimo_preco_compra_brl = 300_000
        self.estado.ultima_compra_ts = datetime.now(timezone.utc).isoformat()

        self.assertEqual(decidir_acao(self.estado, 280_000), {"acao": "NADA"})

    def test_extra_buy_triggers_at_two_percent_drop(self):
        self.estado.capital_brl = 10_000
        self.estado.ultimo_preco_compra_brl = 300_000
        self.estado.ultima_compra_ts = datetime.now(timezone.utc).isoformat()

        self.assertEqual(decidir_acao(self.estado, 293_999)["valor_brl"], 5_000)

    def test_extra_buy_does_not_trigger_before_two_percent_drop(self):
        self.estado.capital_brl = 10_000
        self.estado.ultimo_preco_compra_brl = 300_000
        self.estado.ultima_compra_ts = datetime.now(timezone.utc).isoformat()

        self.assertEqual(decidir_acao(self.estado, 294_001), {"acao": "NADA"})

    def test_take_profit_only_sells_once_until_next_buy(self):
        executar_compra(self.estado, 200_000, 500, "manual")
        acao = decidir_acao(self.estado, 206_000)
        self.assertEqual(acao["motivo"], "take_profit")
        self.assertEqual(acao["percentual"], 50)
        executar_venda(self.estado, 206_000, acao["percentual"], acao["motivo"])

        self.assertEqual(decidir_acao(self.estado, 206_000), {"acao": "NADA"})

        executar_compra(self.estado, 206_000, 100, "manual")
        self.assertFalse(self.estado.take_profit_executado)

    def test_stop_loss_only_sells_once_and_does_not_rebuy_while_falling(self):
        executar_compra(self.estado, 200_000, 500, "manual")
        acao = decidir_acao(self.estado, 180_000)
        self.assertEqual(acao["motivo"], "stop_loss")
        self.assertEqual(acao["percentual"], 50)
        executar_venda(self.estado, 180_000, acao["percentual"], acao["motivo"])

        self.assertEqual(decidir_acao(self.estado, 180_000), {"acao": "NADA"})

        executar_compra(self.estado, 180_000, 100, "manual")
        self.assertFalse(self.estado.stop_loss_executado)

    def test_manual_order_requires_live_quote_and_persists_trade(self):
        motor = MotorSimulador.__new__(MotorSimulador)
        motor.estado = self.estado
        motor.historico = []
        motor.preco_atual = 200_000
        motor.preco_atualizado = True
        motor.lock = threading.Lock()
        motor.erro_persistencia = ""
        motor.equity_history = []
        motor.erro_patrimonio = ""
        motor._pending_equity_flow = 0.0

        with tempfile.TemporaryDirectory() as directory:
            files = {
                "state_file": os.path.join(directory, "state.json"),
                "history_file": os.path.join(directory, "history.json"),
                "trade_journal_file": os.path.join(directory, "trade.json"),
                "equity_file": os.path.join(directory, "equity.json"),
            }
            with patch.dict("config.CONFIG", files):
                trade = motor.comprar_manual(100)

                self.assertEqual(trade.motivo, "manual")
                self.assertEqual(len(motor.historico), 1)
                with open(files["state_file"], encoding="utf-8") as f:
                    state = json.load(f)
                with open(files["history_file"], encoding="utf-8") as f:
                    history = json.load(f)
                self.assertEqual(state["total_compras"], 1)
                self.assertEqual(history[0]["tipo"], "COMPRA")
                self.assertFalse(os.path.exists(files["trade_journal_file"]))

                motor.preco_atualizado = False
                with self.assertRaisesRegex(RuntimeError, "cotação não está atualizada"):
                    motor.comprar_manual(100)
                self.assertEqual(len(motor.historico), 1)

    def test_incomplete_trade_save_is_recovered_before_loading(self):
        motor = MotorSimulador.__new__(MotorSimulador)
        motor.estado = self.estado
        motor.historico = []
        motor.preco_atual = 200_000
        motor.preco_atualizado = True
        motor.lock = threading.Lock()
        motor.erro_persistencia = ""
        motor.equity_history = []
        motor.erro_patrimonio = ""
        motor._pending_equity_flow = 0.0

        with tempfile.TemporaryDirectory() as directory:
            files = {
                "state_file": os.path.join(directory, "state.json"),
                "history_file": os.path.join(directory, "history.json"),
                "trade_journal_file": os.path.join(directory, "trade.json"),
                "equity_file": os.path.join(directory, "equity.json"),
            }
            with patch.dict("config.CONFIG", files):
                with patch.object(
                    motor, "_salvar_historico", side_effect=OSError("disco cheio")
                ):
                    with self.assertRaisesRegex(OSError, "recuperados ao reiniciar"):
                        motor.comprar_manual(100)

                self.assertTrue(os.path.exists(files["trade_journal_file"]))
                with self.assertRaisesRegex(RuntimeError, "Reinicie"):
                    motor.comprar_manual(100)

                recuperador = MotorSimulador.__new__(MotorSimulador)
                recuperador._recuperar_transacao_trade()
                with open(files["state_file"], encoding="utf-8") as f:
                    state = json.load(f)
                with open(files["history_file"], encoding="utf-8") as f:
                    history = json.load(f)
                self.assertEqual(state["total_compras"], 1)
                self.assertEqual(len(history), 1)
                self.assertFalse(os.path.exists(files["trade_journal_file"]))

    def test_automatic_purchase_is_written_to_state_and_history(self):
        motor = MotorSimulador.__new__(MotorSimulador)
        motor.estado = Estado(
            capital_brl=10_000,
            capital_btc=0.01,
            preco_medio_brl=300_000,
            ultima_compra_ts=datetime.now(timezone.utc).isoformat(),
            ultimo_preco_compra_brl=300_000,
        )
        motor.historico = []
        motor.fluxo_caixa = []
        motor.equity_history = []
        motor._pending_equity_flow = 0.0
        motor.erro_patrimonio = ""
        motor.erro_persistencia = ""
        motor.preco_atual = None
        motor.fonte_preco = ""
        motor.preco_atualizado = False
        motor.ultimo_update = None
        motor.ultimo_erro = ""
        motor.historico_cotacao = []
        motor.previsao_dia = []
        motor.sentimento_analistas = 0.0
        motor.artigos_analistas = []
        motor.atualizado_mercado = None
        motor.atualizado_analistas = datetime.now(timezone.utc)
        motor.erro_analise = ""
        motor.rodando = True
        motor.lock = threading.Lock()

        with tempfile.TemporaryDirectory() as directory:
            files = {
                "state_file": os.path.join(directory, "state.json"),
                "history_file": os.path.join(directory, "history.json"),
                "trade_journal_file": os.path.join(directory, "trade.json"),
                "equity_file": os.path.join(directory, "equity.json"),
            }
            quote = Cotacao(
                293_000,
                "Teste",
                datetime.now(timezone.utc),
                True,
            )
            with (
                patch.dict("config.CONFIG", {**files, "intervalo_loop_minutos": 1}),
                patch("main.buscar_cotacao_btc_brl", return_value=quote),
                patch("main.buscar_historico_btc_brl", return_value=[]),
                patch(
                    "main.time.sleep",
                    side_effect=lambda _: setattr(motor, "rodando", False),
                ),
            ):
                motor._loop()

            with open(files["state_file"], encoding="utf-8") as f:
                state = json.load(f)
            with open(files["history_file"], encoding="utf-8") as f:
                history = json.load(f)
            self.assertEqual(state["total_compras"], 1)
            self.assertEqual(history[0]["motivo"], "compra_extra")
            self.assertFalse(os.path.exists(files["trade_journal_file"]))

    def test_corrupt_trade_files_are_not_silently_replaced_by_empty_data(self):
        motor = MotorSimulador.__new__(MotorSimulador)
        with tempfile.TemporaryDirectory() as directory:
            state_path = os.path.join(directory, "state.json")
            history_path = os.path.join(directory, "history.json")
            with open(state_path, "w", encoding="utf-8") as f:
                f.write("{invalid")
            with open(history_path, "w", encoding="utf-8") as f:
                f.write("{}")

            with patch.dict(
                "config.CONFIG",
                {"state_file": state_path, "history_file": history_path},
            ):
                with self.assertRaisesRegex(ValueError, "state.json"):
                    motor._carregar_estado()
                with self.assertRaisesRegex(ValueError, "history.json"):
                    motor._carregar_historico()

            with open(state_path, encoding="utf-8") as f:
                self.assertEqual(f.read(), "{invalid")
            with open(history_path, encoding="utf-8") as f:
                self.assertEqual(f.read(), "{}")

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

    def test_news_link_uses_wrapping_label_and_opens_url_on_click(self):
        app = AppBTC.__new__(AppBTC)
        app.conteudo = Mock()
        app.conteudo.winfo_width.return_value = 800
        label = Mock()

        with (
            patch("main.ctk.CTkLabel", return_value=label) as create_label,
            patch("main.webbrowser.open") as open_url,
        ):
            result = app._criar_link_noticia(
                Mock(), "Notícia de exemplo", "https://example.com/article", 0
            )
            click_handler = label.bind.call_args.args[1]
            click_handler(None)

        self.assertIs(result, label)
        self.assertEqual(create_label.call_args.kwargs["wraplength"], 690)
        self.assertEqual(create_label.call_args.kwargs["cursor"], "hand2")
        open_url.assert_called_once_with("https://example.com/article")

    def test_close_cancels_all_pending_tk_callbacks_before_destroy(self):
        app = AppBTC.__new__(AppBTC)
        app.tk = Mock()
        app.tk.call.return_value = "after#1 after#2 after#3"
        app.tk.splitlist.return_value = ("after#1", "after#2", "after#3")
        app.after_cancel = Mock()

        app._cancelar_callbacks_pendentes()

        self.assertEqual(app.tk.call.call_args.args, ("after", "info"))
        self.assertEqual(
            [call.args[0] for call in app.after_cancel.call_args_list],
            ["after#1", "after#2", "after#3"],
        )

    def test_terminal_buy_and_hold_needs_an_initial_price(self):
        output = StringIO()
        with redirect_stdout(output):
            imprimir_snapshot(self.estado, 200_000)
        self.assertIn("indisponível (informe o preço inicial)", output.getvalue())

        output = StringIO()
        with redirect_stdout(output):
            imprimir_snapshot(self.estado, 200_000, preco_inicial_brl=100_000)
        self.assertIn("+100.00%", output.getvalue())

    def test_terminal_report_uses_deposits_and_withdrawals_in_net_capital(self):
        output = StringIO()
        fluxo_caixa = [
            {"tipo": "DEPOSITO", "valor_brl": 250},
            {"tipo": "SAQUE", "valor_brl": 100},
        ]
        self.estado.capital_brl = 1150
        with redirect_stdout(output):
            imprimir_snapshot(
                self.estado, 200_000, fluxo_caixa=fluxo_caixa
            )

        self.assertIn("Capital líquido:      R$     1.150,00", output.getvalue())
        self.assertIn("Lucro/prejuízo:       R$        +0,00", output.getvalue())

    def test_feed_includes_recent_btc_news_and_marks_analyst_views(self):
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
        self.assertEqual(len(articles), 2)
        self.assertEqual(articles[0].author, "Jane Analyst")
        self.assertEqual(articles[0].sentiment, 1)
        self.assertTrue(articles[0].analyst_view)
        self.assertEqual(articles[0].url, "https://example.com/bitcoin-outlook")
        self.assertEqual(articles[1].sentiment, 0)
        self.assertFalse(articles[1].analyst_view)

    def test_general_bitcoin_outlook_is_shown_but_not_as_analyst_consensus(self):
        now = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
        xml = f"""<rss><channel>
          <item>
            <title>Bitcoin forecast: rally may continue</title>
            <link>https://example.com/forecast</link>
            <description>Bitcoin could rise further.</description>
            <pubDate>{format_datetime(now)}</pubDate>
          </item>
        </channel></rss>"""

        articles = analisar_feed_analistas(xml, "Test Feed", now)
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].sentiment, 1)
        self.assertFalse(articles[0].analyst_view)

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
