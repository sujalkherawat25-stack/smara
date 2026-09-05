"""Compatibility entry point for the strict shared-runtime GAIA runner.

The historical module name is retained for scripts. New reports identify the
runner and scoring policy explicitly, rather than presenting a custom score as
an official leaderboard result.
"""
from .gaia_fair_runner import GaiaFairBenchmark, GaiaOfficialBenchmark
from .evaluation_core import strict_answer_match as question_scorer

__all__ = ["GaiaFairBenchmark", "GaiaOfficialBenchmark", "question_scorer"]
