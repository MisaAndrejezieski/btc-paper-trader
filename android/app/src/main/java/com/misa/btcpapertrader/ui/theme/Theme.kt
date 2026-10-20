package com.misa.btcpapertrader.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable

private val CyberpunkDarkColorScheme = darkColorScheme(
    primary = CyberpunkColors.VerdeMatrix,
    onPrimary = CyberpunkColors.BgPrincipal,
    secondary = CyberpunkColors.VerdeNeon,
    onSecondary = CyberpunkColors.BgPrincipal,
    background = CyberpunkColors.BgPrincipal,
    onBackground = CyberpunkColors.TextoPrimario,
    surface = CyberpunkColors.BgCard,
    onSurface = CyberpunkColors.TextoPrimario,
    surfaceVariant = CyberpunkColors.BgSecundario,
    onSurfaceVariant = CyberpunkColors.TextoSecundario,
    error = CyberpunkColors.Vermelho,
    onError = CyberpunkColors.BgPrincipal
)

@Composable
fun BTCPaperTraderTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = CyberpunkDarkColorScheme,
        typography = CyberpunkTypography,
        content = content
    )
}
