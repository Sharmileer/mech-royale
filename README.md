# Oval Sentinel — Mech Royale

A first competition candidate for `mechfight/1`, built from the official Python starter kit. It optimizes for survival and placement using cover, recovery, navigation, weapon-specific aiming and conservative loot collection.

**Status: locally checked; no practice score yet.** Passing conformance is not evidence of beating the 1.15 starter score, qualifying, or winning. This repository is a starting point for measured competition iterations.

## Run and check

From this directory (the directory containing `Dockerfile`):

```sh
python3 -B -m unittest discover -s tests -v
python3 -B check/check.py -- python3 -B -u bot.py
python3 -B tools/stress.py
python3 -B tools/navigation_smoke.py
python3 -B tools/repo_size.py
```

Python 3.11+ is needed for the optional validation scripts; the submission uses pinned Python 3.13.9. There are no pip dependencies.

Build and run **the exact required Docker conformance command**:

```sh
docker build --platform linux/amd64 -t oval-sentinel .
python3 -B check/check.py --map-dir /arena -- \
  docker run --rm -i --network none --cpus 1 --memory 512m \
  --read-only --tmpfs /tmp:rw,size=64m --user 65534:65534 \
  -v "$PWD/arena:/arena:ro" oval-sentinel
```

On Apple Silicon, the image still must be built for `linux/amd64`. Local emulation timing is not comparable with the scorer's native amd64 CPU. The included GitHub Actions workflow runs the Docker build and exact sandbox check on an amd64 Linux runner.

The Dockerfile pins the official base image by **immutable linux/amd64 digest**, copies only four runtime source files, installs nothing, disables bytecode writes, and sets uid/gid 65534. The map is mounted by the scorer and is not copied into the runtime image. Startup prepares the public map in memory; `act()` performs bounded work with a target budget of 20 ms, leaving room under the server's 50 ms deadline.

## Submission

