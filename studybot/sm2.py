LEECH_THRESHOLD = 4  # consecutive "Again" answers before a card is flagged as a leech


def sm2(ease_factor: float, interval_days: int, repetitions: int, quality: int) -> tuple[float, int, int]:
    """SM-2 algorithm. quality: 1=Again, 3=Hard, 4=Good, 5=Easy.

    Returns (new_ease_factor, new_interval_days, new_repetitions).
    """
    new_ef = max(1.3, ease_factor + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    if quality < 3:
        return new_ef, 1, 0
    if repetitions == 0:
        new_interval = 1
    elif repetitions == 1:
        new_interval = 6
    else:
        new_interval = round(interval_days * ease_factor)
    return new_ef, max(1, new_interval), repetitions + 1
