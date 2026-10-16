"""
Modelos de dados do simulador.
Dataclasses representam o estado e os eventos.
"""

from dataclasses import asdict, dataclass, fields


@dataclass
class Trade:
    """Um trade executado (compra ou venda)."""
    timestamp: str          # ISO 8601
    tipo: str               # "COMPRA" ou "VENDA"
    preco_brl: float        # preço unitário em BRL
    quantidade_btc: float   # quantidade de BTC negociada
    valor_brl: float        # valor total em BRL (antes da taxa)
    taxa_brl: float         # taxa paga em BRL
    motivo: str             # "aporte", "compra_extra", "take_profit", "stop_loss"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CashFlow:
    """Depósito ou saque manual, separado do histórico de trades."""
    timestamp: str
    tipo: str               # "DEPOSITO" ou "SAQUE"
    valor_brl: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Estado:
    """Estado atual do simulador (persistido em data/state.json)."""
    capital_brl: float = 0.0
    capital_btc: float = 0.0
    preco_medio_brl: float = 0.0
    ultima_compra_ts: str = ""          # timestamp da última compra
    ultimo_preco_compra_brl: float = 0.0
    ultimo_aporte_ts: str = ""          # mantido para ler estados antigos
    take_profit_executado: bool = False
    stop_loss_executado: bool = False
    total_compras: int = 0
    total_vendas: int = 0
    total_taxas_brl: float = 0.0
    patrimonio_inicial_brl: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Estado":
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