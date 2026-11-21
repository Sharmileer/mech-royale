# Validation — 2026-11-20

These are implementation checks. The first official practice run was submitted at 7:20 PM ET on November 20, 2026 for team STARLIGHT (receipt BB-8E16196B). It scored **2.58 placement points**, versus the starter baseline of **1.15**. The leaderboard showed STARLIGHT at **32nd of 42 teams** at observation; this is provisional and does not establish qualification.

| Check | Final observed result |
| --- | --- |
| Official recorded-stream conformance | PASS; 300/300 answers on time; zero malformed lines, wrong ticks or stale queued answers |
| Startup | 1,016 ms; official allowance is 20,000 ms |
| Recorded-stream response timing | p50 0.7 ms; p90 1.5 ms; p99 4.6 ms; maximum 5.0 ms |
| Behavioral and geometry tests | 23 tests passed |
| Crowded synthetic observations | 800 processed; zero action-schema errors or nonfinite values |
| Synthetic decision timing | p50 2.28 ms; p99 11.84 ms; maximum 31.77 ms |
| Synthetic process peak RSS | 25,328 KiB, about 24.7 MiB; this is not a cgroup memory-limit test |
| Campus kinematic ground routes | 11/11 ground-connected cases reached their target; one additional case identified a disconnected region requiring jet navigation |
| Official practice score | 2.58 displayed placement points; 48-match practice format; receipt BB-8E16196B |
| Exact Docker sandbox conformance | PASS on GitHub Actions; 300/300 timely responses; maximum 5.1 ms |

The final conformance and stress runs used Python 3.13.9 from the Dockerfile's **exact, digest-verified linux/amd64 base image**, uid/gid 65534, a read-only filesystem, a writable `/tmp`, and CPU affinity restricted to one core through Bubblewrap. This validates the interpreter and key filesystem assumptions; it is **not** a Docker build, a Docker CPU-quota test, a network-isolation test, a 512 MB cgroup test, or a 64 MB tmpfs-limit test. The subsequent [GitHub Actions run](https://github.com/Sharmileer/mech-royale/actions/runs/44842155600) **completed the exact required Docker build and sandbox check successfully** for commit `636b6618e49fd4ad54f8b748bb1cf49ab0a8fb16`. Its startup was 1,044 ms, p50 0.8 ms, p99 4.7 ms and maximum 5.1 ms, with zero late observations or schema errors. All 23 behavioral tests and the source-size check also passed.

Timing varies with host load and CPU. Earlier runs had different maxima; the raw files here record the final source version's runs. No response-time guarantee on the scorer's machine is implied.

The supplied official stream only exercised the starting gun and up to one visible opponent. Synthetic tests add all secondary weapons, 15 opponents, 36 projectiles, low health, low energy, upgrades and small zones. Their state transitions are artificial, so they cannot establish tactical quality. Likewise, navigation smoke tests move a point with the bot's geometry checks and are not a reproduction of the server's acceleration, collision or jet physics.

The navigation smoke test exposed corner collisions in the initial sampled-path implementation. The final implementation checks swept body clearance and passed the connected ground cases. Disconnected courtyards and deliberate rooftop traversal remain limitations for practice-driven follow-up.

Raw evidence: `unit-tests.txt`, `conformance-pinned-python.txt`, `stress-pinned-python.json`, `navigation-smoke.json`. Official input hashes and source URLs are in `source_manifest.json`.