1. Create a public GitHub repository. Put **this folder's contents** at its root: `Dockerfile` must not be hidden inside another folder.
2. Push a commit, wait for the `Bot checks` workflow to pass, and record the full commit SHA.
3. Submit the repository using the [Mech Royale track page](https://www.joinbuilders.org/buildathon/tracks/builders).
4. Record the practice result in `EXPERIMENTS.csv`. Keep every submitted commit reachable; do not rewrite history or delete the public repository before results are published.

The rules provided for this event give a cutoff of **Sunday, November 22, 2026, at 11:00 AM America/New_York (ET)**. Confirm the live track page before the deadline. The team limit is four practice submissions per hour, each evaluated in 48 matches.

## What the bot does

| Area | Behavior |
| --- | --- |
| Navigation | Reads the official terrain and solid polygons; plans ground routes with incremental A*; accounts for body clearance and building corners. |
| Zone | Predicts the current shrink from the observed circle and timing; moves early when the future edge becomes unsafe. |
| Survival | Prefers less exposed positions; retreats at low health; stops dealing distant damage to permit regeneration; stays behind cover when sheltered. |
| Awareness | Uses observed enemies, recent damage bearings, nearby sounds and short-lived loot memory; scans while calm. |
| Movement | Strafes in fights, checks visible projectiles and charging lasers, uses burst boost with an energy reserve, and attempts escape when stuck. |
| Gun | Solves a moving-target intercept using live projectile speed; checks the real torso slew and line of fire; reloads in quiet periods. |
| Laser | Holds to charge and releases to fire; considers early lethal release and mortar-carrier bonus. |
| Mortar | Solves a moving-target low ballistic arc, checks its path and self-splash distance, and reserves the common torso aim briefly. |
| Lightning | Checks range, aim cone and geometry; conserves enough energy for movement. |
| Loadout | Takes useful nearby weapons, keeps the gun, and swaps the secondary only for a meaningful improvement. |
| Progression | Collects safe cubes and loot; farms nearby crates; prefers mobility, repair and defensive upgrades, with late-zone adjustments. |
| Runtime | Uses the official latest-observation wrapper, JSON lines, stderr for errors, and no runtime downloads or writes. |

The policy reacts to observable threats. It does not assume opponents always cooperate, inspect private seeds, or rely on opponent identity. That matters because practice opponents cooperate but qualifying and final opponents compete in free-for-all matches.

## Known limits to investigate with practice feedback

- The kit contains a recorded checker, **not a playable engine**. No local placement, win-rate or kill-count estimate is claimed.
- Navigation is primarily on the ground. It intentionally skips elevated loot and has only an emergency jump/jet fallback; it does not plan full roof-to-roof flight. One sampled campus start was in a disconnected ground region and needs jet navigation.
- Terrain visibility and movement use bilinear samples; prism collision uses geometry. Trees affect vision but not shots, so shot checks intentionally ignore canopy. This is an approximation of the authoritative engine, not an engine clone.
- Aiming assumes constant observed enemy velocity over the short projectile flight. Sharp dodges, gravity and collisions can invalidate that estimate.
- Strategy thresholds and upgrade preferences are initial hypotheses. Change them using practice evidence, not the number of tests passed.
- Docker was unavailable in the authoring workspace. The exact pinned image's Python filesystem was tested using a read-only user namespace, uid 65534 and one CPU affinity. The full Docker cgroup, network and tmpfs-limit check still needs the included workflow or a Docker host.

## A focused iteration plan

Get a practice result early to reserve time for failures and tie-breaking. Preserve the baseline commit and change one policy family per submission.

| Candidate | Change to compare | Question it answers |
| --- | --- | --- |
| V1 | This implementation | Does reliable navigation and aiming beat the starter? |
| V2 | Recovery thresholds and cover weighting | Is the bot dying before it can heal, or retreating too soon to finish a hunter? |
| V3 | Secondary weapon preference and distance | Which loadout improves placement without losing mobility? |
| V4 | Safe farming and zone timing | Are extra cubes worth the exposure and rotation delay? |

If the site supplies per-match results, compare paired placements on the fixed practice matches and inspect the worst cases. If it only supplies an average, record that limitation and avoid attributing a small gain to one mechanism without evidence. The fixed practice seed set is a development set, not an independent generalization test. Do not hard-code routes for apparent practice spawns.

Use replays or logs only when the platform actually makes them available. The current official rulebook says debug drawings are not displayed on the track, and stderr is exposed for conformance failures, so do not depend on either for gameplay feedback.

Prioritize fixes indicated by observed failures: wrong aim or timing, then getting trapped or taking zone damage, then poor fight selection. Full 3D roof planning is worthwhile if practice evidence shows ground navigation is a major failure source. Re-run conformance after each implementation change and leave a buffer before the cutoff for a fresh build.

## Files

- `bot.py`: strategy, loot, upgrades, movement and decision budget.
- `combat.py`: intercepts, ballistic arcs, aim gating and weapon controls.
- `arena_map.py`: terrain, polygons, collision and ground routes.
- `mechfight.py`: unmodified official Python protocol wrapper.
- `starter_bot.py`: unmodified official example, kept for reference and comparison.
- `tests/`: targeted behavioral and geometric tests.
- `tools/`: synthetic stress, kinematic navigation and source-size checks.
- `check/`, `arena/`, `protocol.md`, `mechfight.schema.json`: official kit materials.
- `reports/`: actual local validation results and official source hashes.
- `.github/workflows/check.yml`: required Docker conformance in CI.

Official materials: [rulebook](https://www.joinbuilders.org/buildathon/tracks/builders/files/protocol.md?inline=1), [starter archive](https://www.joinbuilders.org/buildathon/tracks/builders/files/mechfight-kit.tar.gz). The wrapper, checker, schema, recorded stream and map retain their original contents; `reports/source_manifest.json` records the downloaded bytes' hashes. Official assets remain subject to their original terms.
