using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;

namespace Trading.Services
{
    public class QuoteService
    {
        private readonly HttpClient _http = new HttpClient();

        public async Task<string> GetQuoteAsync(string symbol)
        {
            return await _http.GetStringAsync($"https://x/{symbol}");
        }

        public string GetQuote(string symbol)
        {
            return GetQuoteAsync(symbol).Result;
        }

        public async Task RefreshAsync(string[] symbols)
        {
            foreach (var s in symbols)
            {
                await GetQuoteAsync(s);
            }
            Thread.Sleep(100);
            await Task.Run(() => Thread.Sleep(50));
        }
    }
}
