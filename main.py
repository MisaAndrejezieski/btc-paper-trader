"""
BTC Paper Trader — GUI integrada com o simulador.
O simulador roda em thread separada; a GUI atualiza a cada 2 segundos.
Tudo em um único processo: `python main.py`
"""

import json
import os
import queue
import tempfile
import threading
import time
from bisect import bisect_left
from datetime import datetime, timedelta, timezone
from functools import partial
from math import isfinite
from zoneinfo import ZoneInfo
from tkinter import TclError, filedialog, messagebox
import webbrowser

import customtkinter as ctk
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.ticker import FuncFormatter, MaxNLocator
from requests import RequestException

from config import CONFIG
from fetcher import Cotacao, buscar_cotacao_btc_brl
from market_insights import (
    AnalystArticle,
    buscar_historico_btc_brl,
    buscar_historico_btc_brl_periodo,
    buscar_sentimento_analistas,
    calcular_previsao,
)
from models import Estado
from backtest import simular_backtest
from simulator import (
    executar_compra,
    executar_venda,
    executar_venda_quantidade,
    registrar_deposito,
    registrar_saque,
)
from strategy import decidir_acao
from trading_analytics import (
    calcular_metricas_risco,
    calcular_pnl_realizado,
    gerar_csv_trades,
)
from theme import (CORES, FONTE_LABEL, FONTE_PEQUENA, FONTE_SUBTITULO,
                   FONTE_TITULO, FONTE_VALOR, FONTE_VALOR_MEDIO)

FUSO_LOCAL = ZoneInfo("America/Sao_Paulo")


def formatar_reais(valor: float, sinal: bool = False) -> str:
    formato = f"{valor:+,.2f}" if sinal else f"{valor:,.2f}"
    return formato.replace(",", "X").replace(".", ",").replace("X", ".")


# =========================================================================
# MOTOR DO SIMULADOR (roda em thread separada)
# =========================================================================

