package com.misa.btcpapertrader.ui.components

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.misa.btcpapertrader.domain.model.CashFlowModel
import com.misa.btcpapertrader.domain.model.CotacaoModel
import com.misa.btcpapertrader.domain.model.EstadoModel
import com.misa.btcpapertrader.domain.model.TradeModel
import com.misa.btcpapertrader.domain.util.Formatter
import com.misa.btcpapertrader.domain.util.TradingMetrics
import com.misa.btcpapertrader.ui.theme.CyberpunkColors

/**
 * Card com cotação atual do BTC e origem do preço.
 */
@Composable
fun CardCotacao(
    cotacao: CotacaoModel?,
    isLoading: Boolean,
    onRefreshClick: () -> Unit
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = CyberpunkColors.BgCard),
        border = BorderStroke(1.dp, CyberpunkColors.VerdeEscuro),
        shape = RoundedCornerShape(8.dp)
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = "► BTC / BRL (SPOT)",
                    color = CyberpunkColors.TextoSecundario,
                    fontSize = 12.sp,
                    fontFamily = FontFamily.Monospace
                )
                OutlinedButton(
                    onClick = onRefreshClick,
                    enabled = !isLoading,
                    border = BorderStroke(1.dp, CyberpunkColors.VerdeMatrix),
                    colors = ButtonDefaults.outlinedButtonColors(contentColor = CyberpunkColors.VerdeMatrix),
                    shape = RoundedCornerShape(4.dp)
                ) {
                    Text(
                        text = if (isLoading) "ATUALIZANDO..." else "ATUALIZAR",
                        fontSize = 10.sp,
                        fontFamily = FontFamily.Monospace
                    )
                }
            }

            Spacer(modifier = Modifier.height(8.dp))

            if (cotacao != null) {
                Text(
                    text = Formatter.formatarMoeda(cotacao.precoBrl),
                    color = CyberpunkColors.VerdeNeon,
                    fontSize = 26.sp,
                    fontWeight = FontWeight.Bold,
                    fontFamily = FontFamily.Monospace
                )
                Spacer(modifier = Modifier.height(4.dp))
                Row {
                    Text(
                        text = "Fonte: ${cotacao.fonte} ",
                        color = CyberpunkColors.TextoSecundario,
                        fontSize = 11.sp,
                        fontFamily = FontFamily.Monospace
                    )
                    Text(
                        text = if (cotacao.atual) "[ONLINE]" else "[CACHE]",
                        color = if (cotacao.atual) CyberpunkColors.VerdeMatrix else CyberpunkColors.AmareloPastel,
                        fontSize = 11.sp,
                        fontWeight = FontWeight.Bold,
                        fontFamily = FontFamily.Monospace
                    )
                }
            } else {
                Text(
                    text = "Conectando ao mercado...",
                    color = CyberpunkColors.TextoApagado,
                    fontSize = 16.sp,
                    fontFamily = FontFamily.Monospace
                )
            }
        }
    }
}

/**
 * Card de resumo do portfólio e saldos.
 */
@Composable
fun CardResumoPortfolio(
    estado: EstadoModel,
    cotacaoAtual: Double?
) {
    val btcValorBrl = (cotacaoAtual ?: 0.0) * estado.capitalBtc
    val patrimonioTotal = estado.capitalBrl + btcValorBrl
    val lucroTotal = if (estado.patrimonioInicialBrl > 0) patrimonioTotal - estado.patrimonioInicialBrl else 0.0
    val lucroPct = if (estado.patrimonioInicialBrl > 0) (lucroTotal / estado.patrimonioInicialBrl) * 100 else 0.0

    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = CyberpunkColors.BgSecundario),
        border = BorderStroke(1.dp, CyberpunkColors.VerdeMatrix),
        shape = RoundedCornerShape(8.dp)
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Text(
                text = "► PATRIMÔNIO TOTAL",
                color = CyberpunkColors.TextoSecundario,
                fontSize = 12.sp,
                fontFamily = FontFamily.Monospace
            )
            Text(
                text = Formatter.formatarMoeda(patrimonioTotal),
                color = CyberpunkColors.TextoPrimario,
                fontSize = 24.sp,
                fontWeight = FontWeight.Bold,
                fontFamily = FontFamily.Monospace
            )

            if (estado.patrimonioInicialBrl > 0) {
                Text(
                    text = "Resultado: ${Formatter.formatarMoeda(lucroTotal, comSinal = true)} (${Formatter.formatarPorcentagem(lucroPct)})",
                    color = if (lucroTotal >= 0) CyberpunkColors.Lucro else CyberpunkColors.Prejuizo,
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Bold,
                    fontFamily = FontFamily.Monospace
                )
            }

            Spacer(modifier = Modifier.height(12.dp))

            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Column {
                    Text("Caixa (BRL)", color = CyberpunkColors.TextoSecundario, fontSize = 11.sp, fontFamily = FontFamily.Monospace)
                    Text(Formatter.formatarMoeda(estado.capitalBrl), color = CyberpunkColors.TextoPrimario, fontSize = 14.sp, fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold)
                }
                Column {
                    Text("Posição BTC", color = CyberpunkColors.TextoSecundario, fontSize = 11.sp, fontFamily = FontFamily.Monospace)
                    Text(Formatter.formatarBtc(estado.capitalBtc), color = CyberpunkColors.TextoPrimario, fontSize = 14.sp, fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold)
                }
            }

            if (estado.precoMedioBrl > 0) {
                Spacer(modifier = Modifier.height(8.dp))
                Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    Text("Preço Médio BRL:", color = CyberpunkColors.TextoSecundario, fontSize = 11.sp, fontFamily = FontFamily.Monospace)
                    Text(Formatter.formatarMoeda(estado.precoMedioBrl), color = CyberpunkColors.AmareloPastel, fontSize = 11.sp, fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold)
                }
            }
        }
    }
}

