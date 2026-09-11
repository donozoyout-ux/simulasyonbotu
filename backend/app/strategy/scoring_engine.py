DEFAULT_WEIGHTS = {"trend": 20, "structure": 15, "momentum": 15, "volume": 15, "levels": 15, "setup": 10, "risk_reward": 10}


def calculate_score(components: dict[str, float], weights: dict[str, int] | None = None) -> tuple[int, dict[str, int]]:
    weights = weights or DEFAULT_WEIGHTS
    if sum(weights.values()) != 100:
        raise ValueError("Skor ağırlıkları toplamı 100 olmalı")
    breakdown = {key: round(max(0, min(1, components.get(key, 0))) * weight) for key, weight in weights.items()}
    return sum(breakdown.values()), breakdown

