package com.misa.btcpapertrader.domain.util

import java.text.NumberFormat
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * Utilitários de formatação para moeda (BRL), cripto (BTC), porcentagens e datas.
 */
object Formatter {

    private val ptBrLocale = Locale("pt", "BR")

    /**
     * Formata um valor numérico em Reais (BRL) no padrão brasileiro.
     * Exemplo: 1234.56 -> "1.234,56" ou "+1.234,56"
     */
    fun formatarReais(valor: Double, comSinal: Boolean = false): String {
        val formatter = NumberFormat.getNumberInstance(ptBrLocale).apply {
            minimumFractionDigits = 2
            maximumFractionDigits = 2
        }
        val formatted = formatter.format(Math.abs(valor))
        val sinal = when {
            valor > 0 -> if (comSinal) "+" else ""
            valor < 0 -> "-"
            else -> ""
        }
        return "$sinal$formatted"
    }

    /**
     * Formata um valor numérico em BRL adicionando o prefixo R$.
     */
    fun formatarMoeda(valor: Double, comSinal: Boolean = false): String {
        return "R$ ${formatarReais(valor, comSinal)}"
    }

    /**
     * Formata a quantidade de Bitcoin com até 8 casas decimais.
     */
    fun formatarBtc(quantidade: Double): String {
        val formatter = NumberFormat.getNumberInstance(ptBrLocale).apply {
            minimumFractionDigits = 2
            maximumFractionDigits = 8
        }
        return formatter.format(quantidade) + " BTC"
    }

    /**
     * Formata uma taxa percentual com vírgula decimal.
     */
    fun formatarPorcentagem(valor: Double, comSinal: Boolean = true): String {
        val formatter = NumberFormat.getNumberInstance(ptBrLocale).apply {
            minimumFractionDigits = 2
            maximumFractionDigits = 2
        }
        val formatted = formatter.format(Math.abs(valor))
        val sinal = when {
            valor > 0 -> if (comSinal) "+" else ""
            valor < 0 -> "-"
            else -> ""
        }
        return "$sinal$formatted%"
    }

    /**
     * Converte timestamp ISO 8601 em string legível de data e hora local.
     */
    fun formatarDataHora(isoString: String): String {
        if (isoString.isBlank()) return "-"
        return try {
            val inputFormat = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", ptBrLocale).apply {
                timeZone = TimeZone.getTimeZone("UTC")
            }
            val date = inputFormat.parse(isoString) ?: return isoString
            val outputFormat = SimpleDateFormat("dd/MM/yyyy HH:mm:ss", ptBrLocale).apply {
                timeZone = TimeZone.getDefault()
            }
            outputFormat.format(date)
        } catch (e: Exception) {
            isoString
        }
    }
}
