import scala.concurrent.{Await, Future}
import scala.concurrent.duration._

object Pricing {
  def quote(s: String): Int = 1

  def all(xs: Seq[String]): Seq[Int] = {
    val f = Future { xs.map(quote) }
    Await.result(f, 5.seconds)
    xs.map(x => quote(x))
  }

  def slow(): Future[Int] = Future {
    Await.result(Future(1), 1.second)
  }
}
