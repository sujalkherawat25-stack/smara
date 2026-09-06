package com.syntarus.smara.auth

import android.app.Activity
import android.content.Intent
import android.net.Uri

class AuthRepository(private val api: AuthApi) {
    suspend fun restore(): AccountSession? = api.restore()

    suspend fun signIn(activity: Activity): AccountSession {
        val link = api.startDeviceLink()
        val browser = Intent(Intent.ACTION_VIEW, Uri.parse(link.verificationUrl)).apply {
            addCategory(Intent.CATEGORY_BROWSABLE)
        }
        require(browser.resolveActivity(activity.packageManager) != null) {
            "Install or enable a web browser to connect your Smara account."
        }
        activity.startActivity(browser)
        return api.waitForDeviceApproval(link)
    }

    suspend fun logout() = api.logout()
}

