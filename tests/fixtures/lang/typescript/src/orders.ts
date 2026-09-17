import fs from "fs";
import axios from "axios";
import { PrismaClient } from "@prisma/client";

const prisma = new PrismaClient();

function loadConfig(): string {
  return fs.readFileSync("config.json", "utf8");
}

export async function handleOrder(ids: string[]): Promise<void> {
  const cfg = loadConfig();
  for (const id of ids) {
    await prisma.order.findUnique({ where: { id } });
  }
  await axios.get("https://x/prices");
  await axios.get("https://x/prices", { timeout: 2000 });
  const r = await fetch("https://x", { signal: AbortSignal.timeout(1000) });
}

export const refresh = async (urls: string[]) => {
  await Promise.all(urls.map((u) => fetch(u)));
};

class Service {
  async run() {
    this.helper();
  }

  helper() {
    return fs.readFileSync("x");
  }
}
