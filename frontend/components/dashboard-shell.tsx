"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  BarChart3,
  Bot,
  BriefcaseBusiness,
  CandlestickChart,
  CircleDollarSign,
  ListFilter,
  Pause,
  Play,
  RefreshCw,
  Settings,
  ShieldCheck,
  Target,
  WalletCards,
} from "lucide-react";
import { api } from "@/services/api";
import type {
  Analysis,
  Candle,
  DataHealth,
  Decision,
  ForwardStatus,
  Portfolio,
  Position,
  Snapshot,
  StrategyHealth,
  Trade,
  WatchItem,
} from "@/types";
import { PriceChart } from "./price-chart";

type View =
  | "Genel Bakış"
  | "Takip Listesi"
  | "Grafik Analizi"
  | "Pozisyonlar"
  | "İşlemler"
  | "Bot Aktivitesi"
  | "Ayarlar";
const nav: [View, React.ElementType][] = [
  ["Genel Bakış", BarChart3],
  ["Takip Listesi", ListFilter],
  ["Grafik Analizi", CandlestickChart],
  ["Pozisyonlar", BriefcaseBusiness],
  ["İşlemler", CircleDollarSign],
  ["Bot Aktivitesi", Activity],
  ["Ayarlar", Settings],
];
const seedPortfolio: Portfolio = {
  initial_balance: 5000,
  cash_balance: 5000,
  invested_value: 0,
  portfolio_value: 5000,
  realized_pnl: 0,
  unrealized_pnl: 0,
  total_pnl: 0,
  total_return_pct: 0,
  open_positions: 0,
};
const initialHealth: DataHealth = {
  provider: "yahoo",
  mode: "LIVE",
  status: "NO_SCAN",
  valid_symbols: 0,
  failed_symbols: 0,
  stale_symbols: 0,
  errors: [],
};
const initialStrategyHealth: StrategyHealth = { status: "NO_RESEARCH_REPORT" };
const EMPTY_SWINGS: Array<{ timestamp: string; price: number; type: string }> =
  [];

const money = (v: number) =>
  new Intl.NumberFormat("tr-TR", {
    style: "currency",
    currency: "TRY",
    minimumFractionDigits: 2,
  }).format(v);
const pct = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
const fmtDate = (v: string) =>
  new Intl.DateTimeFormat("tr-TR", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(v));

