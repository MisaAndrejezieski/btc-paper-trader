"""
Loop principal do simulador.
Carrega o estado, busca o preço, decide, executa, reporta.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

from config import CONFIG
from fetcher import buscar_preco_btc_brl
from models import Estado, Trade
from report import imprimir_snapshot
from simulator import executar_compra, executar_venda
from strategy import decidir_acao


def carregar_estado() -> Estado:
    """Carrega o estado do disco, ou cria um novo."""
    if os.path.exists(CONFIG["state_file"]):
        with open(CONFIG["state_file"], "r", encoding="utf-8") as f:
            return Estado.from_dict(json.load(f))
    # Estado novo
    estado = Estado()
    estado.capital_brl = CONFIG["capital_inicial_brl"]
    estado.patrimonio_inicial_brl = CONFIG["capital_inicial_brl"]
    return estado


def salvar_estado(estado: Estado) -> None:
    with open(CONFIG["state_file"], "w", encoding="utf-8") as f:
        json.dump(estado.to_dict(), f, indent=2)


def registrar_trade(trade: Trade) -> None:
    """Append de trade no history.json."""
    if trade is None:
        return
    historico = []
    if os.path.exists(CONFIG["history_file"]):
        with open(CONFIG["history_file"], "r", encoding="utf-8") as f:
            historico = json.load(f)
    historico.append(trade.to_dict())
    with open(CONFIG["history_file"], "w", encoding="utf-8") as f:
        json.dump(historico, f, indent=2, ensure_ascii=False)


def executar_acao(estado: Estado, preco: float, acao: dict) -> None:
    """Executa a ação decidida pela estratégia."""
    if acao["acao"] == "COMPRA":
        trade = executar_compra(estado, preco, acao["valor_brl"], acao["motivo"])
        registrar_trade(trade)
        print(f"  [+] COMPRA: R$ {acao['valor_brl']:.2f} ({acao['motivo']})")

    elif acao["acao"] == "VENDA":
        trade = executar_venda(estado, preco, acao["percentual"], acao["motivo"])
        registrar_trade(trade)
        print(f"  [-] VENDA: {acao['percentual']}% da posição ({acao['motivo']})")

    else:
        print("  [.] Nenhuma ação no momento.")


def main() -> None:
    print("Iniciando BTC Paper Trader...")
    print(f"Capital inicial: R$ {CONFIG['capital_inicial_brl']:.2f}")
    print(f"Loop a cada {CONFIG['intervalo_loop_minutos']} min. Ctrl+C para parar.\n")

    estado = carregar_estado()
    iteracoes = 0

    try:
        while True:
            preco = buscar_preco_btc_brl()
            acao = decidir_acao(estado, preco)
            executar_acao(estado, preco, acao)
            salvar_estado(estado)
            imprimir_snapshot(estado, preco)

            iteracoes += 1
            if CONFIG["max_iteracoes"] > 0 and iteracoes >= CONFIG["max_iteracoes"]:
                print("\nLimite de iterações atingido. Encerrando.")
                break

            time.sleep(CONFIG["intervalo_loop_minutos"] * 60)

    except KeyboardInterrupt:
        print("\n\nInterrompido pelo usuário. Estado salvo.")
        salvar_estado(estado)


if __name__ == "__main__":
    main()