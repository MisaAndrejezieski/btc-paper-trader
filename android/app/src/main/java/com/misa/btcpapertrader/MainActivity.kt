package com.misa.btcpapertrader

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import com.misa.btcpapertrader.ui.screens.MainScreen
import com.misa.btcpapertrader.ui.theme.BTCPaperTraderTheme
import com.misa.btcpapertrader.ui.viewmodel.MainViewModel

/**
 * Ponto de entrada da aplicação Android BTC Paper Trader.
 */
class MainActivity : ComponentActivity() {

    private val viewModel: MainViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        setContent {
            BTCPaperTraderTheme {
                MainScreen(viewModel = viewModel)
            }
        }
    }
}
