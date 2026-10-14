package com.brisk.pos.scanner

import android.content.Context
import android.content.SharedPreferences

class ScannerSessionManager(context: Context) {
    private val prefs: SharedPreferences = context.getSharedPreferences("brisk_pos_scanner_prefs", Context.MODE_PRIVATE)

    companion object {
        private const val KEY_BASE_URL = "base_url"
        private const val KEY_SESSION_TOKEN = "session_token"
        private const val KEY_PAIRING_CODE = "pairing_code"
        private const val KEY_COMPANY_NAME = "company_name"
        private const val KEY_TERMINAL_ID = "terminal_id"
        private const val KEY_IS_PAIRED = "is_paired"
        private const val KEY_DEBOUNCE_MS = "debounce_ms"
    }

    /**
     * Stored base URL. Does NOT fallback to a stale hardcoded IP like 192.168.1.10.
     * Empty if not yet configured via QR code or manual entry.
     */
    var baseUrl: String
        get() = prefs.getString(KEY_BASE_URL, "") ?: ""
        set(value) = prefs.edit().putString(KEY_BASE_URL, value.trim().trimEnd('/')).apply()

    val hasConfiguredUrl: Boolean
        get() = baseUrl.isNotEmpty()

    var sessionToken: String
        get() = prefs.getString(KEY_SESSION_TOKEN, "") ?: ""
        set(value) = prefs.edit().putString(KEY_SESSION_TOKEN, value.trim()).apply()

    var pairingCode: String
        get() = prefs.getString(KEY_PAIRING_CODE, "") ?: ""
        set(value) = prefs.edit().putString(KEY_PAIRING_CODE, value.trim()).apply()

    var companyName: String
        get() = prefs.getString(KEY_COMPANY_NAME, "Unpaired Store") ?: "Unpaired Store"
        set(value) = prefs.edit().putString(KEY_COMPANY_NAME, value).apply()

    var terminalId: String
        get() = prefs.getString(KEY_TERMINAL_ID, "POS-001") ?: "POS-001"
        set(value) = prefs.edit().putString(KEY_TERMINAL_ID, value).apply()

    var isPaired: Boolean
        get() = prefs.getBoolean(KEY_IS_PAIRED, false) && sessionToken.isNotEmpty() && baseUrl.isNotEmpty()
        set(value) = prefs.edit().putBoolean(KEY_IS_PAIRED, value).apply()

    var debounceMs: Long
        get() = prefs.getLong(KEY_DEBOUNCE_MS, 1200L)
        set(value) = prefs.edit().putLong(KEY_DEBOUNCE_MS, value).apply()

    fun saveSession(url: String, token: String, code: String, company: String, terminal: String) {
        prefs.edit()
            .putString(KEY_BASE_URL, url.trim().trimEnd('/'))
            .putString(KEY_SESSION_TOKEN, token.trim())
            .putString(KEY_PAIRING_CODE, code.trim())
            .putString(KEY_COMPANY_NAME, company.trim())
            .putString(KEY_TERMINAL_ID, terminal.trim())
            .putBoolean(KEY_IS_PAIRED, true)
            .apply()
    }

    fun clearSession() {
        prefs.edit()
            .remove(KEY_SESSION_TOKEN)
            .remove(KEY_PAIRING_CODE)
            .remove(KEY_COMPANY_NAME)
            .remove(KEY_TERMINAL_ID)
            .putBoolean(KEY_IS_PAIRED, false)
            .apply()
    }
}
