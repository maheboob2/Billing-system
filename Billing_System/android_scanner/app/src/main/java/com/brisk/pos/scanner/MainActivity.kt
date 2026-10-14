package com.brisk.pos.scanner

import android.Manifest
import android.animation.ObjectAnimator
import android.animation.ValueAnimator
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.view.animation.LinearInterpolator
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.camera.core.*
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.content.ContextCompat
import com.brisk.pos.scanner.databinding.ActivityMainBinding
import com.google.mlkit.vision.barcode.common.Barcode
import java.util.UUID
import java.util.concurrent.Executors

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var sessionManager: ScannerSessionManager
    private lateinit var offlineQueue: OfflineScanQueue
    private lateinit var apiClient: PosApiClient
    private lateinit var feedbackHelper: SoundVibrationHelper

    private val cameraExecutor = Executors.newSingleThreadExecutor()
    private var camera: Camera? = null
    private var isTorchOn = false
    private var laserAnimator: ObjectAnimator? = null
    private var currentState = ConnectionState.CONNECTING

    private val heartbeatHandler = Handler(Looper.getMainLooper())
    private val heartbeatRunnable = object : Runnable {
        override fun run() {
            checkConnectionAndHeartbeat()
            heartbeatHandler.postDelayed(this, 8000)
        }
    }

    private val requestPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { isGranted: Boolean ->
        if (isGranted) {
            startCamera()
        } else {
            Toast.makeText(this, "Camera permission is required to scan barcodes.", Toast.LENGTH_LONG).show()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val app = application as BriskScannerApp
        sessionManager = app.sessionManager
        offlineQueue = app.offlineQueue
        apiClient = app.apiClient
        feedbackHelper = SoundVibrationHelper(this)

        // Strict Requirement: Only after successful pairing should app navigate to barcode screen.
        // If not paired, immediately return to the dedicated "Connect to POS" screen.
        if (!sessionManager.isPaired) {
            startActivity(Intent(this, PairingActivity::class.java))
            finish()
            return
        }

        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        setupUI()
        setupLaserAnimation()

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
            startCamera()
        } else {
            requestPermissionLauncher.launch(Manifest.permission.CAMERA)
        }
    }

    override fun onResume() {
        super.onResume()
        if (!sessionManager.isPaired) {
            startActivity(Intent(this, PairingActivity::class.java))
            finish()
            return
        }
        updateHeaderAndStatus()
        heartbeatHandler.post(heartbeatRunnable)
    }

    override fun onPause() {
        super.onPause()
        heartbeatHandler.removeCallbacks(heartbeatRunnable)
    }

    private fun setupUI() {
        binding.tvStoreName.text = sessionManager.companyName
        binding.tvTerminalInfo.text = "Paired to ${sessionManager.terminalId}"

        // Torch toggle
        binding.btnTorch.setOnClickListener {
            toggleTorch()
        }

        // Unpair / Reconfigure action
        binding.btnPairing.setOnClickListener {
            val intent = Intent(this, PairingActivity::class.java).apply {
                putExtra("reconfigure", true)
            }
            startActivity(intent)
        }

        // Tap on connection status allows immediate reconnect or re-pairing
        binding.tvConnectionStatus.setOnClickListener {
            if (currentState == ConnectionState.SESSION_EXPIRED) {
                val intent = Intent(this, PairingActivity::class.java).apply {
                    putExtra("reconfigure", true)
                }
                startActivity(intent)
            } else if (currentState == ConnectionState.DISCONNECTED) {
                binding.tvConnectionStatus.text = "Reconnecting to POS…"
                binding.tvConnectionStatus.setTextColor(ContextCompat.getColor(this, R.color.warning))
                checkConnectionAndHeartbeat()
            }
        }

        // Manual Barcode Entry
        binding.btnSendManual.setOnClickListener {
            val code = binding.etManualBarcode.text.toString().trim()
            if (code.isNotEmpty()) {
                binding.etManualBarcode.text?.clear()
                handleBarcodeScanned(code)
            }
        }

        updateQueueBadge()
    }

    private fun checkConnectionAndHeartbeat() {
        if (!sessionManager.isPaired) {
            currentState = ConnectionState.DISCONNECTED
            updateHeaderAndStatus()
            return
        }

        apiClient.sendHeartbeat { state, diagnostic ->
            currentState = state
            updateHeaderAndStatus()

            if (state == ConnectionState.CONNECTED) {
                // If reconnected and items are pending in offline buffer, flush them now
                if (offlineQueue.size() > 0) {
                    apiClient.syncOfflineQueue { flushed ->
                        if (flushed > 0) {
                            runOnUiThread {
                                Toast.makeText(this, "✅ Synced $flushed offline scans to POS cart", Toast.LENGTH_SHORT).show()
                                updateQueueBadge()
                            }
                        }
                    }
                }
            } else if (state == ConnectionState.SESSION_EXPIRED) {
                Toast.makeText(this, "Session expired on POS terminal. Please re-pair.", Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun updateHeaderAndStatus() {
        binding.tvStoreName.text = sessionManager.companyName
        binding.tvTerminalInfo.text = "Paired to ${sessionManager.terminalId}"

        when (currentState) {
            ConnectionState.CONNECTED -> {
                binding.tvConnectionStatus.text = "● Connected (Live)"
                binding.tvConnectionStatus.setTextColor(ContextCompat.getColor(this, R.color.secondary))
            }
            ConnectionState.CONNECTING -> {
                binding.tvConnectionStatus.text = "Connecting to POS…"
                binding.tvConnectionStatus.setTextColor(ContextCompat.getColor(this, R.color.warning))
            }
            ConnectionState.DISCONNECTED -> {
                val queued = offlineQueue.size()
                binding.tvConnectionStatus.text = if (queued > 0) {
                    "⚠️ POS Offline ($queued Queued) · Tap to Retry"
                } else {
                    "⚠️ POS Offline · Tap to Reconnect"
                }
                binding.tvConnectionStatus.setTextColor(ContextCompat.getColor(this, R.color.danger))
            }
            ConnectionState.SESSION_EXPIRED -> {
                binding.tvConnectionStatus.text = "❌ Session Expired · Tap to Re-pair"
                binding.tvConnectionStatus.setTextColor(ContextCompat.getColor(this, R.color.danger))
            }
        }

        updateQueueBadge()
    }

    private fun updateQueueBadge() {
        val size = offlineQueue.size()
        if (size > 0) {
            binding.tvQueueBadge.visibility = View.VISIBLE
            binding.tvQueueBadge.text = "$size Queued (Offline)"
        } else {
            binding.tvQueueBadge.visibility = View.GONE
        }
    }

    private fun startCamera() {
        val cameraProviderFuture = ProcessCameraProvider.getInstance(this)
        cameraProviderFuture.addListener({
            val cameraProvider = cameraProviderFuture.get()

            val preview = Preview.Builder().build().also {
                it.setSurfaceProvider(binding.previewView.surfaceProvider)
            }

            val imageAnalysis = ImageAnalysis.Builder()
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .build()
                .also {
                    it.setAnalyzer(cameraExecutor, BarcodeAnalyzer(debounceMs = sessionManager.debounceMs) { rawValue, _ ->
                        runOnUiThread {
                            handleBarcodeScanned(rawValue)
                        }
                    })
                }

            val cameraSelector = CameraSelector.DEFAULT_BACK_CAMERA

            try {
                cameraProvider.unbindAll()
                camera = cameraProvider.bindToLifecycle(this, cameraSelector, preview, imageAnalysis)
            } catch (exc: Exception) {
                Toast.makeText(this, "Failed to start camera: ${exc.localizedMessage}", Toast.LENGTH_SHORT).show()
            }
        }, ContextCompat.getMainExecutor(this))
    }

    private fun handleBarcodeScanned(barcode: String) {
        if (currentState == ConnectionState.SESSION_EXPIRED) {
            feedbackHelper.notifyError()
            Toast.makeText(this, "Session expired on terminal. Tap status above to re-pair.", Toast.LENGTH_LONG).show()
            return
        }

        showLoadingResult(barcode)

        val idempotencyKey = UUID.randomUUID().toString()

        apiClient.submitScan(barcode, idempotencyKey) { result ->
            if (result.isQueuedOffline) {
                feedbackHelper.notifyScanSuccess()
                displayOfflineBufferedResult(barcode)
                updateQueueBadge()
                updateHeaderAndStatus()
            } else if (result.success) {
                feedbackHelper.notifyScanSuccess()
                displayProductResult(result)
                updateQueueBadge()
            } else {
                feedbackHelper.notifyError()
                displayErrorResult(result)
            }
        }
    }

    private fun showLoadingResult(barcode: String) {
        binding.bottomCard.visibility = View.VISIBLE
        binding.productResultContainer.visibility = View.VISIBLE
        binding.tvLastBarcode.text = "Scanned: $barcode"
        binding.tvProductName.text = "Looking up product in catalog…"
        binding.tvProductPrice.visibility = View.GONE
        binding.tvProductStock.visibility = View.GONE
    }

    private fun displayProductResult(result: ProductScanResult) {
        binding.bottomCard.visibility = View.VISIBLE
        binding.productResultContainer.visibility = View.VISIBLE
        binding.tvLastBarcode.text = "Barcode: ${result.barcode}"

        if (result.destination == "PRODUCT_FORM") {
            binding.tvProductName.text = "✓ Barcode Sent to Product Form"
            binding.tvProductName.setTextColor(ContextCompat.getColor(this, R.color.primary))
            binding.tvProductPrice.visibility = View.VISIBLE
            binding.tvProductPrice.text = "Target: Add/Edit Product Field"
            binding.tvProductStock.visibility = View.GONE
            return
        }

        if (result.found) {
            binding.tvProductName.text = result.productName ?: "Item Added to POS Cart"
            binding.tvProductName.setTextColor(ContextCompat.getColor(this, R.color.text_primary))

            if (result.sellingPrice != null) {
                binding.tvProductPrice.visibility = View.VISIBLE
                binding.tvProductPrice.text = "₹${"%.2f".format(result.sellingPrice)}"
            } else {
                binding.tvProductPrice.visibility = View.GONE
            }

            if (result.currentStock != null) {
                binding.tvProductStock.visibility = View.VISIBLE
                binding.tvProductStock.text = "Stock: ${result.currentStock.toInt()} units"
            } else {
                binding.tvProductStock.visibility = View.GONE
            }
        } else {
            binding.tvProductName.text = "Product Not Found in Store Catalog"
            binding.tvProductName.setTextColor(ContextCompat.getColor(this, R.color.danger))
            binding.tvProductPrice.visibility = View.GONE
            binding.tvProductStock.visibility = View.GONE
        }
    }

    private fun displayOfflineBufferedResult(barcode: String) {
        binding.bottomCard.visibility = View.VISIBLE
        binding.productResultContainer.visibility = View.VISIBLE
        binding.tvLastBarcode.text = "Queued Offline: $barcode"
        binding.tvProductName.text = "Buffered Offline for POS Cart"
        binding.tvProductName.setTextColor(ContextCompat.getColor(this, R.color.warning))
        binding.tvProductPrice.visibility = View.GONE
        binding.tvProductStock.visibility = View.GONE
    }

    private fun displayErrorResult(result: ProductScanResult) {
        binding.bottomCard.visibility = View.VISIBLE
        binding.productResultContainer.visibility = View.VISIBLE
        binding.tvLastBarcode.text = "Barcode: ${result.barcode}"
        val msg = result.errorMessage ?: if (!result.found) "Product not found in store catalog." else "Could not deliver scan to POS."
        binding.tvProductName.text = msg
        binding.tvProductName.setTextColor(ContextCompat.getColor(this, R.color.danger))
        binding.tvProductPrice.visibility = View.GONE
        binding.tvProductStock.visibility = View.GONE
    }

    private fun setupLaserAnimation() {
        binding.laserLine.post {
            val parentHeight = binding.reticleBox.height.toFloat()
            laserAnimator = ObjectAnimator.ofFloat(binding.laserLine, "translationY", 0f, parentHeight - 8f).apply {
                duration = 2000
                interpolator = LinearInterpolator()
                repeatCount = ValueAnimator.INFINITE
                repeatMode = ValueAnimator.REVERSE
                start()
            }
        }
    }

    private fun toggleTorch() {
        camera?.let { cam ->
            if (cam.cameraInfo.hasFlashUnit()) {
                isTorchOn = !isTorchOn
                cam.cameraControl.enableTorch(isTorchOn)
                binding.btnTorch.imageTintList = ContextCompat.getColorStateList(
                    this,
                    if (isTorchOn) R.color.warning else R.color.text_primary
                )
            } else {
                Toast.makeText(this, "Flashlight unavailable on this device", Toast.LENGTH_SHORT).show()
            }
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        laserAnimator?.cancel()
        cameraExecutor.shutdown()
        feedbackHelper.release()
    }
}
