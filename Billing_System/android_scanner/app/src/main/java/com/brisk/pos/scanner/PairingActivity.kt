package com.brisk.pos.scanner

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.view.View
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.brisk.pos.scanner.databinding.ActivityPairingBinding
import com.google.mlkit.vision.barcode.common.Barcode
import org.json.JSONObject
import java.net.URI
import java.util.concurrent.Executors

class PairingActivity : AppCompatActivity() {

    private lateinit var binding: ActivityPairingBinding
    private lateinit var sessionManager: ScannerSessionManager
    private lateinit var apiClient: PosApiClient
    private lateinit var feedbackHelper: SoundVibrationHelper

    private val cameraExecutor = Executors.newSingleThreadExecutor()
    private var isProcessingPair = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityPairingBinding.inflate(layoutInflater)
        setContentView(binding.root)

        val app = application as BriskScannerApp
        sessionManager = app.sessionManager
        apiClient = app.apiClient
        feedbackHelper = SoundVibrationHelper(this)

        val forceReconfigure = intent.getBooleanExtra("reconfigure", false)

        // If already paired and not forced to reconfigure, check if connection is active
        if (sessionManager.isPaired && !forceReconfigure) {
            binding.btnConnectManual.text = "Verifying Existing Connection…"
            binding.btnConnectManual.isEnabled = false

            apiClient.sendHeartbeat { state, _ ->
                binding.btnConnectManual.isEnabled = true
                binding.btnConnectManual.text = "Connect to POS Terminal"

                if (state == ConnectionState.CONNECTED) {
                    // Previous pairing session is active! Proceed directly to barcode scanner
                    startActivity(Intent(this, MainActivity::class.java))
                    finish()
                } else if (state == ConnectionState.SESSION_EXPIRED) {
                    sessionManager.clearSession()
                    showDiagnostic("Previous session expired on POS terminal. Please scan new pairing QR.", true)
                } else {
                    showDiagnostic("POS server at '${sessionManager.baseUrl}' is currently unreachable. You can scan a new QR or enter host IP below.", true)
                }
            }
        }

