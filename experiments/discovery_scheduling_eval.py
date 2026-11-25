"""Deterministic simulation comparing ATS discovery scheduling strategies over 90 days.

Models the actual 13,728-board directory distribution measured on 24–25 Nov 2026:
- 23 UK-name-hinted boards (11.8% UK-relevant yield, 20% early-career)
- 13,705 general boards (8.3% UK-relevant yield, 5% dead/empty, 3% error rate)
- 30 newly added boards per weekly directory refresh (15% UK-relevant)
- 40 board probes per day (3,600 probes over 90 days)

Compares:
  A. Alphabetical FIFO (naive current queue)
  B. Greedy priority (re-probes known UK boards aggressively, 0% exploration reserve)
  C. Multi-signal priority + 35% reserved exploration + exponential failure backoff (proposed)
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import random


@dataclass
class SimBoard:
    idx: int
    uk_hint: bool
    is_uk_relevant: bool
    has_priority: bool
    is_dead: bool
    added_day: int
    last_probed: int | None = None
    probe_count: int = 0
    known_uk_rel: bool = False
    known_prio: bool = False
    verified_id: bool = False
    vacancies_found_days: int = 0


def build_population(seed: int = 20261125) -> list[SimBoard]:
    rng = random.Random(seed)
    boards: list[SimBoard] = []
    for i in range(13728):
        uk_hint = i < 23
        is_dead = rng.random() < 0.05
        is_uk_rel = (not is_dead) and (rng.random() < (0.118 if uk_hint else 0.083))
        has_prio = is_uk_rel and (rng.random() < 0.22)
        boards.append(SimBoard(i, uk_hint, is_uk_rel, has_prio, is_dead, added_day=0))
    return boards


def simulate(strategy: str, *, days: int = 90, daily_budget: int = 40, seed: int = 20261125) -> dict:
    rng = random.Random(seed + 1)
    boards = build_population(seed)
    next_idx = len(boards)

    total_probes = 0
    total_requests = 0
    uk_relevant_discoveries = 0
    priority_discoveries = 0
    unique_uk_boards_found: set[int] = set()
    unique_unexplored_probed: set[int] = set()
    new_uk_detect_latencies: list[int] = []
    detected_new_uk: set[int] = set()

    for day in range(1, days + 1):
        # Weekly Sunday refresh adds 30 new boards (higher UK startup freshness).
        if day % 7 == 0:
            for _ in range(30):
                is_uk_rel = rng.random() < 0.14
                boards.append(SimBoard(
                    next_idx, uk_hint=(rng.random() < 0.05), is_uk_relevant=is_uk_rel,
                    has_priority=(is_uk_rel and rng.random() < 0.25), is_dead=False, added_day=day,
                ))
                next_idx += 1

        def eligible(b: SimBoard) -> bool:
            if b.last_probed is None:
                return True
            age = day - b.last_probed
            if b.known_uk_rel:
                return age >= 7
            if b.is_dead:
                backoff = min(56, 7 * (2 ** min(max(b.probe_count - 1, 0), 3)))
                return age >= (backoff if strategy == "prioritized_explore" else 30)
            if strategy == "prioritized_explore":
                return age >= (60 if b.probe_count >= 2 else 30)
            return age >= 30

        pool = [b for b in boards if eligible(b)]
        chosen: list[SimBoard] = []

        if strategy == "fifo":
            pool.sort(key=lambda b: (0 if b.uk_hint and b.last_probed is None else 1, 0 if b.last_probed is None else 1, b.last_probed or -1, b.idx))
            chosen = pool[:daily_budget]
        elif strategy == "greedy_priority":
            def gscore(b: SimBoard) -> float:
                s = 0.0
                if b.known_prio:
                    s += 60
                if b.known_uk_rel:
                    s += 40
                if b.uk_hint:
                    s += 20
                if b.last_probed is not None:
                    s += min(day - b.last_probed, 60)
                return s
            pool.sort(key=lambda b: (-gscore(b), b.idx))
            chosen = pool[:daily_budget]
        elif strategy == "prioritized_explore":
            explore_quota = int(round(daily_budget * 0.35))
            unprobed = [b for b in pool if b.last_probed is None]
            # Prioritize newly added directory entries + deterministic hash rotation
            unprobed.sort(key=lambda b: (0 if (day - b.added_day) <= 7 and b.added_day > 0 else 1, hash((b.idx, seed)) & 0xFFFFFFFF))
            chosen = unprobed[:explore_quota]
            chosen_ids = {b.idx for b in chosen}
            rest = [b for b in pool if b.idx not in chosen_ids]

            def pscore(b: SimBoard) -> float:
                s = 0.0
                if b.added_day > 0 and (day - b.added_day) <= 7:
                    s += 30
                if b.verified_id:
                    s += 25
                if b.known_prio:
                    s += 40
                if b.known_uk_rel:
                    s += 30
                if b.uk_hint:
                    s += 20
                if b.last_probed is None:
                    s += 10
                else:
                    s += min(day - b.last_probed, 60)
                return s

            rest.sort(key=lambda b: (-pscore(b), hash((b.idx, seed)) & 0xFFFFFFFF))
            chosen.extend(rest[: daily_budget - len(chosen)])

        for b in chosen:
            first_time = b.last_probed is None
            b.last_probed = day
            b.probe_count += 1
            total_probes += 1
            # Two-pass Greenhouse: 1 req for non-UK/dead, ~1.5 avg reqs for UK-relevant
            total_requests += 2 if b.is_uk_relevant else 1
            if first_time:
                unique_unexplored_probed.add(b.idx)
            if b.is_uk_relevant:
                b.known_uk_rel = True
                if b.has_priority:
                    b.known_prio = True
                if rng.random() < 0.45:
                    b.verified_id = True
                unique_uk_boards_found.add(b.idx)
                uk_relevant_discoveries += 1
                if b.has_priority:
                    priority_discoveries += 1
                if b.added_day > 0 and b.idx not in detected_new_uk:
                    detected_new_uk.add(b.idx)
                    new_uk_detect_latencies.append(day - b.added_day)

    avg_new_latency = round(sum(new_uk_detect_latencies) / len(new_uk_detect_latencies), 1) if new_uk_detect_latencies else None
    return {
        "strategy": strategy,
        "days": days,
        "total_probes": total_probes,
        "total_requests": total_requests,
        "unique_boards_explored": len(unique_unexplored_probed),
        "unique_uk_boards_found": len(unique_uk_boards_found),
        "uk_relevant_observations": uk_relevant_discoveries,
        "priority_observations": priority_discoveries,
        "uk_obs_per_100_requests": round(100.0 * uk_relevant_discoveries / total_requests, 2),
        "new_uk_boards_detected": len(detected_new_uk),
        "avg_new_uk_detect_days": avg_new_latency,
    }


if __name__ == "__main__":
    results = [simulate(s) for s in ("fifo", "greedy_priority", "prioritized_explore")]
    print(json.dumps(results, indent=2))