export function DashboardShell() {
  const [view, setView] = useState<View>("Genel Bakış"),
    [portfolio, setPortfolio] = useState(seedPortfolio),
    [forward, setForward] = useState<ForwardStatus>(),
    [analyses, setAnalyses] = useState<Analysis[]>([]),
    [watch, setWatch] = useState<WatchItem[]>([]),
    [positions, setPositions] = useState<Position[]>([]),
    [trades, setTrades] = useState<Trade[]>([]),
    [decisions, setDecisions] = useState<Decision[]>([]),
    [history, setHistory] = useState<Snapshot[]>([]),
    [candles, setCandles] = useState<Candle[]>([]),
    [selected, setSelected] = useState(""),
    [timeframe, setTimeframe] = useState("15m"),
    [mode, setMode] = useState("DATA ERROR"),
    [health, setHealth] = useState<DataHealth>(initialHealth),
    [strategyHealth, setStrategyHealth] = useState<StrategyHealth>(
      initialStrategyHealth,
    ),
    [loading, setLoading] = useState(false),
    [message, setMessage] = useState("Yerel API bekleniyor"),
    [settings, setSettings] = useState<Record<string, number>>({
      scan_interval_minutes: 15,
      watchlist_score: 70,
      entry_score: 82,
      risk_per_trade_pct: 0.005,
      max_position_size_pct: 0.2,
      max_open_positions: 4,
      min_rr: 1.5,
      commission_rate: 0.001,
      slippage_rate: 0.0005,
    });
  const load = useCallback(async () => {
    try {
      const [p, a, w, pos, t, d, h, s, dh, sh, fw] = await Promise.all([
        api.portfolio(),
        api.analyses(),
        api.watchlist(),
        api.positions(),
        api.trades(),
        api.decisions(),
        api.history(),
        api.settings(),
        api.dataHealth(),
        api.strategyHealth(),
        api.forwardStatus(),
      ]);
      setPortfolio(p);
      setForward(fw);
      setAnalyses(a);
      setWatch(w);
      setPositions(pos);
      setTrades(t);
      setDecisions(d);
      setHistory(h);
      setSettings(s);
      setHealth(dh);
      setStrategyHealth(sh);
      setSelected((current) =>
        a.some((x) => x.symbol === current) ? current : a[0]?.symbol || "",
      );
      setMode(
        dh.status === "DATA_ERROR"
          ? "DATA ERROR"
          : dh.mode === "MOCK"
            ? "MOCK DATA"
            : "LIVE PAPER",
      );
      setMessage("Sistem hazır");
    } catch {
      setAnalyses([]);
      setHealth({ ...initialHealth, status: "DATA_ERROR" });
      setMode("DATA ERROR");
      setMessage("Backend veya veri servisi çevrimdışı");
    }
  }, []);
  useEffect(() => {
    void Promise.resolve().then(load);
  }, [load]);
  useEffect(() => {
    const timer = window.setInterval(() => void load(), 30_000);
    return () => window.clearInterval(timer);
  }, [load]);
  const chosen = analyses.find((x) => x.symbol === selected) || analyses[0];
  useEffect(() => {
    if (!selected) return;
    api
      .candles(selected, timeframe)
      .then(setCandles)
      .catch(() => setCandles([]));
  }, [selected, timeframe]);
  const scan = async () => {
    setLoading(true);
    setMessage("BIST taranıyor…");
    try {
      await api.scan();
      await load();
      setMessage("Tarama tamamlandı");
    } catch {
      setMessage("Tarama başlatılamadı — backend durumunu kontrol edin");
    } finally {
      setLoading(false);
    }
  };
  const winRate = trades.length
    ? (trades.filter((x) => x.realized_pnl > 0).length / trades.length) * 100
    : 0;
  const profitFactor = useMemo(() => {
    const wins = trades
        .filter((x) => x.realized_pnl > 0)
        .reduce((s, x) => s + x.realized_pnl, 0),
      loss = Math.abs(
        trades
          .filter((x) => x.realized_pnl < 0)
          .reduce((s, x) => s + x.realized_pnl, 0),
      );
    return loss ? wins / loss : 0;
  }, [trades]);
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">
            <CandlestickChart size={20} />
          </span>
          <div>
            <b>BIST PILOT</b>
            <small>PAPER TRADING</small>
          </div>
        </div>
        <nav>
          {nav.map(([label, Icon]) => (
            <button
              key={label}
              className={view === label ? "active" : ""}
              onClick={() => setView(label)}
            >
              <Icon size={18} />
              <span>{label}</span>
            </button>
          ))}
        </nav>
        <div className="risk-card">
          <ShieldCheck size={18} />
          <div>
            <b>Güvenli Mod</b>
            <span>Gerçek emirler devre dışı</span>
          </div>
        </div>
        <div className="capital">
          <span>Sanal sermaye</span>
          <b>{money(portfolio.initial_balance)}</b>
        </div>
      </aside>
      <main>
        <header>
          <div>
            <p className="eyebrow">LIVE PAPER • PORTFÖY KONTROL MERKEZİ</p>
            <h1>{view}</h1>
          </div>
          <div className="header-actions">
            <span
              className={`source ${forward?.market_status === "MARKET OPEN" ? "" : "closed"}`}
            >
              <i />
              {forward?.market_status || "MARKET CLOSED"}
            </span>
            <span
              className={`source ${mode.includes("MOCK") ? "mock" : mode.includes("ERROR") ? "error" : "live"}`}
            >
              <i />
              {forward?.provider || mode}
            </span>
            <button
              className="pause-control"
              onClick={async () => {
                if (!forward) return;
                await api.controlPaper(forward.status === "RUNNING");
                await load();
              }}
            >
              {forward?.status === "PAUSED" ? (
                <Play size={15} />
              ) : (
                <Pause size={15} />
              )}{" "}
              {forward?.status || "STOPPED"}
            </button>
            <button
              className="scan"
              onClick={scan}
              disabled={loading || forward?.market_status !== "MARKET OPEN"}
            >
              <RefreshCw size={16} className={loading ? "spin" : ""} />
              {loading ? "Taranıyor" : "Taramayı çalıştır"}
            </button>
          </div>
        </header>
        <div className="statusline">
          <span>{message}</span>
          <span>
            Son kontrol:{" "}
            {new Date().toLocaleTimeString("tr-TR", {
              hour: "2-digit",
              minute: "2-digit",
            })}
          </span>
        </div>
        {view === "Genel Bakış" && (
          <Overview
            portfolio={portfolio}
            forward={forward}
            analyses={analyses}
            positions={positions}
            watch={watch}
            history={history}
            health={health}
            strategyHealth={strategyHealth}
            winRate={winRate}
            profitFactor={profitFactor}
            onSelect={(s) => {
              setSelected(s);
              setView("Grafik Analizi");
            }}
          />
        )}
        {view === "Takip Listesi" && (
          <Watchlist
            rows={watch}
            analyses={analyses}
            onSelect={(s) => {
              setSelected(s);
              setView("Grafik Analizi");
            }}
          />
        )}
        {view === "Grafik Analizi" && (
          <AnalysisViewGroq
            analyses={analyses}
            chosen={chosen}
            selected={selected}
            setSelected={setSelected}
            candles={candles}
            timeframe={timeframe}
            setTimeframe={setTimeframe}
            trades={trades}
          />
        )}
        {view === "Pozisyonlar" && <Positions rows={positions} />}{" "}
        {view === "İşlemler" && <Trades rows={trades} />}{" "}
        {view === "Bot Aktivitesi" && <ActivityFeed rows={decisions} />}{" "}
        {view === "Ayarlar" && (
          <SettingsView
            values={settings}
            setValues={setSettings}
            onSave={async () => {
              try {
                await api.saveSettings(settings);
                setMessage("Ayarlar kaydedildi");
              } catch {
                setMessage(
                  "Aktif run sırasında kritik strateji ayarları değiştirilemez",
                );
              }
            }}
            onTelegramTest={async () => {
              try {
                await api.telegramTest();
                setMessage("Telegram test mesajı gönderildi");
              } catch {
                setMessage("Telegram test başarısız — Render env ayarlarını kontrol et");
              }
            }}
            onReset={async () => {
              if (
                window.prompt("Onay için RESET PAPER PORTFOLIO yazın") !==
                "RESET PAPER PORTFOLIO"
              )
                return;
              try {
                await api.resetPaper();
                await load();
                setMessage(
                  "Eski run arşivlendi; yeni 5.000 TL forward test başlatıldı",
                );
              } catch {
                setMessage("Forward test sıfırlanamadı");
              }
            }}
          />
        )}
      </main>
    </div>
  );
}

