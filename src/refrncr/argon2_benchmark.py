from __future__ import annotations

import argparse
import json
import platform
import secrets
import statistics
from collections.abc import Callable, Sequence
from time import perf_counter_ns
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from argon2.low_level import ARGON2_VERSION

from refrncr.services import PASSWORD_HASHER

Timer = Callable[[], int]
PasswordFactory = Callable[[], str]


def argon2_profile(hasher: PasswordHasher) -> dict[str, int | str]:
    return {
        "type": f"argon2{hasher.type.name.lower()}",
        "version": ARGON2_VERSION,
        "time_cost": hasher.time_cost,
        "memory_cost_kib": hasher.memory_cost,
        "memory_cost_mib": hasher.memory_cost // 1024,
        "parallelism": hasher.parallelism,
        "hash_length_bytes": hasher.hash_len,
        "salt_length_bytes": hasher.salt_len,
    }


def latency_summary(samples_ns: list[int]) -> dict[str, float]:
    samples_ms = [sample / 1_000_000 for sample in samples_ns]
    return {
        "mean_ms": round(statistics.fmean(samples_ms), 3),
        "median_ms": round(statistics.median(samples_ms), 3),
        "minimum_ms": round(min(samples_ms), 3),
        "maximum_ms": round(max(samples_ms), 3),
    }


def run_benchmark(
    samples: int,
    *,
    hasher: PasswordHasher | None = None,
    timer: Timer | None = None,
    password_factory: PasswordFactory = lambda: secrets.token_urlsafe(32),
) -> dict[str, Any]:
    if samples < 1:
        raise ValueError("samples must be at least 1")

    selected_hasher = hasher or PASSWORD_HASHER
    selected_timer = timer or perf_counter_ns
    hash_samples: list[int] = []
    successful_verification_samples: list[int] = []
    failed_verification_samples: list[int] = []

    for _ in range(samples):
        password = password_factory()
        failed_password = f"{password}-synthetic-mismatch"

        started = selected_timer()
        encoded_hash = selected_hasher.hash(password)
        hash_samples.append(selected_timer() - started)

        started = selected_timer()
        selected_hasher.verify(encoded_hash, password)
        successful_verification_samples.append(selected_timer() - started)

        started = selected_timer()
        try:
            selected_hasher.verify(encoded_hash, failed_password)
        except VerifyMismatchError:
            pass
        else:
            raise RuntimeError("synthetic failed-verification sample unexpectedly succeeded")
        failed_verification_samples.append(selected_timer() - started)

    return {
        "context": {
            "warning": "Timings are machine-specific samples, not performance guarantees.",
            "platform": platform.platform(),
            "python_version": platform.python_version(),
        },
        "parameters": argon2_profile(selected_hasher),
        "samples_per_operation": samples,
        "latency": {
            "hash": latency_summary(hash_samples),
            "successful_verification": latency_summary(successful_verification_samples),
            "failed_verification": latency_summary(failed_verification_samples),
        },
    }


def positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark refrncr's configured Argon2id profile with synthetic passwords."
    )
    parser.add_argument(
        "--samples",
        type=positive_integer,
        default=5,
        help="Measured samples for each operation (default: 5).",
    )
    arguments = parser.parse_args(argv)
    print(json.dumps(run_benchmark(arguments.samples), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
