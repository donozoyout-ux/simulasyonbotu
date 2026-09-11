import "@fontsource-variable/inter";
import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "BIST Pilot — Sanal Portföy",
  description: "Deterministik BIST analiz ve paper trading kontrol paneli",
};

export default function RootLayout({children}:{children:React.ReactNode}) {
  return <html lang="tr"><body>{children}</body></html>;
}

