import http from "node:http";

// No timeout: a dead peer hangs the caller forever.
export function fetchPrice(url) {
  return new Promise((resolve, reject) => {
    http.get(url, (res) => {
      let body = "";
      res.on("data", (chunk) => (body += chunk));
      res.on("end", () => resolve(body));
    }).on("error", reject);
  });
}

// Synchronous work on the event loop: nothing else runs meanwhile.
export function crunch(ms) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    // busy
  }
}
