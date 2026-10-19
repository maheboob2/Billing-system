package com.brisk.pos.scanner

import android.content.Context
import android.content.SharedPreferences
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

data class QueuedScan(
    val idempotencyKey: String,
    val barcode: String,
    val timestamp: Long
)

class OfflineScanQueue(context: Context) {
    private val prefs: SharedPreferences = context.getSharedPreferences("brisk_scanner_offline_queue", Context.MODE_PRIVATE)
    private val KEY_QUEUE = "pending_scans"

    @Synchronized
    fun enqueue(barcode: String, idempotencyKey: String = UUID.randomUUID().toString()): QueuedScan {
        val item = QueuedScan(
            idempotencyKey = idempotencyKey,
            barcode = barcode.trim(),
            timestamp = System.currentTimeMillis()
        )
        val list = getItems().toMutableList()
        // Avoid duplicate in queue
        if (list.none { it.barcode == item.barcode && (item.timestamp - it.timestamp) < 2000 }) {
            list.add(item)
            saveItems(list)
        }
        return item
    }

    @Synchronized
    fun getItems(): List<QueuedScan> {
        val raw = prefs.getString(KEY_QUEUE, "[]") ?: "[]"
        val result = mutableListOf<QueuedScan>()
        try {
            val arr = JSONArray(raw)
            for (i in 0 until arr.length()) {
                val obj = arr.getJSONObject(i)
                result.add(
                    QueuedScan(
                        idempotencyKey = obj.getString("key"),
                        barcode = obj.getString("barcode"),
                        timestamp = obj.getLong("ts")
                    )
                )
            }
        } catch (_: Exception) {}
        return result
    }

    @Synchronized
    fun remove(idempotencyKey: String) {
        val list = getItems().filter { it.idempotencyKey != idempotencyKey }
        saveItems(list)
    }

    @Synchronized
    fun size(): Int = getItems().size

    @Synchronized
    fun clear() {
        prefs.edit().putString(KEY_QUEUE, "[]").apply()
    }

    private fun saveItems(items: List<QueuedScan>) {
        val arr = JSONArray()
        for (item in items) {
            val obj = JSONObject().apply {
                put("key", item.idempotencyKey)
                put("barcode", item.barcode)
                put("ts", item.timestamp)
            }
            arr.put(obj)
        }
        prefs.edit().putString(KEY_QUEUE, arr.toString()).apply()
    }
}
