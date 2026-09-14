export const DASHBOARD_TABS = [
  "Ana Sayfa",
  "Tarayıcı",
  "Grafik & Analiz",
  "Piyasa Hafızası",
  "Sistem",
] as const;

export type DashboardView = (typeof DASHBOARD_TABS)[number];
