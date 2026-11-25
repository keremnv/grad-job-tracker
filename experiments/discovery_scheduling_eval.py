"""Deterministic simulation comparing ATS discovery scheduling strategies.

Explicitly distinguishes SIMULATED outcomes from measured production behaviour.
Measures:
- Unique relevant UK employers discovered (not merely repeated observations)
- Unique relevant UK vacancies & unique early-career vacancies discovered
- Repeated vs unique UK observations per 100 requests
- Exploration fairness (provider mix across Greenhouse/Ashby/Lever, unprobed coverage)
- Starvation resistance over 90 and 180 days
- Detection latency for newly added UK directory entries
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import random

PROVIDERS = ("greenhouse", "ashby", "lever")


@dataclass
class SimBoard:
    idx: int
    provider: str
    uk_hint: bool
    is_uk_relevant: bool
    uk_vac_count: int
    prio_vac_count: int
    is_dead: bool
    added_day: int
    last_probed: int | None = None
    probe_count: int = 0
    known_uk_rel: bool = False
    known_prio: bool = False
    verified_id: bool = False


def build_population(seed: int = 20261125) -> list[SimBoard]:
    """Synthetic population parameterised by the 13,749-board directory empirical profile."""
    rng = random.Random(seed)
    # Approximate provider proportions from live 2026-11-25 directory: GH ~45%, Ashby ~31%, Lever ~24%
    boards: list[SimBoard] = []
    for i in range(13749):
        r = rng.random()
        prov = "greenhouse" if r < 0.45 else ("ashby" if r < 0.76 else "lever")
        uk_hint = i < 23
        is_dead = rng.random() < 0.05
        is_uk_rel = (not is_dead) and (rng.random() < (0.118 if uk_hint else 0.083))
        uk_vacs = rng.randint(1, 6) if is_uk_rel else 0
        prio_vacs = (rng.randint(1, 2) if rng.random() < 0.22 else 0) if is_uk_rel else 0
        boards.append(SimBoard(i, prov, uk_hint, is_uk_rel, uk_vacs, prio_vacs, is_dead, added_day=0))
    return boards


def simulate(strategy: str, *, days: int = 90, daily_budget: int = 40, exploration_ratio: float = 0.35, seed: int = 20261125) -> dict:
    rng = random.Random(seed + 1)
    boards = build_population(seed)
    next_idx = len(boards)

    total_probes = 0
    total_requests = 0
    uk_relevant_observations = 0
    priority_observations = 0
    unique_uk_boards: set[int] = set()
    unique_prio_boards: set[int] = set()
    unique_uk_vacancies = 0
    unique_prio_vacancies = 0
    unique_explored: set[int] = set()
    provider_probes = {p: 0 for p in PROVIDERS}
    new_uk_detect_latencies: list[int] = []
    detected_new_uk: set[int] = set()
    total_new_uk_added = 0

    for day in range(1, days + 1):
        # Weekly Sunday refresh adds 30 newly observed boards across providers.
        if day % 7 == 0:
            for k in range(30):
                prov = PROVIDERS[k % 3]
                is_uk_rel = rng.random() < 0.14
                uk_vacs = rng.randint(1, 5) if is_uk_rel else 0
                prio_vacs = (1 if rng.random() < 0.25 else 0) if is_uk_rel else 0
                if is_uk_rel:
                    total_new_uk_added += 1
                boards.append(SimBoard(
                    next_idx, prov, uk_hint=(rng.random() < 0.05), is_uk_relevant=is_uk_rel,
                    uk_vac_count=uk_vacs, prio_vac_count=prio_vacs, is_dead=False, added_day=day,
                ))
                next_idx += 1

        # Simulate natural churn: 3% of UK boards post 1 new vacancy on a given week
        if day % 7 == 3:
            for b in boards:
                if b.is_uk_relevant and b.idx in unique_uk_boards and rng.random() < 0.03:
                    b.uk_vac_count += 1

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
            explore_quota = int(round(daily_budget * exploration_ratio))
            # Provider-balanced deterministic hash rotation for unprobed boards (new entries first)
            buckets = []
            for prov in PROVIDERS:
                unp = [b for b in pool if b.last_probed is None and b.provider == prov]
                unp.sort(key=lambda b: (0 if (b.added_day > 0 and day - b.added_day <= 7) else 1, hash((b.idx, seed)) & 0xFFFFFFFF))
                buckets.append(unp)
            idx = 0
            while len(chosen) < explore_quota and any(idx < len(bk) for bk in buckets):
                for bk in buckets:
                    if idx < len(bk) and len(chosen) < explore_quota:
                        chosen.append(bk[idx])
                idx += 1

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
            provider_probes[b.provider] += 1
            total_requests += 2 if b.is_uk_relevant else 1
            if first_time:
                unique_explored.add(b.idx)
            if b.is_uk_relevant:
                b.known_uk_rel = True
                if b.prio_vac_count > 0:
                    b.known_prio = True
                if rng.random() < 0.45:
                    b.verified_id = True
                if b.idx not in unique_uk_boards:
                    unique_uk_boards.add(b.idx)
                    unique_uk_vacancies += b.uk_vac_count
                    if b.prio_vac_count > 0:
                        unique_prio_boards.add(b.idx)
                        unique_prio_vacancies += b.prio_vac_count
                uk_relevant_observations += 1
                if b.prio_vac_count > 0:
                    priority_observations += 1
                if b.added_day > 0 and b.idx not in detected_new_uk:
                    detected_new_uk.add(b.idx)
                    new_uk_detect_latencies.append(day - b.added_day)

    avg_new_latency = round(sum(new_uk_detect_latencies) / len(new_uk_detect_latencies), 1) if new_uk_detect_latencies else None
    return {
        "kind": "simulated_benchmark",
        "strategy": strategy,
        "days": days,
        "exploration_ratio": exploration_ratio if strategy == "prioritized_explore" else None,
        "total_probes": total_probes,
        "total_requests": total_requests,
        "unique_boards_explored": len(unique_explored),
        "unique_uk_employers_discovered": len(unique_uk_boards),
        "unique_priority_employers_discovered": len(unique_prio_boards),
        "unique_uk_vacancies_discovered": unique_uk_vacancies,
        "unique_priority_vacancies_discovered": unique_prio_vacancies,
        "unique_uk_employers_per_100_requests": round(100.0 * len(unique_uk_boards) / total_requests, 2),
        "repeated_uk_observations": uk_relevant_observations,
        "repeated_priority_observations": priority_observations,
        "uk_obs_per_100_requests": round(100.0 * uk_relevant_observations / total_requests, 2),
        "provider_probe_share": {p: round(provider_probes[p] / total_probes, 3) for p in PROVIDERS},
        "new_uk_boards_added": total_new_uk_added,
        "new_uk_boards_detected": len(detected_new_uk),
        "avg_new_uk_detect_days": avg_new_latency,
    }


if __name__ == "__main__":
    out = {
        "90_day": [
            simulate("fifo", days=90),
            simulate("greedy_priority", days=90),
            simulate("prioritized_explore", days=90, exploration_ratio=0.35),
            simulate("prioritized_explore", days=90, exploration_ratio=0.60),
        ],
        "180_day": [
            simulate("fifo", days=180),
            simulate("greedy_priority", days=180),
            simulate("prioritized_explore", days=180, exploration_ratio=0.35),
            simulate("prioritized_explore", days=180, exploration_ratio=0.60),
        ],
    }
    print(json.dumps(out, indent=2))
