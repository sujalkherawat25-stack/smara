package com.syntarus.smara.auth

data class AccountSession(
    val accountId: String,
    val email: String?,
    val displayName: String?,
    val avatarUrl: String?,
    val plan: String,
)

