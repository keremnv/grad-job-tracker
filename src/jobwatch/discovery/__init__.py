"""Isolated vacancy-first discovery adapters.

Discovery observations are distinct from authoritative monitored-board
observations (`jobwatch.feeds.collect_board` + `jobwatch.postgres.publisher`).
A vacancy disappearing from discovery results never implies closure or
qualified board absence.
"""
from .workable_search import (
    DEFAULT_WORKABLE_UK_QUERIES,
    DiscoveredVacancy,
    DiscoveryReport,
    WorkableSearchAdapter,
    workable_search_discover,
)

__all__ = [
    'DEFAULT_WORKABLE_UK_QUERIES',
    'DiscoveredVacancy',
    'DiscoveryReport',
    'WorkableSearchAdapter',
    'workable_search_discover',
]