function Overview({
  portfolio,
  forward,
  analyses,
  positions,
  watch,
  history,
  health,
  strategyHealth,
  winRate,
  profitFactor,
  onSelect,
}: {
  portfolio: Portfolio;
  forward?: ForwardStatus;
  analyses: Analysis[];
  positions: Position[];
  watch: WatchItem[];
  history: Snapshot[];
  health: DataHealth;
  strategyHealth: StrategyHealth;
  winRate: number;
  profitFactor: number;
  onSelect: (s: string) => void;
}) {
  let peak = portfolio.initial_balance,
    maxDrawdown = 0;
  history.forEach((point) => {
    peak = Math.max(peak, point.portfolio_value);
    maxDrawdown = Math.max(
      maxDrawdown,
      peak ? ((peak - point.portfolio_value) / peak) * 100 : 0,
    );
  });
  const perf = forward?.performance;
  const cards = [
    {
      label: "Portföy Değeri",
      value: money(portfolio.portfolio_value),
      meta: `Başlangıç ${money(portfolio.initial_balance)}`,
      icon: WalletCards,
    },
    {
      label: "Kullanılabilir Nakit",
      value: money(portfolio.cash_balance),
      meta: `%${((portfolio.cash_balance / (portfolio.portfolio_value || 1)) * 100).toFixed(0)} nakit oranı`,
      icon: CircleDollarSign,
    },
    {
      label: "Hisselerde (Yatırım)",
      value: money(portfolio.invested_value),
      meta: `${portfolio.open_positions} aktif pozisyon`,
      icon: BriefcaseBusiness,
    },
    {
      label: "Bugünkü K/Z",
      value: money(forward?.daily_pnl ?? 0),
      meta: "Günlük net değişim",
      icon: Activity,
      tone: (forward?.daily_pnl ?? 0) >= 0 ? "green" : "red",
    },
    {
      label: "Toplam K/Z",
      value: money(portfolio.total_pnl),
      meta: pct(portfolio.total_return_pct),
      icon: Activity,
      tone: portfolio.total_pnl >= 0 ? "green" : "red",
    },
    {
      label: "Açık Pozisyon",
      value: `${portfolio.open_positions} / 4`,
      meta: "Maksimum 4 sınır",
      icon: Target,
    },
    {
      label: "Bugünkü İşlem",
      value: String(forward?.trades_today ?? 0),
      meta: `Toplam ${forward?.trades ?? 0} işlem`,
      icon: CircleDollarSign,
    },
    {
      label: "Takip Listesi",
      value: String(forward?.watchlist_count ?? watch.length),
      meta: "Aday sembol",
      icon: ListFilter,
    },
  ];
  return (
    <>
      <section className="metric-grid">
        {cards.map((c) => (
          <article className="metric" key={c.label}>
            <div>
              <span>{c.label}</span>
              <b className={c.tone}>{c.value}</b>
              <small>{c.meta}</small>
            </div>
            <c.icon size={20} />
          </article>
        ))}
      </section>

      <section className="dashboard-grid">
        <article className="panel forward-status-panel">
          <PanelTitle
            title="Forward Test Durumu"
            sub={`Run ID: ${forward?.run_id || "LIVE-FORWARD"} • ${forward?.strategy_version || "V3_FROZEN_1"}`}
          />
          <div className="strategy-grid">
            <div>
              <span>Durum</span>
              <b className={forward?.status === "RUNNING" ? "green" : "warn"}>
                {forward?.status || "RUNNING"}
              </b>
              <small>{forward?.market_status || "MARKET CLOSED"}</small>
            </div>
            <div>
              <span>Başlangıç</span>
              <b>
                {forward?.forward_test_started_at
                  ? fmtDate(forward.forward_test_started_at)
                  : "Bugün"}
              </b>
              <small>{forward?.running_days ?? 0} gündür devrede</small>
            </div>
            <div>
              <span>Tamamlanan Tarama</span>
              <b>{forward?.completed_scans ?? 0}</b>
              <small>{forward?.signals ?? 0} sinyal üretildi</small>
            </div>
            <div>
              <span>Sağlayıcı</span>
              <b>{forward?.provider?.toUpperCase() || "YAHOO"}</b>
              <small>Mock emri yasak</small>
            </div>
            <div>
              <span>Bot Döngüsü</span>
              <b>{forward?.worker?.current_cadence_minutes ?? (forward?.market_status === "MARKET OPEN" ? 5 : 15)} dk</b>
              <small>Piyasa açık 5 dk • kapanış sonrası 15 dk</small>
            </div>
          </div>
          {forward?.benchmark && (
            <div
              className="benchmark-bar"
              style={{
                marginTop: "0.8rem",
                padding: "0.6rem 0.8rem",
                background: "var(--surface-subtle,#181b20)",
                borderRadius: "6px",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                fontSize: "0.82rem",
              }}
            >
              <span>
                <b>Benchmark:</b> Bot {pct(portfolio.total_return_pct)} vs XU100{" "}
                {forward.benchmark.return_pct != null
                  ? pct(forward.benchmark.return_pct)
                  : "İlk XU100 verisi alınıyor"}
                {forward.benchmark.updated_at
                  ? " • " + fmtDate(forward.benchmark.updated_at)
                  : ""}
              </span>
              {perf?.sample_warning && (
                <span
                  style={{
                    color: "#f59e0b",
                    fontWeight: "bold",
                    fontSize: "0.75rem",
                    background: "rgba(245,158,11,0.15)",
                    padding: "2px 8px",
                    borderRadius: "4px",
                  }}
                >
                  {perf.sample_warning}
                </span>
              )}
            </div>
          )}
        </article>

        <article className="panel scanner-status-panel">
          <PanelTitle
            title="5M İşlem Kontrolü / 15M Strateji"
            sub="Pozisyonlar 5 dakikada bir; yeni strateji sinyali kapanmış 15M mumda"
          />
          <div className="strategy-grid">
            <div>
              <span>Son Tarama</span>
              <b>
                {forward?.scanner.last_scan
                  ? fmtDate(forward.scanner.last_scan)
                  : "—"}
              </b>
              <small>
                {forward?.scanner.last_processed_candle
                  ? `Mum: ${fmtDate(forward.scanner.last_processed_candle)}`
                  : "Bekleniyor"}
              </small>
            </div>
            <div>
              <span>İşlem Kontrolü</span>
              <b>{forward?.worker?.position_check_minutes ?? 5} dk</b>
              <small>
                Stop/hedef kontrolü • Strateji mumu {forward?.worker?.strategy_candle_minutes ?? 15} dk
              </small>
            </div>
            <div>
              <span>Taranan Sembol</span>
              <b>{forward?.scanner.symbols_scanned ?? 0}</b>
              <small>
                {forward?.scanner.valid ?? 0} geçerli •{" "}
                {forward?.scanner.failed ?? 0} hatalı
              </small>
            </div>
            <div>
              <span>Sinyal & Emir</span>
              <b>
                {forward?.scanner.signals ?? 0} / {forward?.scanner.orders ?? 0}
              </b>
              <small>{forward?.scanner.watchlist ?? 0} watchlist</small>
            </div>
          </div>
        </article>
      </section>

      <section className="dashboard-grid">
        <article className="panel equity">
          <PanelTitle
            title="Canlı Portföy Eğrisi (LIVE PAPER)"
            sub="Snapshot bazlı sermaye gelişimi"
          />
          <EquityChart history={history} base={portfolio.initial_balance} />
          <div className="micro-stats">
            <span>
              <b>{pct(winRate)}</b>Win rate
            </span>
            <span>
              <b>{profitFactor.toFixed(2)}</b>Profit factor
            </span>
            <span>
              <b>{maxDrawdown.toFixed(2)}%</b>Max drawdown
            </span>
            <span>
              <b>
                {perf?.average_holding_hours
                  ? `${perf.average_holding_hours.toFixed(1)} sa`
                  : "—"}
              </b>
              Ort. Pozisyon
            </span>
            <span>
              <b>
                {perf?.average_exposure_pct
                  ? `%${perf.average_exposure_pct.toFixed(0)}`
                  : "—"}
              </b>
              Ort. Yatırım
            </span>
          </div>
        </article>

        <article className="panel candidates">
          <PanelTitle title="Öne Çıkan Adaylar" sub="Skora göre sıralı" />
          {analyses.length ? (
            <div className="candidate-list">
              {analyses.slice(0, 5).map((a, i) => (
                <button key={a.symbol} onClick={() => onSelect(a.symbol)}>
                  <span className="rank">{String(i + 1).padStart(2, "0")}</span>
                  <div>
                    <b>{a.symbol}</b>
                    <small>{a.setup.replaceAll("_", " ")}</small>
                  </div>
                  <Score value={a.score} />
                  <span className="price">{money(a.price)}</span>
                </button>
              ))}
            </div>
          ) : (
            <Empty
              icon={ListFilter}
              title="Analiz sonucu yok"
              text="Canlı tarama tamamlandığında adaylar burada görünür."
            />
          )}
        </article>
      </section>

      <section className="lower-grid">
        <article className="panel">
          <PanelTitle
            title="Açık Pozisyonlar"
            sub={`${positions.length} aktif işlem`}
          />
          {positions.length ? (
            <MiniPosition rows={positions} />
          ) : (
            <Empty
              icon={Target}
              title="Henüz açık pozisyon yok"
              text="Teyitli giriş oluştuğunda risk motoru burada sanal pozisyon açar."
            />
          )}
        </article>
        <DataHealthPanel health={health} />
      </section>

      <section className="panel funnel-panel">
        <PanelTitle
          title="Signal Funnel"
          sub="Son taramanın karar daralma zinciri"
        />
        <div className="funnel-row">
          {Object.entries(health.funnel || {}).map(([key, value]) => (
            <div key={key}>
              <b>{value}</b>
              <span>{key.replaceAll("_", " ")}</span>
            </div>
          ))}
        </div>
      </section>
      <StrategyHealthPanel health={strategyHealth} />
    </>
  );
}

