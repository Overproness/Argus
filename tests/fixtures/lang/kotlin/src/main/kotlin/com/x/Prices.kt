package com.x

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request

class Prices(private val client: OkHttpClient) {
    fun fetch(symbol: String): String {
        val req = Request.Builder().url("https://x/$symbol").build()
        return client.newCall(req).execute().body!!.string()
    }

    suspend fun refresh(symbols: List<String>) {
        symbols.forEach { fetch(it) }
        withContext(Dispatchers.IO) { fetch("ETH") }
        Thread.sleep(100)
    }
}
