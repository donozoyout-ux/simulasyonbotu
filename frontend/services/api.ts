import type {Analysis,Candle,DataHealth,Decision,ForwardStatus,NewsHealth,NewsItem,Portfolio,Position,Snapshot,StrategyHealth,Trade,WatchItem} from "@/types";
const API = "/api";

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${API}${path}`, { cache: "no-store" });
  if (!response.ok)
    throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}
export const api = {
  portfolio: () => get<Portfolio>("/portfolio"),
  history: () => get<Snapshot[]>("/portfolio/history"),
  positions: () => get<Position[]>("/positions"),
  trades: () => get<Trade[]>("/trades"),
  watchlist: () => get<WatchItem[]>("/watchlist"),
  analyses: () => get<Analysis[]>("/scanner/results"),
  scannerStatus: () => get<ScannerStatus>("/scanner/status"),
  decisions: () => get<Decision[]>("/decisions"),
  candles: (symbol: string, timeframe = "15m") =>
    get<Candle[]>(`/candles/${symbol}?timeframe=${timeframe}`),
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
  symbolNews: (symbol:string) => get<NewsItem[]>(`/news/${symbol}`),
  newsHealth: () => get<NewsHealth>("/news/health"),
  refreshNews: async () => {const r=await fetch(`${API}/news/refresh`,{method:"POST"});if(!r.ok)throw new Error("Haber yenilenemedi");return r.json()},
  scan: async (maxSymbols?: number) => {
    const query = maxSymbols ? `?max_symbols=${maxSymbols}` : "";
    const r = await fetch(`${API}/scanner/run${query}`, { method: "POST" });
    if (!r.ok) throw new Error("Tarama başlatılamadı");
    return r.json();
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