class MotorSimulador:
    def __init__(self):
        self._recuperar_transacao_trade()
        self.estado = self._carregar_estado()
        self.historico = self._carregar_historico()
        self.fluxo_caixa = self._carregar_fluxo_caixa()
        self.equity_history = self._carregar_historico_patrimonio()
        self._pending_equity_flow = 0.0
        self.erro_patrimonio = ""
        self.erro_persistencia = ""
        self.preco_atual = None
        self.fonte_preco = ""
        self.preco_atualizado = False
        self.ultimo_update = None
        self.ultimo_erro = ""
        self.historico_cotacao = []
        self.previsao_dia = []
        self.sentimento_analistas = 0.0
        self.artigos_analistas: list[AnalystArticle] = []
        self.atualizado_mercado = None
        self.atualizado_analistas = None
        self.erro_analise = ""
        self.rodando = True
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._loop, daemon=True)

    def _carregar_estado(self) -> Estado:
        if os.path.exists(CONFIG["state_file"]):
            try:
                with open(CONFIG["state_file"], "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    raise ValueError("O estado precisa ser um objeto JSON.")
                return Estado.from_dict(data)
            except (
                json.JSONDecodeError, OSError, TypeError, UnicodeError, ValueError
            ) as exc:
                raise ValueError(
                    f"Não foi possível carregar {CONFIG['state_file']}; "
                    f"o arquivo foi preservado: {exc}"
                ) from exc
        estado = Estado()
        estado.capital_brl = CONFIG["capital_inicial_brl"]
        estado.patrimonio_inicial_brl = CONFIG["capital_inicial_brl"]
        return estado

    def _carregar_historico(self):
        if os.path.exists(CONFIG["history_file"]):
            try:
                with open(CONFIG["history_file"], "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, list) or any(
                    not isinstance(trade, dict) for trade in data
                ):
                    raise ValueError("O histórico precisa ser uma lista de trades.")
                return data
            except (json.JSONDecodeError, OSError, UnicodeError, ValueError) as exc:
                raise ValueError(
                    f"Não foi possível carregar {CONFIG['history_file']}; "
                    f"o arquivo foi preservado: {exc}"
                ) from exc
        return []

    def _carregar_fluxo_caixa(self):
        if not os.path.exists(CONFIG["cash_flow_file"]):
            return []
        try:
            with open(CONFIG["cash_flow_file"], "r", encoding="utf-8") as f:
                fluxo_caixa = json.load(f)
            if not isinstance(fluxo_caixa, list) or any(
                not isinstance(evento, dict) for evento in fluxo_caixa
            ):
                raise ValueError("O fluxo de caixa precisa ser uma lista de eventos.")
            return fluxo_caixa
        except (json.JSONDecodeError, OSError, UnicodeError, ValueError) as exc:
            raise ValueError(
                f"Não foi possível carregar {CONFIG['cash_flow_file']}; "
                f"o arquivo foi preservado: {exc}"
            ) from exc

    def _carregar_historico_patrimonio(self):
        path = CONFIG["equity_file"]
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list) or any(
                not isinstance(point, dict) for point in data
            ):
                raise ValueError("O histórico de patrimônio precisa ser uma lista.")
        except (json.JSONDecodeError, OSError, UnicodeError, ValueError) as exc:
            raise ValueError(
                f"Não foi possível carregar {path}; o arquivo foi preservado: {exc}"
            ) from exc
        return data

    @staticmethod
    def _salvar_json_atomico(path: str, data, ensure_ascii: bool = True):
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=directory,
                prefix=f".{os.path.basename(path)}.",
                suffix=".tmp",
                delete=False,
            ) as f:
                temp_path = f.name
                json.dump(data, f, indent=2, ensure_ascii=ensure_ascii)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, path)
            temp_path = None
        finally:
            if temp_path is not None:
                try:
                    os.unlink(temp_path)
                except FileNotFoundError:
                    pass

    def _recuperar_transacao_trade(self):
        path = CONFIG["trade_journal_file"]
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                transaction = json.load(f)
            state = transaction["state"]
            history = transaction["history"]
            if not isinstance(state, dict) or not isinstance(history, list) or any(
                not isinstance(trade, dict) for trade in history
            ):
                raise ValueError("O diário de recuperação contém dados inválidos.")
            Estado.from_dict(state)
            self._salvar_json_atomico(CONFIG["state_file"], state)
            self._salvar_json_atomico(
                CONFIG["history_file"], history, ensure_ascii=False
            )
            os.remove(path)
        except (
            KeyError, json.JSONDecodeError, OSError, TypeError, UnicodeError, ValueError
        ) as exc:
            raise RuntimeError(
                f"Não foi possível recuperar a transação pendente em {path}; "
                f"os arquivos foram preservados: {exc}"
            ) from exc

    def _salvar_estado(self):
        self._salvar_json_atomico(CONFIG["state_file"], self.estado.to_dict())

    def _salvar_historico(self):
        self._salvar_json_atomico(
            CONFIG["history_file"], self.historico, ensure_ascii=False
        )

    def _salvar_fluxo_caixa(self):
        self._salvar_json_atomico(
            CONFIG["cash_flow_file"], self.fluxo_caixa, ensure_ascii=False
        )

    def _salvar_historico_patrimonio(self):
        self._salvar_json_atomico(
            CONFIG["equity_file"], self.equity_history, ensure_ascii=False
        )

    def _persistir_trade(self, estado_anterior: Estado, historico_anterior: list):
        path = CONFIG["trade_journal_file"]
        transaction = {
            "state": self.estado.to_dict(),
            "history": self.historico,
        }
        try:
            self._salvar_json_atomico(path, transaction, ensure_ascii=False)
        except (OSError, TypeError, ValueError):
            self.estado = estado_anterior
            self.historico = historico_anterior
            raise

        try:
            self._salvar_estado()
            self._salvar_historico()
            os.remove(path)
        except (OSError, TypeError, ValueError) as exc:
            self.erro_persistencia = (
                f"Trade pendente de gravação; os dados serão recuperados ao "
                f"reiniciar o simulador ({path}): {exc}"
            )
            raise OSError(self.erro_persistencia) from exc

    def _validar_persistencia(self):
        if getattr(self, "erro_persistencia", ""):
            raise RuntimeError(
                "Há uma transação pendente de gravação. Reinicie o simulador "
                "para recuperar os arquivos antes de operar novamente."
            )

    def _registrar_patrimonio(self, preco: float, timestamp, force: bool = False):
        if not hasattr(self, "equity_history") or not isfinite(preco) or preco <= 0:
            return
        now = timestamp if isinstance(timestamp, datetime) else datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)
        if self.equity_history and not force:
            anterior = datetime.fromisoformat(self.equity_history[-1]["timestamp"])
            if anterior.tzinfo is None:
                anterior = anterior.replace(tzinfo=timezone.utc)
            if anterior.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0) == now.replace(
                minute=0, second=0, microsecond=0
            ):
                if self.erro_patrimonio:
                    self._persistir_historico_patrimonio()
                return

        equity = self.estado.capital_brl + self.estado.capital_btc * preco
        flow = getattr(self, "_pending_equity_flow", 0.0)
        if self.equity_history:
            previous = self.equity_history[-1]
            units = float(previous["units"])
            previous_nav = float(previous["nav"])
            if previous_nav > 0:
                units += flow / previous_nav
            if units <= 0 and equity > 0:
                units = equity
            nav = equity / units if units > 0 else 0.0
            benchmark_btc = max(
                0.0, float(previous["benchmark_btc"]) + flow / preco
            )
        else:
            units = equity
            nav = 1.0 if equity > 0 else 0.0
            benchmark_btc = equity / preco

        self.equity_history.append({
            "timestamp": now.isoformat(),
            "price": preco,
            "cash": self.estado.capital_brl,
            "btc": self.estado.capital_btc,
            "equity": equity,
            "nav": nav,
            "units": units,
            "benchmark_btc": benchmark_btc,
            "benchmark": benchmark_btc * preco,
            "flow": flow,
        })
        self.equity_history = self.equity_history[-15000:]
        self._pending_equity_flow = 0.0
        self._persistir_historico_patrimonio()

    def _persistir_historico_patrimonio(self):
        try:
            self._salvar_historico_patrimonio()
        except OSError as exc:
            self.erro_patrimonio = str(exc)
            print(f"[AVISO] Não foi possível salvar a curva de patrimônio: {exc}")
        else:
            self.erro_patrimonio = ""

    def _registrar_trade(self, trade):
        if trade is None:
            return
        self.historico.append(trade.to_dict())

    def _validar_cotacao_para_operacao(self) -> float:
        if not self.preco_atualizado or self.preco_atual is None:
            raise RuntimeError("A cotação não está atualizada; aguarde uma fonte online.")
        return self.preco_atual

    def comprar_manual(self, valor_brl: float):
        with self.lock:
            self._validar_persistencia()
            preco = self._validar_cotacao_para_operacao()
            if not isfinite(valor_brl) or valor_brl <= 0:
                raise ValueError("Informe um valor de compra maior que zero.")
            if valor_brl > self.estado.capital_brl:
                raise ValueError("O valor da compra excede seu saldo em reais.")
            estado_anterior = Estado.from_dict(self.estado.to_dict())
            historico_anterior = list(self.historico)
            trade = executar_compra(self.estado, preco, valor_brl, "manual")
            self._registrar_trade(trade)
            self._persistir_trade(estado_anterior, historico_anterior)
            self._registrar_patrimonio(
                preco, getattr(self, "ultimo_update", None), force=True
            )
            return trade

    def vender_manual(self, quantidade_btc: float):
        with self.lock:
            self._validar_persistencia()
            preco = self._validar_cotacao_para_operacao()
            estado_anterior = Estado.from_dict(self.estado.to_dict())
            historico_anterior = list(self.historico)
            trade = executar_venda_quantidade(
                self.estado, preco, quantidade_btc, "manual"
            )
            self._registrar_trade(trade)
            self._persistir_trade(estado_anterior, historico_anterior)
            self._registrar_patrimonio(
                preco, getattr(self, "ultimo_update", None), force=True
            )
            return trade

    def depositar_manual(self, valor_brl: float):
        with self.lock:
            self._validar_persistencia()
            evento = registrar_deposito(self.estado, valor_brl)
            self.fluxo_caixa.append(evento.to_dict())
            self._salvar_estado()
            self._salvar_fluxo_caixa()
            self._pending_equity_flow = getattr(self, "_pending_equity_flow", 0.0) + valor_brl
            if getattr(self, "preco_atual", None) is not None:
                self._registrar_patrimonio(
                    self.preco_atual, getattr(self, "ultimo_update", None), force=True
                )
            return evento

    def sacar_manual(self, valor_brl: float):
        with self.lock:
            self._validar_persistencia()
            evento = registrar_saque(self.estado, valor_brl)
            self.fluxo_caixa.append(evento.to_dict())
            self._salvar_estado()
            self._salvar_fluxo_caixa()
            self._pending_equity_flow = getattr(self, "_pending_equity_flow", 0.0) - valor_brl
            if getattr(self, "preco_atual", None) is not None:
                self._registrar_patrimonio(
                    self.preco_atual, getattr(self, "ultimo_update", None), force=True
                )
            return evento

    def iniciar(self):
        if not self.thread.is_alive():
            self.thread.start()

    def parar(self):
        self.rodando = False
        if self.thread.is_alive():
            self.thread.join(timeout=2)

    def _loop(self):
        while self.rodando:
            try:
                cotacao: Cotacao = buscar_cotacao_btc_brl()

                with self.lock:
                    historico_cotacao = self.historico_cotacao
                    previsao_dia = self.previsao_dia
                    atualizado_mercado = self.atualizado_mercado
                    sentimento_analistas = self.sentimento_analistas
                    artigos_analistas = self.artigos_analistas
                    atualizado_analistas = self.atualizado_analistas
                erro_analise = ""
                if cotacao.atual:
                    try:
                        historico_cotacao = buscar_historico_btc_brl()
                        atualizado_mercado = datetime.now(timezone.utc)
                    except (RequestException, ValueError, KeyError, TypeError, IndexError) as exc:
                        erro_analise = f"Histórico indisponível: {exc}"
                        print(f"[AVISO] {erro_analise}")

                    deve_atualizar_analistas = (
                        atualizado_analistas is None
                        or (
                            datetime.now(timezone.utc) - atualizado_analistas
                        ).total_seconds() >= 900
                    )
                    if deve_atualizar_analistas:
                        try:
                            (
                                sentimento_analistas,
                                artigos_analistas,
                                erro_fontes,
                            ) = buscar_sentimento_analistas()
                            atualizado_analistas = datetime.now(timezone.utc)
                            erro_analise = " ".join(
                                part for part in (erro_analise, erro_fontes) if part
                            )
                        except (RuntimeError, RequestException, ValueError) as exc:
                            erro_analise = f"{erro_analise} Analistas indisponíveis: {exc}".strip()
                            print(f"[AVISO] {erro_analise}")

                    if historico_cotacao:
                        try:
                            previsao_dia = calcular_previsao(
                                historico_cotacao,
                                cotacao.preco_brl,
                                sentimento_analistas,
                            )
                        except ValueError as exc:
                            erro_analise = f"{erro_analise} Projeção indisponível: {exc}".strip()
                            previsao_dia = []

                if not self.rodando:
                    break

                with self.lock:
                    acao = (
                        decidir_acao(self.estado, cotacao.preco_brl)
                        if cotacao.atual and not self.erro_persistencia
                        else {"acao": "NADA"}
                    )
                    trade_executado = None
                    estado_anterior = (
                        Estado.from_dict(self.estado.to_dict())
                        if acao["acao"] != "NADA"
                        else None
                    )
                    historico_anterior = list(self.historico)

                    if acao["acao"] == "COMPRA":
                        trade_executado = executar_compra(
                            self.estado, cotacao.preco_brl,
                            acao["valor_brl"], acao["motivo"]
                        )
                        self._registrar_trade(trade_executado)
                    elif acao["acao"] == "VENDA":
                        trade_executado = executar_venda(
                            self.estado, cotacao.preco_brl,
                            acao["percentual"], acao["motivo"]
                        )
                        self._registrar_trade(trade_executado)

                    if trade_executado is not None and estado_anterior is not None:
                        self._persistir_trade(estado_anterior, historico_anterior)

                    self.preco_atual = cotacao.preco_brl
                    self.fonte_preco = cotacao.fonte
                    self.preco_atualizado = cotacao.atual
                    self.ultimo_update = cotacao.timestamp
                    self.ultimo_erro = ""
                    self.historico_cotacao = historico_cotacao
                    self.previsao_dia = previsao_dia
                    self.atualizado_mercado = atualizado_mercado
                    self.sentimento_analistas = sentimento_analistas
                    self.artigos_analistas = artigos_analistas
                    self.atualizado_analistas = atualizado_analistas
                    self.erro_analise = erro_analise
                    if not self.erro_persistencia:
                        if trade_executado is None:
                            self._salvar_estado()
                            self._salvar_historico()
                        self._registrar_patrimonio(
                            cotacao.preco_brl,
                            cotacao.timestamp,
                            force=trade_executado is not None,
                        )

            except Exception as e:
                print(f"[ERRO no motor] {e}")
                with self.lock:
                    self.ultimo_erro = str(e)

            for _ in range(int(CONFIG["intervalo_loop_minutos"] * 60)):
                if not self.rodando:
                    break
                time.sleep(1)


# =========================================================================
# GUI
# =========================================================================

