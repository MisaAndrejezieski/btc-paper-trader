"""
Paleta cyberpunk — verde matrix + pastel em fundo escuro.
Centraliza todas as cores para facilitar ajustes.
"""

CORES = {
    # Fundos
    "bg_principal":   "#0A0E0A",
    "bg_secundario":  "#121A12",
    "bg_card":        "#1A241A",
    "bg_destaque":    "#232E23",

    # Verde matrix
    "verde_matrix":   "#00FF41",
    "verde_neon":     "#39FF14",
    "verde_pastel":   "#A8E6A3",
    "verde_escuro":   "#0D3B0D",

    # Acentos pastel
    "rosa_pastel":    "#F5B8D0",
    "azul_pastel":    "#A8C8F0",
    "amarelo_pastel": "#F5E6A3",
    "vermelho":       "#FF5C5C",

    # Textos
    "texto_primario":   "#E8F5E8",
    "texto_secundario": "#A8B8A8",
    "texto_apagado":    "#5C6B5C",

    # Estados
    "lucro":   "#39FF14",
    "prejuizo": "#FF5C5C",
    "neutro":  "#A8C8F0",
}

# Fontes — usa JetBrains Mono se instalada, senão cai em Consolas
FONTE_MONO = "JetBrains Mono"
FONTE_FALLBACK = "Consolas"

FONTE_TITULO = (FONTE_MONO, 22, "bold")
FONTE_SUBTITULO = (FONTE_MONO, 14, "bold")
FONTE_LABEL = (FONTE_MONO, 12)
FONTE_VALOR = (FONTE_MONO, 28, "bold")
FONTE_VALOR_MEDIO = (FONTE_MONO, 18, "bold")
FONTE_PEQUENA = (FONTE_MONO, 10)