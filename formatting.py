"""Fun??es de formata??o compartilhadas pela interface e pelo ponto de entrada."""


def formatar_reais(valor: float, sinal: bool = False) -> str:
    """Formata valor monetário como reais no padrão brasileiro.

    Args:
        valor: Montante em BRL.
        sinal: Se verdadeiro, sempre inclui sinal ``+`` ou ``-``.

    Returns:
        str: Valor com ponto de milhar e vírgula decimal.

    Raises:
        ValueError: Se o valor não puder ser formatado numericamente.

    Side Effects:
        Nenhum.
    """
    formato = f"{valor:+,.2f}" if sinal else f"{valor:,.2f}"
    return formato.replace(",", "X").replace(".", ",").replace("X", ".")
