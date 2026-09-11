from decimal import Decimal


def decide(score: int, entry_score: int, setup_detected: bool, trend_1d: str, rr: Decimal, min_rr: Decimal = Decimal("1.5"),
           data_valid: bool = True, analysis_complete: bool = True, session_valid: bool = True, source_allowed: bool = True,
           require_bullish_trend: bool = True, reject_bearish_trend: bool = False) -> tuple[str, str]:
    reasons = []
    if not data_valid: reasons.append("data validation başarısız")
    if not analysis_complete: reasons.append("analiz tamamlanamadı")
    if not session_valid: reasons.append("BIST seansı kapalı")
    if not source_allowed: reasons.append("mock kaynak live modda yasak")
    if require_bullish_trend and trend_1d != "bullish": reasons.append("1D trend bullish değil")
    elif reject_bearish_trend and trend_1d == "bearish": reasons.append("1D bearish context girişe uygun değil")
    if not setup_detected: reasons.append("giriş tetikleyicisi yok")
    if score < entry_score: reasons.append(f"skor {score} < {entry_score}")
    if rr < min_rr: reasons.append(f"RR {rr:.2f} < {min_rr}")
    if reasons:
        return "NO_TRADE", "; ".join(reasons)
    return "POSSIBLE_ENTRY", "Yüksek zaman dilimi uyumu + teyitli setup + yeterli risk/getiri"