/**
 * Card informativo das regras da estratégia automática.
 */
@Composable
fun CardEstrategiaStatus(estado: EstadoModel) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = CyberpunkColors.BgCard),
        border = BorderStroke(1.dp, CyberpunkColors.VerdeEscuro),
        shape = RoundedCornerShape(8.dp)
    ) {
        Column(modifier = Modifier.padding(12.dp)) {
            Text(
                text = "► ESTRATÉGIA AUTOMÁTICA",
                color = CyberpunkColors.VerdeMatrix,
                fontSize = 12.sp,
                fontWeight = FontWeight.Bold,
                fontFamily = FontFamily.Monospace
            )
            Spacer(modifier = Modifier.height(6.dp))
            Text("• Compra Extra: R$ 5.000 se o preço cair 2.0%", color = CyberpunkColors.TextoSecundario, fontSize = 11.sp, fontFamily = FontFamily.Monospace)
            Text("• Take-Profit: Vende 50% se subir 3.0%", color = CyberpunkColors.TextoSecundario, fontSize = 11.sp, fontFamily = FontFamily.Monospace)
            Text("• Stop-Loss: Vende 50% se cair 10.0%", color = CyberpunkColors.TextoSecundario, fontSize = 11.sp, fontFamily = FontFamily.Monospace)

            if (estado.takeProfitExecutado || estado.stopLossExecutado) {
                Spacer(modifier = Modifier.height(4.dp))
                Row {
                    if (estado.takeProfitExecutado) {
                        Text("[TAKE-PROFIT EXECUTADO] ", color = CyberpunkColors.VerdeNeon, fontSize = 10.sp, fontFamily = FontFamily.Monospace)
                    }
                    if (estado.stopLossExecutado) {
                        Text("[STOP-LOSS EXECUTADO]", color = CyberpunkColors.Vermelho, fontSize = 10.sp, fontFamily = FontFamily.Monospace)
                    }
                }
            }
        }
    }
}

/**
 * Card com métricas de desempenho e risco (Win Rate, Profit Factor, P&L FIFO).
 */
@Composable
fun CardMetricasRisco(metricas: TradingMetrics) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = CyberpunkColors.BgCard),
        border = BorderStroke(1.dp, CyberpunkColors.VerdeEscuro),
        shape = RoundedCornerShape(8.dp)
    ) {
        Column(modifier = Modifier.padding(12.dp)) {
            Text(
                text = "► MÉTRICAS E DESEMPENHO (FIFO)",
                color = CyberpunkColors.VerdePastel,
                fontSize = 12.sp,
                fontWeight = FontWeight.Bold,
                fontFamily = FontFamily.Monospace
            )
            Spacer(modifier = Modifier.height(8.dp))

            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Column {
                    Text("Win Rate", color = CyberpunkColors.TextoSecundario, fontSize = 10.sp, fontFamily = FontFamily.Monospace)
                    Text(
                        text = if (metricas.winRatePct != null) Formatter.formatarPorcentagem(metricas.winRatePct, false) else "N/A",
                        color = CyberpunkColors.TextoPrimario,
                        fontSize = 14.sp,
                        fontWeight = FontWeight.Bold,
                        fontFamily = FontFamily.Monospace
                    )
                }
                Column {
                    Text("Profit Factor", color = CyberpunkColors.TextoSecundario, fontSize = 10.sp, fontFamily = FontFamily.Monospace)
                    Text(
                        text = when {
                            metricas.profitFactor == null -> "N/A"
                            metricas.profitFactor.isInfinite() -> "∞"
                            else -> String.format("%.2f", metricas.profitFactor)
                        },
                        color = CyberpunkColors.TextoPrimario,
                        fontSize = 14.sp,
                        fontWeight = FontWeight.Bold,
                        fontFamily = FontFamily.Monospace
                    )
                }
                Column {
                    Text("P&L Realizado", color = CyberpunkColors.TextoSecundario, fontSize = 10.sp, fontFamily = FontFamily.Monospace)
                    Text(
                        text = Formatter.formatarMoeda(metricas.totalPnlRealizado, comSinal = true),
                        color = if (metricas.totalPnlRealizado >= 0) CyberpunkColors.Lucro else CyberpunkColors.Prejuizo,
                        fontSize = 14.sp,
                        fontWeight = FontWeight.Bold,
                        fontFamily = FontFamily.Monospace
                    )
                }
            }
        }
    }
}

