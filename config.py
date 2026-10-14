"""
Configurações globais do simulador.
Todos os parâmetros ajustáveis ficam aqui, centralizados.
"""

import os


DATA_DIR = "data"

CONFIG = {
    # --- Capital inicial ---
    "capital_inicial_brl": 0.00,

    # --- Valor base de compra extra ---
    "valor_compra_base_brl": 1000.00,

    # --- Compra extra em queda forte ---
    "queda_para_compra_extra_pct": 2.0,
    "multiplicador_compra_extra": 5.0,

    # --- Take-profit parcial ---
    "take_profit_pct": 3.0,
    "percentual_venda_take_profit": 50,

    # --- Stop-loss de proteção ---
    "stop_loss_pct": 10.0,
    "percentual_venda_stop_loss": 50,
    "stop_loss_ativo": True,

    # --- Loop ---
    "intervalo_loop_minutos": 5,
    "max_iteracoes": 0,

    # --- Custos ---
    "taxa_operacao_pct": 0.1,

    # --- Arquivos ---
    "state_file": os.path.join(DATA_DIR, "state.json"),
    "history_file": os.path.join(DATA_DIR, "history.json"),
    "trade_journal_file": os.path.join(DATA_DIR, "trade_transaction.json"),
    "cash_flow_file": os.path.join(DATA_DIR, "cash_flow.json"),
    "equity_file": os.path.join(DATA_DIR, "equity_history.json"),
    "cache_dir": DATA_DIR,
}