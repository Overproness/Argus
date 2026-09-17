// Two types share the method name `quote`. Name matching links `run` to both;
// SCIP links it only to `Live::quote`.
struct Live;
struct Cached;

impl Live {
    fn quote(&self, symbol: &str) -> f64 {
        std::thread::sleep(std::time::Duration::from_millis(symbol.len() as u64));
        1.0
    }
}

impl Cached {
    fn quote(&self, _symbol: &str) -> f64 {
        0.5
    }
}

fn run(source: &Live) -> f64 {
    source.quote("BTC")
}

fn main() {
    let _ = Cached.quote("ETH");
    println!("{}", run(&Live));
}
