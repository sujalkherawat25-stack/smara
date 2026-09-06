package com.syntarus.smara

import android.app.Application
import com.syntarus.smara.core.AppContainer

class SmaraApplication : Application() {
    val container: AppContainer by lazy { AppContainer(this) }
}

