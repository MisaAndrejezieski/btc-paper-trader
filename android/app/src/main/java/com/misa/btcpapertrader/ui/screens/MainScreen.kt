package com.misa.btcpapertrader.ui.screens

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Snackbar
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.TabRowDefaults
import androidx.compose.material3.TabRowDefaults.tabIndicatorOffset
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.misa.btcpapertrader.domain.util.Formatter
import com.misa.btcpapertrader.ui.components.CardCotacao
import com.misa.btcpapertrader.ui.components.CardEstrategiaStatus
import com.misa.btcpapertrader.ui.components.CardMetricasRisco
import com.misa.btcpapertrader.ui.components.CardResumoPortfolio
import com.misa.btcpapertrader.ui.components.DialogEntradaValor
import com.misa.btcpapertrader.ui.components.ItemTrade
import com.misa.btcpapertrader.ui.theme.CyberpunkColors
import com.misa.btcpapertrader.ui.viewmodel.MainViewModel

@Composable
fun MainScreen(viewModel: MainViewModel) {

    val cotacao by viewModel.cotacaoState.collectAsState()
    val estado by viewModel.estadoState.collectAsState()
    val trades by viewModel.tradesState.collectAsState()
    val cashFlows by viewModel.cashFlowsState.collectAsState()
    val metricas by viewModel.metricasState.collectAsState()
    val isLoading by viewModel.isLoading.collectAsState()
    val mensagemFeedback by viewModel.mensagemFeedback.collectAsState()

    val snackbarHostState = remember { SnackbarHostState() }

    var showDialogDeposito by remember { mutableStateOf(false) }
    var showDialogSaque by remember { mutableStateOf(false) }
    var showDialogComprar by remember { mutableStateOf(false) }
    var showDialogVender by remember { mutableStateOf(false) }
    var showDialogResetConfirm by remember { mutableStateOf(false) }

    var selectedTabIndex by remember { mutableIntStateOf(0) }

    LaunchedEffect(mensagemFeedback) {
        mensagemFeedback?.let { msg ->
            snackbarHostState.showSnackbar(msg)
            viewModel.limparMensagem()
        }
    }

    Scaffold(
        snackbarHost = {
            SnackbarHost(hostState = snackbarHostState) { data ->
                Snackbar(
                    containerColor = CyberpunkColors.BgDestaque,
                    contentColor = CyberpunkColors.VerdeNeon,
                    actionColor = CyberpunkColors.VerdeMatrix
                ) {
                    Text(data.visuals.message, fontFamily = FontFamily.Monospace, fontSize = 12.sp)
                }
            }
        },
        containerColor = CyberpunkColors.BgPrincipal
    ) { paddingValues ->

        LazyColumn(
            modifier = Modifier
                .fillMaxSize()
                .padding(paddingValues)
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {

            // Header Principal
            item {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        text = "BTC PAPER TRADER",
                        color = CyberpunkColors.VerdeMatrix,
                        fontSize = 20.sp,
                        fontWeight = FontWeight.Bold,
                        fontFamily = FontFamily.Monospace
                    )
                    Text(
                        text = "v1.0 [ANDROID]",
                        color = CyberpunkColors.TextoApagado,
                        fontSize = 11.sp,
                        fontFamily = FontFamily.Monospace
                    )
                }
            }

            // Card de Cotação Mercado
            item {
                CardCotacao(
                    cotacao = cotacao,
                    isLoading = isLoading,
                    onRefreshClick = { viewModel.atualizarCotacao() }
                )
            }

            // Card de Patrimônio e Saldos
            item {
                CardResumoPortfolio(
                    estado = estado,
                    cotacaoAtual = cotacao?.precoBrl
                )
            }

            // Botões de Ação Rápida
            item {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        Button(
                            onClick = { showDialogDeposito = true },
                            modifier = Modifier.weight(1f),
                            colors = ButtonDefaults.buttonColors(containerColor = CyberpunkColors.BgCard, contentColor = CyberpunkColors.VerdeMatrix),
                            border = BorderStroke(1.dp, CyberpunkColors.VerdeMatrix),
                            shape = RoundedCornerShape(4.dp)
                        ) {
                            Text("+ DEPOSITAR", fontSize = 11.sp, fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold)
                        }

                        Button(
                            onClick = { showDialogSaque = true },
                            modifier = Modifier.weight(1f),
                            colors = ButtonDefaults.buttonColors(containerColor = CyberpunkColors.BgCard, contentColor = CyberpunkColors.RosaPastel),
                            border = BorderStroke(1.dp, CyberpunkColors.RosaPastel),
                            shape = RoundedCornerShape(4.dp)
                        ) {
                            Text("- SACAR", fontSize = 11.sp, fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold)
                        }
                    }

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        Button(
                            onClick = { showDialogComprar = true },
                            modifier = Modifier.weight(1f),
                            colors = ButtonDefaults.buttonColors(containerColor = CyberpunkColors.VerdeMatrix, contentColor = CyberpunkColors.BgPrincipal),
                            shape = RoundedCornerShape(4.dp)
                        ) {
                            Text("COMPRAR BTC", fontSize = 11.sp, fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold)
                        }

                        Button(
                            onClick = { showDialogVender = true },
                            modifier = Modifier.weight(1f),
                            colors = ButtonDefaults.buttonColors(containerColor = CyberpunkColors.Vermelho, contentColor = CyberpunkColors.BgPrincipal),
                            shape = RoundedCornerShape(4.dp)
                        ) {
                            Text("VENDER BTC", fontSize = 11.sp, fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold)
                        }
                    }
                }
            }

            // Card Status Estratégia Automática
            item {
                CardEstrategiaStatus(estado = estado)
            }

            // Card Métricas Risco
            item {
                CardMetricasRisco(metricas = metricas)
            }

            // Seção de Histórico com Abas
            item {
                Column {
                    TabRow(
                        selectedTabIndex = selectedTabIndex,
                        containerColor = CyberpunkColors.BgSecundario,
                        contentColor = CyberpunkColors.VerdeMatrix,
                        indicator = { tabPositions ->
                            TabRowDefaults.SecondaryIndicator(
                                Modifier.tabIndicatorOffset(tabPositions[selectedTabIndex]),
                                color = CyberpunkColors.VerdeMatrix
                            )
                        }
                    ) {
                        Tab(
                            selected = selectedTabIndex == 0,
                            onClick = { selectedTabIndex = 0 },
                            text = {
                                Text(
                                    "TRADES (${trades.size})",
                                    fontFamily = FontFamily.Monospace,
                                    fontSize = 11.sp,
                                    fontWeight = FontWeight.Bold,
                                    color = if (selectedTabIndex == 0) CyberpunkColors.VerdeMatrix else CyberpunkColors.TextoSecundario
                                )
                            }
                        )
                        Tab(
                            selected = selectedTabIndex == 1,
                            onClick = { selectedTabIndex = 1 },
                            text = {
                                Text(
                                    "FLUXO CAIXA (${cashFlows.size})",
                                    fontFamily = FontFamily.Monospace,
                                    fontSize = 11.sp,
                                    fontWeight = FontWeight.Bold,
                                    color = if (selectedTabIndex == 1) CyberpunkColors.VerdeMatrix else CyberpunkColors.TextoSecundario
                                )
                            }
                        )
                    }
                }
            }

            // Conteúdo da Aba
            if (selectedTabIndex == 0) {
                if (trades.isEmpty()) {
                    item {
                        Box(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(24.dp),
                            contentAlignment = Alignment.Center
                        ) {
                            Text(
                                "Nenhum trade realizado até o momento.",
                                color = CyberpunkColors.TextoApagado,
                                fontSize = 12.sp,
                                fontFamily = FontFamily.Monospace
                            )
                        }
                    }
                } else {
                    items(trades) { trade ->
                        ItemTrade(trade = trade)
                    }
                }
            } else {
                if (cashFlows.isEmpty()) {
                    item {
                        Box(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(24.dp),
                            contentAlignment = Alignment.Center
                        ) {
                            Text(
                                "Nenhum depósito ou saque registrado.",
                                color = CyberpunkColors.TextoApagado,
                                fontSize = 12.sp,
                                fontFamily = FontFamily.Monospace
                            )
                        }
                    }
                } else {
                    items(cashFlows) { cashFlow ->
                        val isDeposito = cashFlow.tipo.uppercase() == "DEPOSITO"
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
                                    Text(
                                        text = "[${cashFlow.tipo}]",
                                        color = if (isDeposito) CyberpunkColors.VerdeMatrix else CyberpunkColors.RosaPastel,
                                        fontWeight = FontWeight.Bold,
                                        fontSize = 12.sp,
                                        fontFamily = FontFamily.Monospace
                                    )
                                    Text(
                                        text = Formatter.formatarDataHora(cashFlow.timestamp),
                                        color = CyberpunkColors.TextoApagado,
                                        fontSize = 10.sp,
                                        fontFamily = FontFamily.Monospace
                                    )
                                }
                                Text(
                                    text = Formatter.formatarMoeda(cashFlow.valorBrl),
                                    color = CyberpunkColors.TextoPrimario,
                                    fontWeight = FontWeight.Bold,
                                    fontSize = 12.sp,
                                    fontFamily = FontFamily.Monospace
                                )
                            }
                        }
                    }
                }
            }

            // Botão de Reset no final
            item {
                Spacer(modifier = Modifier.height(16.dp))
                OutlinedButton(
                    onClick = { showDialogResetConfirm = true },
                    modifier = Modifier.fillMaxWidth(),
                    border = BorderStroke(1.dp, CyberpunkColors.Vermelho),
                    colors = ButtonDefaults.outlinedButtonColors(contentColor = CyberpunkColors.Vermelho),
                    shape = RoundedCornerShape(4.dp)
                ) {
                    Text("RESETAR SIMULAÇÃO", fontSize = 11.sp, fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold)
                }
            }
        }
    }

    // --- DIÁLOGOS DE ENTRADA ---
    if (showDialogDeposito) {
        DialogEntradaValor(
            titulo = "DEPOSITAR SALDO",
            labelCampo = "Valor em Reais (BRL)",
            onConfirm = { valor ->
                viewModel.depositar(valor)
                showDialogDeposito = false
            },
            onDismiss = { showDialogDeposito = false }
        )
    }

    if (showDialogSaque) {
        DialogEntradaValor(
            titulo = "SACAR SALDO",
            labelCampo = "Valor em Reais (BRL)",
            onConfirm = { valor ->
                viewModel.sacar(valor)
                showDialogSaque = false
            },
            onDismiss = { showDialogSaque = false }
        )
    }

    if (showDialogComprar) {
        DialogEntradaValor(
            titulo = "COMPRAR BITCOIN (MANUAL)",
            labelCampo = "Valor em Reais a Investir (BRL)",
            onConfirm = { valor ->
                viewModel.comprarManual(valor)
                showDialogComprar = false
            },
            onDismiss = { showDialogComprar = false }
        )
    }

    if (showDialogVender) {
        DialogEntradaValor(
            titulo = "VENDER BITCOIN (MANUAL)",
            labelCampo = "Quantidade em BTC a Vender",
            onConfirm = { quantidade ->
                viewModel.venderManual(quantidade)
                showDialogVender = false
            },
            onDismiss = { showDialogVender = false }
        )
    }

    if (showDialogResetConfirm) {
        AlertDialog(
            onDismissRequest = { showDialogResetConfirm = false },
            containerColor = CyberpunkColors.BgCard,
            title = {
                Text("RESETAR DADOS?", color = CyberpunkColors.Vermelho, fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold)
            },
            text = {
                Text(
                    "Esta ação apagará todo o histórico de trades, saldos e fluxo de caixa permanentemente.",
                    color = CyberpunkColors.TextoPrimario,
                    fontFamily = FontFamily.Monospace,
                    fontSize = 12.sp
                )
            },
            confirmButton = {
                Button(
                    onClick = {
                        viewModel.resetarSimulacao()
                        showDialogResetConfirm = false
                    },
                    colors = ButtonDefaults.buttonColors(containerColor = CyberpunkColors.Vermelho, contentColor = CyberpunkColors.BgPrincipal)
                ) {
                    Text("SIM, RESETAR", fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold)
                }
            },
            dismissButton = {
                TextButton(onClick = { showDialogResetConfirm = false }) {
                    Text("CANCELAR", color = CyberpunkColors.TextoSecundario, fontFamily = FontFamily.Monospace)
                }
            }
        )
    }
}
