package com.brisk.pos.scanner

import android.os.Handler
import android.os.Looper
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.IOException
import java.net.ConnectException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import java.util.UUID
import java.util.concurrent.TimeUnit

data class ProductScanResult(
    val success: Boolean,
    val barcode: String,
    val found: Boolean,
    val productName: String?,
    val sellingPrice: Double?,
    val currentStock: Double?,
    val isQueuedOffline: Boolean = false,
    val errorMessage: String? = null,
    val destination: String? = null
)

enum class ConnectionState {
    CONNECTING,
    CONNECTED,
    DISCONNECTED,
    SESSION_EXPIRED
}

class PosApiClient(
    private val sessionManager: ScannerSessionManager,
    private val offlineQueue: OfflineScanQueue
) {
    private val client = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(8, TimeUnit.SECONDS)
        .writeTimeout(8, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .build()

    private val jsonMediaType = "application/json; charset=utf-8".toMediaType()
    private val mainHandler = Handler(Looper.getMainLooper())

    /**
     * Attempts pairing with POS terminal using serverUrl and session token/code.
     * Enforces private LAN host security validation.
     */
    fun pairWithSession(
        serverUrl: String,
        sessionToken: String,
        pairingCode: String,
        callback: (Boolean, String, JSONObject?) -> Unit
    ) {
        val validation = NetworkHostValidator.validateServerUrl(serverUrl)
        if (!validation.isValid) {
            callback(false, validation.errorMessage ?: "Invalid server URL", null)
            return
        }

        val cleanUrl = validation.normalizedUrl
        val cleanToken = sessionToken.trim()
        val cleanCode = pairingCode.trim()

        if (cleanToken.isEmpty()) {
            callback(false, "Session token or pairing code cannot be empty.", null)
            return
        }

        val endpoint = "$cleanUrl/api/pos/scanner-session/$cleanToken/scan/"
        val payload = JSONObject().apply {
            put("heartbeat", true)
            put("pairing_code", cleanCode)
            put("device_info", "Brisk Android Barcode Scanner (v1.0 Native)")
        }

        val request = Request.Builder()
            .url(endpoint)
            .post(payload.toString().toRequestBody(jsonMediaType))
            .build()

        client.newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                val diagnostic = formatNetworkException(e, cleanUrl)
                mainHandler.post {
                    callback(false, diagnostic, null)
                }
            }

            override fun onResponse(call: Call, response: Response) {
                val body = response.body?.string().orEmpty()
                mainHandler.post {
                    if (response.isSuccessful) {
                        try {
                            val json = JSONObject(body)
                            if (json.optBoolean("success", false) || json.optBoolean("connected", false)) {
                                val company = json.optString("company_name", "Retail Store")
                                val terminal = json.optString("terminal_id", "POS-001")
                                sessionManager.saveSession(
                                    cleanUrl,
                                    cleanToken,
                                    cleanCode,
                                    company,
                                    terminal
                                )
                                callback(true, "Connected to $company ($terminal)", json)
                            } else {
                                val errMsg = json.optString("error", "Pairing rejected by POS terminal.")
                                callback(false, errMsg, json)
                            }
                        } catch (ex: Exception) {
                            callback(false, "Invalid JSON response from POS server: ${ex.message}", null)
                        }
                    } else if (response.code == 404) {
                        callback(false, "Pairing session not found or expired on POS terminal.", null)
                    } else {
                        val errMsg = try {
                            JSONObject(body).optString("error", "Server returned HTTP ${response.code}")
                        } catch (_: Exception) {
                            "HTTP ${response.code}: Pairing failed."
                        }
                        callback(false, errMsg, null)
                    }
                }
            }
        })
    }

    /**
     * Verifies terminal connection and heartbeat status.
     * Returns: (State, DiagnosticMessage)
     */
    fun sendHeartbeat(callback: (ConnectionState, String) -> Unit) {
        if (!sessionManager.isPaired) {
            callback(ConnectionState.DISCONNECTED, "Unpaired")
            return
        }

        val validation = NetworkHostValidator.validateServerUrl(sessionManager.baseUrl)
        if (!validation.isValid) {
            callback(ConnectionState.DISCONNECTED, validation.errorMessage ?: "Invalid server URL")
            return
        }

        val endpoint = "${validation.normalizedUrl}/api/pos/scanner-session/${sessionManager.sessionToken}/scan/"
        val payload = JSONObject().apply {
            put("heartbeat", true)
            put("device_info", "Brisk Android Barcode Scanner")
        }
        val request = Request.Builder()
            .url(endpoint)
            .post(payload.toString().toRequestBody(jsonMediaType))
            .build()

        client.newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                android.util.Log.w("PosApiClient", "Heartbeat failed for ${maskEndpoint(call.request().url.toString())}: ${e.message}")
                mainHandler.post {
                    callback(ConnectionState.DISCONNECTED, "POS offline: ${e.localizedMessage ?: "Connection failed"}")
                }
            }

            override fun onResponse(call: Call, response: Response) {
                val body = response.body?.string().orEmpty()
                mainHandler.post {
                    if (response.isSuccessful) {
                        callback(ConnectionState.CONNECTED, "Live Connection")
                    } else if (response.code == 404 || response.code == 410) {
                        sessionManager.isPaired = false
                        callback(ConnectionState.SESSION_EXPIRED, "Session expired on terminal. Re-pairing required.")
                    } else {
                        callback(ConnectionState.DISCONNECTED, "HTTP ${response.code} from POS")
                    }
                }
            }
        })
    }

    /**
     * Submits scanned barcode to POS terminal with UUID idempotency key.
     * Buffers in offline queue if temporarily disconnected.
     */
    fun submitScan(
        barcode: String,
        idempotencyKey: String = UUID.randomUUID().toString(),
        callback: (ProductScanResult) -> Unit
    ) {
        if (!sessionManager.isPaired) {
            offlineQueue.enqueue(barcode, idempotencyKey)
            callback(
                ProductScanResult(
                    success = false,
                    barcode = barcode,
                    found = false,
                    productName = null,
                    sellingPrice = null,
                    currentStock = null,
                    isQueuedOffline = true,
                    errorMessage = "Scanner unpaired. Barcode buffered offline."
                )
            )
            return
        }

        val endpoint = "${sessionManager.baseUrl}/api/pos/scanner-session/${sessionManager.sessionToken}/scan/"
        val payload = JSONObject().apply {
            put("barcode", barcode.trim())
            put("idempotency_key", idempotencyKey)
            put("device_info", "Brisk Android Barcode Scanner (v1.0 Native)")
        }

        val request = Request.Builder()
            .url(endpoint)
            .post(payload.toString().toRequestBody(jsonMediaType))
            .build()

        client.newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                offlineQueue.enqueue(barcode, idempotencyKey)
                mainHandler.post {
                    callback(
                        ProductScanResult(
                            success = true,
                            barcode = barcode,
                            found = true,
                            productName = "Buffered Offline",
                            sellingPrice = null,
                            currentStock = null,
                            isQueuedOffline = true,
                            errorMessage = "POS offline. Scan queued locally."
                        )
                    )
                }
            }

            override fun onResponse(call: Call, response: Response) {
                val body = response.body?.string().orEmpty()
                mainHandler.post {
                    if (response.isSuccessful) {
                        try {
                            val json = JSONObject(body)
                            val apiSuccess = json.optBoolean("success", true)
                            val isFound = json.optBoolean("found", true)
                            val dest = json.optString("destination", "POS")
                            val isProductForm = dest == "PRODUCT_FORM"
                            val finalSuccess = apiSuccess && (isProductForm || isFound)
                            val errMsg = json.optString("error").takeIf { it.isNotEmpty() }
                                ?: json.optString("message").takeIf { !finalSuccess && it.isNotEmpty() }
                                ?: if (!isFound && !isProductForm) "Product not found in store catalog" else null

                            callback(
                                ProductScanResult(
                                    success = finalSuccess,
                                    barcode = barcode,
                                    found = isFound,
                                    productName = json.optString("product_name").takeIf { it.isNotEmpty() },
                                    sellingPrice = json.optDouble("selling_price").takeIf { !it.isNaN() },
                                    currentStock = json.optDouble("current_stock").takeIf { !it.isNaN() },
                                    isQueuedOffline = false,
                                    errorMessage = errMsg,
                                    destination = dest
                                )
                            )
                            syncOfflineQueue {}
                        } catch (e: Exception) {
                            callback(
                                ProductScanResult(
                                    success = false,
                                    barcode = barcode,
                                    found = false,
                                    productName = null,
                                    sellingPrice = null,
                                    currentStock = null,
                                    errorMessage = "Error parsing response: ${e.message}"
                                )
                            )
                        }
                    } else if (response.code == 404) {
                        callback(
                            ProductScanResult(
                                success = false,
                                barcode = barcode,
                                found = false,
                                productName = null,
                                sellingPrice = null,
                                currentStock = null,
                                errorMessage = "Session expired on POS terminal."
                            )
                        )
                    } else {
                        val errMsg = try {
                            JSONObject(body).optString("error", "Error sending barcode")
                        } catch (_: Exception) {
                            "Error submitting scan (${response.code})"
                        }
                        callback(
                            ProductScanResult(
                                success = false,
                                barcode = barcode,
                                found = false,
                                productName = null,
                                sellingPrice = null,
                                currentStock = null,
                                errorMessage = errMsg
                            )
                        )
                    }
                }
            }
        })
    }

    fun syncOfflineQueue(onComplete: (Int) -> Unit) {
        val pending = offlineQueue.getItems()
        if (pending.isEmpty() || !sessionManager.isPaired) {
            onComplete(0)
            return
        }

        var synced = 0
        for (item in pending) {
            val endpoint = "${sessionManager.baseUrl}/api/pos/scanner-session/${sessionManager.sessionToken}/scan/"
            val payload = JSONObject().apply {
                put("barcode", item.barcode)
                put("idempotency_key", item.idempotencyKey)
                put("device_info", "Brisk Android Barcode Scanner (v1.0 Native - Offline Sync)")
            }

            val request = Request.Builder()
                .url(endpoint)
                .post(payload.toString().toRequestBody(jsonMediaType))
                .build()

            client.newCall(request).enqueue(object : Callback {
                override fun onFailure(call: Call, e: IOException) {}
                override fun onResponse(call: Call, response: Response) {
                    if (response.isSuccessful) {
                        offlineQueue.remove(item.idempotencyKey)
                        synced++
                    }
                }
            })
        }
        mainHandler.postDelayed({ onComplete(synced) }, 1000)
    }

    fun disconnect(callback: (Boolean) -> Unit) {
        if (!sessionManager.isPaired) {
            callback(true)
            return
        }

        val endpoint = "${sessionManager.baseUrl}/api/pos/scanner-session/${sessionManager.sessionToken}/disconnect/"
        val request = Request.Builder()
            .url(endpoint)
            .post("{}".toRequestBody(jsonMediaType))
            .build()

        client.newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                sessionManager.clearSession()
                mainHandler.post { callback(true) }
            }

            override fun onResponse(call: Call, response: Response) {
                sessionManager.clearSession()
                mainHandler.post { callback(true) }
            }
        })
    }

    private fun maskEndpoint(url: String): String {
        return url.replace(Regex("(/scanner-session/)[^/]+(/)"), "$1***$2")
    }

    private fun formatNetworkException(e: IOException, targetUrl: String): String {
        android.util.Log.e("PosApiClient", "Network failure connecting to POS server '$targetUrl': ${e.javaClass.name}: ${e.message}", e)
        return when (e) {
            is UnknownHostException -> "Cannot resolve host '$targetUrl'. Check network connection."
            is SocketTimeoutException -> "Connection timed out to '$targetUrl'. Ensure PC and phone are on the same Wi-Fi and port 8000 is open."
            is ConnectException -> "Connection refused by '$targetUrl'. Ensure POS server is running (0.0.0.0:8000) and port 8000 is allowed in Windows Firewall."
            else -> {
                val msg = e.localizedMessage ?: e.message ?: "POS unreachable"
                if (e.javaClass.name.contains("Cleartext", ignoreCase = true) || msg.contains("CLEARTEXT", ignoreCase = true)) {
                    "Cleartext HTTP blocked: $msg. Ensure phone and PC are connected to the same local Wi-Fi."
                } else {
                    "Network error (${e.javaClass.simpleName}): $msg"
                }
            }
        }
    }
}
