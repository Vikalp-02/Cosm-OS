"""Measure the engine against synthetic data whose incidents are known."""

from cosmos.evals.run import evaluate, evaluate_seed
from cosmos.evals.score import Match, Planted, Scorecard, read_planted, score

__all__ = ["Match", "Planted", "Scorecard", "evaluate", "evaluate_seed", "read_planted", "score"]
