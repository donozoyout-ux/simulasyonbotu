from decimal import Decimal


def ema(values: list[Decimal], period: int) -> list[Decimal]:
    if period <= 0 or len(values) < period:
        return []
    seed = sum(values[:period]) / Decimal(period)
    multiplier = Decimal(2) / Decimal(period + 1)
    result = [seed]
    for value in values[period:]:
        result.append((value - result[-1]) * multiplier + result[-1])
    return result


def sma(values: list[Decimal], period: int) -> list[Decimal]:
    if period <= 0 or len(values) < period:
        return []
    return [sum(values[i-period+1:i+1]) / Decimal(period) for i in range(period - 1, len(values))]


def rsi(values: list[Decimal], period: int = 14) -> list[Decimal]:
    if len(values) <= period:
        return []
    changes = [values[i] - values[i-1] for i in range(1, len(values))]
    gains = [max(change, Decimal(0)) for change in changes]
    losses = [max(-change, Decimal(0)) for change in changes]
    avg_gain = sum(gains[:period]) / Decimal(period)
    avg_loss = sum(losses[:period]) / Decimal(period)
    result = [Decimal(100) if avg_loss == 0 else Decimal(100) - Decimal(100) / (Decimal(1) + avg_gain / avg_loss)]
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / Decimal(period)
        avg_loss = (avg_loss * (period - 1) + loss) / Decimal(period)
        result.append(Decimal(100) if avg_loss == 0 else Decimal(100) - Decimal(100) / (Decimal(1) + avg_gain / avg_loss))
    return result


def macd(values: list[Decimal], fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, Decimal] | None:
    fast_line, slow_line = ema(values, fast), ema(values, slow)
    if not fast_line or not slow_line:
        return None
    aligned_fast = fast_line[-len(slow_line):]
    line = [a - b for a, b in zip(aligned_fast, slow_line)]
    signal_line = ema(line, signal)
    if not signal_line:
        return None
    current_signal = signal_line[-1]
    return {"macd": line[-1], "signal": current_signal, "histogram": line[-1] - current_signal}


def true_ranges(highs: list[Decimal], lows: list[Decimal], closes: list[Decimal]) -> list[Decimal]:
    return [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])) for i in range(1, len(closes))]


def atr(highs: list[Decimal], lows: list[Decimal], closes: list[Decimal], period: int = 14) -> Decimal | None:
    ranges = true_ranges(highs, lows, closes)
    if len(ranges) < period:
        return None
    value = sum(ranges[:period]) / Decimal(period)
    for current in ranges[period:]:
        value = (value * (period - 1) + current) / Decimal(period)
    return value


def bollinger(values: list[Decimal], period: int = 20, deviations: Decimal = Decimal("2")) -> dict[str, Decimal] | None:
    window = values[-period:]
    if len(window) < period:
        return None
    middle = sum(window) / Decimal(period)
    variance = sum((value-middle) ** 2 for value in window) / Decimal(period)
    std = variance.sqrt()
    return {"upper": middle + deviations*std, "middle": middle, "lower": middle - deviations*std}


def rate_of_change(values: list[Decimal], period: int = 10) -> Decimal | None:
    if len(values) <= period or values[-period-1] == 0:
        return None
    return (values[-1] / values[-period-1] - 1) * 100
