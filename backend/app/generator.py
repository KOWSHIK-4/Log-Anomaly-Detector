#!/usr/bin/env python
"""Synthetic log generator used for demos and end-to-end testing.

Scenarios
---------
normal      steady low error rate (builds the baseline)
spike       sudden burst of errors
ramp        error rate increases gradually until it trips every severity
recovery    returns to normal so the incident is closed

Usage
-----
    python -m app.generator --scenario normal --rate 5
    python -m app.generator --scenario ramp --rate 12 --duration 120
    python -m app.generator --demo          # full scripted demo
"""

from __future__ import annotations

import argparse
import os
import random
import signal
import sys
import time
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402

SERVICES = [
    "api-gateway",
    "auth-service",
    "payment-service",
    "inventory-service",
    "search-service",
    "notification-worker",
    "db-primary",
    "cache-redis",
]

INFO_MESSAGES = [
    "request completed method=GET path=/api/v1/orders status=200 duration_ms={d}",
    "request completed method=POST path=/api/v1/checkout status=201 duration_ms={d}",
    "cache hit ratio={r}% key=user:{n}",
    "health check ok service={svc}",
    "user session authenticated user_id={n} ip=10.0.{a}.{b}",
    "batch job finished records={n} duration_ms={d}",
]

WARN_MESSAGES = [
    "slow query detected duration_ms={d} table=orders",
    "retry attempt {n} for upstream=payment-service",
    "memory usage high percent={r}",
    "connection pool near capacity used={r}%",
]

ERROR_MESSAGES = [
    "database connection refused host=db-primary port=5432",
    "upstream timeout calling payment-service after 5000ms",
    "unhandled exception NullPointerException at InventoryService.reserve",
    "payment gateway returned HTTP 502 for charge id=txn_{h}",
    "failed to write to queue redis-connection-reset",
    "authentication token signature mismatch for user_id={n}",
    "disk write failed no space left on device /var/lib/data",
]

CRITICAL_MESSAGES = [
    "database cluster unreachable - failover initiated",
    "all payment replicas down, checkout unavailable",
    "out of memory, process killed pid={n}",
]


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S") + f".{random.randint(0, 999):03d}Z"


def make_line(level: str) -> str:
    service = random.choice(SERVICES)
    if level == "ERROR":
        message = random.choice(ERROR_MESSAGES)
    elif level == "CRITICAL":
        message = random.choice(CRITICAL_MESSAGES)
    elif level == "WARN":
        message = random.choice(WARN_MESSAGES)
    else:
        message = random.choice(INFO_MESSAGES)
    message = message.format(
        d=random.randint(1, 4000),
        n=random.randint(1, 99999),
        r=random.randint(10, 99),
        svc=service,
        a=random.randint(0, 255),
        b=random.randint(1, 254),
        h=os.urandom(4).hex(),
    )
    return f"{_now_iso()} {level:<8} [{service}] {message}"


def sample_level(error_rate: float, warn_rate: float = 0.08) -> str:
    roll = random.random()
    if roll < error_rate:
        # a small share of errors are CRITICAL -> higher severity alerts
        return "CRITICAL" if random.random() < 0.15 else "ERROR"
    if roll < error_rate + warn_rate:
        return "WARN"
    return "INFO"


def emit(path: Path, lines: List[str]) -> None:
    if not lines:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def run_scenario(
    path: Path,
    error_rate: float,
    lines_per_tick: int,
    interval: float,
    duration: Optional[float] = None,
    stop_flag=None,
) -> None:
    started = time.time()
    written = 0
    while True:
        if stop_flag is not None and stop_flag.is_set():
            break
        if duration and (time.time() - started) >= duration:
            break
        lines = [make_line(sample_level(error_rate)) for _ in range(lines_per_tick)]
        emit(path, lines)
        written += len(lines)
        time.sleep(interval)
    print(f"wrote {written} lines to {path}")


