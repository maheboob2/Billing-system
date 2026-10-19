package com.brisk.pos.scanner

import android.app.Application

class BriskScannerApp : Application() {
    lateinit var sessionManager: ScannerSessionManager
    lateinit var offlineQueue: OfflineScanQueue
    lateinit var apiClient: PosApiClient

    override fun onCreate() {
        super.onCreate()
        sessionManager = ScannerSessionManager(this)
        offlineQueue = OfflineScanQueue(this)
        apiClient = PosApiClient(sessionManager, offlineQueue)
    }
}
