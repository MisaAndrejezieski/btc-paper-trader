"""
Imprime o relatório do simulador no terminal.
"""

from config import CONFIG
from models import Estado


def imprimir_snapshot(estado: Estado, preco_brl: float) -> None:
    """Imprime o estado atual + métricas de performance."""
    valor_btc = estado.capital_btc * preco_brl
    patrimonio = estado.capital_brl + valor_btc
    lucro = patrimonio - estado.patrimonio_inicial_brl
    lucro_pct = (lucro / estado.patrimonio_inicial_brl) * 100 if estado.patrimonio_inicial_brl else 0

    # Buy-and-hold: se tivesse comprado tudo no primeiro preço
    btc_hold = estado.patrimonio_inicial_brl / preco_brl
    patrimonio_hold = btc_hold * preco_brl
    lucro_hold = patrimonio_hold - estado.patrimonio_inicial_brl
    lucro_hold_pct = (lucro_hold / estado.patrimonio_inicial_brl) * 100 if estado.patrimonio_inicial_brl else 0

    # Variação vs preço médio
    if estado.preco_medio_brl > 0:
        var_pm = (preco_brl - estado.preco_medio_brl) / estado.preco_medio_brl * 100
    else:
        var_pm = 0.0

    print("\n" + "=" * 60)
    print("  BTC PAPER TRADER — Snapshot")
    print("=" * 60)
    print(f"  Preço BTC agora:      R$ {preco_brl:>12,.2f}")
    print(f"  Capital em BRL:       R$ {estado.capital_brl:>12,.2f}")
    print(f"  Capital em BTC:       {estado.capital_btc:>12.8f} BTC")
    print(f"  Valor em BTC (BRL):   R$ {valor_btc:>12,.2f}")
    print("-" * 60)
    print(f"  Patrimônio total:     R$ {patrimonio:>12,.2f}")
    print(f"  Lucro/prejuízo:       R$ {lucro:>+12,.2f}  ({lucro_pct:+.2f}%)")
    print(f"  Buy-and-hold:         R$ {lucro_hold:>+12,.2f}  ({lucro_hold_pct:+.2f}%)")
    print("-" * 60)
    print(f"  Preço médio:          R$ {estado.preco_medio_brl:>12,.2f}  ({var_pm:+.2f}%)")
    print(f"  Trades:               {estado.total_compras} compras / {estado.total_vendas} vendas")
    print(f"  Taxas pagas:          R$ {estado.total_taxas_brl:>12,.2f}")
    print("=" * 60)