"""FSRS-5 (Free Spaced Repetition Scheduler) — replaces the old SM-2 scheduler.

Where SM-2 kept one `ease_factor` per card and multiplied intervals by it, FSRS
models memory with two variables per card:

  Stability (S)   days until recall probability decays to the target retention
  Difficulty (D)  intrinsic hardness of the card, 1..10

and a derived quantity:

  Retrievability (R)  probability you'd still recall the card right now

Every review updates D and S based on how long it had been since the last review
(which SM-2 ignores entirely) and how well you did. The next interval is then
solved directly from the forgetting curve for whatever retention you asked for.

The weights below are the published FSRS-5 defaults, fitted on a large public
review dataset. Per-user weight optimisation is possible but needs the full
review history plus a fitting library (torch/scipy) — deliberately out of scope
for a 1GB VM; the defaults are strong on their own.
"""
import math
from typing import Optional

# FSRS-5 default weights w0..w18
DEFAULT_W: tuple[float, ...] = (
    0.40255, 1.18385, 3.173, 15.69105,   # w0..w3  initial stability per rating
    7.1949, 0.5345,                       # w4,w5   initial difficulty
    1.4604, 0.0046,                       # w6,w7   difficulty delta + mean reversion
    1.54575, 0.1192, 1.01925,             # w8..w10 stability on success
    1.9395, 0.11, 0.29605, 2.2698,        # w11..w14 stability on lapse
    0.2315, 2.9898,                       # w15,w16 hard penalty / easy bonus
    0.51655, 0.6621,                      # w17,w18 same-day review
)

# Forgetting curve: R(t) = (1 + FACTOR * t/S) ** DECAY
DECAY = -0.5
FACTOR = 19 / 81

RATING_AGAIN = 1
RATING_HARD = 2
RATING_GOOD = 3
RATING_EASY = 4

# The bot's buttons send SM-2 style qualities (1/3/4/5); FSRS wants 1..4.
QUALITY_TO_RATING = {1: RATING_AGAIN, 3: RATING_HARD, 4: RATING_GOOD, 5: RATING_EASY}

MIN_STABILITY = 0.01      # days
MAX_INTERVAL_DAYS = 3650  # 10 years — stops runaway intervals on very easy cards
DEFAULT_RETENTION = 0.9

LEECH_THRESHOLD = 4  # consecutive "Again" answers before a card is flagged a leech


def _clamp_difficulty(d: float) -> float:
    return min(10.0, max(1.0, d))


def retrievability(elapsed_days: float, stability: float) -> float:
    """Probability of recall after `elapsed_days` given `stability`."""
    if stability <= 0:
        return 0.0
    return (1 + FACTOR * elapsed_days / stability) ** DECAY


def _initial_stability(w: tuple[float, ...], rating: int) -> float:
    return max(MIN_STABILITY, w[rating - 1])


def _initial_difficulty(w: tuple[float, ...], rating: int) -> float:
    return _clamp_difficulty(w[4] - math.exp(w[5] * (rating - 1)) + 1)


def _next_difficulty(w: tuple[float, ...], difficulty: float, rating: int) -> float:
    delta = -w[6] * (rating - 3)
    damped = difficulty + delta * (10 - difficulty) / 9  # harder cards move less
    # Mean-revert toward the difficulty an "Easy" first answer would have given,
    # so a card can never get permanently stuck at maximum difficulty.
    reverted = w[7] * _initial_difficulty(w, RATING_EASY) + (1 - w[7]) * damped
    return _clamp_difficulty(reverted)


def _stability_after_recall(
    w: tuple[float, ...], stability: float, difficulty: float, r: float, rating: int
) -> float:
    hard_penalty = w[15] if rating == RATING_HARD else 1.0
    easy_bonus = w[16] if rating == RATING_EASY else 1.0
    growth = (
        math.exp(w[8])
        * (11 - difficulty)
        * (stability ** -w[9])
        * (math.exp(w[10] * (1 - r)) - 1)
        * hard_penalty
        * easy_bonus
    )
    return stability * (1 + growth)


def _stability_after_lapse(
    w: tuple[float, ...], stability: float, difficulty: float, r: float
) -> float:
    lapsed = (
        w[11]
        * (difficulty ** -w[12])
        * (((stability + 1) ** w[13]) - 1)
        * math.exp(w[14] * (1 - r))
    )
    # Forgetting must never make a card more stable than it already was.
    return min(lapsed, stability)


def _stability_same_day(w: tuple[float, ...], stability: float, rating: int) -> float:
    return stability * math.exp(w[17] * (rating - 3 + w[18]))


def interval_days(stability: float, desired_retention: float = DEFAULT_RETENTION) -> int:
    """Solve the forgetting curve for the day count where R == desired_retention."""
    retention = min(0.99, max(0.7, desired_retention))
    raw = (stability / FACTOR) * (retention ** (1 / DECAY) - 1)
    return max(1, min(MAX_INTERVAL_DAYS, round(raw)))


def schedule(
    stability: Optional[float],
    difficulty: Optional[float],
    elapsed_days: float,
    quality: int,
    desired_retention: float = DEFAULT_RETENTION,
    w: tuple[float, ...] = DEFAULT_W,
) -> tuple[float, float, int]:
    """Advance a card's memory state by one review.

    Returns (new_stability, new_difficulty, next_interval_days).
    Pass stability=None/difficulty=None for a card that has never been reviewed.
    """
    rating = QUALITY_TO_RATING.get(quality, RATING_GOOD)

    if stability is None or difficulty is None:
        new_stability = _initial_stability(w, rating)
        new_difficulty = _initial_difficulty(w, rating)
    else:
        r = retrievability(elapsed_days, stability)
        new_difficulty = _next_difficulty(w, difficulty, rating)
        if elapsed_days < 1:
            new_stability = _stability_same_day(w, stability, rating)
        elif rating == RATING_AGAIN:
            new_stability = _stability_after_lapse(w, stability, difficulty, r)
        else:
            new_stability = _stability_after_recall(w, stability, difficulty, r, rating)

    new_stability = max(MIN_STABILITY, new_stability)
    return new_stability, new_difficulty, interval_days(new_stability, desired_retention)


def seed_from_sm2(
    ease_factor: Optional[float], interval: Optional[int], repetitions: Optional[int]
) -> tuple[Optional[float], Optional[float]]:
    """Derive starting FSRS state from a card's legacy SM-2 values.

    A never-reviewed card gets no state — it initialises properly on first review.
    Otherwise the current interval is a decent proxy for stability, and ease_factor
    maps inversely onto difficulty (EF 2.5 was the easy default, 1.3 the floor).
    """
    if not repetitions:
        return None, None
    stability = max(MIN_STABILITY, float(interval or 1))
    ef = min(2.5, max(1.3, float(ease_factor or 2.5)))
    difficulty = 10 - (ef - 1.3) * (7 / 1.2)  # EF 2.5 -> D 3, EF 1.3 -> D 10
    return stability, _clamp_difficulty(difficulty)
