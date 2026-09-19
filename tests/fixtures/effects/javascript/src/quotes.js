import axios from "axios";

export async function fetchQuote(url) {
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      return await axios.get(url);
    } catch (e) {
      continue;
    }
  }
  return null;
}

export async function fetchQuoteBackoff(url) {
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      return await axios.get(url, { timeout: 2000 });
    } catch (e) {
      await sleep(100 * 2 ** attempt);
    }
  }
  return null;
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}
