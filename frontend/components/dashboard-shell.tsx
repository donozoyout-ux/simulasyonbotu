"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  BarChart3,
  Bot,
  BriefcaseBusiness,
  CandlestickChart,
  CircleDollarSign,
  Database,
  CloudDownload,
  ListFilter,
  Newspaper,
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
  ScannerStatus,
  Snapshot,
  StrategyHealth,
  Trade,
  WatchItem,
  NewsItem,
  NewsHealth,
  MarketMemoryHealth,
  BackfillStatus,
  CollectionActivity,
  NewsMetrics,
  NewsSourceHealth,
  ReactionQueueStatus,
  MarketSnapshot,
  MarketMemorySymbol,
  NewsReaction,
  SnapshotDetail,
  UnmatchedNews,
} from "@/types";
import { PriceChart } from "./price-chart";

type View =
  | "Genel Bakış"
  | "Takip Listesi"
  | "Tarama Merkezi"
  | "Grafik Analizi"
  | "Pozisyonlar"
  | "İşlemler"
  | "Bot Aktivitesi"
  | "Haberler / KAP"
  | "Piyasa Hafızası"
  | "Veri Toplama Merkezi"
  | "Ayarlar";
const nav: [View, React.ElementType][] = [
  ["Genel Bakış", BarChart3],
  ["Takip Listesi", ListFilter],
  ["Tarama Merkezi", RefreshCw],
  ["Grafik Analizi", CandlestickChart],
  ["Pozisyonlar", BriefcaseBusiness],
  ["İşlemler", CircleDollarSign],
  ["Bot Aktivitesi", Activity],
  ["Haberler / KAP", Newspaper],
  ["Piyasa Hafızası", Database],
  ["Veri Toplama Merkezi", CloudDownload],
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
  const scanInFlight = useRef(false);
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
    [historyAt,setHistoryAt]=useState(""),
    [mode, setMode] = useState("DATA ERROR"),
    [health, setHealth] = useState<DataHealth>(initialHealth),
    [scannerStatus, setScannerStatus] = useState<ScannerStatus>(),
    [strategyHealth, setStrategyHealth] = useState<StrategyHealth>(
      initialStrategyHealth,
    ),
    [news,setNews]=useState<NewsItem[]>([]),
    [newsHealth,setNewsHealth]=useState<NewsHealth>(),
    [memoryHealth,setMemoryHealth]=useState<MarketMemoryHealth>(),
    [backfill,setBackfill]=useState<BackfillStatus>(),
    [newsMetrics,setNewsMetrics]=useState<NewsMetrics>(),
    [sourceHealth,setSourceHealth]=useState<NewsSourceHealth[]>([]),
    [reactionQueue,setReactionQueue]=useState<ReactionQueueStatus>(),
    [collectionActivity,setCollectionActivity]=useState<CollectionActivity[]>([]),
    [unmatchedNews,setUnmatchedNews]=useState<UnmatchedNews[]>([]),
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
      const [p, a, w, pos, t, d, h, s, dh, sh, fw, ss, nh, mh, bf] = await Promise.all([
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
        api.scannerStatus().catch(()=>undefined),
        api.newsHealth().catch(()=>undefined),
        api.marketMemoryHealth().catch(()=>undefined),
        api.backfillStatus().catch(()=>undefined),
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
      setScannerStatus(ss);
      setNewsHealth(nh);
      setMemoryHealth(mh);
      setBackfill(bf);
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
    void api.newsArchive().then(setNews).catch(()=>setNews([]));
  }, [load]);
  useEffect(() => {
    const timer = window.setInterval(() => void load(), 30_000);
    return () => window.clearInterval(timer);
  }, [load]);
  const loadCollection=useCallback(async()=>{
    try{
      const [metrics,sources,reactions,activity,memory,bf,unmatched]=await Promise.all([
        api.newsMetrics(),api.newsSourcesHealth(),api.reactionQueueStatus(),api.collectionActivity(),
        api.marketMemoryHealth(),api.backfillStatus(),api.unmatchedNews(),
      ]);
      setNewsMetrics(metrics);setSourceHealth(sources);setReactionQueue(reactions);
      setCollectionActivity(activity);setMemoryHealth(memory);setBackfill(bf);setUnmatchedNews(unmatched);
    }catch{setMessage("Veri toplama metrikleri geçici olarak alınamadı")}
  },[]);
  useEffect(()=>{
    if(view!=="Veri Toplama Merkezi")return;
    void Promise.resolve().then(loadCollection);const timer=window.setInterval(()=>void loadCollection(),30_000);
    return()=>window.clearInterval(timer);
  },[view,loadCollection]);
  const chosen = analyses.find((x) => x.symbol === selected) || analyses[0];
  useEffect(() => {
    if (!selected) return;
    api
      .candles(selected, timeframe,historyAt||undefined)
      .then(setCandles)
      .catch(() => setCandles([]));
  }, [selected, timeframe,historyAt]);
  const scan = async (maxSymbols?: number) => {
    if (scanInFlight.current) return;
    scanInFlight.current = true;
    setLoading(true);
    setMessage("BIST taranıyor…");
    try {
      const result = await api.scan(maxSymbols);
      await load();
      setMessage(result.analysis_mode === "ANALYSIS_ONLY" ? `Kapalı piyasa analizi tamamlandı • ${result.analyzed} sembol • emirler devre dışı` : "Tarama tamamlandı");
    } catch {
      setMessage("Tarama başlatılamadı — backend durumunu kontrol edin");
    } finally {
      scanInFlight.current = false;
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
              onClick={() => void scan()}
              disabled={loading}
            >
              <RefreshCw size={16} className={loading ? "spin" : ""} />
              {loading ? "Taranıyor" : forward?.market_status==="MARKET CLOSED"?"Analizi çalıştır":"Taramayı çalıştır"}
            </button>
          </div>
        </header>
        <div className="statusline">
          <span>{message}</span>
          <span suppressHydrationWarning>
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
            newsHealth={newsHealth}
            memoryHealth={memoryHealth}
          />
        )}
        {view === "Takip Listesi" && (
          <Watchlist
            rows={watch}
            analyses={analyses}
            status={scannerStatus}
            onSelect={(s) => {
              setSelected(s);
              setView("Grafik Analizi");
            }}
          />
        )}
        {view === "Tarama Merkezi" && (
          <ScannerCenter
            status={scannerStatus}
            analyses={analyses}
            loading={loading}
            onScan={scan}
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
            positions={positions}
            news={news.filter(item=>item.symbol===chosen?.symbol)}
            historyAt={historyAt}
            setHistoryAt={setHistoryAt}
          />
        )}
        {view === "Pozisyonlar" && <Positions rows={positions} />}{" "}
        {view === "İşlemler" && <Trades rows={trades} />}{" "}
        {view === "Bot Aktivitesi" && <ActivityFeed rows={decisions} />}{" "}
        {view === "Haberler / KAP" && (
          <NewsPanel rows={news} health={newsHealth} onRefresh={async()=>{await api.refreshNews();setNews(await api.newsArchive());await load()}} />
        )}{" "}
        {view === "Piyasa Hafızası" && (
          <MarketMemoryView symbols={analyses.map(item=>item.symbol)} health={memoryHealth} backfill={backfill}/>
        )}{" "}
        {view === "Veri Toplama Merkezi" && (
          <DataCollectionCenter metrics={newsMetrics} sources={sourceHealth} reactions={reactionQueue}
            memory={memoryHealth} backfill={backfill} activity={collectionActivity} unmatched={unmatchedNews}/>
        )}{" "}
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
  newsHealth,
  memoryHealth,
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
  newsHealth?: NewsHealth;
  memoryHealth?: MarketMemoryHealth;
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
      <section className="panel news-health">
        <PanelTitle title="24/7 Piyasa Hafızası" sub={`Durum: ${memoryHealth?.status||"NO_DATA"} • Son 24 saat: ${newsHealth?.last_24h||0} haber`}/>
        <div className="health-row">
          <span><b>Önemli</b> {newsHealth?.important_24h||0}</span>
          <span><b>Gece</b> {newsHealth?.overnight_24h||0}</span>
          <span><b>KAP</b> {newsHealth?.kap_24h||0}</span>
          <span><b>Snapshot</b> {memoryHealth?.snapshot_count||0}</span>
          <span><b>Reaction</b> {memoryHealth?.reaction_count||0}</span>
          <span><b>Hata</b> {newsHealth?.errors||0}</span>
        </div>
      </section>
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
  status,
  onSelect,
}: {
  rows: WatchItem[];
  analyses: Analysis[];
  status?: ScannerStatus;
  onSelect: (s: string) => void;
}) {
  return (
    <article className="panel table-panel">
      <PanelTitle
        title="Otomatik Takip Listesi"
        sub={`${rows.length} aday • puana göre sıralı`}
      />
      {status?.analysis_mode==="ANALYSIS_ONLY"?<div className="health-banner warn" style={{marginBottom:"1rem"}}><ShieldCheck size={18}/><span><b>PİYASA KAPALI — ANALİZ MODU</b> Bu sonuçlar son kapanmış piyasa verileriyle hesaplanır. Yeni emir oluşturulmaz.</span></div>:null}
      {!rows.length ? (
        <div className="health-banner warn" style={{ marginBottom: "1rem" }}>
          <ListFilter size={18} />
          <span>
            <b>Henüz 70+ skorlu aday yok.</b>
            Tarama yapılmıyor anlamına gelmez; tüm taranan hisseleri ve eleme nedenlerini Tarama Merkezi’nde görebilirsin.
          </span>
        </div>
      ) : null}
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
              <th>RSI</th>
              <th>Relatif Güç</th>
              <th>Haber</th>
              <th>AI</th>
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
                  <td>{a?.details.indicators?.rsi?.toFixed(1) || "—"}</td>
                  <td>{a?.details.relative_strength?.label || "NO_DATA"}</td>
                  <td>{a?.details.news?.items?.[0]?.ai_sentiment || "NO_NEWS"}</td>
                  <td>{a?.ai_result?.verdict || a?.ai_status || "—"}</td>
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

function ScannerCenter({
  status,
  analyses,
  loading,
  onScan,
  onSelect,
}: {
  status?: ScannerStatus;
  analyses: Analysis[];
  loading: boolean;
  onScan: (maxSymbols?: number) => Promise<void>;
  onSelect: (s: string) => void;
}) {
  const last = status?.last_scan;
  const providerErrors = status?.symbol_health?.length ? status.symbol_health : (last?.errors ?? []);
  return (
    <div className="scanner-center">
      <article className="panel">
        <PanelTitle
          title="Tarama Merkezi"
          sub="Botun hangi hisseleri analiz ettiğini, neden elediğini ve takip listesine ne taşıdığını burada gör"
        />
        <div className="strategy-grid">
          <div>
            <span>Scanner</span>
            <b>{status?.analysis_mode==="ANALYSIS_ONLY"?"ANALYSIS ONLY":status?.status || "BEKLENIYOR"}</b>
            <small>{status?.market_status || "—"}</small>
          </div>
          <div><span>Entries</span><b>{status?.entries_enabled?"ENABLED":"DISABLED"}</b><small>Order authority</small></div>
          <div><span>Source candle</span><b>{status?.last_market_candle?fmtDate(status.last_market_candle):"—"}</b><small>Son geçerli piyasa mumu</small></div>
          <div>
            <span>Batch</span>
            <b>{status?.scanner_symbol_limit ?? 30} sembol</b>
            <small>Her 15M turunda rotasyonlu tarama</small>
          </div>
          <div>
            <span>Provider / Worker</span>
            <b>{status?.provider?.toUpperCase() || "—"}</b>
            <small>{status?.auto_worker ? "Auto worker aktif" : "Auto worker kapalı"}</small>
          </div>
          <div>
            <span>Taranan</span>
            <b>{last?.total_symbols ?? 0}</b>
            <small>Son batch sembol sayısı</small>
          </div>
          <div>
            <span>Başarılı</span>
            <b>{last?.valid_symbols ?? 0}</b>
            <small>Analizi tamamlanan</small>
          </div>
          <div>
            <span>Hatalı</span>
            <b>{last?.failed_symbols ?? 0}</b>
            <small>{last?.stale_symbols ?? 0} stale</small>
          </div>
          <div>
            <span>En Yüksek Skor</span>
            <b>{status?.score_stats?.highest ?? 0}</b>
            <small>Ortalama {status?.score_stats?.average ?? 0}</small>
          </div>
          <div>
            <span>70+ Aday</span>
            <b>{status?.score_stats?.above_watchlist ?? 0}</b>
            <small>Watchlist eşiği 70</small>
          </div>
          <div>
            <span>82+ Aday</span>
            <b>{status?.score_stats?.above_entry ?? 0}</b>
            <small>Entry eşiği 82</small>
          </div>
          <div>
            <span>Takip Listesi</span>
            <b>{status?.watchlist_count ?? 0}</b>
            <small>≥ {status?.watchlist_score ?? 70} • entry ≥ {status?.entry_score ?? 82}</small>
          </div>
          <div>
            <span>Analiz Edilmiş</span>
            <b>{status?.latest_analysis_symbols ?? analyses.length}</b>
            <small>Son snapshot’ı bulunan sembol</small>
          </div>
          <div>
            <span>Son Tarama</span>
            <b>{last?.completed_at ? fmtDate(last.completed_at) : last?.started_at ? "Çalışıyor" : "—"}</b>
            <small>{last?.duration_ms ? (last.duration_ms / 1000).toFixed(1) + " sn" : "Henüz tamamlanmadı"}</small>
          </div>
          <div>
            <span>Tarama Başlangıcı</span>
            <b>{last?.started_at ? fmtDate(last.started_at) : "—"}</b>
            <small>{last?.completed_at ? `Bitiş: ${fmtDate(last.completed_at)}` : "Bitiş bekleniyor"}</small>
          </div>
          <div>
            <span>Sinyal / Emir</span>
            <b>{last?.signals ?? 0} / {last?.entries ?? 0}</b>
            <small>82+ aday ve açılan paper emir</small>
          </div>
          <div>
            <span>Sonraki Otomatik Tarama</span>
            <b>{status?.analysis_mode==="ANALYSIS_ONLY"&&status?.next_off_hours_scan?fmtDate(status.next_off_hours_scan):status?.next_automatic_scan ? fmtDate(status.next_automatic_scan) : "—"}</b>
            <small>{status?.analysis_mode==="ANALYSIS_ONLY"?`${status.off_hours_scan_interval_minutes} dk off-hours cadence`:"Yeni kapanmış 15M mumda"}</small>
          </div>
        </div>
        <div className="header-actions" style={{ marginTop: "1rem", justifyContent: "flex-start" }}>
          <button
            className="scan"
            disabled={loading}
            onClick={() => void onScan(status?.manual_scan_symbol_limit ?? 10)}
          >
            <RefreshCw size={16} className={loading ? "spin" : ""} />
            {loading ? "Taranıyor" : (status?.manual_scan_symbol_limit ?? 10) + (status?.analysis_mode==="ANALYSIS_ONLY"?" hisse analiz et":" hisse tara")}
          </button>
          <button
            className="pause-control"
            disabled={loading}
            onClick={() => void onScan(status?.analysis_mode==="ANALYSIS_ONLY"?(status?.off_hours_scan_symbol_limit??30):(status?.scanner_symbol_limit??30))}
          >
            <RefreshCw size={15} />
            {status?.analysis_mode==="ANALYSIS_ONLY"?`${status?.off_hours_scan_symbol_limit??30} hisse analiz et`:"Batch taraması"}
          </button>
        </div>
        {status?.market_status === "MARKET CLOSED" ? (
          <div className="health-banner warn" style={{ marginTop: "1rem" }}>
            <RefreshCw size={18} />
            <span><b>PİYASA KAPALI — ANALİZ MODU.</b> Son kapanmış piyasa verileri analiz edilir; yeni emir oluşturulmaz.</span>
          </div>
        ) : null}
        {providerErrors.length ? (
          <div className="health-errors" style={{ marginTop: "1rem" }}>
            <b>Provider hataları</b>
            <div style={{overflowX:"auto",marginTop:"0.6rem"}}>
              <table>
                <thead><tr><th>Symbol</th><th>Error Type</th><th>Message</th><th>Consecutive Failures</th><th>Retry At</th></tr></thead>
                <tbody>{providerErrors.slice(0,20).map((item)=>(
                  <tr key={item.symbol+"-"+item.error}>
                    <td><b>{item.symbol}</b></td><td>{item.type||"UNKNOWN"}</td><td>{item.message||item.error}</td>
                    <td>{item.consecutive_failures??1}</td><td>{item.retry_at?fmtDate(item.retry_at):"—"}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          </div>
        ) : null}
      </article>

      <article className="panel table-panel">
        <PanelTitle
          title="Taranan Hisseler"
          sub={analyses.length + " sembol • takip listesine girmese bile son analiz sonucu görünür"}
        />
        {analyses.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Sembol</th>
                  <th>Fiyat</th>
                  <th>Skor</th>
                  <th>Karar</th>
                  <th>Setup</th>
                  <th>1D Trend</th>
                  <th>1H Yapı</th>
                  <th>RSI</th>
                  <th>MACD</th>
                  <th>RVOL</th>
                  <th>ATR</th>
                  <th>R/R</th>
                  <th>Relative Strength</th>
                  <th>AI</th>
                  <th>Haber</th>
                  <th>Neden</th>
                  <th>Güncelleme</th>
                </tr>
              </thead>
              <tbody>
                {analyses.map((a) => (
                  <tr key={a.symbol} onClick={() => onSelect(a.symbol)}>
                    <td><b>{a.symbol}</b></td>
                    <td>{money(a.price)}</td>
                    <td><Score value={a.score} /></td>
                    <td><Tag text={a.decision} /></td>
                    <td>{a.setup}</td>
                    <td>{a.trend}</td>
                    <td>{a.market_structure}</td>
                    <td>{a.details.indicators?.rsi?.toFixed(1) ?? "—"}</td>
                    <td>{a.details.indicators?.macd?.histogram?.toFixed(2) ?? "—"}</td>
                    <td>{a.details.volume?.rvol != null ? `${a.details.volume.rvol.toFixed(2)}x` : "—"}</td>
                    <td>{a.details.volatility?.atr?.toFixed(2) ?? "—"}</td>
                    <td>{a.details.risk_reward?.toFixed(2) ?? "—"}</td>
                    <td>{a.details.relative_strength?.label || "NO_DATA"}</td>
                    <td>{a.ai_result?.verdict || a.ai_status || "—"}</td>
                    <td>{a.details.news?.items?.[0]?.ai_sentiment || "NO_NEWS"}</td>
                    <td>{a.details.universe && a.details.universe.status !== "TRADABLE" ? `${a.details.universe.status}: ${a.details.universe.reason}` : a.reason}</td>
                    <td>{fmtDate(a.analyzed_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            icon={RefreshCw}
            title="Henüz analiz snapshot'ı yok"
            text="Piyasa açıksa 10 hisse test taraması başlat. Sonuçlar takip listesi eşiğinin altında kalsa bile burada görünür."
          />
        )}
      </article>
    </div>
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
  positions,
  news,
  historyAt,
  setHistoryAt,
}: {
  analyses: Analysis[];
  chosen?: Analysis;
  selected: string;
  setSelected: (s: string) => void;
  candles: Candle[];
  timeframe: string;
  setTimeframe: (v: string) => void;
  trades: Trade[];
  positions: Position[];
  news: NewsItem[];
  historyAt: string;
  setHistoryAt: (value:string)=>void;
}) {
  const [historical,setHistorical]=useState<MarketSnapshot>();
  const loadHistorical=async()=>{
    if(!chosen||!historyAt)return;
    try{setHistorical(await api.marketSnapshot(chosen.symbol,new Date(historyAt).toISOString()))}
    catch{setHistorical({symbol:chosen.symbol,timestamp:historyAt,status:"NOT_AVAILABLE",reason:"Historical snapshot alınamadı"})}
  };
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
        <div className="history-controls">
          <b>Historical mode</b>
          <input type="datetime-local" value={historyAt} onChange={event=>{setHistoryAt(event.target.value);setHistorical(undefined)}}/>
          <button onClick={()=>void loadHistorical()} disabled={!historyAt}>O tarihteki botu göster</button>
          {historyAt&&<button onClick={()=>{setHistoryAt("");setHistorical(undefined)}}>Canlıya dön</button>}
        </div>
        {historical&&<div className="historical-state"><b>O TARİHTEKİ BOT DURUMU • {historical.status}</b><span>Fiyat {historical.price?money(historical.price):"—"} • Skor {historical.technical_score??"—"} • Trend {historical.trend||"—"} • Structure {historical.market_structure||"—"} • RSI {historical.rsi?.toFixed(1)??"—"} • MACD {historical.macd_histogram?.toFixed(3)??"—"} • RVOL {historical.rvol?.toFixed(2)??"—"} • ATR {historical.atr?.toFixed(2)??"—"} • Destek {historical.support?.toFixed(2)??"—"} • Direnç {historical.resistance?.toFixed(2)??"—"} • Setup {historical.setup||"—"} • R/R {historical.risk_reward?.toFixed(2)??"—"}</span>{historical.reason&&<small>{historical.reason}</small>}</div>}
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
            {["5m", "15m", "1h", "1d"].map((tf) => (
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
                    supportLow: lv.support_zone?.low,
                    supportHigh: lv.support_zone?.high,
                    resistanceLow: lv.resistance_zone?.low,
                    resistanceHigh: lv.resistance_zone?.high,
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
                label: item.exit_reason?.toUpperCase().includes("STOP") ? "STOP" : "TAKE PROFIT",
              }))}
            entries={[...trades.filter(item=>item.symbol===chosen.symbol).map(item=>({timestamp:item.entry_time,price:item.entry_price,label:"BUY"})),...positions.filter(item=>item.symbol===chosen.symbol).map(item=>({timestamp:item.opened_at,price:item.entry_price,label:"BUY"}))]}
            news={news}
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
          <span>■ KAP / Haber</span>
        </div>
        {news.length>0&&<div className="chart-news-list">{news.slice(0,5).map(item=><a key={item.id||item.source_id} href={item.url} target="_blank" rel="noreferrer"><b>{item.source} • {item.ai_importance??"—"}</b> {item.title}</a>)}</div>}
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
          <Stat label="Analysis Mode" value={chosen.details.analysis_mode||"LIVE"} />
          <Stat label="Market" value={chosen.details.market_open===false?"CLOSED":"OPEN"} />
          <Stat label="Order Authority" value={chosen.details.entries_enabled?"SESSION GATED":"DISABLED"} />
          <Stat label="Last Market Candle" value={chosen.details.source_candle_timestamp?fmtDate(chosen.details.source_candle_timestamp):"—"} />
          <Stat label="AI" value="ADVISORY ONLY" />
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
          <Stat label="RSI 14" value={chosen.details.indicators?.rsi?.toFixed(1)} />
          <Stat label="MACD" value={chosen.details.indicators?.macd?.histogram?.toFixed(3)} />
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
          <Stat label="VWAP ilişkisi" value={chosen.details.indicators?.vwap ? (chosen.price>=chosen.details.indicators.vwap?"ÜZERİNDE":"ALTINDA") : "—"} />
          <Stat label="XU100'e göre" value={chosen.details.relative_strength?.label || "NO_DATA"} />
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
        <NewsSummaryCard analysis={chosen} />
        <CombinedViewCard analysis={chosen} />
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

function NewsSummaryCard({analysis}:{analysis:Analysis}){
  const news=analysis.details.news;
  const latest=news?.items?.[0];
  return <section className="ai-opinion waiting"><div className="ai-opinion-head"><div><span>HABER / KAP</span><b>{latest?.source||"NO_NEWS"}</b></div><em>{latest?.ai_importance!=null?`${latest.ai_importance}/100`:"VERİ YOK"}</em></div>{latest?<><p><b>{latest.title}</b></p><p>{latest.ai_summary||"AI özeti henüz hazır değil."}</p><small>{latest.ai_sentiment||"NEUTRAL"} • {fmtDate(latest.published_at)}</small></>:<div className="ai-empty"><Newspaper size={17}/><span>Bu sembol için haber bulunamadı.</span></div>}</section>
}

function CombinedViewCard({analysis}:{analysis:Analysis}){
  const combined=analysis.ai_result?.combined_ai;
  return <section className="ai-opinion ready"><div className="ai-opinion-head"><div><span>BİRLEŞİK GÖRÜŞ</span><b>{combined?.combined_view||"BEKLENİYOR"}</b></div><em>EXECUTION AUTHORITY: FALSE</em></div><p>{combined?.summary||"Teknik ve haber bağlamı tamamlandığında oluşur."}</p>{combined?.main_risks?.length?<small>Risk: {combined.main_risks.join(" • ")}</small>:null}</section>
}

const memoryValue=(value:unknown,digits=2)=>typeof value==="number"?value.toFixed(digits):value==null||value===""?"NOT_AVAILABLE":String(value);
const memoryReturn=(value?:number)=>value==null?"NOT_AVAILABLE":`${value>=0?"+":""}${value.toFixed(2)}%`;

function NewsEventDetail({item,reaction}:{item:NewsItem;reaction?:NewsReaction}){
  return <section className="memory-inspector-section"><div className="inspector-title"><span>NEWS EVENT</span><b>{item.source}</b></div><div className="inspector-list"><span>Published <b>{fmtDate(item.published_at)}</b></span><span>Category <b>{item.category}</b></span><span>Sentiment <b>{item.ai_sentiment||"NOT_AVAILABLE"}</b></span><span>Importance <b>{item.ai_importance??"NOT_AVAILABLE"}</b></span><span>Overnight <b>{item.overnight_news?"YES":"NO"}</b></span><span>Match <b>{item.symbol_match_method||"NOT_AVAILABLE"} • {item.symbol_match_confidence??"—"}</b></span></div><h3>{item.title}</h3><p>{item.ai_summary||"AI summary NOT_AVAILABLE"}</p><div className="reaction-grid"><span>Status <b>{reaction?.status||"NOT_AVAILABLE"}</b></span><span>15M <b>{memoryReturn(reaction?.return_15m)}</b></span><span>1H <b>{memoryReturn(reaction?.return_1h)}</b></span><span>1D <b>{memoryReturn(reaction?.return_1d)}</b></span><span>5D <b>{memoryReturn(reaction?.return_5d)}</b></span><span>Gap <b>{memoryReturn(reaction?.gap_pct)}</b></span><span>Abnormal 1D <b>{memoryReturn(reaction?.abnormal_return_1d)}</b></span><span>Volume Δ <b>{memoryReturn(reaction?.volume_change)}</b></span><span>RVOL after <b>{memoryValue(reaction?.rvol_after)}</b></span></div></section>
}

function HistoricalInspector({detail,selectedNews,selectedReaction,onNewsSelect,loading,error}:{detail?:SnapshotDetail;selectedNews?:NewsItem;selectedReaction?:NewsReaction;onNewsSelect:(item:NewsItem)=>void;loading:boolean;error:string}){
  if(loading)return <aside className="panel memory-inspector"><div className="chart-placeholder"><RefreshCw className="spin"/><b>Karar detayı yükleniyor...</b></div></aside>;
  if(error)return <aside className="panel memory-inspector"><PanelTitle title="Geçmiş Karar Detayı" sub="PARTIAL"/><p>{error}</p></aside>;
  if(!detail&&!selectedNews)return <aside className="panel memory-inspector"><PanelTitle title="LIVE MEMORY" sub="Snapshot veya haber marker'ı seçin"/><p className="inspector-empty">Bir geçmiş snapshot seçildiğinde botun o andaki kayıtlı kararı ve sonrasında oluşan gerçek fiyat performansı burada açılır.</p></aside>;
  const reactionFor=(item:NewsItem)=>detail?.reactions.find(row=>row.news_id===item.id);
  if(selectedNews&&!detail)return <aside className="panel memory-inspector"><NewsEventDetail item={selectedNews} reaction={selectedReaction}/></aside>;
  const snapshot=detail!.snapshot,analysis=detail!.analysis,future=detail!.future_performance;
  const thought=[["Score",snapshot.technical_score],["Decision",analysis?.decision],["Setup",snapshot.setup],["Trend",snapshot.trend],["Structure",snapshot.market_structure],["AI verdict",analysis?.ai_result?.verdict||analysis?.ai_status],["News context",detail!.news.length]];
  const after=[["15M",memoryReturn(future.return_15m)],["1H",memoryReturn(future.return_1h)],["1D",memoryReturn(future.return_1d)],["5D",memoryReturn(future.return_5d)],["MFE 5D",memoryReturn(future.mfe_5d)],["MAE 5D",memoryReturn(future.mae_5d)],["Quality",future.decision_quality]];
  const technical=[["RSI",snapshot.rsi],["MACD",snapshot.macd],["MACD Signal",snapshot.macd_signal],["MACD Histogram",snapshot.macd_histogram],["EMA20",snapshot.ema20],["EMA50",snapshot.ema50],["EMA200",snapshot.ema200],["Bollinger Upper",snapshot.bb_upper],["Bollinger Middle",snapshot.bb_middle],["Bollinger Lower",snapshot.bb_lower],["ATR",snapshot.atr],["ATR %",snapshot.atr_pct],["VWAP",snapshot.vwap],["RVOL",snapshot.rvol],["Volume SMA20",snapshot.volume_sma20],["Support",snapshot.support],["Resistance",snapshot.resistance],["Swing High",snapshot.swing_high],["Swing Low",snapshot.swing_low],["BOS",snapshot.bos],["CHOCH",snapshot.choch],["Risk / Reward",snapshot.risk_reward],["Relative Strength 1D",snapshot.relative_strength_1d],["Relative Strength 5D",snapshot.relative_strength_5d],["Relative Strength 20D",snapshot.relative_strength_20d]];
  return <aside className="panel memory-inspector"><PanelTitle title="GEÇMİŞ KARAR DETAYI" sub={`${detail!.status} • ${snapshot.analysis_mode==="ANALYSIS_ONLY"?"PİYASA KAPALI ANALİZİ":snapshot.analysis_mode||"NOT_AVAILABLE"}`}/><div className="inspector-meta"><span>{snapshot.symbol}</span><b>{fmtDate(snapshot.timestamp)}</b><span>{snapshot.price!=null?money(snapshot.price):"NOT_AVAILABLE"}</span><em>{snapshot.status}</em></div><div className="then-after"><section><h3>BOT O ANDA NE DÜŞÜNDÜ</h3>{thought.map(([label,value])=><span key={String(label)}>{label}<b>{memoryValue(value)}</b></span>)}</section><section><h3>SONRA NE OLDU</h3>{after.map(([label,value])=><span key={String(label)}>{label}<b>{memoryValue(value)}</b></span>)}</section></div><div className="inspector-source"><span>Data source <b>{snapshot.data_source||"NOT_AVAILABLE"}</b></span><span>Data quality <b>{snapshot.data_quality||"NOT_AVAILABLE"}</b></span><span>Future data <b>{future.status}</b></span></div><p className="inspector-reason"><b>Decision reason:</b> {analysis?.reason||"NOT_AVAILABLE"}</p><details open><summary>Teknik snapshot</summary><div className="technical-grid">{technical.map(([label,value])=><span key={String(label)}>{label}<b>{memoryValue(value)}</b></span>)}</div></details><details><summary>Stored AI history</summary><div className="ai-history"><b>{analysis?.ai_model||"AI NOT_AVAILABLE"}</b><span>{analysis?.ai_status||"NOT_AVAILABLE"}</span><p>{analysis?.ai_result?.summary||analysis?.ai_result?.reason||"Geçmiş kayıtlı AI sonucu yok; yeni AI çağrısı yapılmadı."}</p></div></details><details open><summary>Yakındaki haberler ({detail!.news.length})</summary><div className="nearby-news">{detail!.news.length?detail!.news.map(item=><button key={item.id} onClick={()=>onNewsSelect(item)}><time>{fmtDate(item.published_at)}</time><b>{item.source}</b><span>{item.title}</span><em>{reactionFor(item)?.status||"NO_REACTION"}</em></button>):<span>±24 saat içinde linked news yok.</span>}</div></details>{selectedNews&&<NewsEventDetail item={selectedNews} reaction={reactionFor(selectedNews)||selectedReaction}/>}</aside>
}

function MarketMemoryView({symbols,health,backfill}:{symbols:string[];health?:MarketMemoryHealth;backfill?:BackfillStatus}){
  const [symbol,setSymbol]=useState(symbols[0]||"THYAO"),[filter,setFilter]=useState("ALL");
  const [inventory,setInventory]=useState<MarketMemorySymbol[]>([]),[timeframe,setMemoryTimeframe]=useState("15m"),[range,setRange]=useState("1M"),[anchorAt,setAnchorAt]=useState("");
  const [candles,setMemoryCandles]=useState<Candle[]>([]),[candleLoading,setCandleLoading]=useState(false),[candleError,setCandleError]=useState("");
  const [timeline,setTimeline]=useState<MarketSnapshot[]>([]),[archive,setArchive]=useState<NewsItem[]>([]),[reactions,setReactions]=useState<NewsReaction[]>([]);
  const [detail,setDetail]=useState<SnapshotDetail>(),[selectedNews,setSelectedNews]=useState<NewsItem>(),[detailLoading,setDetailLoading]=useState(false),[detailError,setDetailError]=useState("");
  useEffect(()=>{let active=true;void api.marketMemorySymbols().then(rows=>{if(active)setInventory(rows)}).catch(()=>{if(active)setInventory([])});return()=>{active=false}},[]);
  const allSymbols=Array.from(new Set([...symbols,...inventory.map(item=>item.symbol),symbol])).sort();
  const matchesFilter=(value:string,selected=filter)=>{const row=inventory.find(item=>item.symbol===value);return selected==="ALL"||selected==="NEWS"&&!!row?.news_count||selected==="SNAPSHOT"&&!!row?.snapshot_count||selected==="REACTION"&&!!row?.reaction_count};
  const filteredSymbols=allSymbols.filter(item=>matchesFilter(item));
  const changeSymbol=(value:string)=>{setAnchorAt("");setDetail(undefined);setSelectedNews(undefined);setTimeline([]);setArchive([]);setSymbol(value)};
  const applyFilter=(value:string)=>{setFilter(value);const matches=allSymbols.filter(item=>matchesFilter(item,value));if(matches.length&&!matches.includes(symbol))changeSymbol(matches[0])};
  useEffect(()=>{let active=true;void Promise.all([api.marketHistory(symbol),api.marketNews(symbol),api.newsReactions(symbol)]).then(([snapshots,items,reactionRows])=>{if(active){setTimeline(snapshots);setArchive(items);setReactions(reactionRows)}}).catch(()=>{if(active){setTimeline([]);setArchive([]);setReactions([])}});return()=>{active=false}},[symbol]);
  useEffect(()=>{let active=true;const end=anchorAt||undefined,base=end?new Date(end):new Date(),days=range==="1D"?1:range==="5D"?5:range==="1M"?30:range==="3M"?90:0;const start=days?new Date(base.getTime()-days*86400000).toISOString():undefined;void Promise.resolve().then(()=>{if(!active)return undefined;setCandleLoading(true);setCandleError("");setMemoryCandles([]);return api.historicalCandles(symbol,timeframe,{limit:500,start,end,at:end})}).then(rows=>{if(active&&rows)setMemoryCandles(rows)}).catch(error=>{if(active)setCandleError(error instanceof Error?error.message:"Candle verisi alınamadı")}).finally(()=>{if(active)setCandleLoading(false)});return()=>{active=false}},[symbol,timeframe,range,anchorAt]);
  const inspectSnapshot=useCallback((item:MarketSnapshot)=>{setAnchorAt(item.timestamp);setSelectedNews(undefined);setDetailLoading(true);setDetailError("");void api.marketSnapshotDetail(item.symbol,item.timestamp).then(setDetail).catch(error=>setDetailError(error instanceof Error?error.message:"Detay alınamadı")).finally(()=>setDetailLoading(false))},[]);
  const inspectNews=useCallback((item:NewsItem)=>setSelectedNews(item),[]);
  const latestSnapshot=timeline.at(-1),latestCandle=candles.at(-1),firstTime=candles[0]?.timestamp,lastTime=latestCandle?.timestamp;
  const inChart=(stamp:string)=>!firstTime||!lastTime||(new Date(stamp)>=new Date(firstTime)&&new Date(stamp)<=new Date(lastTime));
  const chartSnapshots=timeline.filter(item=>inChart(item.timestamp)),chartNews=archive.filter(item=>inChart(item.published_at));
  const levelSnapshot=detail?.snapshot||chartSnapshots.at(-1)||latestSnapshot,levels=levelSnapshot&&(levelSnapshot.support!=null||levelSnapshot.resistance!=null)?{support:levelSnapshot.support,resistance:levelSnapshot.resistance}:undefined;
  const source=Array.from(new Set(candles.map(item=>item.source).filter(Boolean))).join(" / ")||"DB";
  const completed=inventory.reduce((sum,item)=>sum+item.completed_reaction_count,0),linked=inventory.reduce((sum,item)=>sum+item.news_count,0);
  return <div className="memory-layout"><article className="panel memory-price-panel"><div className="memory-mode"><b>{detail?"HISTORICAL INSPECTION":"LIVE MEMORY"}</b><span>DB ONLY • provider çağrısı yok</span></div><div className="chart-head"><div><b>{symbol} • {timeframe.toUpperCase()}</b><span>Gerçek historical OHLC • DB-first</span></div><select value={symbol} onChange={event=>changeSymbol(event.target.value)}>{filteredSymbols.map(item=><option key={item}>{item}</option>)}</select></div><div className="memory-filters">{[["ALL","TÜMÜ"],["NEWS","HABERLİ"],["SNAPSHOT","SNAPSHOT VAR"],["REACTION","REACTION VAR"]].map(([value,label])=><button key={value} className={filter===value?"active":""} onClick={()=>applyFilter(value)}>{label}</button>)}</div><div className="memory-header-stats"><div><span>Latest Price</span><b>{latestCandle?money(latestCandle.close):latestSnapshot?.price!=null?money(latestSnapshot.price):"—"}</b></div><div><span>Snapshots</span><b>{health?.snapshot_count??timeline.length}</b></div><div><span>Linked News</span><b>{linked}</b></div><div><span>Completed Reactions</span><b>{completed}</b></div><div><span>Recorded</span><b>{health?.recorded_count??0}</b></div><div><span>Reconstructed</span><b>{health?.reconstructed_count??0}</b></div><div><span>Candles loaded</span><b>{candles.length}</b></div><div><span>Data source</span><b>{source}</b></div></div><div className="memory-controls"><div className="timeframes">{["5m","15m","1h","1d"].map(value=><button key={value} className={timeframe===value?"active":""} onClick={()=>setMemoryTimeframe(value)}>{value.toUpperCase()}</button>)}</div><div className="timeframes">{[["1D","1G"],["5D","5G"],["1M","1A"],["3M","3A"],["ALL","TÜMÜ"]].map(([value,label])=><button key={value} className={range===value?"active":""} onClick={()=>setRange(value)}>{label}</button>)}</div>{anchorAt&&<button className="pause-control" onClick={()=>{setAnchorAt("");setDetail(undefined);setSelectedNews(undefined)}}>Güncele dön</button>}</div>{candleLoading?<div className="chart-placeholder"><RefreshCw className="spin"/><b>Historical candles yükleniyor...</b></div>:candleError?<div className="chart-placeholder"><CandlestickChart/><b>Candle verisi alınamadı</b><span>{candleError}</span></div>:candles.length?<PriceChart candles={candles} levels={levels} news={chartNews} snapshots={chartSnapshots} focusTime={anchorAt} onNewsSelect={inspectNews} onSnapshotSelect={inspectSnapshot}/>:<div className="chart-placeholder"><CandlestickChart/><b>Bu sembol/timeframe için historical candle bulunamadı.</b><span>Grafik yalnızca Candle tablosundaki gerçek OHLC verisini gösterir.</span></div>}<h3>Score history / snapshot timeline</h3><div className="memory-timeline">{timeline.length?timeline.map(item=><button className={detail?.snapshot.timestamp===item.timestamp?"selected":""} key={item.id??item.timestamp} onClick={()=>inspectSnapshot(item)}><time>{fmtDate(item.timestamp)}</time><b>{item.price!=null?money(item.price):"—"}</b><span>skor {item.technical_score??"—"} • {item.trend||"—"} • {item.market_structure||"—"} • {item.analysis_mode||"—"}</span></button>):<span>Snapshot henüz yok; historical candle grafiği bağımsız olarak görüntülenir.</span>}</div></article><div className="memory-side"><HistoricalInspector detail={detail} selectedNews={selectedNews} selectedReaction={reactions.find(row=>row.news_id===selectedNews?.id)} onNewsSelect={inspectNews} loading={detailLoading} error={detailError}/><aside className="panel"><PanelTitle title="Backfill & Arşiv" sub="Cursor tabanlı, tekrar başlatılabilir"/><div className="health-row">{backfill?.tasks.map(item=><span key={item.task}><b>{item.task}</b> {item.status} • cursor {item.cursor} • {item.processed_items} işlendi</span>)}</div><h3>{symbol} haber zaman çizgisi</h3><div className="chart-news-list">{archive.slice(-10).reverse().map(item=><button key={item.id} onClick={()=>inspectNews(item)}><b>{item.source} {item.overnight_news?"• GECE":""}</b> {item.title}<small>{reactions.find(row=>row.news_id===item.id)?.status||"NO_REACTION"}</small></button>)}</div></aside></div></div>
}

function DataCollectionCenter({metrics,sources,reactions,memory,backfill,activity,unmatched}:{metrics?:NewsMetrics;sources:NewsSourceHealth[];reactions?:ReactionQueueStatus;memory?:MarketMemoryHealth;backfill?:BackfillStatus;activity:CollectionActivity[];unmatched:UnmatchedNews[]}){
  const sourceFailures=sources.filter(item=>item.enabled&&["ERROR","WAF_BLOCKED","RATE_LIMITED"].includes(item.status));
  const coreErrors=[backfill?.status,memory?.status].filter(item=>item==="ERROR").length;
  const overall=!metrics&&!memory&&!backfill||coreErrors>=2?"ERROR":sourceFailures.length||backfill?.status==="PARTIAL"?"DEGRADED":"RUNNING";
  const stat=(label:string,value:string|number)=><div><span>{label}</span><b>{value??"NOT_AVAILABLE"}</b></div>;
  return <div className="collection-center"><div className={`collection-banner ${overall.toLowerCase()}`}><CloudDownload size={22}/><div><span>DATA COLLECTION</span><b>{overall}</b></div><small>30 saniyede bir sağlık ve metrik verileri yenilenir</small></div><div className="collector-grid"><article className="panel collector-card"><PanelTitle title="CANDLE BACKFILL" sub={backfill?.status||"NOT_AVAILABLE"}/><div className="progress"><i style={{width:`${backfill?.progress_pct||0}%`}}/></div><div className="collector-stats">{stat("Progress",`${backfill?.progress_pct||0}%`)}{stat("Cursor",backfill?.cursor||0)}{stat("Completed",backfill?.completed_symbols||0)}{stat("Remaining",backfill?.remaining_symbols||0)}{stat("Candles",backfill?.candle_count||0)}{stat("Last",backfill?.last_symbol||"—")}</div><div className="timeframe-strip">{["5m","15m","1h","1d"].map(x=><span key={x}>{x.toUpperCase()} <b>{backfill?.timeframes?.[x]||0}</b></span>)}</div>{backfill?.last_error&&<p className="collector-error">{backfill.last_error}</p>}</article><article className="panel collector-card"><PanelTitle title="MARKET MEMORY" sub={memory?.status||"NOT_AVAILABLE"}/><div className="collector-stats">{stat("Snapshots",memory?.snapshot_count||0)}{stat("Recorded",memory?.recorded_count||0)}{stat("Reconstructed",memory?.reconstructed_count||0)}{stat("Symbols",memory?.symbols||0)}{stat("Errors",memory?.error_count||0)}{stat("Last",memory?.last_snapshot_at?fmtDate(memory.last_snapshot_at):"—")}</div><div className="timeframe-strip">{["5m","15m","1h","1d"].map(x=><span key={x}>{x.toUpperCase()} <b>{memory?.timeframes?.[x]||0}</b></span>)}</div></article><article className="panel collector-card"><PanelTitle title="NEWS COLLECTION" sub="Normalize + dedupe + symbol reconciliation"/><div className="collector-stats">{stat("Total",metrics?.total_news||0)}{stat("Linked",metrics?.symbol_linked||0)}{stat("Link rate",`${metrics?.link_rate_pct||0}%`)}{stat("Unmatched",metrics?.unmatched||0)}{stat("Pending",metrics?.reconcile_pending||0)}{stat("Last cycle",metrics?.reconciled_last_cycle?.last_cycle_processed||0)}{stat("AI Processed",metrics?.ai_processed||0)}{stat("Overnight",metrics?.overnight_count||0)}</div><div className="timeframe-strip">{Object.entries(metrics?.match_methods||{}).map(([key,value])=><span key={key}>{key} <b>{value}</b></span>)}</div></article><article className="panel collector-card"><PanelTitle title="NEWS REACTION QUEUE" sub={`${reactions?.progress_pct||0}% complete`}/><div className="progress"><i style={{width:`${reactions?.progress_pct||0}%`}}/></div><div className="collector-stats">{stat("Pending",reactions?.pending||0)}{stat("Waiting Open",reactions?.waiting_market_open||0)}{stat("Waiting 15M",reactions?.waiting_15m||0)}{stat("Waiting 1H",reactions?.waiting_1h||0)}{stat("Waiting 1D",reactions?.waiting_1d||0)}{stat("Waiting 5D",reactions?.waiting_5d||0)}{stat("Complete",reactions?.complete||0)}{stat("Error",reactions?.error||0)}</div></article></div><article className="panel table-panel"><PanelTitle title="Eşleşmeyen Haberler" sub="Düşük güvenli adaylar sembole otomatik bağlanmaz"/><div className="table-scroll"><table><thead><tr><th>Date</th><th>Source</th><th>Title</th><th>Reason</th><th>Best candidate</th><th>Confidence</th></tr></thead><tbody>{unmatched.map(item=><tr key={item.id}><td>{fmtDate(item.published_at)}</td><td>{item.source}</td><td>{item.title}</td><td>{item.unmatched_reason||"PENDING"}</td><td>{item.best_candidate||"—"}</td><td>{item.best_candidate_confidence??"—"}</td></tr>)}</tbody></table></div></article><article className="panel table-panel"><PanelTitle title="Source Status" sub="KAP WAF_BLOCKED beklenen dış servis kısıtıdır"/><div className="table-scroll"><table><thead><tr><th>Source</th><th>Type</th><th>Status</th><th>Last Success</th><th>Last Item</th><th>Fetched</th><th>Inserted</th><th>Duplicates</th><th>Failures</th><th>Last Error</th></tr></thead><tbody>{sources.map(item=><tr key={item.source}><td><b>{item.source}</b></td><td>{item.type}</td><td><span className={`collector-status ${item.status==="WAF_BLOCKED"?"warning":""}`}>{item.status}</span></td><td>{item.last_success_at?fmtDate(item.last_success_at):"—"}</td><td>{item.last_item_at?fmtDate(item.last_item_at):"—"}</td><td>{item.items_fetched}</td><td>{item.items_inserted}</td><td>{item.duplicates}</td><td>{item.consecutive_failures}</td><td>{item.last_error||"—"}</td></tr>)}</tbody></table></div></article><article className="panel table-panel"><PanelTitle title="Son Veri Toplama İşlemleri" sub="Kalıcı collector activity günlüğü"/><div className="table-scroll"><table><thead><tr><th>Time</th><th>Module</th><th>Source / Symbol</th><th>Action</th><th>Status</th><th>Detail</th></tr></thead><tbody>{activity.map(item=><tr key={item.id}><td>{fmtDate(item.created_at)}</td><td>{item.module}</td><td>{item.subject||"—"}</td><td>{item.action}</td><td>{item.status}</td><td>{item.detail||"—"}</td></tr>)}</tbody></table></div></article></div>
}

function NewsPanel({rows,health,onRefresh}:{rows:NewsItem[];health?:NewsHealth;onRefresh:()=>Promise<void>}){
  const [query,setQuery]=useState(""),[source,setSource]=useState("ALL"),[category,setCategory]=useState("ALL"),[sentiment,setSentiment]=useState("ALL"),[importance,setImportance]=useState("0"),[start,setStart]=useState(""),[end,setEnd]=useState(""),[overnight,setOvernight]=useState(false),[reaction,setReaction]=useState(false);
  const filtered=rows.filter(item=>(source==="ALL"||item.source===source)&&(category==="ALL"||item.category===category)&&(sentiment==="ALL"||item.ai_sentiment===sentiment)&&(item.ai_importance||0)>=Number(importance)&&(!query||`${item.symbol} ${item.title}`.toLocaleLowerCase("tr").includes(query.toLocaleLowerCase("tr")))&&(!start||new Date(item.published_at)>=new Date(start))&&(!end||new Date(item.published_at)<=new Date(`${end}T23:59:59`))&&(!overnight||item.overnight_news)&&(!reaction||item.reaction?.status==="COMPLETE"));
  const values=(key:"source"|"category")=>Array.from(new Set(rows.map(item=>item[key]).filter(Boolean))).sort();
  const ret=(value?:number)=>value==null?"—":`${value.toFixed(2)}%`;
  return <><article className="panel news-health"><PanelTitle title="News Health" sub={`Durum: ${health?.status||"NO_DATA"} • 24s: ${health?.last_24h||0} • Önemli: ${health?.important_24h||0} • Gece: ${health?.overnight_24h||0} • KAP: ${health?.kap_24h||0}`}/><div className="health-row">{Object.entries(health?.sources||{}).map(([name,state])=><span key={name}><b>{name}</b> {state.status} • {state.new_items} yeni • {state.parse_errors} parse hata</span>)}</div></article><article className="panel table-panel"><div className="chart-head"><div><b>Haber Arşivi</b><span>{filtered.length} / {rows.length} kayıt</span></div><button className="scan" onClick={()=>void onRefresh()}><RefreshCw size={14}/> Yenile</button></div><div className="archive-filters wide"><input placeholder="Sembol veya başlık ara" value={query} onChange={e=>setQuery(e.target.value)}/><input type="date" value={start} onChange={e=>setStart(e.target.value)}/><input type="date" value={end} onChange={e=>setEnd(e.target.value)}/><select value={source} onChange={e=>setSource(e.target.value)}><option value="ALL">Tüm kaynaklar</option>{values("source").map(x=><option key={x}>{x}</option>)}</select><select value={category} onChange={e=>setCategory(e.target.value)}><option value="ALL">Tüm kategoriler</option>{values("category").map(x=><option key={x}>{x}</option>)}</select><select value={sentiment} onChange={e=>setSentiment(e.target.value)}><option value="ALL">Tüm sentiment</option><option>POSITIVE</option><option>NEUTRAL</option><option>NEGATIVE</option></select><select value={importance} onChange={e=>setImportance(e.target.value)}><option value="0">Tüm önem</option><option value="50">Önem ≥50</option><option value="80">Önem ≥80</option></select><label><input type="checkbox" checked={overnight} onChange={e=>setOvernight(e.target.checked)}/> Overnight only</label><label><input type="checkbox" checked={reaction} onChange={e=>setReaction(e.target.checked)}/> Reaction complete only</label></div><div className="table-scroll"><table><thead><tr><th>Date</th><th>Symbol</th><th>Source</th><th>Title</th><th>Category</th><th>Sentiment</th><th>Importance</th><th>Reaction status</th><th>15M</th><th>1H</th><th>1D</th><th>5D</th><th>Abnormal 1D</th><th>AI summary</th></tr></thead><tbody>{filtered.map(item=><tr key={`${item.source}-${item.source_id||item.id}`}><td>{fmtDate(item.published_at)}</td><td><b>{item.symbol||"UNMATCHED"}</b></td><td>{item.source}</td><td><a href={item.url} target="_blank" rel="noreferrer">{item.title}</a></td><td>{item.category}</td><td>{item.ai_sentiment||"PENDING"}</td><td>{item.ai_importance??"—"}</td><td>{item.reaction?.status||"NOT_AVAILABLE"}</td><td>{ret(item.reaction?.return_15m)}</td><td>{ret(item.reaction?.return_1h)}</td><td>{ret(item.reaction?.return_1d)}</td><td>{ret(item.reaction?.return_5d)}</td><td>{ret(item.reaction?.abnormal_return_1d)}</td><td>{item.ai_summary||"—"}</td></tr>)}</tbody></table></div></article></>
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
