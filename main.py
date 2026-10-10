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

import customtkinter as ctk
import matplotlib

matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from config import CONFIG
from fetcher import buscar_preco_btc_brl
from models import Estado
from simulator import executar_compra, executar_venda
from strategy import decidir_acao
from theme import (CORES, FONTE_LABEL, FONTE_PEQUENA, FONTE_SUBTITULO,
                   FONTE_TITULO, FONTE_VALOR, FONTE_VALOR_MEDIO)

# =========================================================================
# MOTOR DO SIMULADOR (roda em thread separada)
# =========================================================================

class MotorSimulador:
    def __init__(self):
        self.estado = self._carregar_estado()
        self.historico = self._carregar_historico()
        self.preco_atual = None
        self.ultimo_update = None
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

    def iniciar(self):
        self.thread.start()

    def parar(self):
        self.rodando = False

    def _loop(self):
        while self.rodando:
            try:
                preco = buscar_preco_btc_brl()

                with self.lock:
                    acao = decidir_acao(self.estado, preco)

                    if acao["acao"] == "COMPRA":
                        trade = executar_compra(
                            self.estado, preco,
                            acao["valor_brl"], acao["motivo"]
                        )
                        self._registrar_trade(trade)
                    elif acao["acao"] == "VENDA":
                        trade = executar_venda(
                            self.estado, preco,
                            acao["percentual"], acao["motivo"]
                        )
                        self._registrar_trade(trade)

                    self.preco_atual = preco
                    self.ultimo_update = datetime.now()
                    self._salvar_estado()
                    self._salvar_historico()

            except Exception as e:
                print(f"[ERRO no motor] {e}")

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

        self.title("BTC Paper Trader")
        self.geometry("1100x780")
        self.minsize(1000, 720)
        self.configure(fg_color=CORES["bg_principal"])
        ctk.set_appearance_mode("dark")

        # Motor
        self.motor = MotorSimulador()
        self.motor.iniciar()

        # Grid principal: 6 linhas, coluna única
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)   # frame do meio (trades + métricas) cresce
        self.grid_rowconfigure(4, weight=1)   # gráfico cresce igual

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

        # ----- LINHA 3: Trades + Métricas (lado a lado) -----
        self.frame_baixo = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_baixo.grid(row=3, column=0, sticky="nsew", padx=20, pady=10)
        self.frame_baixo.grid_columnconfigure(0, weight=1)
        self.frame_baixo.grid_columnconfigure(1, weight=1)
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

        # ----- LINHA 4: Gráfico -----
        self.frame_grafico = ctk.CTkFrame(self, fg_color=CORES["bg_card"], corner_radius=10)
        self.frame_grafico.grid(row=4, column=0, sticky="nsew", padx=20, pady=(0, 10))
        self.frame_grafico.grid_columnconfigure(0, weight=1)
        self.frame_grafico.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            self.frame_grafico, text="PATRIMÔNIO AO LONGO DO TEMPO",
            font=FONTE_SUBTITULO, text_color=CORES["verde_pastel"]
        ).grid(row=0, column=0, sticky="w", padx=15, pady=(10, 0))

        self.fig, self.ax = plt.subplots(figsize=(10, 2), dpi=90)
        self.fig.patch.set_facecolor(CORES["bg_card"])
        self.ax.set_facecolor(CORES["bg_secundario"])
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.frame_grafico)
        self.canvas.get_tk_widget().grid(row=1, column=0, sticky="nsew", padx=15, pady=(5, 15))

        # ----- LINHA 5: Status -----
        self.lbl_status = ctk.CTkLabel(
            self, text="Iniciando...",
            font=FONTE_PEQUENA, text_color=CORES["texto_apagado"]
        )
        self.lbl_status.grid(row=5, column=0, sticky="w", padx=20, pady=(0, 10))

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
        self.motor.parar()
        self.destroy()

    # -----------------------------------------------------------------
    def atualizar_gui(self):
        with self.motor.lock:
            estado = self.motor.estado
            historico = list(self.motor.historico)
            preco = self.motor.preco_atual
            ultimo_update = self.motor.ultimo_update

        if preco and estado:
            self._atualizar_tela(estado, historico, preco)
            ts = ultimo_update.strftime("%H:%M:%S") if ultimo_update else "—"
            self.lbl_status.configure(
                text=f"● Rodando  |  Atualizado: {ts}  |  Fonte: CoinGecko",
                text_color=CORES["verde_matrix"]
            )
        else:
            self.lbl_status.configure(
                text="⏳ Aguardando primeiro preço da CoinGecko...",
                text_color=CORES["amarelo_pastel"]
            )

        self.after(2000, self.atualizar_gui)

    def _atualizar_tela(self, estado, historico, preco):
        capital_brl = estado.capital_brl
        capital_btc = estado.capital_btc
        preco_medio = estado.preco_medio_brl
        inicial = estado.patrimonio_inicial_brl or 1

        valor_btc = capital_btc * preco
        patrimonio = capital_brl + valor_btc
        lucro = patrimonio - inicial
        lucro_pct = (lucro / inicial * 100) if inicial else 0

        self.card_preco.lbl_valor.configure(
            text=f"R$ {preco:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        )
        self.card_preco.lbl_var.configure(
            text="preço atual CoinGecko", text_color=CORES["texto_secundario"]
        )

        cor_patrimonio = CORES["lucro"] if lucro >= 0 else CORES["prejuizo"]
        self.card_patrimonio.lbl_valor.configure(
            text=f"R$ {patrimonio:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
            text_color=CORES["texto_primario"]
        )
        self.card_patrimonio.lbl_var.configure(
            text=f"{'▲' if lucro >= 0 else '▼'} {lucro_pct:+.2f}%  ({lucro:+,.2f})",
            text_color=cor_patrimonio
        )

        self.lbl_brl.configure(
            text=f"R$ {capital_brl:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        )
        self.lbl_btc.configure(text=f"{capital_btc:.8f}")

        if preco_medio > 0:
            var_pm = (preco - preco_medio) / preco_medio * 100
            cor_pm = CORES["lucro"] if var_pm >= 0 else CORES["prejuizo"]
            self.lbl_preco_medio.configure(
                text=f"R$ {preco_medio:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                text_color=CORES["texto_primario"]
            )
            self.lbl_variacao.configure(text=f"{var_pm:+.2f}%", text_color=cor_pm)
        else:
            self.lbl_preco_medio.configure(text="—", text_color=CORES["texto_primario"])
            self.lbl_variacao.configure(text="—", text_color=CORES["texto_primario"])

        self.metricas_labels["compras"].configure(text=str(estado.total_compras))
        self.metricas_labels["vendas"].configure(text=str(estado.total_vendas))
        self.metricas_labels["taxas"].configure(
            text=f"R$ {estado.total_taxas_brl:.2f}".replace(".", ",")
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
        self._atualizar_grafico(historico, inicial)

    def _calcular_proximo_aporte(self, estado):
        ultimo = estado.ultimo_aporte_ts
        if not ultimo:
            return "agora"
        try:
            dt_ultimo = datetime.fromisoformat(ultimo)
            if dt_ultimo.tzinfo is None:
                dt_ultimo = dt_ultimo.replace(tzinfo=timezone.utc)
            agora = datetime.now(timezone.utc)
            segundos_passados = (agora - dt_ultimo).total_seconds()
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
                preco = t.get("preco_brl", 0)
                valor = t.get("valor_brl", 0)
                motivo = t.get("motivo", "")
                ts = t.get("timestamp", "")[:19].replace("T", " ")

                sinal = "[+]" if tipo == "COMPRA" else "[-]"
                self.txt_trades.insert(
                    "end",
                    f"{sinal} {ts}  {motivo:<14}  "
                    f"R$ {valor:>10,.2f}  @ R$ {preco:,.2f}\n"
                )

        self.txt_trades.configure(state="disabled")

    def _atualizar_grafico(self, historico, inicial):
        self.ax.clear()

        if historico and len(historico) >= 2:
            valores = []
            saldo = inicial
            for t in historico:
                saldo -= t.get("taxa_brl", 0)
                valores.append(saldo)

            self.ax.plot(
                range(len(valores)), valores,
                color=CORES["verde_matrix"], linewidth=2,
                marker="o", markersize=4,
                markerfacecolor=CORES["verde_neon"],
            )
            self.ax.fill_between(
                range(len(valores)), valores,
                alpha=0.15, color=CORES["verde_matrix"]
            )
            self.ax.axhline(
                inicial, color=CORES["texto_apagado"],
                linestyle="--", linewidth=1, alpha=0.5
            )
            self.ax.set_xticks([])
            self.ax.tick_params(colors=CORES["texto_secundario"], labelsize=8)
            for spine in self.ax.spines.values():
                spine.set_color(CORES["verde_escuro"])
            self.ax.grid(True, alpha=0.1, color=CORES["verde_matrix"])
        else:
            self.ax.text(
                0.5, 0.5, "aguardando trades...",
                ha="center", va="center",
                color=CORES["texto_apagado"], fontsize=10,
                transform=self.ax.transAxes
            )
            self.ax.set_xticks([])
            self.ax.set_yticks([])
            for spine in self.ax.spines.values():
                spine.set_color(CORES["verde_escuro"])

        self.canvas.draw()


# =========================================================================
# PONTO DE ENTRADA
# =========================================================================

if __name__ == "__main__":
    app = AppBTC()
    app.mainloop()