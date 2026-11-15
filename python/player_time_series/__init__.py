"""Player time-series training package."""

from .dataset import PlayerSeries, Representation, load_player_series
from .model import TimeSeriesTrainer, predict_next_year, train_model
from .strategies import AbsoluteStrategy, PercentageStrategy

__all__ = [
    "AbsoluteStrategy",
    "PercentageStrategy",
    "PlayerSeries",
    "Representation",
    "TimeSeriesTrainer",
    "load_player_series",
    "predict_next_year",
    "train_model",
]
