import type {Analysis,BackfillStatus,Candle,CollectionActivity,DataHealth,Decision,EventStudy,ForwardStatus,MarketMemoryHealth,MarketMemorySymbol,MarketSnapshot,NewsHealth,NewsItem,NewsMetrics,NewsReaction,NewsSourceHealth,Portfolio,Position,ReactionQueueStatus,ScannerRunResponse,ScannerStatus,Snapshot,SnapshotDetail,StrategyHealth,Trade,UnmatchedNews,WatchItem} from "@/types";
const API = "/api";

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API}${path}`, { cache: "no-store", signal });
  if (!response.ok)
    throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}
export const api = {
  health: () => get<{status:string;mode:string;provider:string;real_orders:boolean}>("/health"),
  portfolio: () => get<Portfolio>("/portfolio"),
  history: () => get<Snapshot[]>("/portfolio/history"),
  positions: () => get<Position[]>("/positions"),
  trades: () => get<Trade[]>("/trades"),
  watchlist: () => get<WatchItem[]>("/watchlist"),
  analyses: () => get<Analysis[]>("/scanner/results"),
  scannerStatus: () => get<ScannerStatus>("/scanner/status"),
  decisions: () => get<Decision[]>("/decisions"),
  candles: (symbol: string, timeframe = "15m", at?:string) =>
    get<Candle[]>(`/candles/${symbol}?timeframe=${timeframe}${at?`&at=${encodeURIComponent(at)}`:""}`),
  historicalCandles: (symbol:string,timeframe="15m",options:{limit?:number;start?:string;end?:string;at?:string;signal?:AbortSignal}={}) => {
    const params=new URLSearchParams({timeframe,limit:String(options.limit??300),db_only:"true"});
    if(options.start)params.set("start",options.start);if(options.end)params.set("end",options.end);if(options.at)params.set("at",options.at);
    return get<Candle[]>(`/candles/${symbol}?${params.toString()}`,options.signal);
  },
  dataHealth: () => get<DataHealth>("/data-health"),
  telegramStatus: () => get<{enabled:boolean;configured:boolean;signal_alerts:boolean}>("/telegram/status"),
  strategyHealth: () => get<StrategyHealth>("/strategy-health"),
  forwardStatus: () => get<ForwardStatus>("/forward/status"),
  settings: () => get<Record<string, number>>("/settings"),
  telegramTest: async () => {
    const r = await fetch(`${API}/telegram/test`, { method: "POST" });
    if (!r.ok) throw new Error("Telegram test mesajı gönderilemedi");
    return r.json();
  },
  news: () => get<NewsItem[]>("/news"),
  newsArchive: (query="") => get<NewsItem[]>(`/news/archive${query?`?${query}`:""}`),
  symbolNews: (symbol:string) => get<NewsItem[]>(`/news/${symbol}`),
  newsReactions: (symbol:string,signal?:AbortSignal) => get<NewsReaction[]>(`/news/reactions/${symbol}`,signal),
  eventStudy: () => get<EventStudy[]>("/news/event-study"),
  newsHealth: () => get<NewsHealth>("/news/health"),
  newsMetrics: () => get<NewsMetrics>("/news/metrics"),
  unmatchedNews: () => get<UnmatchedNews[]>("/news/unmatched?limit=100"),
  newsSourcesHealth: () => get<NewsSourceHealth[]>("/news/sources/health"),
  reactionQueueStatus: () => get<ReactionQueueStatus>("/news/reactions/status"),
  recentReactions: () => get<NewsReaction[]>("/news/reactions/recent"),
  collectionActivity: () => get<CollectionActivity[]>("/data-collection/activity"),
  marketHistory: (symbol:string,signal?:AbortSignal) => get<MarketSnapshot[]>(`/market-history/${symbol}`,signal),
  marketTrend: (symbol:string) => get<Array<{timestamp:string;price:number;trend?:string;structure?:string;score?:number;analysis_mode?:"LIVE"|"ANALYSIS_ONLY"}>>(`/market-history/${symbol}/trend`),
  marketSnapshot: (symbol:string,at:string) => get<MarketSnapshot>(`/market-history/${symbol}/snapshot?at=${encodeURIComponent(at)}`),
  marketSnapshotDetail: (symbol:string,at:string,signal?:AbortSignal) => get<SnapshotDetail>(`/market-history/${symbol}/snapshot-detail?at=${encodeURIComponent(at)}`,signal),
  marketNews: (symbol:string,signal?:AbortSignal) => get<NewsItem[]>(`/market-history/${symbol}/news`,signal),
  linkedNewsSymbols: () => get<MarketMemorySymbol[]>("/news/linked-symbols"),
  marketMemorySymbols: (signal?:AbortSignal) => get<MarketMemorySymbol[]>("/market-memory/symbols",signal),
  marketMemoryHealth: () => get<MarketMemoryHealth>("/market-memory/health"),
  backfillStatus: () => get<BackfillStatus>("/backfill/status"),
  refreshNews: async () => {const r=await fetch(`${API}/news/refresh`,{method:"POST"});if(!r.ok)throw new Error("Haber yenilenemedi");return r.json()},
  scan: async (maxSymbols?: number) => {
    const query = maxSymbols ? `?max_symbols=${maxSymbols}` : "";
    const r = await fetch(`${API}/scanner/run${query}`, { method: "POST" });
    if (!r.ok) throw new Error("Tarama başlatılamadı");
    return r.json() as Promise<ScannerRunResponse>;
  },
  saveSettings: async (data: Record<string, number>) => {
    const r = await fetch(`${API}/settings`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!r.ok) throw new Error("Ayarlar kaydedilemedi");
    return r.json();
  },
  controlPaper: async (paused: boolean) => {
    const r = await fetch(`${API}/forward/control`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paused }),
    });
    if (!r.ok) throw new Error("Paper trading durumu değiştirilemedi");
    return r.json();
  },
  resetPaper: async () => {
    const r = await fetch(`${API}/portfolio/reset`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirmation: "RESET PAPER PORTFOLIO" }),
    });
    if (!r.ok) throw new Error("Forward test sıfırlanamadı");
    return r.json();
  },
};
