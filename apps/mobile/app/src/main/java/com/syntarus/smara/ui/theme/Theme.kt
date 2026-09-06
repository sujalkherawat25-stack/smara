package com.syntarus.smara.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val SmaraColors = darkColorScheme(
    primary = Color(0xFFB7F20A),
    onPrimary = Color(0xFF07100A),
    secondary = Color(0xFF78E6AC),
    background = Color(0xFF080B0D),
    surface = Color(0xFF101518),
    onBackground = Color(0xFFF2F5F4),
    onSurface = Color(0xFFF2F5F4),
)

@Composable
fun SmaraTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = SmaraColors, content = content)
}

