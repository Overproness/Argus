import axios from "axios";

export async function fetchQuote(url: string): Promise<unknown> {
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      return await axios.get(url);
    } catch (e: unknown) {
      continue;
    }
  }
  return null;
}
