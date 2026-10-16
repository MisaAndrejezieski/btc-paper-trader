"""
Imprime o relatório do simulador no terminal.
"""

import argparse
import json
import os

from models import Estado


def _formatar_reais(valor: float, sinal: bool = False) -> str:
    formato = f"{valor:+,.2f}" if sinal else f"{valor:,.2f}"
    return formato.replace(",", "X").replace(".", ",").replace("X", ".")


def imprimir_snapshot(
    estado: Estado,
    preco_brl: float,
    preco_inicial_brl: float | None = None,
    fluxo_caixa: list[dict] | None = None,
) -> None:
    """Imprime o estado atual + métricas de performance."""
    valor_btc = estado.capital_btc * preco_brl
    patrimonio = estado.capital_brl + valor_btc
    fluxo_caixa = fluxo_caixa or []
    depositos = sum(
        item["valor_brl"] for item in fluxo_caixa if item["tipo"] == "DEPOSITO"
    )
    saques = sum(item["valor_brl"] for item in fluxo_caixa if item["tipo"] == "SAQUE")
    capital_aportado = estado.patrimonio_inicial_brl + depositos - saques
    lucro = patrimonio - capital_aportado
    lucro_pct = (lucro / capital_aportado) * 100 if capital_aportado > 0 else 0

    if preco_inicial_brl is not None and preco_inicial_brl > 0:
        btc_hold = estado.patrimonio_inicial_brl / preco_inicial_brl
        patrimonio_hold = btc_hold * preco_brl
        lucro_hold = patrimonio_hold - estado.patrimonio_inicial_brl
        lucro_hold_pct = (
            (lucro_hold / estado.patrimonio_inicial_brl) * 100
            if estado.patrimonio_inicial_brl
            else 0
        )
    else:
        lucro_hold = None
        lucro_hold_pct = None

    # Variação vs preço médio
    if estado.preco_medio_brl > 0:
        var_pm = (preco_brl - estado.preco_medio_brl) / estado.preco_medio_brl * 100
    else:
        var_pm = 0.0

    print("\n" + "=" * 60)
    print("  BTC PAPER TRADER — Snapshot")
    print("=" * 60)
    print(f"  Preço BTC agora:      R$ {_formatar_reais(preco_brl):>12}")
    print(f"  Capital em BRL:       R$ {_formatar_reais(estado.capital_brl):>12}")
    print(f"  Capital em BTC:       {estado.capital_btc:>12.8f} BTC")
    print(f"  Valor em BTC (BRL):   R$ {_formatar_reais(valor_btc):>12}")
    print("-" * 60)
    print(f"  Patrimônio total:     R$ {_formatar_reais(patrimonio):>12}")
    print(f"  Depósitos totais:     R$ {_formatar_reais(depositos):>12}")
    print(f"  Saques totais:        R$ {_formatar_reais(saques):>12}")
    print(f"  Capital líquido:      R$ {_formatar_reais(capital_aportado):>12}")
    print(
        f"  Lucro/prejuízo:       R$ {_formatar_reais(lucro, sinal=True):>12}  ({lucro_pct:+.2f}%)"
    )
    if lucro_hold is None:
        print("  Buy-and-hold:         indisponível (informe o preço inicial)")
    else:
        print(
            f"  Buy-and-hold:         R$ {_formatar_reais(lucro_hold, sinal=True):>12}  "
            f"({lucro_hold_pct:+.2f}%)"
        )
    print("-" * 60)
    print(
        f"  Preço médio:          R$ {_formatar_reais(estado.preco_medio_brl):>12}  ({var_pm:+.2f}%)"
    )
    print(
        f"  Trades:               {estado.total_compras} compras / {estado.total_vendas} vendas"
    )
    print(f"  Taxas pagas:          R$ {_formatar_reais(estado.total_taxas_brl):>12}")
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Exibe um snapshot do BTC Paper Trader."
    )
    parser.add_argument(
        "--price", type=float, required=True, help="Preço atual do BTC em BRL."
    )
    parser.add_argument(
        "--initial-price",
        type=float,
        help="Preço inicial para comparação buy-and-hold.",
    )
    parser.add_argument("--state", default=os.path.join("data", "state.json"))
    parser.add_argument("--cash-flow", default=os.path.join("data", "cash_flow.json"))
    args = parser.parse_args()

    with open(args.state, encoding="utf-8") as file:
        estado = Estado.from_dict(json.load(file))
    fluxo_caixa = []
    if os.path.exists(args.cash_flow):
        with open(args.cash_flow, encoding="utf-8") as file:
            fluxo_caixa = json.load(file)
    imprimir_snapshot(
        estado,
        args.price,
        preco_inicial_brl=args.initial_price,
        fluxo_caixa=fluxo_caixa,
    )


if __name__ == "__main__":
    main()
