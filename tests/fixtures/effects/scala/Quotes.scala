import sttp.client3._

object Quotes {
  def fetch(url: String): Option[String] = {
    val backend = HttpClientSyncBackend()
    for (attempt <- 0 until 3) {
      try {
        return Some(basicRequest.get(uri"$url").send(backend).body.toString)
      } catch {
        case e: Exception => ()
      }
    }
    None
  }
}
