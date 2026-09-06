plugins {
    id("com.android.application") version "9.2.0" apply false
    // AGP 9.2 embeds Kotlin 2.3.10; the Compose compiler plugin must match it.
    id("org.jetbrains.kotlin.plugin.compose") version "2.3.10" apply false
}
