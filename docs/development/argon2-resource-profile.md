# Argon2id resource profile

refrncr Community stores local passwords with Argon2id. Argon2id combines Argon2i's resistance to
side-channel attacks with Argon2d's resistance to accelerated cracking, and is provided by the
project's existing `argon2-cffi` dependency.

## Selected profile

The authoritative configuration is `PASSWORD_HASHER` in `src/refrncr/services.py`:

| Parameter | Value |
| --- | ---: |
| Algorithm | Argon2id |
| Version | 19 (0x13) |
| Time cost | 3 |
| Memory cost | 65,536 KiB (64 MiB) |
| Parallelism | 4 |
| Hash length | 32 bytes |
| Salt length | 16 bytes |

The library generates a unique random salt for every hash. The memory cost is the total Argon2
working-memory request for one hash or verification, not 64 MiB per parallel lane. Allow
approximately 64 MiB plus library and process overhead for each operation in flight. For example,
four truly concurrent operations can require roughly 256 MiB of Argon2 working memory in addition
to normal application memory. Parallelism also increases the CPU capacity that one operation can
use, subject to the runtime and available cores.

Increasing any cost parameter can improve resistance to offline password cracking, but also
increases interactive login latency and concurrent CPU or memory consumption. Parameter changes
must therefore be reviewed and benchmarked rather than inferred from results on another machine.

## Run the benchmark

From an installed development checkout, run:

```shell
python -m refrncr.argon2_benchmark --samples 5
```

The utility imports the application's configured `PASSWORD_HASHER`, uses generated synthetic
passwords, and reports JSON containing the parameters and hash, successful-verification, and
failed-verification latency summaries. It is opt-in: application startup and the normal test suite
do not execute the resource benchmark.

Results are machine-specific samples, not normative performance guarantees. Operators should run
the utility on representative target hardware and consider the application's expected concurrent
authentication load. The configured parameters are authoritative; timings recorded on developer
machines are not.

## Concurrency and throttling ownership

REF-44 documents the cost of an individual credential operation. It deliberately does not add
request throttling, process-local semaphores, concurrency rejection, Redis, or distributed
coordination. HTTP/browser concurrency controls and source-aware login-attempt throttling belong
to REF-43, where request identity, response behavior, denial-of-service tradeoffs, and recovery can
be designed together at the browser-session boundary.
