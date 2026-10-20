package com.misa.btcpapertrader.domain.util

import com.misa.btcpapertrader.domain.model.TradeModel
import kotlin.math.abs

/**
 * Métricas calculadas sobre o histórico de trades.
 */
data class TradingMetrics(
    val winRatePct: Double?,
    val profitFactor: Double?,
    val totalPnlRealizado: Double,
    val totalTradesFechados: Int,
    val totalVitorias: Int,
    val totalDerrotas: Int
)

/**
 * Lote de compra para o cálculo FIFO de P&L realizado.
 */
private data class FifoLot(
    var quantidadeRemaining: Double,
    val custoUnitarioBrl: Double
)

object TradingAnalytics {

    /**
     * Calculador de P&L realizado por trade de VENDA utilizando lotes FIFO.
     * Retorna um mapa [tradeId -> P&L em BRL (líquido)].
     */
    fun calcularPnlRealizadoFifo(trades: List<TradeModel>): Map<Long, Double> {
        val lots = mutableListOf<FifoLot>()
        val result = mutableMapOf<Long, Double>()

        for (trade in trades) {
            val tipo = trade.tipo.uppercase()
            if (tipo == "COMPRA") {
                if (trade.quantidadeBtc > 0 && trade.valorBrl >= 0) {
                    val custoUnitario = (trade.valorBrl) / trade.quantidadeBtc
                    lots.add(FifoLot(trade.quantidadeBtc, custoUnitario))
                }
            } else if (tipo == "VENDA" && trade.quantidadeBtc > 0) {
                val receitaLiquidaPorBtc = (trade.valorBrl - trade.taxaBrl) / trade.quantidadeBtc
                var remaining = trade.quantidadeBtc
                var pnl = 0.0

                val lotIterator = lots.iterator()
                while (lotIterator.hasNext() && remaining > 1e-12) {
                    val lot = lotIterator.next()
                    val matched = Math.min(remaining, lot.quantidadeRemaining)
                    pnl += matched * (receitaLiquidaPorBtc - lot.custoUnitarioBrl)
                    lot.quantidadeRemaining -= matched
                    remaining -= matched
                    if (lot.quantidadeRemaining <= 1e-12) {
                        lotIterator.remove()
                    }
                }

                if (remaining <= 1e-9) {
                    result[trade.id] = pnl
                }
            }
        }
        return result
    }

    /**
     * Calcula métricas agregadas de risco e desempenho (Win Rate, Profit Factor, P&L total).
     */
    fun calcularMetricas(trades: List<TradeModel>): TradingMetrics {
        val pnlMap = calcularPnlRealizadoFifo(trades)
        val sellPnls = pnlMap.values.toList()

        val wins = sellPnls.filter { it > 0 }
        val losses = sellPnls.filter { it < 0 }

        val winRate = if (sellPnls.isNotEmpty()) (wins.size.toDouble() / sellPnls.size) * 100 else null
        val sumWins = wins.sum()
        val sumLosses = abs(losses.sum())

        val profitFactor = when {
            losses.isNotEmpty() && sumLosses > 0 -> sumWins / sumLosses
            wins.isNotEmpty() -> Double.POSITIVE_INFINITY
            else -> null
        }

        return TradingMetrics(
            winRatePct = winRate,
            profitFactor = profitFactor,
            totalPnlRealizado = sellPnls.sum(),
            totalTradesFechados = sellPnls.size,
            totalVitorias = wins.size,
            totalDerrotas = losses.size
        )
    }
}
