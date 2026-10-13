"""
BTC Paper Trader — GUI integrada com o simulador.
O simulador roda em thread separada; a GUI atualiza a cada 2 segundos.
Tudo em um único processo: `python main.py`
"""

import json
import os
import threading
import time
from datetime import datetime, timezone
from functools import partial
from math import isfinite
from zoneinfo import ZoneInfo
from tkinter import messagebox
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
    buscar_sentimento_analistas,
    calcular_previsao,
)
from models import Estado
from simulator import executar_compra, executar_venda, executar_venda_quantidade
from strategy import decidir_acao
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
        self.estado = self._carregar_estado()
        self.historico = self._carregar_historico()
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
                    return Estado.from_dict(json.load(f))
            except (json.JSONDecodeError, OSError, TypeError):
                pass
        estado = Estado()
        estado.capital_brl = CONFIG["capital_inicial_brl"]
        estado.patrimonio_inicial_brl = CONFIG["capital_inicial_brl"]
        return estado

    def _carregar_historico(self):
        if os.path.exists(CONFIG["history_file"]):
            try:
                with open(CONFIG["history_file"], "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return []

    def _salvar_estado(self):
        with open(CONFIG["state_file"], "w", encoding="utf-8") as f:
            json.dump(self.estado.to_dict(), f, indent=2)

    def _salvar_historico(self):
        with open(CONFIG["history_file"], "w", encoding="utf-8") as f:
            json.dump(self.historico, f, indent=2, ensure_ascii=False)

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
            preco = self._validar_cotacao_para_operacao()
            if not isfinite(valor_brl) or valor_brl <= 0:
                raise ValueError("Informe um valor de compra maior que zero.")
            if valor_brl > self.estado.capital_brl:
                raise ValueError("O valor da compra excede seu saldo em reais.")
            trade = executar_compra(self.estado, preco, valor_brl, "manual")
            self._registrar_trade(trade)
            self._salvar_estado()
            self._salvar_historico()
            return trade

    def vender_manual(self, quantidade_btc: float):
        with self.lock:
            preco = self._validar_cotacao_para_operacao()
            trade = executar_venda_quantidade(
                self.estado, preco, quantidade_btc, "manual"
            )
            self._registrar_trade(trade)
            self._salvar_estado()
            self._salvar_historico()
            return trade

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

                with self.lock:
                    acao = (
                        decidir_acao(self.estado, cotacao.preco_brl)
                        if cotacao.atual
                        else {"acao": "NADA"}
                    )

                    if acao["acao"] == "COMPRA":
                        trade = executar_compra(
                            self.estado, cotacao.preco_brl,
                            acao["valor_brl"], acao["motivo"]
                        )
                        self._registrar_trade(trade)
                    elif acao["acao"] == "VENDA":
                        trade = executar_venda(
                            self.estado, cotacao.preco_brl,
                            acao["percentual"], acao["motivo"]
                        )
                        self._registrar_trade(trade)

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
                    self._salvar_estado()
                    self._salvar_historico()

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
        self.title("BTC Paper Trader")
        self.geometry("1240x900")
        self.minsize(1080, 760)
        self.configure(fg_color=CORES["bg_principal"])
        ctk.set_appearance_mode("dark")

        # Motor
        self.motor = MotorSimulador()
        self.motor.iniciar()

        # Grid principal: 7 linhas, coluna única
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=3, minsize=265)
        self.grid_rowconfigure(5, weight=2, minsize=135)

        # ----- LINHA 0: Título -----
        ctk.CTkLabel(
            self, text="⚡ BTC PAPER TRADER",
            font=FONTE_TITULO, text_color=CORES["verde_matrix"]
        ).grid(row=0, column=0, pady=(15, 5), sticky="w", padx=20)

        # ----- LINHA 1: Preço + Patrimônio -----
        self.frame_topo = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_topo.grid(row=1, column=0, sticky="ew", padx=20, pady=5)
        self.frame_topo.grid_columnconfigure((0, 1), weight=1)

        self.card_preco = self._criar_card(self.frame_topo, "PREÇO BTC", "R$ —", "—")
        self.card_preco.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        self.card_patrimonio = self._criar_card(self.frame_topo, "PATRIMÔNIO", "R$ —", "—")
        self.card_patrimonio.grid(row=0, column=1, sticky="nsew", padx=(10, 0))

        # ----- LINHA 2: Posição -----
        self.frame_posicao = ctk.CTkFrame(self, fg_color=CORES["bg_card"], corner_radius=10)
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

        # ----- LINHA 3: Operações manuais -----
        self.frame_manual = ctk.CTkFrame(self, fg_color=CORES["bg_card"], corner_radius=10)
        self.frame_manual.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 10))
        self.frame_manual.grid_columnconfigure((0, 1), weight=1, uniform="ordem")

        ctk.CTkLabel(
            self.frame_manual, text="OPERAÇÃO MANUAL (paper trading)",
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

        # ----- LINHA 4: Cotação e expectativa de mercado -----
        self.frame_graficos = ctk.CTkFrame(
            self, fg_color=CORES["bg_card"], corner_radius=10
        )
        self.frame_graficos.grid(row=4, column=0, sticky="nsew", padx=20, pady=(0, 10))
        self.frame_graficos.grid_columnconfigure(0, weight=1)
        self.frame_graficos.grid_rowconfigure(1, weight=1, minsize=185)

        ctk.CTkLabel(
            self.frame_graficos,
            text="MERCADO BTC/BRL",
            font=FONTE_SUBTITULO,
            text_color=CORES["verde_pastel"],
        ).grid(row=0, column=0, sticky="w", padx=15, pady=(10, 0))

        self.fig, self.axes = plt.subplots(1, 2, figsize=(12, 2.55), dpi=90)
        self.fig.patch.set_facecolor(CORES["bg_card"])
        self.canvas_graficos = FigureCanvasTkAgg(self.fig, master=self.frame_graficos)
        self.canvas_graficos.get_tk_widget().grid(
            row=1, column=0, sticky="nsew", padx=14, pady=(4, 0)
        )

        self.lbl_previsao_status = ctk.CTkLabel(
            self.frame_graficos,
            text="Aguardando dados de mercado...",
            font=FONTE_PEQUENA,
            text_color=CORES["texto_secundario"],
            anchor="w",
            wraplength=1160,
        )
        self.lbl_previsao_status.grid(
            row=2, column=0, sticky="ew", padx=16, pady=(0, 4)
        )

        self.frame_fontes_analistas = ctk.CTkFrame(
            self.frame_graficos, fg_color="transparent"
        )
        self.frame_fontes_analistas.grid(
            row=3, column=0, sticky="ew", padx=12, pady=(0, 7)
        )
        self.frame_fontes_analistas.grid_columnconfigure(
            (0, 1, 2, 3), weight=1, uniform="fonte"
        )
        self.botoes_fontes_analistas = []
        self.widgets_fontes_analistas = []

        # ----- LINHA 5: Trades + Métricas (lado a lado) -----
        self.frame_baixo = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_baixo.grid(row=5, column=0, sticky="nsew", padx=20, pady=10)
        self.frame_baixo.grid_columnconfigure(0, weight=3, uniform="painel")
        self.frame_baixo.grid_columnconfigure(1, weight=2, uniform="painel")
        self.frame_baixo.grid_rowconfigure(0, weight=1)

        # Card trades
        self.card_trades = ctk.CTkFrame(self.frame_baixo, fg_color=CORES["bg_card"], corner_radius=10)
        self.card_trades.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.card_trades.grid_columnconfigure(0, weight=1)
        self.card_trades.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            self.card_trades, text="ÚLTIMOS TRADES",
            font=FONTE_SUBTITULO, text_color=CORES["verde_pastel"]
        ).grid(row=0, column=0, sticky="w", padx=15, pady=(10, 5))

        self.txt_trades = ctk.CTkTextbox(
            self.card_trades, font=FONTE_PEQUENA,
            fg_color=CORES["bg_secundario"],
            text_color=CORES["texto_primario"],
            border_width=0, wrap="none"
        )
        self.txt_trades.grid(row=1, column=0, sticky="nsew", padx=15, pady=(0, 15))
        self.txt_trades.configure(state="disabled")

        # Card métricas
        self.card_metricas = ctk.CTkFrame(self.frame_baixo, fg_color=CORES["bg_card"], corner_radius=10)
        self.card_metricas.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        self.card_metricas.grid_columnconfigure(1, weight=1)
        # Cada linha tem peso igual, exceto a última que absorve sobra
        for r in range(6):
            self.card_metricas.grid_rowconfigure(r + 1, weight=1)

        ctk.CTkLabel(
            self.card_metricas, text="MÉTRICAS",
            font=FONTE_SUBTITULO, text_color=CORES["verde_pastel"]
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=15, pady=(10, 5))

        self.metricas_labels = {}
        linhas_metricas = [
            ("compras", "Compras"),
            ("vendas", "Vendas"),
            ("taxas", "Taxas pagas"),
            ("variacao", "Variação"),
            ("buy_hold", "Buy-and-hold"),
            ("proximo_aporte", "Próximo aporte"),
        ]
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

        # ----- LINHA 6: Status -----
        self.lbl_status = ctk.CTkLabel(
            self, text="Iniciando...",
            font=FONTE_PEQUENA, text_color=CORES["texto_apagado"]
        )
        self.lbl_status.grid(row=6, column=0, sticky="w", padx=20, pady=(0, 10))

        self.protocol("WM_DELETE_WINDOW", self._ao_fechar)
        self.atualizar_gui()

    # -----------------------------------------------------------------
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
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

        self.motor.parar()
        self.destroy()

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
        if not self.winfo_exists():
            return

        with self.motor.lock:
            estado = self.motor.estado
            historico = list(self.motor.historico)
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

        if preco and estado:
            self._atualizar_tela(estado, historico, preco, fonte_preco)
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
            "PROJEÇÃO ESTATÍSTICA · PRÓXIMAS 24H",
            color=CORES["verde_pastel"], fontsize=10, loc="left",
        )
        if previsao:
            previsao_local = [p.timestamp.astimezone(FUSO_LOCAL) for p in previsao]
            esperados = [p.expected_brl for p in previsao]
            minimos = [p.low_brl for p in previsao]
            maximos = [p.high_brl for p in previsao]
            ax_previsao.fill_between(
                previsao_local, minimos, maximos,
                color=CORES["azul_pastel"], alpha=0.18, label="faixa modelada",
            )
            ax_previsao.plot(
                previsao_local, esperados, color=CORES["azul_pastel"],
                linewidth=2, label="cenário central",
            )
            if historico:
                contexto = historico[-6:]
                ax_previsao.plot(
                    [ts.astimezone(FUSO_LOCAL) for ts, _ in contexto],
                    [valor for _, valor in contexto],
                    color=CORES["texto_secundario"], linewidth=1.2,
                    linestyle="--", label="histórico recente",
                )
            ax_previsao.legend(
                loc="upper center", ncol=3, fontsize=7, frameon=False,
                labelcolor=CORES["texto_secundario"],
            )
            final = previsao[-1]
            variacao = (
                (final.expected_brl / preco_atual - 1) * 100
                if preco_atual else 0.0
            )
            intervalo = (
                f"Esperado em 24h: R$ {formatar_reais(final.expected_brl)} "
                f"({variacao:+.2f}%) · Faixa: "
                f"R$ {formatar_reais(final.low_brl)}–{formatar_reais(final.high_brl)}"
            )
        else:
            ax_previsao.text(
                0.5, 0.5, "Aguardando dados horários suficientes",
                ha="center", va="center", color=CORES["texto_apagado"],
                transform=ax_previsao.transAxes,
            )
            intervalo = "Projeção indisponível até haver histórico suficiente."

        self.fig.tight_layout(pad=1.2)
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
        direcao = "positiva" if sentimento > 0.05 else "negativa" if sentimento < -0.05 else "neutra"
        sentimento_texto = (
            f"Notícias de analistas: {direcao} ({sentimento:+.2f}; "
            f"{len(artigos)} matérias, {horario_analistas})"
        )
        self.lbl_previsao_status.configure(
            text=(
                f"{intervalo}  |  Histórico: {horario_mercado}  |  "
                f"{sentimento_texto}  |  Estimativa, não recomendação."
                + (f"  |  Aviso: {erro}" if erro else "")
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
                text="Sem matérias recentes de analistas com sinal direcional.",
                font=FONTE_PEQUENA,
                text_color=CORES["texto_apagado"],
                anchor="w",
            )
            mensagem_fontes.grid(
                row=0, column=0, columnspan=4, sticky="ew", padx=5
            )
            self.widgets_fontes_analistas.append(mensagem_fontes)
        else:
            for index, article in enumerate(artigos[:4]):
                sinal = "+" if article.sentiment > 0 else "−"
                titulo = article.title
                if len(titulo) > 24:
                    titulo = f"{titulo[:21].rstrip()}..."
                texto = f"{article.publisher} {sinal} {titulo}"
                button = ctk.CTkButton(
                    self.frame_fontes_analistas,
                    text=texto,
                    command=partial(webbrowser.open, article.url),
                    anchor="w",
                    height=26,
                    fg_color="transparent",
                    hover_color=CORES["bg_destaque"],
                    text_color=CORES["azul_pastel"],
                    font=FONTE_PEQUENA,
                )
                button.grid(
                    row=0, column=index, sticky="ew", padx=3, pady=(0, 2),
                )
                self.botoes_fontes_analistas.append(button)
                self.widgets_fontes_analistas.append(button)

    def _atualizar_tela(self, estado, historico, preco, fonte_preco):
        capital_brl = estado.capital_brl
        capital_btc = estado.capital_btc
        preco_medio = estado.preco_medio_brl
        inicial = estado.patrimonio_inicial_brl or 1

        valor_btc = capital_btc * preco
        patrimonio = capital_brl + valor_btc
        lucro = patrimonio - inicial
        lucro_pct = (lucro / inicial * 100) if inicial else 0

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
        var_total = ((patrimonio - inicial) / inicial * 100) if inicial else 0
        self.metricas_labels["variacao"].configure(
            text=f"{var_total:+.2f}%",
            text_color=CORES["lucro"] if var_total >= 0 else CORES["prejuizo"]
        )

        if historico:
            primeiro = historico[0]
            preco_inicial = primeiro.get("preco_brl", 0)
            if preco_inicial > 0:
                btc_hold = inicial / preco_inicial
                patrimonio_hold = btc_hold * preco
                lucro_hold = patrimonio_hold - inicial
                lucro_hold_pct = (lucro_hold / inicial) * 100
                cor_hold = CORES["lucro"] if lucro_hold >= 0 else CORES["prejuizo"]
                self.metricas_labels["buy_hold"].configure(
                    text=f"{lucro_hold_pct:+.2f}%",
                    text_color=cor_hold
                )
            else:
                self.metricas_labels["buy_hold"].configure(
                    text="—", text_color=CORES["texto_apagado"]
                )
        else:
            self.metricas_labels["buy_hold"].configure(
                text="—", text_color=CORES["texto_apagado"]
            )

        self.metricas_labels["proximo_aporte"].configure(
            text=self._calcular_proximo_aporte(estado),
            text_color=CORES["texto_primario"]
        )

        self._atualizar_trades(historico)

    def _calcular_proximo_aporte(self, estado):
        ultimo = estado.ultimo_aporte_ts
        if not ultimo:
            return "agora"
        try:
            dt_ultimo = self._para_horario_local(ultimo)
            agora = datetime.now(timezone.utc)
            segundos_passados = (agora - dt_ultimo.astimezone(timezone.utc)).total_seconds()
            segundos_alvo = CONFIG["intervalo_aporte_horas"] * 3600
            restante = segundos_alvo - segundos_passados
            if restante <= 0:
                return "agora"
            horas = int(restante // 3600)
            minutos = int((restante % 3600) // 60)
            if horas > 0:
                return f"em {horas}h {minutos}min"
            return f"em {minutos}min"
        except (ValueError, TypeError):
            return "—"

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