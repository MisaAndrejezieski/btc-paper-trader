# BTC Paper Trader

Simulador de paper trading de Bitcoin em Python. Começa com saldo zerado;
adicione saldo fictício manualmente antes de realizar a primeira compra.
Acompanha o preço real do BTC e simula trades automáticos de compra extra,
take-profit parcial e stop-loss, com capital fictício. Depósitos e saques são
movimentações manuais de saldo, separadas do histórico de trades.

## Estratégia

- **Compra extra:** compra R$ 5.000 (valor-base de R$ 1.000 vezes o multiplicador
  5) se o preço cair 2% desde a última compra; usa o preço médio como referência
  em estados antigos. Só executa se houver saldo suficiente em reais.
- **Primeira compra:** feita manualmente por você. Depois, compras extras e
  vendas automáticas usam apenas o saldo e os BTC já existentes no simulador.
- **Take-profit:** vende 50% uma vez se o preço subir 3% sobre o preço médio;
  pode voltar a disparar depois de uma nova compra.
- **Stop-loss:** vende 50% uma vez se o preço cair 10% sobre o preço médio;
  enquanto a queda persistir, não recompra automaticamente. Uma nova compra
  reabilita as regras de saída.
- **Vendas automáticas parciais:** não liquidam toda a posição de uma vez.
  Uma venda manual pode liquidar a quantidade informada, inclusive todo o BTC.
- **Backtest histórico:** selecione 7, 30, 90 dias ou 1 ano. A simulação começa
  com metade do patrimônio em BTC e metade em caixa e compara a estratégia com
  buy-and-hold, incluindo a taxa de operação configurada. Resultados passados
  não prevêem resultados futuros; a disponibilidade depende da CoinGecko.
- **Patrimônio e benchmark:** a curva é registrada a partir da instalação desta
  versão em `equity_history.json`, com ajustes para depósitos e saques. O
  benchmark buy-and-hold acompanha os mesmos fluxos, comprando/vendendo BTC na
  cotação registrada. Dados anteriores não são reconstruídos.
- **Métricas de risco:** drawdown e Sharpe são calculados sobre a série de
  patrimônio registrada; win rate, profit factor e tempo em posição usam trades
  fechados e lotes FIFO; exposição média é ponderada pela duração entre amostras.
  O Sharpe anualizado só fica disponível com observações em pelo menos três dias
  (dois retornos diários). Vendas sem compra correspondente no histórico não têm
  P&L FIFO calculável.
- **Exportação:** use “Exportar CSV” para salvar o histórico completo, incluindo
  P&L realizado por venda em FIFO e taxas. O arquivo usa separador ponto-e-vírgula
  para abrir melhor em planilhas configuradas para português.
- **Notificações:** trades automáticos geram um aviso visual temporário e um
  sinal sonoro na interface.
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
  momentum recente e um ajuste limitado pelo sinal lexical de matérias
  explicitamente sobre analistas. O painel próprio exibe até cinco notícias
  recentes sobre BTC dos feeds CoinDesk e Cointelegraph, com título, fonte,
  autoria, horário e classificação lexical; a classificação não é consenso de
  mercado nem previsão oficial. Se não houver matérias
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

Execute a interface com `python main.py`. Registre um depósito fictício para
adicionar saldo e faça a primeira compra manual. Depois, as regras automáticas
de trading usam apenas o saldo disponível; o simulador não adiciona capital nem
faz compras periódicas.