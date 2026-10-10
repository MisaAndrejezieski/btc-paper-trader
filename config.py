"""
Configurações globais do simulador.
Todos os parâmetros ajustáveis ficam aqui, centralizados.
"""

CONFIG = {
    # --- Capital inicial ---
    "capital_inicial_brl": 5000.00,        # R$ 5.000 fictícios

    # --- Aporte periódico (DCA) ---
    "intervalo_aporte_horas": 24,           # Aporta a cada 24h
    "valor_aporte_brl": 150.00,             # R$ 150 por aporte

    # --- Compra extra em queda forte ---
    "queda_para_compra_extra_pct": 5.0,     # Se caiu >= 5% desde a última compra
    "multiplicador_compra_extra": 2.0,      # Compra o dobro do aporte normal

    # --- Take-profit parcial ---
    "take_profit_pct": 8.0,                 # Vende quando lucro >= 8% sobre preço médio
    "percentual_venda_take_profit": 30,     # Vende 30% da posição em BTC

    # --- Stop-loss de proteção ---
    "stop_loss_pct": 25.0,                  # Se caiu >= 25% desde preço médio
    "percentual_venda_stop_loss": 50,       # Vende 50% da posição
    "stop_loss_ativo": True,                # Pode desligar para testar

    # --- Loop ---
    "intervalo_loop_minutos": 5,            # Checa preço a cada 5 min
    "max_iteracoes": 0,                     # 0 = infinito (Ctrl+C para parar)

    # --- Custos ---
    "taxa_operacao_pct": 0.1,               # 0,1% por operação (Binance padrão)

    # --- Conversão ---
    "usd_brl_fixo": 4.98,                   # Fallback se API de câmbio falhar

    # --- Arquivos ---
    "state_file": "state.json",
    "history_file": "history.json",
    "cache_dir": "data",
}