class AppBTC(ctk.CTk):
    def __init__(self):
        super().__init__()

        self._after_id = None
        self._ultimo_grafico_renderizado = None
        self._ultimo_patrimonio_renderizado = None
        self._backtest_em_execucao = False
        self._backtest_resultados = queue.Queue()
        self._metricas_cache_key = None
        self._metricas_cache = None
        self._toast_after_id = None
        self._closing = False
        self.title("BTC Paper Trader")
        largura_tela = self.winfo_screenwidth()
        altura_tela = self.winfo_screenheight()
        largura_inicial = min(1240, max(760, largura_tela - 80))
        altura_inicial = min(900, max(560, altura_tela - 100))
        self.geometry(f"{largura_inicial}x{altura_inicial}")
        self.minsize(min(760, largura_tela - 40), min(560, altura_tela - 80))
        self.configure(fg_color=CORES["bg_principal"])
        ctk.set_appearance_mode("dark")

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.conteudo = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
            scrollbar_button_color=CORES["verde_escuro"],
            scrollbar_button_hover_color=CORES["verde_matrix"],
        )
        self.conteudo.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 0))
        self.conteudo.grid_columnconfigure(0, weight=1)
        self.conteudo.grid_rowconfigure(4, weight=3, minsize=330)
        self.conteudo.grid_rowconfigure(6, weight=2, minsize=220)
        self.conteudo.grid_rowconfigure(7, weight=2, minsize=220)
        self.conteudo.grid_rowconfigure(8, weight=2, minsize=220)

        # Motor
        try:
            self.motor = MotorSimulador()
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror(
                "Não foi possível abrir os dados do simulador",
                f"Nenhum dado foi substituído.\n\n{exc}",
                parent=self,
            )
            self.destroy()
            return
        self._notified_trade_count = len(self.motor.historico)
        self.motor.iniciar()

        # Grid principal: coluna única, com painéis roláveis
        # ----- LINHA 0: Título -----
        ctk.CTkLabel(
            self.conteudo, text="⚡ BTC PAPER TRADER",
            font=FONTE_TITULO, text_color=CORES["verde_matrix"]
        ).grid(row=0, column=0, pady=(15, 5), sticky="w", padx=20)

        # ----- LINHA 1: Preço + Patrimônio -----
        self.frame_topo = ctk.CTkFrame(self.conteudo, fg_color="transparent")
        self.frame_topo.grid(row=1, column=0, sticky="ew", padx=20, pady=5)
        self.frame_topo.grid_columnconfigure((0, 1), weight=1)

        self.card_preco = self._criar_card(self.frame_topo, "PREÇO BTC", "R$ —", "—")
        self.card_preco.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        self.card_patrimonio = self._criar_card(self.frame_topo, "PATRIMÔNIO", "R$ —", "—")
        self.card_patrimonio.grid(row=0, column=1, sticky="nsew", padx=(10, 0))

        # ----- LINHA 2: Posição -----
        self.frame_posicao = ctk.CTkFrame(self.conteudo, fg_color=CORES["bg_card"], corner_radius=10)
        self.frame_posicao.grid(row=2, column=0, sticky="ew", padx=20, pady=10)
        self.frame_posicao.grid_columnconfigure((0, 1, 2, 3), weight=1)

        ctk.CTkLabel(
            self.frame_posicao, text="POSIÇÃO",
            font=FONTE_SUBTITULO, text_color=CORES["verde_pastel"]
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=15, pady=(10, 5))

        self.lbl_brl = self._criar_label_posicao(self.frame_posicao, "BRL", "—", 0, 0)
        self.lbl_btc = self._criar_label_posicao(self.frame_posicao, "BTC", "—", 0, 1)
        self.lbl_preco_medio = self._criar_label_posicao(self.frame_posicao, "PREÇO MÉDIO", "—", 0, 2)
        self.lbl_variacao = self._criar_label_posicao(self.frame_posicao, "VARIAÇÃO", "—", 0, 3)

        # ----- LINHA 3: Trades manuais e fluxo de caixa -----
        self.frame_manual = ctk.CTkFrame(self.conteudo, fg_color=CORES["bg_card"], corner_radius=10)
        self.frame_manual.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 10))
        self.frame_manual.grid_columnconfigure((0, 1), weight=1, uniform="ordem")

        ctk.CTkLabel(
            self.frame_manual, text="TRADES MANUAIS (paper trading)",
            font=FONTE_SUBTITULO, text_color=CORES["verde_pastel"]
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=15, pady=(8, 4))

        self.grupo_compra = ctk.CTkFrame(self.frame_manual, fg_color="transparent")
        self.grupo_compra.grid(row=1, column=0, sticky="ew", padx=(15, 10), pady=(0, 10))
        self.grupo_compra.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            self.grupo_compra, text="Comprar (R$)", font=FONTE_PEQUENA,
            text_color=CORES["texto_secundario"]
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.entrada_compra = ctk.CTkEntry(
            self.grupo_compra, placeholder_text="ex.: 100,00", width=130
        )
        self.entrada_compra.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ctk.CTkButton(
            self.grupo_compra, text="Comprar BTC", command=self._comprar_manual,
            fg_color=CORES["verde_matrix"], hover_color=CORES["verde_escuro"], width=130
        ).grid(row=0, column=2, sticky="e")

        self.grupo_venda = ctk.CTkFrame(self.frame_manual, fg_color="transparent")
        self.grupo_venda.grid(row=1, column=1, sticky="ew", padx=(10, 15), pady=(0, 10))
        self.grupo_venda.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            self.grupo_venda, text="Vender (BTC)", font=FONTE_PEQUENA,
            text_color=CORES["texto_secundario"]
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.entrada_venda = ctk.CTkEntry(
            self.grupo_venda, placeholder_text="ex.: 0,0001", width=130
        )
        self.entrada_venda.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ctk.CTkButton(
            self.grupo_venda, text="Vender BTC", command=self._vender_manual,
            fg_color="#a63d40", hover_color="#7d2d30", width=130
        ).grid(row=0, column=2, sticky="e")

        ctk.CTkLabel(
            self.frame_manual, text="FLUXO DE CAIXA (não é trade)",
            font=FONTE_SUBTITULO, text_color=CORES["verde_pastel"]
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=15, pady=(0, 4))

        self.grupo_deposito = ctk.CTkFrame(self.frame_manual, fg_color="transparent")
        self.grupo_deposito.grid(row=3, column=0, sticky="ew", padx=(15, 10), pady=(0, 10))
        self.grupo_deposito.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            self.grupo_deposito, text="Depositar (R$)", font=FONTE_PEQUENA,
            text_color=CORES["texto_secundario"]
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.entrada_deposito = ctk.CTkEntry(
            self.grupo_deposito, placeholder_text="ex.: 500,00", width=130
        )
        self.entrada_deposito.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ctk.CTkButton(
            self.grupo_deposito, text="Depositar", command=self._depositar_manual,
            fg_color=CORES["verde_matrix"], hover_color=CORES["verde_escuro"], width=130
        ).grid(row=0, column=2, sticky="e")

        self.grupo_saque = ctk.CTkFrame(self.frame_manual, fg_color="transparent")
        self.grupo_saque.grid(row=3, column=1, sticky="ew", padx=(10, 15), pady=(0, 10))
        self.grupo_saque.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            self.grupo_saque, text="Sacar (R$)", font=FONTE_PEQUENA,
            text_color=CORES["texto_secundario"]
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.entrada_saque = ctk.CTkEntry(
            self.grupo_saque, placeholder_text="ex.: 200,00", width=130
        )
        self.entrada_saque.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ctk.CTkButton(
            self.grupo_saque, text="Sacar", command=self._sacar_manual,
            fg_color="#a63d40", hover_color="#7d2d30", width=130
        ).grid(row=0, column=2, sticky="e")

        # ----- LINHA 4: Cotação e expectativa de mercado -----
        self.frame_graficos = ctk.CTkFrame(
            self.conteudo, fg_color=CORES["bg_card"], corner_radius=10
        )
        self.frame_graficos.grid(row=4, column=0, sticky="nsew", padx=20, pady=(0, 10))
        self.frame_graficos.grid_columnconfigure(0, weight=1)
        self.frame_graficos.grid_rowconfigure(1, weight=1, minsize=260)

        ctk.CTkLabel(
            self.frame_graficos,
            text="BTC/BRL · PREÇO OBSERVADO E CENÁRIO",
            font=FONTE_SUBTITULO,
            text_color=CORES["verde_pastel"],
        ).grid(row=0, column=0, sticky="w", padx=15, pady=(10, 0))

        self.fig, self.axes = plt.subplots(1, 2, figsize=(12, 3.25), dpi=90)
        self.fig.patch.set_facecolor(CORES["bg_card"])
        self.canvas_graficos = FigureCanvasTkAgg(self.fig, master=self.frame_graficos)
        self.canvas_graficos.get_tk_widget().configure(width=1)
        self.canvas_graficos.get_tk_widget().grid(
            row=1, column=0, sticky="nsew", padx=14, pady=(4, 0)
        )

        self.lbl_previsao_status = ctk.CTkLabel(
            self.frame_graficos,
            text="Aguardando dados de mercado...",
            font=FONTE_LABEL,
            text_color=CORES["verde_pastel"],
            anchor="w",
            wraplength=max(400, largura_inicial - 100),
        )
        self.lbl_previsao_status.grid(
            row=2, column=0, sticky="ew", padx=16, pady=(5, 2)
        )

        self.lbl_modelo_status = ctk.CTkLabel(
            self.frame_graficos,
            text=(
                "Estimativa experimental baseada em momentum recente, "
                "volatilidade observada e sinal limitado de notícias."
            ),
            font=FONTE_PEQUENA,
            text_color=CORES["texto_secundario"],
            anchor="w",
            wraplength=max(400, largura_inicial - 100),
        )
        self.lbl_modelo_status.grid(
            row=3, column=0, sticky="ew", padx=16, pady=(0, 4)
        )

        # ----- Painel independente de notícias -----
        self.frame_noticias = ctk.CTkFrame(
            self.conteudo, fg_color=CORES["bg_card"], corner_radius=10
        )
        self.frame_noticias.grid(
            row=5, column=0, sticky="ew", padx=20, pady=(0, 10)
        )
        self.frame_noticias.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self.frame_noticias,
            text="NOTÍCIAS E PERSPECTIVAS DE ANALISTAS",
            font=FONTE_SUBTITULO,
            text_color=CORES["verde_pastel"],
        ).grid(row=0, column=0, sticky="w", padx=15, pady=(10, 2))
        self.lbl_noticias_status = ctk.CTkLabel(
            self.frame_noticias,
            text="Aguardando consulta aos feeds...",
            font=FONTE_PEQUENA,
            text_color=CORES["texto_secundario"],
            anchor="w",
            wraplength=max(400, largura_inicial - 100),
        )
        self.lbl_noticias_status.grid(
            row=1, column=0, sticky="ew", padx=15, pady=(0, 6)
        )
        self.frame_fontes_analistas = ctk.CTkFrame(
            self.frame_noticias, fg_color="transparent"
        )
        self.frame_fontes_analistas.grid(
            row=2, column=0, sticky="ew", padx=12, pady=(0, 8)
        )
        self.frame_fontes_analistas.grid_columnconfigure(0, weight=1)
        self.botoes_fontes_analistas = []
        self.widgets_fontes_analistas = []

        self.frame_controles_backtest = ctk.CTkFrame(
            self.frame_graficos, fg_color="transparent"
        )
        self.frame_controles_backtest.grid(
            row=5, column=0, sticky="ew", padx=15, pady=(0, 10)
        )
        self.frame_controles_backtest.grid_columnconfigure(3, weight=1)
        ctk.CTkLabel(
            self.frame_controles_backtest,
            text="BACKTEST",
            font=FONTE_LABEL,
            text_color=CORES["verde_pastel"],
        ).grid(row=0, column=0, padx=(0, 8))
        self.periodo_backtest = ctk.CTkOptionMenu(
            self.frame_controles_backtest,
            values=["7 dias", "30 dias", "90 dias", "1 ano"],
            width=115,
        )
        self.periodo_backtest.grid(row=0, column=1, padx=(0, 8))
        self.botao_backtest = ctk.CTkButton(
            self.frame_controles_backtest,
            text="Rodar backtest",
            command=self._rodar_backtest,
            width=130,
            fg_color=CORES["verde_matrix"],
            hover_color=CORES["verde_escuro"],
        )
        self.botao_backtest.grid(row=0, column=2, padx=(0, 10))
        self.lbl_backtest_status = ctk.CTkLabel(
            self.frame_controles_backtest,
            text="Simulação inicia com 50% em BTC e 50% em caixa.",
            font=FONTE_PEQUENA,
            text_color=CORES["texto_secundario"],
            anchor="w",
        )
        self.lbl_backtest_status.grid(row=0, column=3, sticky="ew")

        # ----- LINHA 6: Resultado do backtest -----
        self.frame_resultado_backtest = ctk.CTkFrame(
            self.conteudo, fg_color=CORES["bg_card"], corner_radius=10
        )
        self.frame_resultado_backtest.grid(
            row=6, column=0, sticky="ew", padx=20, pady=(0, 10)
        )
        self.frame_resultado_backtest.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self.frame_resultado_backtest,
            text="BACKTEST · ESTRATÉGIA VS. BUY-AND-HOLD",
            font=FONTE_SUBTITULO,
            text_color=CORES["verde_pastel"],
        ).grid(row=0, column=0, sticky="w", padx=15, pady=(10, 0))
        self.fig_backtest, self.ax_backtest = plt.subplots(
            figsize=(11, 2.4), dpi=90
        )
        self.fig_backtest.patch.set_facecolor(CORES["bg_card"])
        self.canvas_backtest = FigureCanvasTkAgg(
            self.fig_backtest, master=self.frame_resultado_backtest
        )
        self.canvas_backtest.get_tk_widget().configure(width=1)
        self.canvas_backtest.get_tk_widget().grid(
            row=1, column=0, sticky="ew", padx=14, pady=(4, 10)
        )
        self.frame_resultado_backtest.grid_remove()

        # ----- LINHA 7: Patrimônio histórico -----
        self.frame_patrimonio = ctk.CTkFrame(
            self.conteudo, fg_color=CORES["bg_card"], corner_radius=10
        )
        self.frame_patrimonio.grid(
            row=7, column=0, sticky="ew", padx=20, pady=(0, 10)
        )
        self.frame_patrimonio.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self.frame_patrimonio,
            text="PATRIMÔNIO AO LONGO DO TEMPO",
            font=FONTE_SUBTITULO,
            text_color=CORES["verde_pastel"],
        ).grid(row=0, column=0, sticky="w", padx=15, pady=(10, 0))
        self.fig_patrimonio, self.ax_patrimonio = plt.subplots(
            figsize=(11, 2.4), dpi=90
        )
        self.fig_patrimonio.patch.set_facecolor(CORES["bg_card"])
        self.canvas_patrimonio = FigureCanvasTkAgg(
            self.fig_patrimonio, master=self.frame_patrimonio
        )
        self.canvas_patrimonio.get_tk_widget().configure(width=1)
        self.canvas_patrimonio.get_tk_widget().grid(
            row=1, column=0, sticky="ew", padx=14, pady=(4, 0)
        )
        self.lbl_patrimonio_status = ctk.CTkLabel(
            self.frame_patrimonio,
            text="A curva será formada conforme novas amostras forem registradas.",
            font=FONTE_PEQUENA,
            text_color=CORES["texto_secundario"],
            anchor="w",
        )
        self.lbl_patrimonio_status.grid(
            row=2, column=0, sticky="ew", padx=16, pady=(0, 8)
        )

        # ----- LINHA 8: Trades + Métricas (lado a lado) -----
        self.frame_baixo = ctk.CTkFrame(self.conteudo, fg_color="transparent")
        self.frame_baixo.grid(row=8, column=0, sticky="nsew", padx=20, pady=10)
        self.frame_baixo.grid_columnconfigure(0, weight=3, uniform="painel")
        self.frame_baixo.grid_columnconfigure(1, weight=2, uniform="painel")
        self.frame_baixo.grid_rowconfigure(0, weight=1)

        # Card trades
        self.card_trades = ctk.CTkFrame(self.frame_baixo, fg_color=CORES["bg_card"], corner_radius=10)
        self.card_trades.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.card_trades.grid_columnconfigure((0, 1), weight=1)
        self.card_trades.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(
            self.card_trades, text="ÚLTIMOS TRADES",
            font=FONTE_SUBTITULO, text_color=CORES["verde_pastel"]
        ).grid(row=0, column=0, sticky="w", padx=15, pady=(10, 5))
        ctk.CTkButton(
            self.card_trades,
            text="Exportar CSV",
            command=self._exportar_trades_csv,
            width=105,
            height=28,
            fg_color=CORES["bg_destaque"],
            hover_color=CORES["verde_escuro"],
        ).grid(row=0, column=1, sticky="e", padx=15, pady=(8, 4))

        self.txt_trades = ctk.CTkTextbox(
            self.card_trades, font=FONTE_PEQUENA,
            fg_color=CORES["bg_secundario"],
            text_color=CORES["texto_primario"],
            border_width=0, wrap="none"
        )
        self.txt_trades.grid(
            row=2, column=0, columnspan=2, sticky="nsew", padx=15, pady=(0, 15)
        )
        self.txt_trades.configure(state="disabled")

        # Card métricas
        self.card_metricas = ctk.CTkFrame(self.frame_baixo, fg_color=CORES["bg_card"], corner_radius=10)
        self.card_metricas.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        self.card_metricas.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            self.card_metricas, text="MÉTRICAS",
            font=FONTE_SUBTITULO, text_color=CORES["verde_pastel"]
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=15, pady=(10, 5))

        self.metricas_labels = {}
        linhas_metricas = [
            ("compras", "Compras"),
            ("vendas", "Vendas"),
            ("taxas", "Taxas pagas"),
            ("depositos", "Depósitos"),
            ("saques", "Saques"),
            ("capital_aportado", "Capital líquido"),
            ("lucro", "Lucro/prejuízo"),
            ("rentabilidade", "Rentabilidade"),
            ("drawdown", "Drawdown máximo"),
            ("sharpe", "Sharpe anualizado"),
            ("win_rate", "Win rate"),
            ("profit_factor", "Profit factor"),
            ("holding_time", "Tempo médio em posição"),
            ("exposure", "Exposição média em BTC"),
        ]
        for r in range(len(linhas_metricas)):
            self.card_metricas.grid_rowconfigure(r + 1, weight=1)

        for i, (chave, rotulo) in enumerate(linhas_metricas, start=1):
            ctk.CTkLabel(
                self.card_metricas, text=f"{rotulo}:",
                font=FONTE_LABEL, text_color=CORES["texto_secundario"]
            ).grid(row=i, column=0, sticky="w", padx=(15, 10), pady=2)
            lbl = ctk.CTkLabel(
                self.card_metricas, text="—",
                font=FONTE_LABEL, text_color=CORES["texto_primario"]
            )
            lbl.grid(row=i, column=1, sticky="w", padx=(0, 15), pady=2)
            self.metricas_labels[chave] = lbl

        # ----- Status -----
        self.lbl_status = ctk.CTkLabel(
            self, text="Iniciando...",
            font=FONTE_PEQUENA, text_color=CORES["texto_apagado"]
        )
        self.lbl_status.grid(row=1, column=0, sticky="ew", padx=16, pady=(4, 8))
        self.bind("<Configure>", self._ajustar_largura_textos, add="+")

        self.protocol("WM_DELETE_WINDOW", self._ao_fechar)
        self.atualizar_gui()

    # -----------------------------------------------------------------
    def _ajustar_largura_textos(self, event):
        if event.widget is not self:
            return
        self.conteudo.configure(
            width=max(400, self.winfo_width() - 16),
            height=max(300, self.winfo_height() - 60),
        )
        largura = max(400, self.winfo_width() - 48)
        largura_grafico = max(480, self.winfo_width() - 104)
        canvas_grafico = self.canvas_graficos.get_tk_widget()
        if int(canvas_grafico.cget("width")) != largura_grafico:
            canvas_grafico.configure(width=largura_grafico)
        for canvas in (self.canvas_backtest, self.canvas_patrimonio):
            widget = canvas.get_tk_widget()
            if int(widget.cget("width")) != largura_grafico:
                widget.configure(width=largura_grafico)
        self.lbl_previsao_status.configure(
            wraplength=max(400, self.conteudo.winfo_width() - 64)
        )
        self.lbl_modelo_status.configure(
            wraplength=max(400, self.conteudo.winfo_width() - 64)
        )
        self.lbl_noticias_status.configure(
            wraplength=max(400, self.conteudo.winfo_width() - 64)
        )
        self.lbl_status.configure(wraplength=largura)

    def _criar_card(self, parent, titulo, valor, variacao):
        card = ctk.CTkFrame(parent, fg_color=CORES["bg_card"], corner_radius=10)
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            card, text=titulo, font=FONTE_LABEL, text_color=CORES["verde_pastel"]
        ).grid(row=0, column=0, sticky="w", padx=15, pady=(10, 0))

        lbl_valor = ctk.CTkLabel(
            card, text=valor, font=FONTE_VALOR, text_color=CORES["texto_primario"]
        )
        lbl_valor.grid(row=1, column=0, sticky="w", padx=15, pady=(0, 0))

        lbl_var = ctk.CTkLabel(
            card, text=variacao, font=FONTE_LABEL, text_color=CORES["texto_secundario"]
        )
        lbl_var.grid(row=2, column=0, sticky="w", padx=15, pady=(0, 10))

        card.lbl_valor = lbl_valor
        card.lbl_var = lbl_var
        return card

    def _criar_label_posicao(self, parent, titulo, valor, row, col):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid(row=row + 1, column=col, sticky="w", padx=15, pady=(0, 15))

        ctk.CTkLabel(
            frame, text=titulo, font=FONTE_PEQUENA, text_color=CORES["texto_secundario"]
        ).pack(anchor="w")

        lbl = ctk.CTkLabel(
            frame, text=valor, font=FONTE_VALOR_MEDIO, text_color=CORES["texto_primario"]
        )
        lbl.pack(anchor="w")
        return lbl

    def _ao_fechar(self):
        if self._closing:
            return
        self._closing = True
        for callback_id in (self._after_id, self._toast_after_id):
            if callback_id is not None:
                try:
                    self.after_cancel(callback_id)
                except (RuntimeError, TclError):
                    pass
        self._after_id = None
        self._toast_after_id = None
        self.motor.parar()
        plt.close(self.fig)
        plt.close(self.fig_backtest)
        plt.close(self.fig_patrimonio)
        self.destroy()

    def _exportar_trades_csv(self):
        destino = filedialog.asksaveasfilename(
            parent=self,
            title="Exportar histórico de trades",
            defaultextension=".csv",
            filetypes=[("Arquivo CSV", "*.csv")],
            initialfile="trades_btc.csv",
        )
        if not destino:
            return
        try:
            with self.motor.lock:
                csv_texto = gerar_csv_trades(list(self.motor.historico))
            with open(destino, "w", encoding="utf-8-sig", newline="") as arquivo:
                arquivo.write(csv_texto)
        except OSError as exc:
            messagebox.showerror(
                "Não foi possível exportar", str(exc), parent=self
            )
            return
        messagebox.showinfo(
            "Exportação concluída",
            f"Trades exportados para:\n{destino}",
            parent=self,
        )

    def _rodar_backtest(self):
        if self._backtest_em_execucao:
            return
        with self.motor.lock:
            preco = (
                self.motor.preco_atual
                if self.motor.preco_atualizado else None
            )
            capital = (
                self.motor.estado.capital_brl
                + self.motor.estado.capital_btc * preco
                if preco is not None else 0.0
            )
        if capital <= 0:
            self.lbl_backtest_status.configure(
                text="Backtest indisponível: aguarde cotação online e patrimônio válido.",
                text_color=CORES["amarelo_pastel"],
            )
            return

        periodo_texto = self.periodo_backtest.get()
        dias = {"7 dias": 7, "30 dias": 30, "90 dias": 90, "1 ano": 365}.get(
            periodo_texto
        )
        if dias is None:
            self.lbl_backtest_status.configure(
                text="Selecione um período de backtest válido.",
                text_color=CORES["amarelo_pastel"],
            )
            return

        self._backtest_em_execucao = True
        self.botao_backtest.configure(state="disabled")
        self.lbl_backtest_status.configure(
            text=f"Buscando {periodo_texto} de dados históricos na CoinGecko...",
            text_color=CORES["texto_secundario"],
        )

        def trabalho():
            try:
                history = buscar_historico_btc_brl_periodo(dias)
                result = simular_backtest(history, capital)
            except (
                RequestException, OSError, RuntimeError, ValueError,
                KeyError, TypeError, IndexError,
            ) as exc:
                self._backtest_resultados.put((None, str(exc), periodo_texto))
            else:
                self._backtest_resultados.put((result, "", periodo_texto))

        threading.Thread(target=trabalho, daemon=True).start()

    def _finalizar_backtest(self, resultado, erro: str, periodo: str):
        self._backtest_em_execucao = False
        self.botao_backtest.configure(state="normal")
        if erro:
            self.lbl_backtest_status.configure(
                text=f"Backtest não concluído: {erro}",
                text_color=CORES["amarelo_pastel"],
            )
            return
        self.frame_resultado_backtest.grid()
        pontos = resultado["points"]
        datas = [self._para_horario_local(p["timestamp"]) for p in pontos]
        estrategia = [p["strategy"] for p in pontos]
        buy_hold = [p["buy_hold"] for p in pontos]
        eixo = self.ax_backtest
        eixo.clear()
        eixo.set_facecolor(CORES["bg_secundario"])
        eixo.tick_params(colors=CORES["texto_secundario"], labelsize=8)
        eixo.grid(True, alpha=0.16, color=CORES["verde_matrix"])
        eixo.plot(datas, estrategia, color=CORES["verde_matrix"], label="Estratégia")
        eixo.plot(
            datas, buy_hold, color=CORES["azul_pastel"],
            linestyle="--", label="Buy-and-hold",
        )
        eixo.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m", tz=FUSO_LOCAL))
        eixo.xaxis.set_major_locator(
            mdates.AutoDateLocator(minticks=4, maxticks=8, tz=FUSO_LOCAL)
        )
        eixo.yaxis.set_major_formatter(
            FuncFormatter(lambda value, pos: f"R$ {value / 1000:.0f} mil")
        )
        eixo.legend(
            loc="upper left", fontsize=8, frameon=False,
            labelcolor=CORES["texto_secundario"],
        )
        for spine in eixo.spines.values():
            spine.set_color(CORES["verde_escuro"])
        self.fig_backtest.subplots_adjust(left=0.08, right=0.99, top=0.95, bottom=0.18)
        self.canvas_backtest.draw_idle()
        diferenca = resultado["final_strategy_brl"] - resultado["final_buy_hold_brl"]
        self.lbl_backtest_status.configure(
            text=(
                f"{periodo}: estratégia R$ {formatar_reais(resultado['final_strategy_brl'])} "
                f"({resultado['strategy_return_pct']:+.2f}%) · "
                f"buy-and-hold R$ {formatar_reais(resultado['final_buy_hold_brl'])} "
                f"({resultado['buy_hold_return_pct']:+.2f}%) · "
                f"diferença R$ {formatar_reais(diferenca, sinal=True)} · "
                f"{resultado['trade_count']} operações. "
                "Capital simulado 50% BTC/50% caixa; resultado passado não prevê retorno."
            ),
            text_color=CORES["texto_secundario"],
        )

    def _mostrar_notificacao_trade(self, trade: dict):
        if not hasattr(self, "lbl_toast"):
            self.lbl_toast = ctk.CTkLabel(
                self,
                text="",
                fg_color=CORES["bg_destaque"],
                text_color=CORES["verde_pastel"],
                corner_radius=8,
                font=FONTE_LABEL,
            )
        tipo = str(trade.get("tipo", "trade")).upper()
        valor = float(trade.get("valor_brl", 0) or 0)
        motivo = str(trade.get("motivo", "")).replace("_", " ")
        self.lbl_toast.configure(
            text=f"  🔔 {tipo} automática · {motivo} · R$ {formatar_reais(valor)}  "
        )
        self.lbl_toast.place(relx=0.98, rely=0.02, anchor="ne")
        self.bell()
        if self._toast_after_id is not None:
            self.after_cancel(self._toast_after_id)
        self._toast_after_id = self.after(7000, self.lbl_toast.place_forget)

    def _criar_link_noticia(self, parent, text: str, url: str, index: int):
        label = ctk.CTkLabel(
            parent,
            text=text,
            anchor="w",
            justify="left",
            height=58,
            wraplength=max(400, self.conteudo.winfo_width() - 110),
            fg_color=CORES["bg_secundario"],
            corner_radius=6,
            text_color=CORES["azul_pastel"],
            font=FONTE_PEQUENA,
            cursor="hand2",
        )
        label.grid(row=index, column=0, sticky="ew", padx=3, pady=(0, 5))
        label.bind(
            "<Button-1>",
            lambda _event, article_url=url: webbrowser.open(article_url),
        )
        return label

    @staticmethod
    def _ler_numero(texto: str) -> float:
        texto = texto.strip().replace("R$", "").replace(" ", "")
        if "," in texto:
            texto = texto.replace(".", "").replace(",", ".")
        valor = float(texto)
        if not isfinite(valor):
            raise ValueError("Informe um número válido.")
        return valor

    def _comprar_manual(self):
        try:
            valor = self._ler_numero(self.entrada_compra.get())
            trade = self.motor.comprar_manual(valor)
            self.entrada_compra.delete(0, "end")
            messagebox.showinfo(
                "Compra simulada",
                f"Compra de R$ {formatar_reais(trade.valor_brl)} registrada "
                f"a R$ {formatar_reais(trade.preco_brl)}/BTC.",
                parent=self,
            )
        except (OSError, ValueError, RuntimeError) as e:
            messagebox.showerror("Não foi possível comprar", str(e), parent=self)

    def _vender_manual(self):
        try:
            quantidade = self._ler_numero(self.entrada_venda.get())
            trade = self.motor.vender_manual(quantidade)
            self.entrada_venda.delete(0, "end")
            messagebox.showinfo(
                "Venda simulada",
                f"Venda de {trade.quantidade_btc:.8f} BTC registrada "
                f"a R$ {formatar_reais(trade.preco_brl)}/BTC.",
                parent=self,
            )
        except (OSError, ValueError, RuntimeError) as e:
            messagebox.showerror("Não foi possível vender", str(e), parent=self)

    def _depositar_manual(self):
        try:
            valor = self._ler_numero(self.entrada_deposito.get())
            evento = self.motor.depositar_manual(valor)
            self.entrada_deposito.delete(0, "end")
            messagebox.showinfo(
                "Depósito registrado",
                f"Depósito de R$ {formatar_reais(evento.valor_brl)} registrado.",
                parent=self,
            )
        except (OSError, ValueError) as e:
            messagebox.showerror("Não foi possível depositar", str(e), parent=self)

    def _sacar_manual(self):
        try:
            valor = self._ler_numero(self.entrada_saque.get())
            evento = self.motor.sacar_manual(valor)
            self.entrada_saque.delete(0, "end")
            messagebox.showinfo(
                "Saque registrado",
                f"Saque de R$ {formatar_reais(evento.valor_brl)} registrado.",
                parent=self,
            )
        except (OSError, ValueError) as e:
            messagebox.showerror("Não foi possível sacar", str(e), parent=self)

    @staticmethod
    def _para_horario_local(timestamp: str | datetime) -> datetime:
        dt = (
            timestamp
            if isinstance(timestamp, datetime)
            else datetime.fromisoformat(timestamp)
        )
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(FUSO_LOCAL)

    # -----------------------------------------------------------------
    def atualizar_gui(self):
        if self._closing or not self.winfo_exists():
            return

        while True:
            try:
                resultado, erro, periodo = self._backtest_resultados.get_nowait()
            except queue.Empty:
                break
            self._finalizar_backtest(resultado, erro, periodo)

        if not self.motor.lock.acquire(timeout=0.05):
            self._after_id = self.after(100, self.atualizar_gui)
            return
        try:
            estado = self.motor.estado
            historico = list(self.motor.historico)
            fluxo_caixa = list(self.motor.fluxo_caixa)
            equity_history = list(self.motor.equity_history)
            erro_patrimonio = self.motor.erro_patrimonio
            erro_persistencia = self.motor.erro_persistencia
            preco = self.motor.preco_atual
            ultimo_update = self.motor.ultimo_update
            fonte_preco = self.motor.fonte_preco
            preco_atualizado = self.motor.preco_atualizado
            ultimo_erro = self.motor.ultimo_erro
            historico_cotacao = list(self.motor.historico_cotacao)
            previsao_dia = list(self.motor.previsao_dia)
            sentimento_analistas = self.motor.sentimento_analistas
            artigos_analistas = list(self.motor.artigos_analistas)
            atualizado_mercado = self.motor.atualizado_mercado
            atualizado_analistas = self.motor.atualizado_analistas
            erro_analise = self.motor.erro_analise
        finally:
            self.motor.lock.release()

        if preco and estado:
            self._atualizar_tela(
                estado, historico, preco, fonte_preco, fluxo_caixa, equity_history
            )
            novas_operacoes = historico[self._notified_trade_count:]
            for trade in novas_operacoes:
                if trade.get("motivo") not in ("manual", "aporte"):
                    self._mostrar_notificacao_trade(trade)
            self._notified_trade_count = len(historico)
            ts = (
                self._para_horario_local(ultimo_update).strftime("%d/%m %H:%M:%S")
                if ultimo_update else "—"
            )
            if preco_atualizado:
                status = f"● Cotação atualizada: {ts} (Carambeí)  |  Fonte: {fonte_preco}"
                cor_status = CORES["verde_matrix"]
            else:
                status = f"⚠ Cotação em cache de {ts} (Carambeí)  |  Operações bloqueadas"
                cor_status = CORES["amarelo_pastel"]
            if erro_patrimonio:
                status += f"  |  Erro ao salvar curva: {erro_patrimonio}"
                cor_status = CORES["amarelo_pastel"]
            if erro_persistencia:
                status += f"  |  {erro_persistencia}"
                cor_status = CORES["amarelo_pastel"]
            self.lbl_status.configure(text=status, text_color=cor_status)
        else:
            self.lbl_status.configure(
                text=f"⏳ Aguardando cotação online. {ultimo_erro}",
                text_color=CORES["amarelo_pastel"]
            )

        chave_graficos = (
            ultimo_update,
            atualizado_mercado,
            atualizado_analistas,
            len(historico_cotacao),
            len(previsao_dia),
            len(artigos_analistas),
            erro_analise,
        )
        if chave_graficos != self._ultimo_grafico_renderizado:
            self._atualizar_graficos(
                historico_cotacao,
                previsao_dia,
                artigos_analistas,
                sentimento_analistas,
                preco,
                atualizado_mercado,
                atualizado_analistas,
                erro_analise,
            )
            self._ultimo_grafico_renderizado = chave_graficos

        chave_patrimonio = (
            len(equity_history),
            equity_history[-1].get("timestamp") if equity_history else None,
            equity_history[-1].get("equity") if equity_history else None,
        )
        if chave_patrimonio != self._ultimo_patrimonio_renderizado:
            self._atualizar_grafico_patrimonio(equity_history, historico)
            self._ultimo_patrimonio_renderizado = chave_patrimonio

        if not self._closing:
            self._after_id = self.after(2000, self.atualizar_gui)

    def _atualizar_graficos(
        self,
        historico,
        previsao,
        artigos,
        sentimento,
        preco_atual,
        atualizado_mercado,
        atualizado_analistas,
        erro,
    ):
        ax_real, ax_previsao = self.axes
        for ax in self.axes:
            ax.clear()
            ax.set_facecolor(CORES["bg_secundario"])
            ax.tick_params(colors=CORES["texto_secundario"], labelsize=8)
            ax.grid(True, alpha=0.16, color=CORES["verde_matrix"])
            ax.yaxis.set_major_formatter(
                FuncFormatter(
                    lambda valor, pos: (
                        f"R$ {valor / 1000:.0f} mil" if pos is not None else ""
                    )
                )
            )
            ax.xaxis.set_major_formatter(
                mdates.DateFormatter("%H:%M", tz=FUSO_LOCAL)
            )
            ax.xaxis.set_major_locator(
                mdates.AutoDateLocator(minticks=3, maxticks=4, tz=FUSO_LOCAL)
            )
            ax.yaxis.set_major_locator(MaxNLocator(4))
            for spine in ax.spines.values():
                spine.set_color(CORES["verde_escuro"])

        ax_real.set_title(
            "COTAÇÃO REAL · ÚLTIMAS 24H",
            color=CORES["verde_pastel"], fontsize=10, loc="left",
        )
        if historico:
            horarios = [ts.astimezone(FUSO_LOCAL) for ts, _ in historico]
            valores = [valor for _, valor in historico]
            ax_real.plot(
                horarios, valores, color=CORES["verde_matrix"],
                linewidth=1.8, label="BTC/BRL",
            )
            ax_real.fill_between(
                horarios, valores, min(valores),
                color=CORES["verde_matrix"], alpha=0.10,
            )
            if preco_atual is not None:
                agora_local = datetime.now(FUSO_LOCAL)
                ax_real.scatter(
                    [agora_local], [preco_atual], color=CORES["amarelo_pastel"],
                    s=24, zorder=3, label="último preço",
                )
        else:
            ax_real.text(
                0.5, 0.5, "Histórico indisponível",
                ha="center", va="center", color=CORES["texto_apagado"],
                transform=ax_real.transAxes,
            )

        ax_previsao.set_title(
            "CENÁRIO ESTIMADO · PRÓXIMAS 24H",
            color=CORES["verde_pastel"], fontsize=10, loc="left", pad=9,
        )
        if previsao:
            previsao_local = [p.timestamp.astimezone(FUSO_LOCAL) for p in previsao]
            esperados = [p.expected_brl for p in previsao]
            minimos = [p.low_brl for p in previsao]
            maximos = [p.high_brl for p in previsao]

            if historico:
                contexto = [
                    (ts.astimezone(FUSO_LOCAL), valor)
                    for ts, valor in historico[-6:]
                ]
                ax_previsao.plot(
                    [item[0] for item in contexto],
                    [item[1] for item in contexto],
                    color=CORES["texto_secundario"],
                    linewidth=1.5,
                    linestyle=":",
                    label="real recente",
                    zorder=2,
                )
            ax_previsao.fill_between(
                previsao_local, minimos, maximos,
                color=CORES["azul_pastel"],
                alpha=0.20,
                label="faixa estimada",
                zorder=1,
            )
            preco_final = esperados[-1]
            variacao = (
                (preco_final / preco_atual - 1) * 100
                if preco_atual else 0.0
            )
            cor_cenario = (
                CORES["lucro"] if variacao > 0
                else CORES["prejuizo"] if variacao < 0
                else CORES["azul_pastel"]
            )
            ax_previsao.plot(
                previsao_local, esperados, color=cor_cenario,
                linewidth=2.4, label="cenário central",
                zorder=3,
            )
            inicio = previsao_local[0]
            fim = previsao_local[-1]
            ax_previsao.axvline(
                inicio, color=CORES["amarelo_pastel"],
                linewidth=1, linestyle="--", alpha=0.8,
            )
            if preco_atual is not None:
                ax_previsao.scatter(
                    [inicio], [preco_atual], color=CORES["amarelo_pastel"],
                    edgecolors=CORES["bg_secundario"], linewidths=0.7,
                    s=42, zorder=5, label="agora",
                )
            ax_previsao.scatter(
                [fim], [preco_final], color=cor_cenario, s=34,
                edgecolors=CORES["bg_secundario"], linewidths=0.7,
                zorder=5,
            )
            ax_previsao.annotate(
                f"24h  {variacao:+.1f}%",
                xy=(fim, preco_final),
                xytext=(-8, 10 if variacao >= 0 else -16),
                textcoords="offset points",
                ha="right",
                color=cor_cenario,
                fontsize=8,
                fontweight="bold",
            )
            inicio_contexto = (
                contexto[0][0]
                if historico and contexto else inicio
            )
            ax_previsao.set_xlim(inicio_contexto, fim)
            ax_previsao.xaxis.set_major_formatter(
                mdates.DateFormatter("%Hh", tz=FUSO_LOCAL)
            )
            ax_previsao.xaxis.set_major_locator(
                mdates.HourLocator(interval=6, tz=FUSO_LOCAL)
            )
            ax_previsao.set_xlabel(
                "horário local", color=CORES["texto_secundario"], fontsize=8
            )
            ax_previsao.legend(
                loc="upper left", ncol=1, fontsize=7, frameon=False,
                labelcolor=CORES["texto_secundario"],
            )
            ax_previsao.margins(y=0.18)
            eixo_previsao_status = (
                f"CENÁRIO CENTRAL EM 24H: R$ {formatar_reais(preco_final)} "
                f"({variacao:+.2f}%)   ·   "
                f"FAIXA MODELADA: R$ {formatar_reais(minimos[-1])}–"
                f"R$ {formatar_reais(maximos[-1])}"
            )
            cor_previsao_status = cor_cenario
            faixa_total_pct = (
                (maximos[-1] - minimos[-1]) / preco_atual * 100
                if preco_atual else 0.0
            )
            detalhe_cenario = (
                f"Referência atual: R$ {formatar_reais(preco_atual or 0)}  ·  "
                f"A faixa tem amplitude de {faixa_total_pct:.2f}% do preço atual; "
                "não representa probabilidade nem garantia."
            )
        else:
            ax_previsao.text(
                0.5, 0.5,
                "Ainda não há dados horários suficientes\npara estimar as próximas 24h.",
                ha="center", va="center", color=CORES["texto_apagado"],
                transform=ax_previsao.transAxes,
            )
            eixo_previsao_status = "CENÁRIO DE 24H INDISPONÍVEL"
            cor_previsao_status = CORES["amarelo_pastel"]
            detalhe_cenario = (
                "São necessárias ao menos 6 variações horárias válidas. "
                "O preço observado continua disponível no gráfico à esquerda."
            )

        self.fig.subplots_adjust(
            left=0.08, right=0.99, top=0.88, bottom=0.20, wspace=0.16
        )
        self.canvas_graficos.draw_idle()

        if atualizado_analistas:
            horario_analistas = atualizado_analistas.astimezone(
                FUSO_LOCAL
            ).strftime("%d/%m %H:%M")
        else:
            horario_analistas = "sem atualização"
        if atualizado_mercado:
            horario_mercado = atualizado_mercado.astimezone(
                FUSO_LOCAL
            ).strftime("%d/%m %H:%M")
        else:
            horario_mercado = "sem histórico"
        direcao = "positivo" if sentimento > 0.05 else "negativo" if sentimento < -0.05 else "neutro"
        analises_direcionais = sum(
            article.analyst_view and article.sentiment != 0
            for article in artigos
        )
        sentimento_texto = (
            f"sinal das análises de analistas {direcao} ({sentimento:+.2f}, "
            f"{analises_direcionais} matérias direcionais; {horario_analistas})"
        )
        self.lbl_noticias_status.configure(
            text=(
                f"{len(artigos)} notícia(s) recente(s) sobre BTC · "
                f"{analises_direcionais} análise(s) direcional(is) de analistas · "
                f"sinal agregado {direcao} ({sentimento:+.2f}) · "
                f"atualizado: {horario_analistas}. "
                "Amostra limitada aos feeds e últimas 24h; leitura lexical, não consenso."
                + (f" Aviso da consulta: {erro}" if erro and not artigos else "")
            ),
            text_color=(
                CORES["lucro"] if sentimento > 0.05
                else CORES["prejuizo"] if sentimento < -0.05
                else CORES["texto_secundario"]
            ),
        )
        self.lbl_previsao_status.configure(
            text=eixo_previsao_status,
            text_color=cor_previsao_status,
        )
        self.lbl_modelo_status.configure(
            text=(
                f"{detalhe_cenario} Base: momentum das últimas 6h + "
                f"volatilidade recente; {sentimento_texto}. "
                f"Dados de mercado atualizados: {horario_mercado}. "
                "Estimativa experimental, não recomendação."
                + (f" Aviso: {erro}" if erro else "")
            ),
            text_color=CORES["texto_secundario"],
        )

        for widget in self.widgets_fontes_analistas:
            widget.destroy()
        self.widgets_fontes_analistas.clear()
        self.botoes_fontes_analistas.clear()
        if not artigos:
            mensagem_fontes = ctk.CTkLabel(
                self.frame_fontes_analistas,
                text=(
                    "Nenhuma notícia recente sobre BTC foi encontrada nos feeds. "
                    "Eles são consultados periodicamente."
                ),
                font=FONTE_PEQUENA,
                text_color=CORES["texto_apagado"],
                anchor="w",
            )
            mensagem_fontes.grid(
                row=0, column=0, sticky="ew", padx=5
            )
            self.widgets_fontes_analistas.append(mensagem_fontes)
        else:
            for index, article in enumerate(artigos[:5]):
                publicado = article.published_at.astimezone(
                    FUSO_LOCAL
                ).strftime("%d/%m %H:%M")
                if article.analyst_view and article.sentiment:
                    classificacao = (
                        "ANÁLISE DE ANALISTA · "
                        f"tom lexical {article.sentiment:+.2f}"
                    )
                elif article.sentiment:
                    classificacao = (
                        "NOTÍCIA BTC · tendência lexical "
                        f"{'positiva' if article.sentiment > 0 else 'negativa'}"
                    )
                else:
                    classificacao = "NOTÍCIA BTC · sem sinal direcional identificado"
                texto = (
                    f"{classificacao} · {article.publisher} · {publicado}\n"
                    f"{article.title}\n"
                    f"{article.author} · Abrir matéria"
                )
                link = self._criar_link_noticia(
                    self.frame_fontes_analistas, texto, article.url, index
                )
                self.botoes_fontes_analistas.append(link)
                self.widgets_fontes_analistas.append(link)

    def _atualizar_tela(
        self, estado, historico, preco, fonte_preco, fluxo_caixa, equity_history
    ):
        capital_brl = estado.capital_brl
        capital_btc = estado.capital_btc
        preco_medio = estado.preco_medio_brl
        depositos = sum(
            float(item["valor_brl"])
            for item in fluxo_caixa if item["tipo"] == "DEPOSITO"
        )
        saques = sum(
            float(item["valor_brl"])
            for item in fluxo_caixa if item["tipo"] == "SAQUE"
        )
        capital_aportado = estado.patrimonio_inicial_brl + depositos - saques

        valor_btc = capital_btc * preco
        patrimonio = capital_brl + valor_btc
        lucro = patrimonio - capital_aportado
        lucro_pct = (lucro / capital_aportado * 100) if capital_aportado > 0 else 0

        self.card_preco.lbl_valor.configure(
            text=f"R$ {formatar_reais(preco)}"
        )
        self.card_preco.lbl_var.configure(
            text=f"fonte: {fonte_preco}", text_color=CORES["texto_secundario"]
        )

        cor_patrimonio = CORES["lucro"] if lucro >= 0 else CORES["prejuizo"]
        self.card_patrimonio.lbl_valor.configure(
            text=f"R$ {formatar_reais(patrimonio)}",
            text_color=CORES["texto_primario"]
        )
        self.card_patrimonio.lbl_var.configure(
            text=f"{'▲' if lucro >= 0 else '▼'} {lucro_pct:+.2f}%  "
                 f"(R$ {formatar_reais(lucro, sinal=True)})",
            text_color=cor_patrimonio
        )

        self.lbl_brl.configure(
            text=f"R$ {formatar_reais(capital_brl)}"
        )
        self.lbl_btc.configure(text=f"{capital_btc:.8f}")

        if preco_medio > 0:
            var_pm = (preco - preco_medio) / preco_medio * 100
            cor_pm = CORES["lucro"] if var_pm >= 0 else CORES["prejuizo"]
            self.lbl_preco_medio.configure(
                text=f"R$ {formatar_reais(preco_medio)}",
                text_color=CORES["texto_primario"]
            )
            self.lbl_variacao.configure(text=f"{var_pm:+.2f}%", text_color=cor_pm)
        else:
            self.lbl_preco_medio.configure(text="—", text_color=CORES["texto_primario"])
            self.lbl_variacao.configure(text="—", text_color=CORES["texto_primario"])

        self.metricas_labels["compras"].configure(text=str(estado.total_compras))
        self.metricas_labels["vendas"].configure(text=str(estado.total_vendas))
        self.metricas_labels["taxas"].configure(
            text=f"R$ {formatar_reais(estado.total_taxas_brl)}"
        )
        self.metricas_labels["depositos"].configure(
            text=f"R$ {formatar_reais(depositos)}"
        )
        self.metricas_labels["saques"].configure(
            text=f"R$ {formatar_reais(saques)}"
        )
        self.metricas_labels["capital_aportado"].configure(
            text=f"R$ {formatar_reais(capital_aportado)}"
        )
        self.metricas_labels["lucro"].configure(
            text=f"R$ {formatar_reais(lucro, sinal=True)}",
            text_color=CORES["lucro"] if lucro >= 0 else CORES["prejuizo"]
        )
        self.metricas_labels["rentabilidade"].configure(
            text=f"{lucro_pct:+.2f}%",
            text_color=CORES["lucro"] if lucro_pct >= 0 else CORES["prejuizo"]
        )
        metricas_key = (
            len(historico),
            historico[-1].get("timestamp") if historico else None,
            len(equity_history),
            equity_history[-1].get("timestamp") if equity_history else None,
            equity_history[-1].get("nav") if equity_history else None,
        )
        if metricas_key != self._metricas_cache_key:
            self._metricas_cache = calcular_metricas_risco(
                historico, equity_history
            )
            self._metricas_cache_key = metricas_key
        metricas = self._metricas_cache
        drawdown = metricas["max_drawdown_pct"]
        sharpe = metricas["sharpe_ratio"]
        win_rate = metricas["win_rate_pct"]
        profit_factor = metricas["profit_factor"]
        holding_hours = metricas["average_holding_hours"]
        exposure = metricas["average_exposure_pct"]
        self.metricas_labels["drawdown"].configure(
            text=f"{drawdown:.2f}%" if drawdown is not None else "—"
        )
        self.metricas_labels["sharpe"].configure(
            text=f"{sharpe:.2f}" if sharpe is not None else "—"
        )
        self.metricas_labels["win_rate"].configure(
            text=f"{win_rate:.1f}%" if win_rate is not None else "—"
        )
        self.metricas_labels["profit_factor"].configure(
            text=(
                "∞" if profit_factor == float("inf")
                else f"{profit_factor:.2f}" if profit_factor is not None else "—"
            )
        )
        self.metricas_labels["holding_time"].configure(
            text=(
                f"{holding_hours / 24:.1f} dias"
                if holding_hours is not None else "—"
            )
        )
        self.metricas_labels["exposure"].configure(
            text=f"{exposure:.1f}%" if exposure is not None else "—"
        )

        self._atualizar_trades(historico)

    def _atualizar_grafico_patrimonio(self, equity_history, historico):
        eixo = self.ax_patrimonio
        eixo.clear()
        eixo.set_facecolor(CORES["bg_secundario"])
        eixo.tick_params(colors=CORES["texto_secundario"], labelsize=8)
        eixo.grid(True, alpha=0.16, color=CORES["verde_matrix"])
        for spine in eixo.spines.values():
            spine.set_color(CORES["verde_escuro"])

        if not equity_history:
            eixo.text(
                0.5, 0.5, "Curva começa a ser registrada a partir da primeira cotação.",
                ha="center", va="center", color=CORES["texto_apagado"],
                transform=eixo.transAxes,
            )
            self.lbl_patrimonio_status.configure(
                text="Sem amostras registradas ainda; a primeira será salva na próxima cotação."
            )
        else:
            timestamps = [
                self._para_horario_local(point["timestamp"])
                for point in equity_history
            ]
            values = [float(point["equity"]) for point in equity_history]
            benchmark = [float(point["benchmark"]) for point in equity_history]
            eixo.plot(
                timestamps, values, color=CORES["verde_matrix"],
                linewidth=1.8, marker="o",
                markevery=max(1, len(timestamps) // 20),
                markersize=7 if len(timestamps) == 1 else 3.5,
                label="Estratégia",
            )
            eixo.plot(
                timestamps, benchmark, color=CORES["azul_pastel"],
                linestyle="--", linewidth=1.4, marker="s",
                markevery=max(1, len(timestamps) // 20),
                markersize=6 if len(timestamps) == 1 else 3,
                label="Buy-and-hold BTC",
            )
            if len(timestamps) == 1:
                eixo.set_xlim(
                    timestamps[0] - timedelta(hours=6),
                    timestamps[0] + timedelta(hours=6),
                )
                eixo.xaxis.set_major_formatter(
                    mdates.DateFormatter("%H:%M", tz=FUSO_LOCAL)
                )
                eixo.xaxis.set_major_locator(
                    mdates.HourLocator(interval=3, tz=FUSO_LOCAL)
                )
                eixo.annotate(
                    f"1ª amostra · {timestamps[0]:%d/%m %H:%M}",
                    xy=(timestamps[0], values[0]),
                    xytext=(8, 12),
                    textcoords="offset points",
                    color=CORES["verde_pastel"],
                    fontsize=8,
                )
                self.lbl_patrimonio_status.configure(
                    text=(
                        f"1 amostra registrada em {timestamps[0]:%d/%m %H:%M}. "
                        "A linha evolutiva ficará visível após as próximas atualizações horárias."
                    )
                )
            else:
                self.lbl_patrimonio_status.configure(
                    text=(
                        f"{len(timestamps)} amostras · de "
                        f"{timestamps[0]:%d/%m %H:%M} a "
                        f"{timestamps[-1]:%d/%m %H:%M} · registro horário"
                    )
                )
            for trade in historico:
                try:
                    trade_time = self._para_horario_local(trade["timestamp"])
                    index = bisect_left(timestamps, trade_time)
                    if index >= len(timestamps):
                        index = len(timestamps) - 1
                    elif index > 0 and (
                        trade_time - timestamps[index - 1]
                    ) <= (timestamps[index] - trade_time):
                        index -= 1
                except (KeyError, TypeError, ValueError):
                    continue
                if abs((timestamps[index] - trade_time).total_seconds()) > 3600:
                    continue
                is_buy = str(trade.get("tipo", "")).upper() == "COMPRA"
                eixo.scatter(
                    [timestamps[index]], [values[index]],
                    marker="^" if is_buy else "v",
                    color=CORES["verde_matrix"] if is_buy else CORES["prejuizo"],
                    s=32, zorder=4,
                )
            if len(timestamps) > 1:
                eixo.xaxis.set_major_formatter(
                    mdates.DateFormatter("%d/%m %Hh", tz=FUSO_LOCAL)
                )
                eixo.xaxis.set_major_locator(
                    mdates.AutoDateLocator(minticks=4, maxticks=8, tz=FUSO_LOCAL)
                )
            eixo.yaxis.set_major_formatter(
                FuncFormatter(lambda value, pos: f"R$ {value / 1000:.0f} mil")
            )
            eixo.legend(
                loc="upper left", fontsize=8, frameon=False,
                labelcolor=CORES["texto_secundario"],
            )
            self.fig_patrimonio.subplots_adjust(
                left=0.08, right=0.99, top=0.95, bottom=0.18
            )
        self.canvas_patrimonio.draw_idle()

    def _atualizar_trades(self, historico):
        self.txt_trades.configure(state="normal")
        self.txt_trades.delete("1.0", "end")

        if not historico:
            self.txt_trades.insert("end", "Nenhum trade ainda.\n")
        else:
            for t in historico[-15:][::-1]:
                tipo = t.get("tipo", "?")
                preco = float(t.get("preco_brl", 0) or 0)
                valor = float(t.get("valor_brl", 0) or 0)
                motivo = str(t.get("motivo", "")).strip() or "-"
                ts_raw = str(t.get("timestamp", "")).strip()
                try:
                    ts = self._para_horario_local(ts_raw).strftime("%d/%m %H:%M:%S")
                except ValueError:
                    ts = "-"

                sinal = "[+]" if tipo == "COMPRA" else "[-]"
                self.txt_trades.insert(
                    "end",
                    f"{sinal} {ts:<19}  {motivo:<14}  "
                    f"R$ {formatar_reais(valor):>10}  @ R$ {formatar_reais(preco)}\n"
                )

        self.txt_trades.configure(state="disabled")

# =========================================================================
# PONTO DE ENTRADA
# =========================================================================

if __name__ == "__main__":
    app = AppBTC()
    app.mainloop()