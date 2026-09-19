namespace Demo;

public static class Client
{
    // HttpClient.Timeout defaults to 100 s, and .Result blocks the calling thread until then.
    public static string Fetch(string url) => new HttpClient().GetStringAsync(url).Result;
}
