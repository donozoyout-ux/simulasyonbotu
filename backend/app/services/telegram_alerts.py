from __future__ import annotations

from html import escape

from sqlalchemy import desc, select

from app.models import DecisionLog, ScanRun
from app.services.system_health import scan_data_status
from app.services.telegram import TelegramNotifier


class TelegramDataHealthAlerter:
    def __init__(self, db, config, notifier=None):
        self.db, self.config = db, config
        self.notifier = notifier or TelegramNotifier(config)

    def _send_once(self, key: str, text: str, run_id: str | None):
        if self.db.scalar(select(DecisionLog.id).where(
            DecisionLog.category == "TELEGRAM", DecisionLog.reason == key).limit(1)):
            return {"status": "DEDUPED"}
        result = self.notifier.send(text)
        self.db.add(DecisionLog(category="TELEGRAM", decision="SENT" if result.get("status") == "SENT" else "FAILED",
            reason=key, details=result, run_id=run_id))
        self.db.commit()
        return result

    @staticmethod
    def _top_errors(run):
        lines = []
        for item in (run.errors or [])[:5]:
            symbol = escape(str(item.get("symbol", "UNKNOWN")))
            kind = escape(str(item.get("type") or item.get("error") or "PROVIDER_ERROR"))
            lines.append(f"{symbol} • {kind}")
        return "\n".join(lines) or "Not available"

    def notify_scan(self, run: ScanRun):
        if not (self.config.telegram_data_health_alerts and self.notifier.configured):
            return {"status": "DISABLED_OR_UNCONFIGURED"}
        if run.analysis_mode != "LIVE":
            return {"status": "OFF_HOURS_IGNORED"}
        previous = self.db.scalar(select(ScanRun).where(
            ScanRun.run_id == run.run_id, ScanRun.analysis_mode == "LIVE", ScanRun.id < run.id,
            ScanRun.completed_at.is_not(None)).order_by(desc(ScanRun.id)).limit(1))
        current_status = scan_data_status(run); previous_status = scan_data_status(previous)
        if current_status == "DATA_ERROR":
            return self._send_once(f"DATA_HEALTH:{run.id}", "🚨 <b>DATA ERROR</b>\n\n"
                f"Mode: LIVE\nProvider: {escape(run.provider)}\nScan: {run.total_symbols}\n"
                f"Valid: {run.valid_symbols}\nFailed: {run.failed_symbols}\n\nTop errors:\n{self._top_errors(run)}\n\n"
                "Paper entries güvenlik nedeniyle etkilenebilir.", run.run_id)
        if previous_status == "DATA_ERROR":
            return self._send_once(f"DATA_RECOVERY:{run.id}", "✅ <b>DATA RECOVERED</b>\n\n"
                f"Provider: {escape(run.provider)}\nValid: {run.valid_symbols}/{run.total_symbols}\nScanner devam ediyor.", run.run_id)
        if current_status == "PARTIAL" and previous_status == "OK":
            return self._send_once(f"DATA_DEGRADED:{run.id}", "⚠️ <b>DATA DEGRADED</b>\n\n"
                f"{run.valid_symbols}/{run.total_symbols} valid\n{run.failed_symbols} failed", run.run_id)
        return {"status": "NO_TRANSITION"}
