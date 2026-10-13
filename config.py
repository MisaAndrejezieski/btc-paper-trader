"""
Configurações globais do simulador.
Todos os parâmetros ajustáveis ficam aqui, centralizados.
"""

CONFIG = {
    # --- Capital inicial ---
    "capital_inicial_brl": 5000.00,

    # --- Aporte periódico (DCA) ---
    "intervalo_aporte_horas": 24,
    "valor_aporte_brl": 150.00,

    # --- Compra extra em queda forte ---
    "queda_para_compra_extra_pct": 5.0,
    "multiplicador_compra_extra": 2.0,

    # --- Take-profit parcial ---
    "take_profit_pct": 8.0,
    "percentual_venda_take_profit": 30,

    # --- Stop-loss de proteção ---
    "stop_loss_pct": 25.0,
    "percentual_venda_stop_loss": 50,
    "stop_loss_ativo": True,

    # --- Loop ---
    "intervalo_loop_minutos": 5,
    "max_iteracoes": 0,

    # --- Custos ---
    "taxa_operacao_pct": 0.1,

    # --- Arquivos ---
    "state_file": "state.json",
    "history_file": "history.json",
    "cache_dir": "data",
}