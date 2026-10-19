package com.brisk.pos.scanner

import java.net.URI

object NetworkHostValidator {

    data class ValidationResult(
        val isValid: Boolean,
        val errorMessage: String? = null,
        val normalizedUrl: String = ""
    )

    /**
     * Validates that the provided POS server URL conforms to security policy:
     * - HTTPS is permitted for any valid domain or IP.
     * - Cleartext HTTP is ONLY permitted for local private network hosts (RFC 1918 / localhost / mDNS).
     * - Cleartext HTTP to arbitrary public internet hosts is strictly rejected.
     */
    fun validateServerUrl(rawUrl: String): ValidationResult {
        val trimmed = rawUrl.trim()
        if (trimmed.isEmpty()) {
            return ValidationResult(false, "Server URL cannot be empty")
        }

        val urlWithScheme = if (!trimmed.startsWith("http://", ignoreCase = true) &&
            !trimmed.startsWith("https://", ignoreCase = true)
        ) {
            "http://$trimmed"
        } else {
            trimmed
        }

        return try {
            val uri = URI(urlWithScheme)
            val scheme = uri.scheme?.lowercase() ?: return ValidationResult(false, "Missing URL scheme (http/https)")
            val host = uri.host?.lowercase() ?: return ValidationResult(false, "Invalid host address in URL: $rawUrl")
            val port = if (uri.port != -1) uri.port else (if (scheme == "https") 443 else 80)

            if (scheme != "http" && scheme != "https") {
                return ValidationResult(false, "Unsupported protocol: $scheme. Use http:// or https://")
            }

            val portSuffix = when {
                scheme == "http" && port == 80 -> ""
                scheme == "https" && port == 443 -> ""
                else -> ":$port"
            }

            if (scheme == "https") {
                val normalized = "https://$host$portSuffix"
                return ValidationResult(true, null, normalized)
            }

            // For cleartext HTTP, enforce strict private LAN scoping
            if (isLocalOrPrivateHost(host)) {
                val normalized = "http://$host$portSuffix"
                ValidationResult(true, null, normalized)
            } else {
                ValidationResult(
                    false,
                    "Cleartext HTTP is only allowed for local POS private networks (e.g. 192.168.x.x, 10.x.x.x). " +
                            "Host '$host' requires HTTPS to protect business data."
                )
            }
        } catch (e: Exception) {
            ValidationResult(false, "Invalid URL format: ${e.message}")
        }
    }

    /**
     * Checks if a host is localhost, RFC 1918 private IPv4, link-local, or local LAN domain.
     */
    fun isLocalOrPrivateHost(host: String): Boolean {
        val cleanHost = host.removePrefix("[").removeSuffix("]").trim()
        if (cleanHost == "localhost" || cleanHost == "127.0.0.1" || cleanHost == "::1") return true
        if (cleanHost.endsWith(".local") || cleanHost.endsWith(".lan") || cleanHost.endsWith(".internal")) return true

        val parts = cleanHost.split(".")
        if (parts.size != 4) return false

        val octets = parts.map { it.toIntOrNull() ?: return false }
        if (octets.any { it !in 0..255 }) return false

        val (o1, o2, _, _) = octets

        // 10.0.0.0/8
        if (o1 == 10) return true

        // 172.16.0.0/12 (172.16.0.0 – 172.31.255.255)
        if (o1 == 172 && o2 in 16..31) return true

        // 192.168.0.0/16
        if (o1 == 192 && o2 == 168) return true

        // 127.0.0.0/8 (Loopback)
        if (o1 == 127) return true

        // 169.254.0.0/16 (Link-Local / APIPA)
        if (o1 == 169 && o2 == 254) return true

        return false
    }
}
