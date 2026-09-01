import json

import pytest
from argon2.exceptions import VerifyMismatchError
from argon2.low_level import Type

from refrncr import argon2_benchmark
from refrncr.services import PASSWORD_HASHER


class FakeHasher:
    type = Type.ID
    time_cost = 3
    memory_cost = 64 * 1024
    parallelism = 4
    hash_len = 32
    salt_len = 16

    def hash(self, password):
        return f"encoded:{password}"

    def verify(self, encoded_hash, password):
        if encoded_hash != f"encoded:{password}":
            raise VerifyMismatchError
        return True


def test_profile_is_read_from_the_application_password_hasher():
    assert argon2_benchmark.argon2_profile(PASSWORD_HASHER) == {
        "type": "argon2id",
        "version": 19,
        "time_cost": 3,
        "memory_cost_kib": 65536,
        "memory_cost_mib": 64,
        "parallelism": 4,
        "hash_length_bytes": 32,
        "salt_length_bytes": 16,
    }


def test_benchmark_produces_structured_output_without_wall_clock_assumptions(monkeypatch, capsys):
    ticks = iter(range(0, 13_000_000, 1_000_000))
    monkeypatch.setattr(argon2_benchmark, "PASSWORD_HASHER", FakeHasher())
    monkeypatch.setattr(argon2_benchmark, "perf_counter_ns", lambda: next(ticks))
    monkeypatch.setattr(argon2_benchmark.secrets, "token_urlsafe", lambda _size: "synthetic-only")

    assert argon2_benchmark.main(["--samples", "2"]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["parameters"] == argon2_benchmark.argon2_profile(FakeHasher())
    assert output["samples_per_operation"] == 2
    assert set(output["latency"]) == {
        "hash",
        "successful_verification",
        "failed_verification",
    }
    assert "machine-specific" in output["context"]["warning"]
    assert "synthetic-only" not in json.dumps(output)


@pytest.mark.parametrize("samples", [0, -1])
def test_benchmark_rejects_non_positive_sample_counts(samples):
    with pytest.raises(ValueError, match="samples must be at least 1"):
        argon2_benchmark.run_benchmark(samples, hasher=FakeHasher())
