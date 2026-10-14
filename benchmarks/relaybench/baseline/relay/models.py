from dataclasses import dataclass


@dataclass(frozen=True)
class Delivery:
    ticket: int
    sku: str
    payload: dict


@dataclass(frozen=True)
class Job:
    key: str
    completed_at: float | None
