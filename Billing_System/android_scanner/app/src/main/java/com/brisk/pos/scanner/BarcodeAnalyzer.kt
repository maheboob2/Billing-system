package com.brisk.pos.scanner

import androidx.annotation.OptIn
import androidx.camera.core.ExperimentalGetImage
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import com.google.mlkit.vision.barcode.BarcodeScannerOptions
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage

class BarcodeAnalyzer(
    private val debounceMs: Long = 1200L,
    private val onBarcodeScanned: (String, Int) -> Unit
) : ImageAnalysis.Analyzer {

    // Configure ML Kit Barcode Scanning for comprehensive retail formats
    private val options = BarcodeScannerOptions.Builder()
        .setBarcodeFormats(
            Barcode.FORMAT_EAN_13,
            Barcode.FORMAT_EAN_8,
            Barcode.FORMAT_UPC_A,
            Barcode.FORMAT_UPC_E,
            Barcode.FORMAT_CODE_128,
            Barcode.FORMAT_CODE_39,
            Barcode.FORMAT_CODE_93,
            Barcode.FORMAT_QR_CODE,
            Barcode.FORMAT_DATA_MATRIX,
            Barcode.FORMAT_ITF,
            Barcode.FORMAT_CODABAR
        )
        .build()

    private val scanner = BarcodeScanning.getClient(options)

    private var lastScannedCode: String? = null
    private var lastScannedTimestamp: Long = 0L

    @OptIn(ExperimentalGetImage::class)
    override fun analyze(imageProxy: ImageProxy) {
        val mediaImage = imageProxy.image
        if (mediaImage != null) {
            val image = InputImage.fromMediaImage(mediaImage, imageProxy.imageInfo.rotationDegrees)
            scanner.process(image)
                .addOnSuccessListener { barcodes ->
                    for (barcode in barcodes) {
                        val rawValue = barcode.rawValue?.trim() ?: continue
                        if (rawValue.isEmpty()) continue

                        val now = System.currentTimeMillis()
                        // Debounce / Cooldown check
                        if (rawValue == lastScannedCode && (now - lastScannedTimestamp) < debounceMs) {
                            continue
                        }

                        lastScannedCode = rawValue
                        lastScannedTimestamp = now
                        onBarcodeScanned(rawValue, barcode.format)
                        break // Process one primary barcode per frame
                    }
                }
                .addOnFailureListener {
                    // Silent frame analysis error
                }
                .addOnCompleteListener {
                    imageProxy.close()
                }
        } else {
            imageProxy.close()
        }
    }
}
