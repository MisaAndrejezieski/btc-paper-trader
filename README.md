# BTC Paper Trader

Simulador de paper trading de Bitcoin em Python.
Acompanha o preço real do BTC e simula trades automáticos de compra extra,
take-profit parcial e stop-loss, com capital fictício. Depósitos e saques são
movimentações manuais de saldo, separadas do histórico de trades.

## Estratégia

- **Compra extra:** compra o dobro do valor-base configurado se o preço cair
  5% desde a última compra; usa o preço médio como referência em estados antigos.
- **Take-profit:** vende 30% se o preço subir 8% sobre o preço médio
- **Stop-loss:** vende 50% se o preço cair 25% sobre o preço médio
- **Nunca vende tudo:** sempre fica uma parte exposta
- **Operação manual:** informe um valor em reais para comprar ou uma quantidade
  de BTC para vender. As ordens são simuladas e incluem a taxa configurada.
- **Fluxo de caixa:** registre depósitos e saques manualmente. Eles alteram o
  saldo em BRL, não são trades, não pagam taxa e ficam em `cash_flow.json`.
  Saques só podem usar o saldo disponível em reais; BTC não é vendido
  automaticamente para cobrir um saque.
- **Capital e resultado:** o capital líquido considera o capital inicial mais
  depósitos menos saques. O lucro/prejuízo exibido compara esse valor ao
  patrimônio atual.
- **Limite da estratégia automática:** as regras atuais são fixas e não
  aprendem nem garantem lucro. Acompanhe o histórico e as métricas antes de
  considerar qualquer mudança na estratégia; todas as operações aqui são
  simuladas.
- **Cotação:** CoinGecko em BRL, com Binance BTC/BRL como alternativa. O preço
  em cache é exibido quando as fontes falham, mas nenhuma operação é executada
  até uma cotação online voltar.
- **Horários:** exibidos no fuso de Carambeí, Paraná (America/Sao_Paulo).
- **Gráficos:** cotação BTC/BRL das últimas 24h e cenário estatístico para as
  próximas 24h, com faixa baseada na volatilidade observada. A projeção combina
  momentum recente e um ajuste limitado pelo sinal de matérias que reportam
  perspectivas direcionais de analistas em CoinDesk e Cointelegraph. As matérias
  são exibidas como links; o sentimento é uma classificação lexical simples,
  não consenso de mercado nem previsão oficial. Se não houver matérias
  elegíveis, a projeção usa apenas os dados de mercado.
- **Atualização dos gráficos:** acompanha o ciclo de cotação (5 minutos);
  matérias são consultadas a cada 15 minutos.
- **Limites da projeção:** estimativa experimental, com incerteza explícita;
  não constitui recomendação ou aconselhamento financeiro.

## Setup

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Execute a interface com `python main.py`. A estratégia automática de trading
continua ativa, mas não adiciona capital nem faz compras periódicas de DCA;
deposite saldo manualmente para disponibilizar mais reais para os trades.