function StrategyHealthPanel({ health }: { health: StrategyHealth }) {
  if (health.status !== "READY")
    return (
      <section className="panel strategy-health">
        <PanelTitle
          title="Strategy Health"
          sub="Canonical replay raporu bekleniyor"
        />
        <Empty
          icon={BarChart3}
          title="Araştırma sonucu yok"
          text="Replay tamamlandığında skor ve setup sağlığı burada görünür."
        />
      </section>
    );
  const replay = health.last_replay,
    v4 = health.v4,
    exit = v4?.exit_diagnostics;
  const period = (value?: { start?: string; end?: string }) =>
    value?.start && value.end
      ? `${fmtDate(value.start)} — ${fmtDate(value.end)}`
      : "Veri bekleniyor";
  return (
    <section className="panel strategy-health">
      <PanelTitle
        title="Strategy Health"
        sub={`${health.dataset?.trading_days} işlem günü • ${health.dataset?.symbols.length} sembol • ${health.dataset?.source}`}
      />
      <div className="strategy-grid">
        <div>
          <span>Skor ortalaması</span>
          <b>
            {health.score_before?.mean.toFixed(1)} →{" "}
            {health.score_after?.mean.toFixed(1)}
          </b>
          <small>Maksimum {health.score_after?.max}</small>
        </div>
        <div>
          <span>Setup sayıları</span>
          <b>
            {Object.values(health.setup_counts || {}).reduce(
              (sum, value) => sum + value,
              0,
            )}
          </b>
          <small>
            {Object.entries(health.setup_counts || {})
              .map(([key, value]) => `${key.replaceAll("_", " ")} ${value}`)
              .join(" • ")}
          </small>
        </div>
        <div>
          <span>Portfolio replay</span>
          <b className={(replay?.net_pnl || 0) >= 0 ? "green" : "red"}>
            {replay ? money(replay.ending_equity) : "—"}
          </b>
          <small>
            {replay
              ? `${pct(replay.return_pct)} • ${replay.trades} işlem`
              : "—"}
          </small>
        </div>
        <div>
          <span>En sık redler</span>
          <b>{health.top_rejections?.[0]?.[1] || 0}</b>
          <small>
            {health.top_rejections
              ?.slice(0, 3)
              .map(([key, value]) => `${key.replaceAll("_", " ")} ${value}`)
              .join(" • ")}
          </small>
        </div>
      </div>
      {v4 && (
        <>
          <div
            className={`research-state ${v4.status === "READY" ? "ready" : "waiting"}`}
          >
            <ShieldCheck size={17} />
            <div>
              <b>V4 OOS • {v4.status.replaceAll("_", " ")}</b>
              <span>{v4.sample_confidence?.replaceAll("_", " ")}</span>
            </div>
          </div>
          <div className="research-grid">
            <div>
              <span>Research dönemi</span>
              <b>{period(v4.research_period)}</b>
              <small>Mevcut canonical dataset</small>
            </div>
            <div>
              <span>OOS dönemi</span>
              <b>{period(v4.oos_period)}</b>
              <small>{v4.oos_trade_count} portfolio işlemi</small>
            </div>
            <div>
              <span>15M row ayrımı</span>
              <b>{v4.evaluation_rows.toLocaleString("tr-TR")}</b>
              <small>
                {v4.warmup_rows.toLocaleString("tr-TR")} warm-up row
              </small>
            </div>
            <div>
              <span>Config hash</span>
              <b className="hash">
                {v4.strategy_config_hash?.slice(0, 12) || "—"}
              </b>
              <small>V3 kuralları donduruldu</small>
            </div>
            <div>
              <span>Exit süresi</span>
              <b>{exit ? `${exit.median_holding_hours.toFixed(2)} sa` : "—"}</b>
              <small>
                {v4.exit_diagnostics_scope?.replaceAll("_", " ") || "OOS"}{" "}
                median
              </small>
            </div>
            <div>
              <span>Exit excursions</span>
              <b>
                {exit
                  ? `${exit.average_mfe.toFixed(2)} / ${exit.average_mae.toFixed(2)}`
                  : "—"}
              </b>
              <small>
                MFE / MAE • recovery{" "}
                {exit
                  ? `${exit.stopped_then_recovered_pct.entry_4h || 0}%`
                  : "—"}
              </small>
            </div>
          </div>
        </>
      )}
    </section>
  );
}

