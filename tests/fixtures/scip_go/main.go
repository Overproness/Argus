// Two types share the method name Quote. Name matching links run to both;
// SCIP links it only to Live.Quote.
package main

import "fmt"

type Live struct{}
type Cached struct{}

func (Live) Quote(symbol string) float64 {
	return 1.0
}

func (Cached) Quote(symbol string) float64 {
	return 0.5
}

func run(source Live) float64 {
	return source.Quote("BTC")
}

func main() {
	Cached{}.Quote("ETH")
	fmt.Println(run(Live{}))
}
