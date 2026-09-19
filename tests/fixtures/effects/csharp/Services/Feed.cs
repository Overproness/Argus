using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;

namespace Trading.Services
{
    public class Feed
    {
        private readonly HttpClient _http = new HttpClient();

        public async Task<string> Quote(string url)
        {
            for (var attempt = 0; attempt < 4; attempt++)
            {
                try
                {
                    return await _http.GetStringAsync(url);
                }
                catch (HttpRequestException)
                {
                }
            }
            return null;
        }

        public async Task<string> Slow(string url)
        {
            Thread.Sleep(5000);
            return await Quote(url);
        }

        public async Task<string> Guarded(string url)
        {
            return await Slow(url).WaitAsync(TimeSpan.FromSeconds(2));
        }
    }
}
