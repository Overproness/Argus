# Relay

Relay accepts batches of delivery jobs, allocates receipt numbers, checks
inventory, delivers messages, and records delivery receipts in SQLite. A small
Rust edge component samples upstream peers and builds regional dashboards.
All default endpoints are loopback addresses. There are no credentials.

## Run

Python 3.11 or newer, standard library only:

```sh
python -m relay
python -m unittest discover -s tests
```

Rust:

```sh
cargo test --manifest-path edge/Cargo.toml --locked
cargo run --manifest-path edge/Cargo.toml --locked
```

## Service contracts

- Inventory reads must leave the event loop available to other requests.
- Event reads must give up after the client's configured read deadline.
- Receipt numbers are unique even when authorization requests overlap.
- A successful receipt write survives reopening the database.
- Retention removes every expired completed job, including adjacent jobs.
- Failed delivery attempts stop after three tries.
- A dispatcher runs at most four deliveries concurrently by default.
- A health-check deadline remains effective when a probe takes too long.
- Malformed formatting input must not make later requests unable to acquire
  the formatter's lock.
- An event buffer holds at most sixteen waiting events by default; callers
  get a refusal when it is full.
- Edge peer sampling leaves the current Tokio worker free during a slow read.
- Cache locks are released before asynchronous publication waits.
- A refresh acknowledges completion only after its owned task has succeeded;
  worker errors must reach the caller.
- Dashboard sources are independent and should overlap their waits.

These guarantees apply to the public library entry points as well as the demo.
