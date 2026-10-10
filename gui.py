"""
GUI do BTC Paper Trader — visualização em tempo real.
Lê state.json e history.json, atualiza a cada 3 segundos.
Não controla nada — só mostra.
"""

import json
import os
from datetime import datetime

import customtkinter as ctk
import matplotlib

matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from config import CONFIG
from theme import (CORES, FONTE_LABEL, FONTE_PEQUENA, FONTE_SUBTITULO,
                   FONTE_TITULO, FONTE_VALOR, FONTE_VALOR_MEDIO)

INTERVALO_ATUALIZACAO_MS = 3000  # 3 segundos


class AppBTC(ctk.CTk):
    def __init__(self):
        super().__init__()

        # --- Configuração da janela ---
        self.title("BTC Paper Trader")
        self.geometry("1000x700")
        self.configure(fg_color=CORES["bg_principal"])
        ctk.set_appearance_mode("dark")

        # --- Grid principal ---
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)  # linha do gráfico expande

        # --- Título ---
        titulo = ctk.CTkLabel(
            self, text="⚡ BTC PAPER TRADER",
            font=FONTE_TITULO, text_color=CORES["verde_matrix"]
        )
        titulo.grid(row=0, column=0, pady=(15, 5), sticky="w", padx=20)

        # --- Cards superiores: Preço + Patrimônio ---
        self.frame_topo = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_topo.grid(row=1, column=0, sticky="ew", padx=20, pady=5)
        self.frame_topo.grid_columnconfigure((0, 1), weight=1)

        self.card_preco = self._criar_card(self.frame_topo, "PREÇO BTC", "R$ —", "—")
        self.card_preco.grid(row=0, column=0, sticky="ew", padx=(0, 10))

        self.card_patrimonio = self._criar_card(self.frame_topo, "PATRIMÔNIO", "R$ —", "—")
        self.card_patrimonio.grid(row=0, column=1, sticky="ew", padx=(10, 0))

        # --- Card posição ---
        self.frame_posicao = ctk.CTkFrame(
            self, fg_color=CORES["bg_card"], corner_radius=10
        )
        self.frame_posicao.grid(row=2, column=0, sticky="ew", padx=20, pady=10)
        self.frame_posicao.grid_columnconfigure((0, 1, 2, 3), weight=1)

        self.lbl_posicao_titulo = ctk.CTkLabel(
            self.frame_posicao, text="POSIÇÃO",
            font=FONTE_SUBTITULO, text_color=CORES["verde_pastel"]
        )
        self.lbl_posicao_titulo.grid(row=0, column=0, columnspan=4, sticky="w", padx=15, pady=(10, 5))

        self.lbl_brl = self._criar_label_posicao(self.frame_posicao, "BRL", "—", 0, 1)
        self.lbl_btc = self._criar_label_posicao(self.frame_posicao, "BTC", "—", 0, 2)
        self.lbl_preco_medio = self._criar_label_posicao(self.frame_posicao, "PREÇO MÉDIO", "—", 0, 3)

        # --- Frame inferior: trades + métricas ---
        self.frame_baixo = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_baixo.grid(row=3, column=0, sticky="nsew", padx=20, pady=10)
        self.frame_baixo.grid_columnconfigure(0, weight=1)
        self.frame_baixo.grid_columnconfigure(1, weight=1)
        self.frame_baixo.grid_rowconfigure(0, weight=1)

        # --- Card últimos trades ---
        self.card_trades = ctk.CTkFrame(
            self.frame_baixo, fg_color=CORES["bg_card"], corner_radius=10
        )
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
            border_width=0, height=150
        )
        self.txt_trades.grid(row=1, column=0, sticky="nsew", padx=15, pady=(0, 15))
        self.txt_trades.configure(state="disabled")

        # --- Card métricas ---
        self.card_metricas = ctk.CTkFrame(
            self.frame_baixo, fg_color=CORES["bg_card"], corner_radius=10
        )
        self.card_metricas.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        self.card_metricas.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            self.card_metricas, text="MÉTRICAS",
            font=FONTE_SUBTITULO, text_color=CORES["verde_pastel"]
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=15, pady=(10, 5))

        self.metricas_labels = {}
        for i, (chave, rotulo) in enumerate([
            ("compras", "Compras"),
            ("vendas", "Vendas"),
            ("taxas", "Taxas pagas"),
            ("variacao", "Variação"),
        ], start=1):
            ctk.CTkLabel(
                self.card_metricas, text=f"{rotulo}:",
                font=FONTE_LABEL, text_color=CORES["texto_secundario"]
            ).grid(row=i, column=0, sticky="w", padx=(15, 10), pady=3)
            lbl = ctk.CTkLabel(
                self.card_metricas, text="—",
                font=FONTE_LABEL, text_color=CORES["texto_primario"]
            )
            lbl.grid(row=i, column=1, sticky="w", padx=(0, 15), pady=3)
            self.metricas_labels[chave] = lbl

        # --- Gráfico ---
        self.frame_grafico = ctk.CTkFrame(
            self, fg_color=CORES["bg_card"], corner_radius=10
        )
        self.frame_grafico.grid(row=4, column=0, sticky="ew", padx=20, pady=(0, 10))
        self.frame_grafico.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self.frame_grafico, text="PATRIMÔNIO AO LONGO DO TEMPO",
            font=FONTE_SUBTITULO, text_color=CORES["verde_pastel"]
        ).grid(row=0, column=0, sticky="w", padx=15, pady=(10, 0))

        self.fig, self.ax = plt.subplots(figsize=(9, 2.2), dpi=90)
        self.fig.patch.set_facecolor(CORES["bg_card"])
        self.ax.set_facecolor(CORES["bg_secundario"])
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.frame_grafico)
        self.canvas.get_tk_widget().grid(row=1, column=0, sticky="ew", padx=15, pady=(5, 15))

        # --- Status bar ---
        self.lbl_status = ctk.CTkLabel(
            self, text="Aguardando dados...",
            font=FONTE_PEQUENA, text_color=CORES["texto_apagado"]
        )
        self.lbl_status.grid(row=5, column=0, sticky="w", padx=20, pady=(0, 10))

        # --- Inicia loop de atualização ---
        self.atualizar()

    # -----------------------------------------------------------------
    def _criar_card(self, parent, titulo, valor, variacao):
        """Cria um card com título, valor grande e variação."""
        card = ctk.CTkFrame(parent, fg_color=CORES["bg_card"], corner_radius=10)
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            card, text=titulo, font=FONTE_LABEL,
            text_color=CORES["verde_pastel"]
        ).grid(row=0, column=0, sticky="w", padx=15, pady=(10, 0))

        lbl_valor = ctk.CTkLabel(
            card, text=valor, font=FONTE_VALOR,
            text_color=CORES["texto_primario"]
        )
        lbl_valor.grid(row=1, column=0, sticky="w", padx=15, pady=(0, 0))

        lbl_var = ctk.CTkLabel(
            card, text=variacao, font=FONTE_LABEL,
            text_color=CORES["texto_secundario"]
        )
        lbl_var.grid(row=2, column=0, sticky="w", padx=15, pady=(0, 10))

        card.lbl_valor = lbl_valor
        card.lbl_var = lbl_var
        return card

    def _criar_label_posicao(self, parent, titulo, valor, row, col):
        """Cria um bloco de label + valor para o card de posição."""
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid(row=row + 1, column=col, sticky="w", padx=15, pady=(0, 15))

        ctk.CTkLabel(
            frame, text=titulo, font=FONTE_PEQUENA,
            text_color=CORES["texto_secundario"]
        ).pack(anchor="w")

        lbl = ctk.CTkLabel(
            frame, text=valor, font=FONTE_VALOR_MEDIO,
            text_color=CORES["texto_primario"]
        )
        lbl.pack(anchor="w")
        return lbl

    # -----------------------------------------------------------------
    def carregar_estado(self):
        """Lê state.json e history.json. Retorna (estado, historico)."""
        estado = None
        historico = []

        if os.path.exists(CONFIG["state_file"]):
            try:
                with open(CONFIG["state_file"], "r", encoding="utf-8") as f:
                    estado = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        if os.path.exists(CONFIG["history_file"]):
            try:
                with open(CONFIG["history_file"], "r", encoding="utf-8") as f:
                    historico = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        return estado, historico

    def buscar_preco_cache(self):
        """Lê o preço atual do cache da CoinGecko."""
        cache_path = os.path.join(CONFIG["cache_dir"], "last_price.json")
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    return json.load(f).get("preco_brl")
            except (json.JSONDecodeError, OSError):
                return None
        return None

    # -----------------------------------------------------------------
    def atualizar(self):
        """Ciclo de atualização: lê arquivos e redesenha a GUI."""
        estado, historico = self.carregar_estado()
        preco = self.buscar_preco_cache()

        if estado and preco:
            self._atualizar_tela(estado, historico, preco)
            self.lbl_status.configure(
                text=f"● Conectado  |  Atualizado: {datetime.now().strftime('%H:%M:%S')}  |  "
                     f"Fonte: cache CoinGecko",
                text_color=CORES["verde_matrix"]
            )
        else:
            self.lbl_status.configure(
                text="⚠  Aguardando state.json e data/last_price.json — "
                     "rode `python main.py` em outro terminal",
                text_color=CORES["amarelo_pastel"]
            )

        self.after(INTERVALO_ATUALIZACAO_MS, self.atualizar)

    def _atualizar_tela(self, estado, historico, preco):
        """Atualiza todos os widgets com os dados atuais."""
        capital_brl = estado.get("capital_brl", 0)
        capital_btc = estado.get("capital_btc", 0)
        preco_medio = estado.get("preco_medio_brl", 0)
        inicial = estado.get("patrimonio_inicial_brl", 1)

        valor_btc = capital_btc * preco
        patrimonio = capital_brl + valor_btc
        lucro = patrimonio - inicial
        lucro_pct = (lucro / inicial * 100) if inicial else 0

        # --- Card Preço ---
        self.card_preco.lbl_valor.configure(
            text=f"R$ {preco:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        )
        self.card_preco.lbl_var.configure(
            text="preço atual CoinGecko", text_color=CORES["texto_secundario"]
        )

        # --- Card Patrimônio ---
        cor_patrimonio = CORES["lucro"] if lucro >= 0 else CORES["prejuizo"]
        self.card_patrimonio.lbl_valor.configure(
            text=f"R$ {patrimonio:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
            text_color=CORES["texto_primario"]
        )
        self.card_patrimonio.lbl_var.configure(
            text=f"{'▲' if lucro >= 0 else '▼'} {lucro_pct:+.2f}%  ({lucro:+,.2f})",
            text_color=cor_patrimonio
        )

        # --- Posição ---
        self.lbl_brl.configure(text=f"R$ {capital_brl:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        self.lbl_btc.configure(text=f"{capital_btc:.8f}")

        if preco_medio > 0:
            var_pm = (preco - preco_medio) / preco_medio * 100
            cor_pm = CORES["lucro"] if var_pm >= 0 else CORES["prejuizo"]
            self.lbl_preco_medio.configure(
                text=f"R$ {preco_medio:,.2f}  ({var_pm:+.2f}%)".replace(",", "X").replace(".", ",").replace("X", "."),
                text_color=cor_pm
            )
        else:
            self.lbl_preco_medio.configure(text="—", text_color=CORES["texto_primario"])

        # --- Métricas ---
        self.metricas_labels["compras"].configure(text=str(estado.get("total_compras", 0)))
        self.metricas_labels["vendas"].configure(text=str(estado.get("total_vendas", 0)))
        self.metricas_labels["taxas"].configure(
            text=f"R$ {estado.get('total_taxas_brl', 0):.2f}".replace(".", ",")
        )
        var_total = ((patrimonio - inicial) / inicial * 100) if inicial else 0
        self.metricas_labels["variacao"].configure(
            text=f"{var_total:+.2f}%",
            text_color=CORES["lucro"] if var_total >= 0 else CORES["prejuizo"]
        )

        # --- Trades ---
        self._atualizar_trades(historico)

        # --- Gráfico ---
        self._atualizar_grafico(historico, inicial)

    def _atualizar_trades(self, historico):
        """Mostra os últimos 15 trades."""
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
        """Desenha o gráfico de patrimônio ao longo do tempo."""
        self.ax.clear()

        if historico:
            # Reconstrói patrimônio aproximado ao longo do tempo
            # (simplificação: mostra o valor de cada trade e o saldo acumulado)
            timestamps = []
            valores = []
            saldo = inicial
            for t in historico:
                ts = t.get("timestamp", "")[:19].replace("T", " ")
                timestamps.append(ts)
                if t.get("tipo") == "COMPRA":
                    saldo -= t.get("taxa_brl", 0)
                elif t.get("tipo") == "VENDA":
                    saldo -= t.get("taxa_brl", 0)
                valores.append(saldo)

            if len(valores) >= 2:
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
                self._grafico_vazio()
        else:
            self._grafico_vazio()

        self.canvas.draw()

    def _grafico_vazio(self):
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


if __name__ == "__main__":
    app = AppBTC()
    app.mainloop()