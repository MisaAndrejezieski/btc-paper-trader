package com.misa.btcpapertrader.domain.usecase

import com.misa.btcpapertrader.domain.model.CashFlowModel
import com.misa.btcpapertrader.domain.model.EstrategiaAcao
import com.misa.btcpapertrader.domain.model.EstadoModel
import com.misa.btcpapertrader.domain.model.TradeModel
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * Configurações da estratégia e do simulador (espelho do config.py original).
 */
object Config {
    const val VALOR_COMPRA_BASE_BRL = 1000.00
    const val QUEDA_PARA_COMPRA_EXTRA_PCT = 2.0
    const val MULTIPLICADOR_COMPRA_EXTRA = 5.0
    const val TAKE_PROFIT_PCT = 3.0
    const val PERCENTUAL_VENDA_TAKE_PROFIT = 50.0
    const val STOP_LOSS_PCT = 10.0
    const val PERCENTUAL_VENDA_STOP_LOSS = 50.0
    const val STOP_LOSS_ATIVO = true
    const val TAXA_OPERACAO_PCT = 0.1
}

/**
 * Motor de execução de trading e motor de estratégia automática.
 * Tradução 1:1 de strategy.py e simulator.py do projeto Python.
 */
object TradingEngine {

    private fun agoraIsoUtc(): String {
        val sdf = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US)
        sdf.timeZone = TimeZone.getTimeZone("UTC")
        return sdf.format(Date())
    }

    /**
     * Avalia o estado atual e o preço do mercado e decide a ação automática.
     * Função pura (tradução exata de decidir_acao em strategy.py).
     */
    fun decidirAcao(estado: EstadoModel, precoBrl: Double): EstrategiaAcao {
        require(precoBrl > 0 && precoBrl.isFinite()) { "O preço precisa ser maior que zero." }

        val variacao = if (estado.precoMedioBrl > 0) {
            (precoBrl - estado.precoMedioBrl) / estado.precoMedioBrl
        } else {
            0.0
        }

        // Evita repetir a proteção ou recomprar enquanto a queda persiste após stop-loss
        if (Config.STOP_LOSS_ATIVO && estado.stopLossExecutado && variacao <= -(Config.STOP_LOSS_PCT / 100.0)) {
            return EstrategiaAcao.Nada
        }

        // 1. Verifica Stop-loss (prioridade)
        if (Config.STOP_LOSS_ATIVO && !estado.stopLossExecutado && estado.precoMedioBrl > 0) {
            if (variacao <= -(Config.STOP_LOSS_PCT / 100.0)) {
                return EstrategiaAcao.Venda(
                    percentual = Config.PERCENTUAL_VENDA_STOP_LOSS,
                    motivo = "stop_loss"
                )
            }
        }

        // 2. Verifica Take-profit
        if (estado.precoMedioBrl > 0 && estado.capitalBtc > 0 && !estado.takeProfitExecutado) {
            if (variacao >= (Config.TAKE_PROFIT_PCT / 100.0)) {
                return EstrategiaAcao.Venda(
                    percentual = Config.PERCENTUAL_VENDA_TAKE_PROFIT,
                    motivo = "take_profit"
                )
            }
        }

        // 3. Verifica compra extra em queda forte
        if (estado.ultimaCompraTs.isNotBlank() && estado.capitalBrl > 0) {
            val ultimaCompraPreco = if (estado.ultimoPrecoCompraBrl > 0) estado.ultimoPrecoCompraBrl else estado.precoMedioBrl
            if (ultimaCompraPreco > 0) {
                val variacaoUltimaCompra = (precoBrl - ultimaCompraPreco) / ultimaCompraPreco
                if (variacaoUltimaCompra <= -(Config.QUEDA_PARA_COMPRA_EXTRA_PCT / 100.0)) {
                    val valor = Config.VALOR_COMPRA_BASE_BRL * Config.MULTIPLICADOR_COMPRA_EXTRA
                    if (estado.capitalBrl >= valor) {
                        return EstrategiaAcao.Compra(
                            valorBrl = valor,
                            motivo = "compra_extra"
                        )
                    }
                }
            }
        }

        return EstrategiaAcao.Nada
    }

    /**
     * Executa um depósito fictício.
     */
    fun registrarDeposito(estado: EstadoModel, valorBrl: Double): Pair<EstadoModel, CashFlowModel> {
        require(valorBrl > 0 && valorBrl.isFinite()) { "O valor do depósito precisa ser maior que zero." }
        val novoEstado = estado.copy(
            capitalBrl = estado.capitalBrl + valorBrl,
            patrimonioInicialBrl = if (estado.patrimonioInicialBrl == 0.0) valorBrl else estado.patrimonioInicialBrl
        )
        val cashFlow = CashFlowModel(
            timestamp = agoraIsoUtc(),
            tipo = "DEPOSITO",
            valorBrl = valorBrl
        )
        return Pair(novoEstado, cashFlow)
    }

    /**
     * Executa um saque fictício.
     */
    fun registrarSaque(estado: EstadoModel, valorBrl: Double): Pair<EstadoModel, CashFlowModel> {
        require(valorBrl > 0 && valorBrl.isFinite()) { "O valor do saque precisa ser maior que zero." }
        require(valorBrl <= estado.capitalBrl) { "O valor do saque excede o saldo disponível em reais." }

        val novoEstado = estado.copy(capitalBrl = estado.capitalBrl - valorBrl)
        val cashFlow = CashFlowModel(
            timestamp = agoraIsoUtc(),
            tipo = "SAQUE",
            valorBrl = valorBrl
        )
        return Pair(novoEstado, cashFlow)
    }

    /**
     * Executa uma compra simulada com preço médio ponderado e taxa.
     */
    fun executarCompra(
        estado: EstadoModel,
        precoBrl: Double,
        valorBrlDesejado: Double,
        motivo: String
    ): Pair<EstadoModel, TradeModel> {
        require(precoBrl > 0 && precoBrl.isFinite()) { "O preço precisa ser maior que zero." }
        require(valorBrlDesejado > 0 && valorBrlDesejado.isFinite()) { "O valor da compra precisa ser maior que zero." }

        val valorEfetivoBrl = Math.min(valorBrlDesejado, estado.capitalBrl)
        require(valorEfetivoBrl > 0) { "Não há saldo em reais suficiente para comprar." }

        val taxa = valorEfetivoBrl * (Config.TAXA_OPERACAO_PCT / 100.0)
        val valorLiquido = valorEfetivoBrl - taxa
        val quantidadeComprada = valorLiquido / precoBrl

        val novoTotalBtc = estado.capitalBtc + quantidadeComprada
        val novoPrecoMedio = if (novoTotalBtc > 0) {
            ((estado.precoMedioBrl * estado.capitalBtc) + (precoBrl * quantidadeComprada)) / novoTotalBtc
        } else {
            precoBrl
        }

        val timestamp = agoraIsoUtc()
        val novoEstado = estado.copy(
            capitalBrl = estado.capitalBrl - valorEfetivoBrl,
            capitalBtc = novoTotalBtc,
            precoMedioBrl = novoPrecoMedio,
            ultimaCompraTs = timestamp,
            ultimoPrecoCompraBrl = precoBrl,
            takeProfitExecutado = false,
            stopLossExecutado = false,
            totalCompras = estado.totalCompras + 1,
            totalTaxasBrl = estado.totalTaxasBrl + taxa
        )

        val trade = TradeModel(
            timestamp = timestamp,
            tipo = "COMPRA",
            precoBrl = precoBrl,
            quantidadeBtc = quantidadeComprada,
            valorBrl = valorEfetivoBrl,
            taxaBrl = taxa,
            motivo = motivo
        )

        return Pair(novoEstado, trade)
    }

    /**
     * Executa uma venda por quantidade de BTC.
     */
    fun executarVendaQuantidade(
        estado: EstadoModel,
        precoBrl: Double,
        quantidadeVenda: Double,
        motivo: String
    ): Pair<EstadoModel, TradeModel> {
        require(precoBrl > 0 && precoBrl.isFinite()) { "O preço precisa ser maior que zero." }
        require(quantidadeVenda > 0 && quantidadeVenda.isFinite()) { "A quantidade de BTC precisa ser maior que zero." }
        require(quantidadeVenda <= estado.capitalBtc + 1e-9) { "A quantidade informada excede o saldo disponível em BTC." }

        val qtdReal = Math.min(quantidadeVenda, estado.capitalBtc)
        val valorBruto = qtdReal * precoBrl
        val taxa = valorBruto * (Config.TAXA_OPERACAO_PCT / 100.0)
        val valorLiquido = valorBruto - taxa

        var novoBtc = estado.capitalBtc - qtdReal
        var novoPrecoMedio = estado.precoMedioBrl

        if (novoBtc <= 1e-12) {
            novoBtc = 0.0
            novoPrecoMedio = 0.0
        }

        val novoEstado = estado.copy(
            capitalBrl = estado.capitalBrl + valorLiquido,
            capitalBtc = novoBtc,
            precoMedioBrl = novoPrecoMedio,
            takeProfitExecutado = if (motivo == "take_profit") true else estado.takeProfitExecutado,
            stopLossExecutado = if (motivo == "stop_loss") true else estado.stopLossExecutado,
            totalVendas = estado.totalVendas + 1,
            totalTaxasBrl = estado.totalTaxasBrl + taxa
        )

        val trade = TradeModel(
            timestamp = agoraIsoUtc(),
            tipo = "VENDA",
            precoBrl = precoBrl,
            quantidadeBtc = qtdReal,
            valorBrl = valorBruto,
            taxaBrl = taxa,
            motivo = motivo
        )

        return Pair(novoEstado, trade)
    }

    /**
     * Executa venda por percentual da posição atual em BTC (ex: 50%).
     */
    fun executarVendaPercentual(
        estado: EstadoModel,
        precoBrl: Double,
        percentual: Double,
        motivo: String
    ): Pair<EstadoModel, TradeModel>? {
        require(percentual in 0.01..100.0) { "O percentual precisa estar entre 0 e 100%." }
        val quantidade = estado.capitalBtc * (percentual / 100.0)
        if (quantidade <= 0) return null
        return executarVendaQuantidade(estado, precoBrl, quantidade, motivo)
    }
}