        setupUI()
        checkCameraPermissionAndStart()
    }

    private fun setupUI() {
        if (sessionManager.hasConfiguredUrl) {
            binding.etServerUrl.setText(sessionManager.baseUrl)
        }
        binding.etPairingCode.setText(sessionManager.pairingCode)

        if (sessionManager.isPaired) {
            binding.btnDisconnect.visibility = View.VISIBLE
            binding.btnDisconnect.setOnClickListener {
                apiClient.disconnect {
                    Toast.makeText(this, "Disconnected from POS terminal", Toast.LENGTH_SHORT).show()
                    binding.btnDisconnect.visibility = View.GONE
                    binding.etPairingCode.text?.clear()
                    showDiagnostic("Disconnected. Ready to pair new terminal.", false)
                }
            }
        }

        binding.btnConnectManual.setOnClickListener {
            val serverUrl = binding.etServerUrl.text.toString().trim()
            val code = binding.etPairingCode.text.toString().trim()

            if (serverUrl.isEmpty()) {
                binding.etServerUrl.error = "Server URL is required (e.g. http://192.168.31.128:8000)"
                return@setOnClickListener
            }
            if (code.isEmpty()) {
                binding.etPairingCode.error = "Pairing code is required (e.g. 123-456)"
                return@setOnClickListener
            }

            attemptPairing(serverUrl, sessionToken = code, pairingCode = code)
        }
    }

    private fun checkCameraPermissionAndStart() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
            startQrCamera()
        } else {
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.CAMERA), 101)
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 101 && grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
            startQrCamera()
        }
    }

    private fun startQrCamera() {
        val cameraProviderFuture = ProcessCameraProvider.getInstance(this)
        cameraProviderFuture.addListener({
            val cameraProvider = cameraProviderFuture.get()
            val preview = Preview.Builder().build().also {
                it.setSurfaceProvider(binding.pairingPreviewView.surfaceProvider)
            }

            val imageAnalysis = ImageAnalysis.Builder()
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .build()
                .also {
                    it.setAnalyzer(cameraExecutor, BarcodeAnalyzer(debounceMs = 1500L) { rawCode, format ->
                        if (format == Barcode.FORMAT_QR_CODE && !isProcessingPair) {
                            runOnUiThread { parseAndHandleQrCode(rawCode) }
                        }
                    })
                }

            val cameraSelector = CameraSelector.DEFAULT_BACK_CAMERA
            try {
                cameraProvider.unbindAll()
                cameraProvider.bindToLifecycle(this, cameraSelector, preview, imageAnalysis)
            } catch (_: Exception) {
                showDiagnostic("Could not open camera preview for QR scanning.", true)
            }
        }, ContextCompat.getMainExecutor(this))
    }

    private fun parseAndHandleQrCode(qrData: String) {
        if (isProcessingPair) return
        isProcessingPair = true

        try {
            // Case 1: Structured JSON payload from POS QR code
            if (qrData.startsWith("{") && qrData.endsWith("}")) {
                val json = JSONObject(qrData)
                val serverUrl = json.optString("base_url", "").trim().ifEmpty {
                    sessionManager.baseUrl
                }
                val token = json.optString("session_token", "").trim()
                val code = json.optString("pairing_code", "").trim()

                if (serverUrl.isNotEmpty() && (token.isNotEmpty() || code.isNotEmpty())) {
                    feedbackHelper.notifyScanSuccess()
                    attemptPairing(serverUrl, token.ifEmpty { code }, code.ifEmpty { token })
                    return
                }
            }

            // Case 2: URL format with session query param (e.g. http://192.168.31.128:8000/pos/scanner/?session=xyz)
            if (qrData.contains("/pos/scanner/?session=")) {
                val uri = URI(qrData)
                val scheme = uri.scheme ?: "http"
                val host = uri.host ?: ""
                val port = if (uri.port != -1) ":${uri.port}" else ""
                val serverUrl = "$scheme://$host$port"

                val queryParams = uri.query?.split("&")?.associate {
                    val parts = it.split("=")
                    parts[0] to (if (parts.size > 1) parts[1] else "")
                } ?: emptyMap()
                val token = queryParams["session"].orEmpty().trim()

                if (token.isNotEmpty()) {
                    feedbackHelper.notifyScanSuccess()
                    attemptPairing(serverUrl, token, token)
                    return
                }
            }

            // Case 3: Raw token or pairing code string (e.g. "123-456")
            val rawCode = qrData.trim()
            binding.etPairingCode.setText(rawCode)

            if (sessionManager.hasConfiguredUrl) {
                feedbackHelper.notifyScanSuccess()
                attemptPairing(sessionManager.baseUrl, rawCode, rawCode)
            } else {
                feedbackHelper.notifyError()
                isProcessingPair = false
                showDiagnostic("Pairing code '$rawCode' captured. Please enter POS Server URL and tap Connect.", false)
            }

        } catch (e: Exception) {
            isProcessingPair = false
            feedbackHelper.notifyError()
            showDiagnostic("Unrecognized QR format: ${e.localizedMessage}", true)
        }
    }

    private fun attemptPairing(serverUrl: String, sessionToken: String, pairingCode: String) {
        val validation = NetworkHostValidator.validateServerUrl(serverUrl)
        if (!validation.isValid) {
            showDiagnostic(validation.errorMessage ?: "Invalid server URL", true)
            feedbackHelper.notifyError()
            isProcessingPair = false
            return
        }

        binding.btnConnectManual.isEnabled = false
        binding.btnConnectManual.text = "Connecting…"
        showDiagnostic("Connecting to ${validation.normalizedUrl}…", false)

        apiClient.pairWithSession(validation.normalizedUrl, sessionToken, pairingCode) { success, message, _ ->
            binding.btnConnectManual.isEnabled = true
            binding.btnConnectManual.text = "Connect to POS Terminal"
            isProcessingPair = false

            if (success) {
                feedbackHelper.notifyScanSuccess()
                Toast.makeText(this, "✅ $message", Toast.LENGTH_LONG).show()

                // Only after successful pairing navigate to barcode scanning screen
                val intent = Intent(this, MainActivity::class.java)
                startActivity(intent)
                finish()
            } else {
                feedbackHelper.notifyError()
                showDiagnostic("Connection Failed: $message", true)
                Toast.makeText(this, "❌ $message", Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun showDiagnostic(text: String, isError: Boolean) {
        binding.etServerUrl.error = if (isError) text else null
        Toast.makeText(this, text, Toast.LENGTH_LONG).show()
    }

    override fun onDestroy() {
        super.onDestroy()
        cameraExecutor.shutdown()
        feedbackHelper.release()
    }
}