def run_ramp(
    path: Path,
    start_rate: float,
    end_rate: float,
    seconds: float,
    lines_per_tick: int,
    interval: float,
    stop_flag=None,
) -> None:
    """Linearly increase the error rate from ``start_rate`` to ``end_rate``."""

    started = time.time()
    written = 0
    while True:
        elapsed = time.time() - started
        if stop_flag is not None and stop_flag.is_set() or elapsed >= seconds:
            break
        progress = min(1.0, elapsed / seconds)
        rate = start_rate + (end_rate - start_rate) * progress
        lines = [make_line(sample_level(rate)) for _ in range(lines_per_tick)]
        emit(path, lines)
        written += len(lines)
        time.sleep(interval)
    print(f"wrote {written} lines to {path}")


#: Scripted demo: baseline -> LOW -> MEDIUM -> HIGH -> CRITICAL -> recovery.
DEMO_STAGES = [
    ("PHASE 1  normal traffic - learning the baseline", "steady", 0.02, 40.0),
    ("PHASE 2  mild degradation - expect LOW", "steady", 0.08, 30.0),
    ("PHASE 3  rising errors - expect MEDIUM / HIGH", "ramp", 0.35, 30.0),
    ("PHASE 4  outage - expect CRITICAL", "steady", 0.85, 30.0),
    ("PHASE 5  recovery - incident closes", "steady", 0.02, 35.0),
]


def demo(path: Path, rate: int = 8, stop_flag=None) -> None:
    """Full scripted demo that walks through every severity level."""

    def stopped() -> bool:
        return stop_flag is not None and stop_flag.is_set()

    print(f"Demo log stream -> {path}")
    print("Press Ctrl+C to stop early.\n")
    for label, kind, rate_value, seconds in DEMO_STAGES:
        if stopped():
            break
        print(f"  {label}")
        if kind == "ramp":
            run_ramp(path, 0.08, rate_value, seconds, rate, 0.25, stop_flag)
        else:
            run_scenario(path, rate_value, rate, 0.25, seconds, stop_flag)
    print("\ndemo complete - the dashboard should show LOW -> MEDIUM/HIGH -> CRITICAL -> RECOVERED")


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Synthetic log generator")
    parser.add_argument("--file", default=str(settings.log_file), help="target log file")
    parser.add_argument("--scenario", default="normal", choices=["normal", "spike", "ramp", "recovery"])
    parser.add_argument("--rate", type=float, default=0.03, help="error rate 0..1 (or peak rate for --scenario ramp)")
    parser.add_argument("--lines", type=int, default=8, help="lines per tick")
    parser.add_argument("--interval", type=float, default=0.25, help="seconds between ticks")
    parser.add_argument("--duration", type=float, default=0, help="0 = run until Ctrl+C")
    parser.add_argument("--demo", action="store_true", help="run the full scripted demo")
    parser.add_argument("--truncate", action="store_true", help="clear the file before writing")
    args = parser.parse_args()

    path = Path(args.file)
    if args.truncate and path.exists():
        path.unlink()

    stop_flag = {"stop": False}

    def _handle(_signum, _frame):
        stop_flag["stop"] = True
        print("\nstopping generator...")
        raise SystemExit(0)

    try:
        signal.signal(signal.SIGINT, _handle)
    except Exception:
        pass

    class _Flag:
        def is_set(self) -> bool:
            return stop_flag["stop"]

    flag = _Flag()
    try:
        if args.demo:
            demo(path, args.lines, flag)
        elif args.scenario == "ramp":
            print(f"scenario=ramp 2% -> {args.rate:.0%} over {args.duration or 60:.0f}s -> {path}")
            run_ramp(path, 0.02, args.rate, args.duration or 60.0, args.lines, args.interval, flag)
        else:
            effective_rate = {
                "normal": 0.02,
                "spike": 0.9,
                "recovery": 0.01,
            }.get(args.scenario, args.rate)
            print(f"scenario={args.scenario} error_rate={effective_rate:.0%} -> {path}")
            run_scenario(path, effective_rate, args.lines, args.interval, args.duration or None, flag)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
