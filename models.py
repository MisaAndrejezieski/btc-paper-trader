"""
Modelos de dados do simulador.
Dataclasses representam o estado e os eventos.
"""

from dataclasses import asdict, dataclass, fields


@dataclass
class Trade:
    """Uma compra ou venda simulada registrada no histórico.

    Attributes:
        timestamp: Data/hora ISO 8601 da operação; normalmente inclui UTC.
        tipo: Direção da operação, ``"COMPRA"`` ou ``"VENDA"``.
        preco_brl: Preço unitário do BTC em reais (BRL/BTC).
        quantidade_btc: Quantidade negociada em BTC.
        valor_brl: Valor bruto da operação em BRL, antes das taxas.
        taxa_brl: Taxa da operação em BRL.
        motivo: Origem da operação, como ``"manual"``, ``"aporte"``,
            ``"compra_extra"``, ``"take_profit"`` ou ``"stop_loss"``.
    """

    timestamp: str
    tipo: str
    preco_brl: float
    quantidade_btc: float
    valor_brl: float
    taxa_brl: float
    motivo: str

    def to_dict(self) -> dict:
        """Converte os campos do trade em um dicionário serializável.

        Returns:
            dict: Mapeamento dos nomes de campo para seus valores.

        Side Effects:
            Nenhum; cria um dicionário novo sem alterar o trade.
        """
        return asdict(self)


@dataclass
class CashFlow:
    """Depósito ou saque manual, separado do histórico de trades.

    Attributes:
        timestamp: Data/hora ISO 8601 do evento, normalmente em UTC.
        tipo: Direção do fluxo, ``"DEPOSITO"`` ou ``"SAQUE"``.
        valor_brl: Valor movimentado em reais (BRL), sempre positivo.
    """

    timestamp: str
    tipo: str
    valor_brl: float

    def to_dict(self) -> dict:
        """Converte o evento em um dicionário serializável.

        Returns:
            dict: Mapeamento dos nomes de campo para seus valores.

        Side Effects:
            Nenhum; cria um dicionário novo sem alterar o evento.
        """
        return asdict(self)


@dataclass
class Estado:
    """Estado contábil e estratégico atual, salvo em ``state.json``.

    Valores monetários são BRL, quantidades de cripto são BTC, preços são
    BRL/BTC e contadores são inteiros. Timestamps são strings ISO 8601.

    Attributes:
        capital_brl: Saldo de caixa disponível em reais.
        capital_btc: Saldo atual de Bitcoin.
        preco_medio_brl: Custo médio de aquisição por BTC em BRL/BTC.
        ultima_compra_ts: Timestamp ISO 8601 da compra mais recente.
        ultimo_preco_compra_brl: Preço BRL/BTC da compra mais recente.
        ultimo_aporte_ts: Campo legado mantido para compatibilidade de leitura.
        take_profit_executado: Indica se o take-profit da posição já ocorreu.
        stop_loss_executado: Indica se o stop-loss da posição já ocorreu.
        total_compras: Número acumulado de operações de compra.
        total_vendas: Número acumulado de operações de venda.
        total_taxas_brl: Total acumulado de taxas em reais.
        patrimonio_inicial_brl: Capital inicial usado como referência de
            resultado, em reais.
    """

    capital_brl: float = 0.0
    capital_btc: float = 0.0
    preco_medio_brl: float = 0.0
    ultima_compra_ts: str = ""  # timestamp da última compra
    ultimo_preco_compra_brl: float = 0.0
    ultimo_aporte_ts: str = ""  # mantido para ler estados antigos
    take_profit_executado: bool = False
    stop_loss_executado: bool = False
    total_compras: int = 0
    total_vendas: int = 0
    total_taxas_brl: float = 0.0
    patrimonio_inicial_brl: float = 0.0

    def to_dict(self) -> dict:
        """Converte o estado em um dicionário adequado a JSON.

        Returns:
            dict: Cópia dos campos atuais do estado.

        Side Effects:
            Nenhum; não altera o estado.
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Estado":
        """Cria estado de um registro persistido, ignorando chaves desconhecidas.

        Args:
            d: Dicionário JSON contendo zero ou mais campos conhecidos.

        Returns:
            Estado: Instância preenchida; campos ausentes usam defaults.

        Raises:
            TypeError: Se ``d`` não for um dicionário.

        Side Effects:
            Nenhum; não lê nem grava arquivos.
        """
        if not isinstance(d, dict):
            raise TypeError("O estado precisa ser um dicionário.")
        payload = {
            field_def.name: d.get(field_def.name, field_def.default)
            for field_def in fields(cls)
        }
        for key, value in d.items():
            if key in payload:
                payload[key] = value
        return cls(**payload)
