package demo

import kotlinx.coroutines.withTimeout
import okhttp3.OkHttpClient
import okhttp3.Request

class Prices(private val client: OkHttpClient) {
    fun fetch(url: String): String {
        val req = Request.Builder().url(url).build()
        return client.newCall(req).execute().body!!.string()
    }

    suspend fun guarded(url: String): String = withTimeout(2000) {
        fetch(url)
    }
}