function DataHealthPanel({ health }: { health: DataHealth }) {
  return (
    <article className="panel">
      <PanelTitle
        title="Data Health"
        sub={`${health.provider} • ${health.mode}`}
      />
      <div className="health-grid">
        <span>
          <b>{health.valid_symbols}</b>Geçerli
        </span>
        <span>
          <b className={health.failed_symbols ? "red" : ""}>
            {health.failed_symbols}
          </b>
          Hatalı
        </span>
        <span>
          <b className={health.stale_symbols ? "red" : ""}>
            {health.stale_symbols}
          </b>
          Stale
        </span>
        <span>
          <b>
            {health.scanner_duration_ms
              ? `${health.scanner_duration_ms} ms`
              : "—"}
          </b>
          Süre
        </span>
      </div>
      <div
        className={`health-banner ${health.status === "OK" ? "ok" : "warn"}`}
      >
        <ShieldCheck size={18} />
        <span>
          <b>{health.status}</b>
          {health.scanner_last_run
            ? `Son tarama ${fmtDate(health.scanner_last_run)}`
            : "Henüz tarama yapılmadı"}
          {health.last_successful_fetch
            ? ` • Son veri ${fmtDate(health.last_successful_fetch)}`
            : ""}
        </span>
      </div>
      {health.errors?.length ? (
        <div className="health-errors">
          {health.errors.slice(0, 4).map((item) => (
            <p key={`${item.symbol}-${item.error}`}>
              <b>{item.symbol}</b> {item.error}
            </p>
          ))}
        </div>
      ) : null}
    </article>
  );
}

