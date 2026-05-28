import math


def log2_score(g_value: int) -> float:
    return math.log2(g_value)


def mean_score(g_values: list[int]) -> float:
    if not g_values:
        return 0.0
    return sum(log2_score(g) for g in g_values) / len(g_values)