/**
 * Item individual da lista de trades.
 */
@Composable
fun ItemTrade(trade: TradeModel) {
    val isCompra = trade.tipo.uppercase() == "COMPRA"
    val corTipo = if (isCompra) CyberpunkColors.VerdeNeon else CyberpunkColors.RosaPastel

    Card(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp),
        colors = CardDefaults.cardColors(containerColor = CyberpunkColors.BgSecundario),
        border = BorderStroke(1.dp, CyberpunkColors.BgDestaque),
        shape = RoundedCornerShape(4.dp)
    ) {
        Row(
            modifier = Modifier
                .padding(10.dp)
                .fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column {
                Row {
                    Text(
                        text = "[${trade.tipo}] ",
                        color = corTipo,
                        fontWeight = FontWeight.Bold,
                        fontSize = 12.sp,
                        fontFamily = FontFamily.Monospace
                    )
                    Text(
                        text = trade.motivo,
                        color = CyberpunkColors.TextoSecundario,
                        fontSize = 12.sp,
                        fontFamily = FontFamily.Monospace
                    )
                }
                Text(
                    text = Formatter.formatarDataHora(trade.timestamp),
                    color = CyberpunkColors.TextoApagado,
                    fontSize = 10.sp,
                    fontFamily = FontFamily.Monospace
                )
            }

            Column(horizontalAlignment = Alignment.End) {
                Text(
                    text = Formatter.formatarBtc(trade.quantidadeBtc),
                    color = CyberpunkColors.TextoPrimario,
                    fontWeight = FontWeight.Bold,
                    fontSize = 12.sp,
                    fontFamily = FontFamily.Monospace
                )
                Text(
                    text = "@ ${Formatter.formatarMoeda(trade.precoBrl)}",
                    color = CyberpunkColors.TextoSecundario,
                    fontSize = 11.sp,
                    fontFamily = FontFamily.Monospace
                )
            }
        }
    }
}

/**
 * Dialog genérico para inserção de valor monetário ou quantidade.
 */
@Composable
fun DialogEntradaValor(
    titulo: String,
    labelCampo: String,
    onConfirm: (Double) -> Unit,
    onDismiss: () -> Unit
) {
    var textValue by remember { mutableStateOf("") }
    var errorText by remember { mutableStateOf<String?>(null) }

    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = CyberpunkColors.BgCard,
        titleColor = CyberpunkColors.VerdeMatrix,
        title = {
            Text(titulo, fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold)
        },
        text = {
            Column {
                OutlinedTextField(
                    value = textValue,
                    onValueChange = {
                        textValue = it
                        errorText = null
                    },
                    label = { Text(labelCampo, color = CyberpunkColors.TextoSecundario) },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedBorderColor = CyberpunkColors.VerdeMatrix,
                        unfocusedBorderColor = CyberpunkColors.TextoApagado,
                        focusedTextColor = CyberpunkColors.TextoPrimario
                    )
                )
                if (errorText != null) {
                    Text(
                        text = errorText!!,
                        color = CyberpunkColors.Vermelho,
                        fontSize = 11.sp,
                        fontFamily = FontFamily.Monospace,
                        modifier = Modifier.padding(top = 4.dp)
                    )
                }
            }
        },
        confirmButton = {
            Button(
                onClick = {
                    val doubleValue = textValue.replace(",", ".").toDoubleOrNull()
                    if (doubleValue != null && doubleValue > 0) {
                        onConfirm(doubleValue)
                    } else {
                        errorText = "Digite um valor numérico válido e maior que zero."
                    }
                },
                colors = ButtonDefaults.buttonColors(containerColor = CyberpunkColors.VerdeMatrix, contentColor = CyberpunkColors.BgPrincipal)
            ) {
                Text("CONFIRMAR", fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold)
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) {
                Text("CANCELAR", color = CyberpunkColors.TextoSecundario, fontFamily = FontFamily.Monospace)
            }
        }
    )
}
