"""Synthetic statements with hidden ground truth (demo data + evaluation)."""
from __future__ import annotations

from datetime import date, timedelta

from .world import World


def demo_world(end: date | None = None, seed: int = 7, scale: float = 0.75) -> World:
    """The world behind the 'Load demo data' button: a fictional hostel student, ~4 months, ~570 transactions."""
    return World(seed=seed, end=end or (date.today() - timedelta(days=1)), scale=scale)