function Watchlist({
  rows,
  analyses,
  onSelect,
}: {
  rows: WatchItem[];
  analyses: Analysis[];
  onSelect: (s: string) => void;
}) {
  return (
    <article className="panel table-panel">
      <PanelTitle
        title="Otomatik Takip Listesi"
        sub={`${rows.length} aday • puana göre sıralı`}
      />
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Sembol</th>
              <th>Fiyat</th>
              <th>Skor</th>
              <th>Kalite</th>
              <th>Trend</th>
              <th>Yapı</th>
              <th>Setup</th>
              <th>Hacim</th>
              <th>Momentum</th>
              <th>Destek</th>
              <th>Direnç</th>
              <th>R/R</th>
              <th>Durum</th>
              <th>Güncelleme</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const a = analyses.find((x) => x.symbol === row.symbol);
              return (
                <tr key={row.symbol} onClick={() => onSelect(row.symbol)}>
                  <td>
                    <b>{row.symbol}</b>
                  </td>
                  <td>
                    {row.price != null
                      ? money(row.price)
                      : a
                        ? money(a.price)
                        : "—"}
                  </td>
                  <td>
                    <Score value={row.score} />
                  </td>
                  <td>{row.setup_quality?.toFixed(0) ?? "—"}</td>
                  <td>
                    <Tag text={row.trend || a?.trend || "—"} />
                  </td>
                  <td>{row.structure || a?.market_structure || "—"}</td>
                  <td>{row.setup}</td>
                  <td>{a?.details.volume?.rvol?.toFixed(2) || "—"}x</td>
                  <td>{a?.details.momentum?.label || "—"}</td>
                  <td>
                    {row.support != null
                      ? money(row.support)
                      : a?.details.levels
                        ? money(a.details.levels.support)
                        : "—"}
                  </td>
                  <td>
                    {row.resistance != null
                      ? money(row.resistance)
                      : a?.details.levels
                        ? money(a.details.levels.resistance)
                        : "—"}
                  </td>
                  <td>
                    {row.rr?.toFixed(2) ??
                      a?.details.risk_reward?.toFixed(2) ??
                      "—"}
                  </td>
                  <td>
                    <Tag text={row.status} />
                  </td>
                  <td>{fmtDate(row.last_analyzed_at)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </article>
  );
}

function AnalysisViewGroq({
  analyses,
  chosen,
  selected,
  setSelected,
  candles,
  timeframe,
  setTimeframe,
  trades,
}: {
  analyses: Analysis[];
  chosen?: Analysis;
  selected: string;
  setSelected: (s: string) => void;
  candles: Candle[];
  timeframe: string;
  setTimeframe: (v: string) => void;
  trades: Trade[];
}) {
  if (!chosen)
    return (
      <article className="panel">
        <Empty
          icon={CandlestickChart}
          title="Analiz snapshot’ı yok"
          text="Bir live tarama tamamlandıktan sonra backend seviyeleri ve mumlar burada gösterilir."
        />
      </article>
    );
  const lv = chosen.details.levels,
    setup = chosen.details.setup,
    context = chosen.details.analysis_context;
  return (
    <div className="analysis-layout">
      <article className="panel chart-panel">
        <div className="chart-head">
          <div>
            <select
              value={selected}
              onChange={(e) => setSelected(e.target.value)}
            >
              {analyses.map((a) => (
                <option key={a.symbol}>{a.symbol}</option>
              ))}
            </select>
            <span>{money(chosen.price)}</span>
          </div>
          <div className="timeframes">
            {["15m", "1h", "1d"].map((tf) => (
              <button
                key={tf}
                className={timeframe === tf ? "active" : ""}
                onClick={() => setTimeframe(tf)}
              >
                {tf.toUpperCase()}
              </button>
            ))}
          </div>
        </div>
        {candles.length ? (
          <PriceChart
            candles={candles}
            levels={
              lv?.support && lv?.resistance
                ? {
                    support: lv.support,
                    resistance: lv.resistance,
                    entry: setup?.entry_area,
                    stop: setup?.invalidation_level,
                    target: setup?.target,
                  }
                : undefined
            }
            swings={
              timeframe === "1h"
                ? chosen.details.structure?.swings || EMPTY_SWINGS
                : EMPTY_SWINGS
            }
            exits={trades
              .filter((item) => item.symbol === chosen.symbol)
              .map((item) => ({
                timestamp: item.exit_time,
                price: item.exit_price,
              }))}
          />
        ) : (
          <div className="chart-placeholder">
            <CandlestickChart />
            <b>{timeframe.toUpperCase()} kapalı mum verisi yok</b>
            <span>
              Frontend analiz üretmez; yalnızca backend snapshot’ını çizer.
            </span>
          </div>
        )}
        <div className="legend">
          <span className="support-dot">Destek zone</span>
          <span className="resistance-dot">Direnç zone</span>
          <span className="entry-dot">Giriş</span>
          <span className="stop-dot">Stop</span>
          <span className="target-dot">Hedef</span>
        </div>
      </article>
      <aside className="panel insight">
        <div className="score-hero">
          <Score value={chosen.score} />
          <div>
            <small>BOT KARARI</small>
            <b>{chosen.decision.replaceAll("_", " ")}</b>
          </div>
        </div>
        <dl>
          <Stat label="Data source" value={chosen.data_source} />
          <Stat
            label="Last closed candle"
            value={
              context?.entry_candle_time
                ? fmtDate(context.entry_candle_time)
                : "—"
            }
          />
          <Stat label="Data freshness" value={context?.freshness?.["15m"]} />
          <Stat
            label="1D Trend"
            value={chosen.details.timeframes?.["1d"]?.label}
          />
          <Stat label="1H Structure" value={chosen.market_structure} />
          <Stat label="15M Trigger" value={chosen.setup} />
          <Stat label="Momentum" value={chosen.details.momentum?.label} />
          <Stat
            label="RVOL"
            value={
              chosen.details.volume?.rvol
                ? `${chosen.details.volume.rvol.toFixed(2)}x`
                : "—"
            }
          />
          <Stat
            label="ATR"
            value={chosen.details.volatility?.atr?.toFixed(2)}
          />
          <Stat
            label="Risk / Getiri"
            value={chosen.details.risk_reward?.toFixed(2)}
          />
        </dl>
        <div className="levels">
          <div>
            <span>Destek</span>
            <b>{lv?.support ? money(lv.support) : "—"}</b>
          </div>
          <div>
            <span>Direnç</span>
            <b>{lv?.resistance ? money(lv.resistance) : "—"}</b>
          </div>
        </div>
        <div className="reason">
          <Bot size={18} />
          <p>{chosen.reason}</p>
        </div>
        <AISecondOpinionCard analysis={chosen} />
      </aside>
    </div>
  );
}

function AnalysisView({analyses,chosen,selected,setSelected,candles,timeframe,setTimeframe,trades}:{analyses:Analysis[];chosen?:Analysis;selected:string;setSelected:(s:string)=>void;candles:Candle[];timeframe:string;setTimeframe:(v:string)=>void;trades:Trade[]}){if(!chosen)return <article className="panel"><Empty icon={CandlestickChart} title="Analiz snapshot’ı yok" text="Bir live tarama tamamlandıktan sonra backend seviyeleri ve mumlar burada gösterilir."/></article>;const lv=chosen.details.levels,setup=chosen.details.setup,context=chosen.details.analysis_context;return <div className="analysis-layout"><article className="panel chart-panel"><div className="chart-head"><div><select value={selected} onChange={e=>setSelected(e.target.value)}>{analyses.map(a=><option key={a.symbol}>{a.symbol}</option>)}</select><span>{money(chosen.price)}</span></div><div className="timeframes">{["15m","1h","1d"].map(tf=><button key={tf} className={timeframe===tf?"active":""} onClick={()=>setTimeframe(tf)}>{tf.toUpperCase()}</button>)}</div></div>{candles.length?<PriceChart candles={candles} levels={lv?.support&&lv?.resistance?{support:lv.support,resistance:lv.resistance,entry:setup?.entry_area,stop:setup?.invalidation_level,target:setup?.target}:undefined} swings={timeframe==="1h"?(chosen.details.structure?.swings||EMPTY_SWINGS):EMPTY_SWINGS} exits={trades.filter(item=>item.symbol===chosen.symbol).map(item=>({timestamp:item.exit_time,price:item.exit_price}))}/>:<div className="chart-placeholder"><CandlestickChart/><b>{timeframe.toUpperCase()} kapalı mum verisi yok</b><span>Frontend analiz üretmez; yalnızca backend snapshot’ını çizer.</span></div>}<div className="legend"><span className="support-dot">Destek zone</span><span className="resistance-dot">Direnç zone</span><span className="entry-dot">Giriş</span><span className="stop-dot">Stop</span><span className="target-dot">Hedef</span></div></article><aside className="panel insight"><div className="score-hero"><Score value={chosen.score}/><div><small>BOT KARARI</small><b>{chosen.decision.replaceAll("_"," ")}</b></div></div><dl><Stat label="Data source" value={chosen.data_source}/><Stat label="Last closed candle" value={context?.entry_candle_time?fmtDate(context.entry_candle_time):"—"}/><Stat label="Data freshness" value={context?.freshness?.["15m"]}/><Stat label="1D Trend" value={chosen.details.timeframes?.["1d"]?.label}/><Stat label="1H Structure" value={chosen.market_structure}/><Stat label="15M Trigger" value={chosen.setup}/><Stat label="Momentum" value={chosen.details.momentum?.label}/><Stat label="RVOL" value={chosen.details.volume?.rvol?`${chosen.details.volume.rvol.toFixed(2)}x`:"—"}/><Stat label="ATR" value={chosen.details.volatility?.atr?.toFixed(2)}/><Stat label="Risk / Getiri" value={chosen.details.risk_reward?.toFixed(2)}/></dl><div className="levels"><div><span>Destek</span><b>{lv?.support?money(lv.support):"—"}</b></div><div><span>Direnç</span><b>{lv?.resistance?money(lv.resistance):"—"}</b></div></div><div className="reason"><Bot size={18}/><p>{chosen.reason}</p></div>{chosen.details.ai&&<div className="reason"><Bot size={18}/><div><b>AI • {chosen.details.ai.status==="OK"?`${chosen.details.ai.verdict} %${chosen.details.ai.confidence??0}`:chosen.details.ai.status}</b><p>{chosen.details.ai.summary||"AI analizi bu aday için henüz hazır değil."}</p>{chosen.details.ai.risks?.length?<small>Risk: {chosen.details.ai.risks.join(" • ")}</small>:null}</div></div>}</aside></div>}
function AISecondOpinionCard({ analysis }: { analysis: Analysis }) {
  const opinion = analysis.ai_result;
  const status = analysis.ai_status || opinion?.status || "AI_UNAVAILABLE";
  const labels: Record<string, string> = {
    DISABLED: "AI kapalı",
    API_KEY_MISSING: "API key eksik",
    RATE_LIMITED: "Rate limit",
    SKIPPED_LOW_SCORE: "Score AI eşiğinin altında",
    AUTH_ERROR: "Provider kimlik doğrulama hatası",
    MALFORMED_RESPONSE: "Provider yanıtı geçersiz",
    AI_UNAVAILABLE: "Provider hatası",
  };
  return (
    <section className={`ai-opinion ${status === "OK" ? "ready" : "waiting"}`}>
      <div className="ai-opinion-head">
        <div>
          <span>AI İKİNCİ GÖRÜŞ</span>
          <b>Groq / GPT-OSS 20B</b>
        </div>
        <em>EMİR YETKİSİ YOK</em>
      </div>
      {status === "OK" && opinion ? (
        <>
          <div className="ai-verdict">
            <span>
              Karar <b>{opinion.verdict}</b>
            </span>
            <span>
              Güven <b>{opinion.confidence}%</b>
            </span>
          </div>
          <p>{opinion.summary || "Özet sağlanmadı."}</p>
          <div className="ai-columns">
            <div>
              <span>Güçlü Noktalar</span>
              <ul>
                {(opinion.strengths || []).map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
            <div>
              <span>Riskler</span>
              <ul>
                {(opinion.risks || []).map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          </div>
          <small>
            <b>Geçersizlik:</b> {opinion.invalidation_note || "Belirtilmedi"}
          </small>
        </>
      ) : (
        <div className="ai-empty">
          <Bot size={17} />
          <span>
            {labels[status] || opinion?.reason || "AI sonucu bulunmuyor"}
          </span>
        </div>
      )}
    </section>
  );
}

function Positions({ rows }: { rows: Position[] }) {
  return (
    <article className="panel table-panel">
      <PanelTitle
        title="Açık Pozisyonlar"
        sub="Her taramada stop ve hedef yeniden kontrol edilir"
      />
      {rows.length ? (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Sembol</th>
                <th>Giriş</th>
                <th>Maliyet</th>
                <th>Güncel</th>
                <th>Lot</th>
                <th>Değer</th>
                <th>Stop</th>
                <th>Hedef</th>
                <th>Gerçekleşmemiş K/Z</th>
                <th>K/Z %</th>
                <th>Setup</th>
                <th>Skor</th>
                <th>Açılış</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => {
                const cost = p.entry_price * p.quantity + p.entry_fees;
                const pnl = p.current_price * p.quantity - cost;
                return (
                  <tr key={p.id}>
                    <td>
                      <b>{p.symbol}</b>
                    </td>
                    <td>{money(p.entry_price)}</td>
                    <td>{money(cost)}</td>
                    <td>{money(p.current_price)}</td>
                    <td>{p.quantity}</td>
                    <td>{money(p.current_price * p.quantity)}</td>
                    <td className="red">{money(p.stop_price)}</td>
                    <td className="green">{money(p.target_price)}</td>
                    <td className={pnl >= 0 ? "green" : "red"}>{money(pnl)}</td>
                    <td className={pnl >= 0 ? "green" : "red"}>
                      {pct(cost ? (pnl / cost) * 100 : 0)}
                    </td>
                    <td>{p.setup_type}</td>
                    <td>
                      <Score value={p.signal_score} />
                    </td>
                    <td>{fmtDate(p.opened_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty
          icon={BriefcaseBusiness}
          title="Açık pozisyon yok"
          text="Risk ve giriş koşulları birlikte sağlandığında paper broker işlem açar."
        />
      )}
    </article>
  );
}

function Trades({ rows }: { rows: Trade[] }) {
  return (
    <article className="panel table-panel">
      <PanelTitle
        title="İşlem Günlüğü"
        sub="Maliyetler sonrası gerçekleşmiş sonuçlar"
      />
      {rows.length ? (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Sembol</th>
                <th>Giriş</th>
                <th>Çıkış</th>
                <th>Lot</th>
                <th>Brüt K/Z</th>
                <th>Komisyon</th>
                <th>Slippage</th>
                <th>Net K/Z</th>
                <th>Getiri</th>
                <th>Setup</th>
                <th>Skor</th>
                <th>Süre</th>
                <th>Çıkış nedeni</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((t) => (
                <tr key={t.id}>
                  <td>
                    <b>{t.symbol}</b>
                  </td>
                  <td>{money(t.entry_price)}</td>
                  <td>{money(t.exit_price)}</td>
                  <td>{t.quantity}</td>
                  <td>{money(t.realized_pnl + t.fees + t.slippage_cost)}</td>
                  <td>{money(t.fees)}</td>
                  <td>{money(t.slippage_cost)}</td>
                  <td className={t.realized_pnl >= 0 ? "green" : "red"}>
                    {money(t.realized_pnl)}
                  </td>
                  <td>{pct(t.return_pct)}</td>
                  <td>{t.setup_type}</td>
                  <td>
                    <Score value={t.signal_score} />
                  </td>
                  <td>
                    {Math.max(
                      1,
                      Math.round(
                        (new Date(t.exit_time).getTime() -
                          new Date(t.entry_time).getTime()) /
                          3600000,
                      ),
                    )}{" "}
                    sa
                  </td>
                  <td>{t.exit_reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty
          icon={CircleDollarSign}
          title="Kapanmış işlem yok"
          text="Stop, hedef veya manuel kapanış sonrasında tüm gerekçeler burada saklanır."
        />
      )}
    </article>
  );
}

function ActivityFeed({ rows }: { rows: Decision[] }) {
  return (
    <article className="panel activity-panel">
      <PanelTitle
        title="Bot Aktivitesi"
        sub="İşlem açılan ve reddedilen bütün kararlar"
      />
      {rows.length ? (
        <div className="timeline">
          {rows.map((r) => (
            <div className="event" key={r.id}>
              <time>
                {new Date(r.created_at).toLocaleTimeString("tr-TR", {
                  hour: "2-digit",
                  minute: "2-digit",
                  second: "2-digit",
                })}
              </time>
              <i
                className={
                  r.decision.includes("NO_") || r.category.includes("ERROR")
                    ? "warn"
                    : ""
                }
              />
              <div>
                <span>{r.category}</span>
                <b>
                  {r.symbol && `${r.symbol} — `}
                  {r.decision.replaceAll("_", " ")}
                </b>
                <p>{r.reason}</p>
              </div>
              {r.score != null && <Score value={r.score} />}
            </div>
          ))}
        </div>
      ) : (
        <Empty
          icon={Activity}
          title="Aktivite yok"
          text="Scanner çalıştığında yalnızca backend kararları burada görünür."
        />
      )}
    </article>
  );
}

function SettingsView({
  values,
  setValues,
  onSave,
  onTelegramTest,
  onReset,
}: {
  values: Record<string, number>;
  setValues: (v: Record<string, number>) => void;
  onSave: () => void;
  onTelegramTest: () => void;
  onReset: () => void;
}) {
  const fields: [string, string, string, number][] = [
    ["scan_interval_minutes", "Tarama aralığı", "Dakika", 1],
    ["watchlist_score", "Takip listesi skoru", "0–100", 1],
    ["entry_score", "Giriş skoru", "0–100", 1],
    ["risk_per_trade_pct", "İşlem başı risk", "Ondalık oran", 0.001],
    ["max_position_size_pct", "Maksimum pozisyon", "Ondalık oran", 0.01],
    ["max_open_positions", "Maksimum açık pozisyon", "Adet", 1],
    ["min_rr", "Minimum risk/getiri", "Oran", 0.1],
    ["commission_rate", "Komisyon", "Ondalık oran", 0.0001],
    ["slippage_rate", "Slippage", "Ondalık oran", 0.0001],
  ];
  return (
    <div className="settings-grid">
      <article className="panel">
        <PanelTitle
          title="Strateji Ayarları"
          sub="Değişiklikler veritabanında saklanır"
        />
        <div className="form-grid">
          {fields.map(([key, label, hint, step]) => (
            <label key={key}>
              <span>
                {label}
                <small>{hint}</small>
              </span>
              <input
                type="number"
                step={step}
                value={values[key] ?? 0}
                onChange={(e) =>
                  setValues({ ...values, [key]: Number(e.target.value) })
                }
              />
            </label>
          ))}
        </div>
        <button className="save" onClick={onSave}>
          Ayarları kaydet
        </button>
        <button className="save" onClick={onTelegramTest}>
          Telegram test mesajı gönder
        </button>
      </article>
      <aside className="panel warning-box">
        <ShieldCheck />
        <h3>Paper trading sınırı</h3>
        <p>
          V6 gerçek broker’a bağlanmaz, gerçek para kullanmaz ve yalnızca LONG
          sanal işlemler üretir. Aktif run strateji parametrelerini dondurur.
        </p>
        <ul>
          <li>Kesirli lot yok</li>
          <li>Komisyon ve slippage dahil</li>
          <li>Eksik veride fail-safe</li>
          <li>Kapanmamış mum kullanılmaz</li>
        </ul>
        <button className="danger-action" onClick={onReset}>
          Paper portföyü sıfırla
        </button>
      </aside>
    </div>
  );
}

function PanelTitle({ title, sub }: { title: string; sub: string }) {
  return (
    <div className="panel-title">
      <div>
        <h2>{title}</h2>
        <p>{sub}</p>
      </div>
      <button aria-label="Menü">•••</button>
    </div>
  );
}
function Score({ value }: { value: number }) {
  return (
    <span
      className={`score ${value >= 82 ? "high" : value >= 70 ? "mid" : "low"}`}
    >
      {value}
    </span>
  );
}
function Tag({ text }: { text: string }) {
  return (
    <span
      className={`tag ${text.toLowerCase().includes("bull") || text.includes("ENTRY") ? "positive" : ""}`}
    >
      {text.replaceAll("_", " ")}
    </span>
  );
}
function Stat({ label, value }: { label: string; value?: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value?.replaceAll("_", " ") || "—"}</dd>
    </div>
  );
}
function Empty({
  icon: Icon,
  title,
  text,
}: {
  icon: React.ElementType;
  title: string;
  text: string;
}) {
  return (
    <div className="empty">
      <Icon />
      <b>{title}</b>
      <p>{text}</p>
    </div>
  );
}
function MiniPosition({ rows }: { rows: Position[] }) {
  return (
    <div className="candidate-list">
      {rows.map((p) => (
        <button key={p.id}>
          <div>
            <b>{p.symbol}</b>
            <small>
              {p.quantity} lot • {p.setup_type}
            </small>
          </div>
          <span className="price">{money(p.current_price)}</span>
        </button>
      ))}
    </div>
  );
}
function EquityChart({ history, base }: { history: Snapshot[]; base: number }) {
  const points =
    history.length > 1
      ? history.map((x) => x.portfolio_value)
      : [base, base, base, base];
  const min = Math.min(...points) * 0.998,
    max = Math.max(...points) * 1.002,
    coords = points
      .map(
        (p, i) =>
          `${(i / (points.length - 1 || 1)) * 100},${72 - ((p - min) / (max - min || 1)) * 58}`,
      )
      .join(" ");
  return (
    <div className="equity-chart">
      <span>{money(max)}</span>
      <svg viewBox="0 0 100 78" preserveAspectRatio="none">
        <defs>
          <linearGradient id="fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#14d99a" stopOpacity=".26" />
            <stop offset="1" stopColor="#14d99a" stopOpacity="0" />
          </linearGradient>
        </defs>
        <polygon points={`0,78 ${coords} 100,78`} fill="url(#fill)" />
        <polyline
          points={coords}
          fill="none"
          stroke="#14d99a"
          strokeWidth="1.2"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
      <small>Başlangıç {money(base)}</small>
    </div>
  );
}
