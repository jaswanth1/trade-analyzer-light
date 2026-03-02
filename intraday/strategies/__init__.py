"""Intraday strategy modules — institutional grade.

Each strategy is a pure function: (features, regimes) -> candidate_trade | None
Strategies: ORB, pullback, compression breakout, mean-reversion, swing continuation, MLR.
"""

from intraday.strategies.orb import evaluate_orb
from intraday.strategies.pullback import evaluate_pullback
from intraday.strategies.compression import evaluate_compression
from intraday.strategies.mean_revert import evaluate_mean_revert
from intraday.strategies.swing import evaluate_swing
from intraday.strategies.mlr import evaluate_mlr

__all__ = [
    "evaluate_orb",
    "evaluate_pullback",
    "evaluate_compression",
    "evaluate_mean_revert",
    "evaluate_swing",
    "evaluate_mlr",
]
