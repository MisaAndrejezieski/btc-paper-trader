# BTC Paper Trader

Simulador de paper trading de Bitcoin em Python. 
Acompanha o preço real do BTC e simula uma estratégia de 
DCA + take-profit parcial + stop-loss, com capital fictício.
Também permite registrar compras e vendas manuais na cotação online atual.

## Estratégia

- **DCA:** aporte fixo a cada 24h
- **Compra extra:** dobra o aporte se o preço cair 5%
- **Take-profit:** vende 30% se o preço subir 8% sobre o preço médio
- **Stop-loss:** vende 50% se o preço cair 25% sobre o preço médio
- **Nunca vende tudo:** sempre fica uma parte exposta
- **Operação manual:** informe um valor em reais para comprar ou uma quantidade
  de BTC para vender. As ordens são simuladas e incluem a taxa configurada.
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

Execute a interface com `python main.py`. A estratégia automática continua ativa
junto às operações manuais.