package com.misa.btcpapertrader.domain.model

/**
 * Representação de uma compra ou venda registrada no histórico.
 */
data class TradeModel(
    val id: Long = 0,
    val timestamp: String,
    val tipo: String, // "COMPRA" ou "VENDA"
    val precoBrl: Double,
    val quantidadeBtc: Double,
    val valorBrl: Double,
    val taxaBrl: Double,
    val motivo: String // "manual", "compra_extra", "take_profit", "stop_loss"
)

/**
 * Representação de fluxo de caixa (depósitos e saques manuais).
 */
data class CashFlowModel(
    val id: Long = 0,
    val timestamp: String,
    val tipo: String, // "DEPOSITO" ou "SAQUE"
    val valorBrl: Double
)

/**
 * Representação do estado atual do portfólio e parâmetros estratégicos.
 */
data class EstadoModel(
    val capitalBrl: Double = 0.0,
    val capitalBtc: Double = 0.0,
    val precoMedioBrl: Double = 0.0,
    val ultimaCompraTs: String = "",
    val ultimoPrecoCompraBrl: Double = 0.0,
    val takeProfitExecutado: Boolean = false,
    val stopLossExecutado: Boolean = false,
    val totalCompras: Int = 0,
    val totalVendas: Int = 0,
    val totalTaxasBrl: Double = 0.0,
    val patrimonioInicialBrl: Double = 0.0
)

/**
 * Cotação spot obtida online ou do cache local.
 */
data class CotacaoModel(
    val precoBrl: Double,
    val fonte: String,
    val timestampIso: String,
    val atual: Boolean
)

/**
 * Resultado da decisão da estratégia automática.
 */
sealed class EstrategiaAcao {
    object Nada : EstrategiaAcao()
    data class Compra(val valorBrl: Double, val motivo: String) : EstrategiaAcao()
    data class Venda(val percentual: Double, val motivo: String) : EstrategiaAcao()
}
