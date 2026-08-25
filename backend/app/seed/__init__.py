"""Deterministic seed-data generator for the RazorShield simulation dataset.

The dataset is synthetic. It is generated from a fixed random seed so the same
command always produces the same population, the same behavioural histories and
the same demo scenarios.
"""

from app.seed.config import SeedConfig
from app.seed.runner import SeedResult, seed_database

__all__ = ["SeedConfig", "SeedResult", "seed_database"]
