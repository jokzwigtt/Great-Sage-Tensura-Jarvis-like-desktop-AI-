package com.greatsage.assistant

import java.net.HttpURLConnection
import java.net.URL

/** Tiny HTTP helper (runs on background threads only). */
object Http {
    fun request(
        url: String,
        method: String = "GET",
        body: String? = null,
        headers: Map<String, String> = emptyMap(),
        connectTimeoutMs: Int = 8000,
        readTimeoutMs: Int = 30000,
    ): Pair<Int, String> {
        val c = URL(url).openConnection() as HttpURLConnection
        try {
            c.requestMethod = method
            c.connectTimeout = connectTimeoutMs
            c.readTimeout = readTimeoutMs
            c.setRequestProperty("User-Agent", "GreatSage-Android")
            for ((k, v) in headers) c.setRequestProperty(k, v)
            if (body != null) {
                c.doOutput = true
                c.setRequestProperty("Content-Type", "application/json")
                c.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            }
            val code = c.responseCode
            val text = if (code in 200..299) {
                c.inputStream.bufferedReader().use { it.readText() }
            } else {
                c.errorStream?.bufferedReader()?.use { it.readText() } ?: ""
            }
            return code to text
        } finally {
            c.disconnect()
        }
    }
}
