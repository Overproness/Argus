# Two classes share the method name `quote`. Name matching links `run` to both;
# SCIP links it only to `Live.quote`.


class Live:
    def quote(self, symbol):
        return 1.0


class Cached:
    def quote(self, symbol):
        return 0.5


def run(source: "Live"):
    return source.quote("BTC")


if __name__ == "__main__":
    Cached().quote("ETH")
    run(Live())
