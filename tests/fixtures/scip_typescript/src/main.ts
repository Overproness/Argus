// Two classes share the method name `quote`. Name matching links `run` to both;
// SCIP links it only to `Live.quote`.
class Live {
  quote(symbol: string): number {
    return 1.0;
  }
}

class Cached {
  quote(symbol: string): number {
    return 0.5;
  }
}

function run(source: Live): number {
  return source.quote("BTC");
}

new Cached().quote("ETH");
run(new Live());
