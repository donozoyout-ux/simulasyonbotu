# BIST Pilot — Sanal Portföy / AI Trading Simulator V6

BIST hisselerini kapanmış OHLCV mumlarıyla deterministik olarak tarayan, kendi teknik analizini yapan ve 5.000 TL sanal sermayeyle LONG paper trading gerçekleştiren yerel uygulama. Gerçek broker, gerçek emir veya gerçek para entegrasyonu **yoktur**.

## V4 OOS validation

V4, V3 strateji konfigürasyonunu hash ile dondurur ve V3 dönemiyle çakışan OOS verisini reddeder. Mevcut veri bütünlüğü ve OOS readiness raporu:

```powershell
cd backend
..\.venv\Scripts\python.exe -m scripts.v4_oos
```

Yahoo şu anda V3 dışında bağımsız 15 dakikalık geçmiş sunmadığı için sahte OOS sonucu üretilmez. Lisanslı veri için EODHD adaptörü 5 dakikalık mumları deterministik biçimde 15 dakikaya toplar. `EODHD_API_TOKEN` tanımlandıktan sonra `python -m scripts.build_v4_oos_dataset` ve `python -m scripts.v4_oos` çalıştırılabilir.

## V5 sağlayıcı yeterliliği ve gerçek OOS

V5, Yahoo, EODHD ve Twelve Data sağlayıcılarını PnL kullanmadan kapsama, geçmiş derinliği, OHLC bütünlüğü, seans/zaman dilimi uyumu ve tekrar üretilebilirlik ölçütleriyle değerlendirir. Anahtarlar yalnızca ortam değişkenlerinden okunur; `.env` git tarafından dışlanır ve hata metinlerindeki anahtar parametreleri maskelenir.

```powershell
cd backend
..\.venv\Scripts\python.exe -m scripts.qualify_provider
..\.venv\Scripts\python.exe -m scripts.build_v5_oos_dataset
..\.venv\Scripts\python.exe -m scripts.v5_oos
```

Yeterlilik raporu `backend/data/v5_provider_qualification.json` dosyasına yazılır ve `/api/provider-qualification` üzerinden okunabilir. `EODHD_API_TOKEN` veya `TWELVE_DATA_API_KEY` yoksa sistem `WAITING_FOR_PROVIDER_CREDENTIAL` durumunda kalır; mock veri, Yahoo fallback'i veya sıfırlarla doldurulmuş performans sonucu üretmez.

Twelve Data credential mevcutsa aynı qualification komutu XIST `/stocks` discovery sonucundan deterministik olarak 40 small/mid aday seçer, muhafazakâr BIST30 dışlama kümesini uygular ve her sembolün 15M/1H/1D kapsamını raporlar. En az 25 sembol 1 Şubat 2026 veya daha eski 15M geçmişe ve kalite eşiklerine sahip olmadıkça `twelvedata_small_mid.status=PARTIAL` ve `replay_gate=BLOCKED` kalır.

## Mimari

- `backend/app/market_data`: Sağlayıcı soyutlaması, Yahoo Chart OHLCV adaptörü, katı normalizasyon ve BIST seans takvimi.
- `backend/app/analysis`: EMA, RSI, MACD, ATR, Bollinger, trend, swing/market structure, destek-direnç, momentum, hacim ve volatilite.
- `backend/app/strategy`: BREAKOUT, PULLBACK, SUPPORT_BOUNCE, TREND_CONTINUATION setup'ları; 0–100 scoring ve signal kararı.
- `backend/app/scanner`: 1D → 1H → 15M pipeline, sembol bazında hata izolasyonu, watchlist ve otomatik giriş.
- `backend/app/portfolio`: Decimal muhasebe, risk boyutlandırma, komisyon/slippage, paper broker ve fixed stop/target yönetimi.
- `backend/app/journal`: İşlem açılmayan kararlar dahil audit logları.
- `frontend`: Next.js App Router, TypeScript ve Lightweight Charts ile responsive koyu dashboard.
- `PostgreSQL`: Kalıcı portföy, analiz, mum, emir, işlem ve snapshot verileri.

## Docker ile çalıştırma

1. İsteğe bağlı olarak `.env.example` dosyasını `.env` adıyla kopyalayın.
2. `docker compose up --build` çalıştırın.
3. Dashboard: `http://localhost:3000`
4. API dokümantasyonu: `http://localhost:8000/docs`

Backend açılışta tabloları güvenli biçimde oluşturur. Alembic migration çalıştırmak için:

```bash
docker compose exec backend alembic upgrade head
```

## Docker olmadan geliştirme

Python 3.12+ ile:

```bash
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt
.venv/Scripts/uvicorn app.main:app --reload
```

Yeni terminalde Node.js 22+ ile:

```bash
cd frontend
npm install
npm run dev
```

Docker olmadan varsayılan DB SQLite'tır; production-benzeri yerel akışta Docker PostgreSQL kullanılır.

## Market data ve mock ayrımı

Varsayılan `DATA_MODE=live` ve sağlayıcı Yahoo'nun herkese açık chart OHLCV endpointidir; sistem harici BUY/SELL sinyali tüketmez. Live modda sağlayıcı hatası **asla mock veriye düşmez**: sembol `NO_TRADE` olur ve hata veri sağlığı paneli ile karar günlüğüne yazılır. Mock yalnızca bilinçli `DATA_MODE=mock` seçiminde kullanılır ve arayüzde açıkça **MOCK DATA** görünür.

Yalnızca kapanmış mumlar normalizasyondan geçer. Sayısal OHLCV zorunludur; string/bool/null, NaN, sıfır veya negatif fiyat, duplicate timestamp, ters OHLC aralığı ve gelecekteki mum fail-safe olarak reddedilir. BIST seansı `Europe/Istanbul`, hafta sonu ve genişletilebilir tatil listesiyle kontrol edilir.

## Scanner ve paper trading

Otomatik tarayıcı web prosesinin içinde çalışmaz. Ayrı `scripts.forward_worker` prosesi BIST seansında her kapanmış 15 dakikalık mumu DB idempotency anahtarıyla yalnızca bir kez işler. Manuel tarama `POST /api/scanner/run` ile yapılabilir; piyasa kapalıyken yeni giriş üretmez. Her sembol diğerlerinden izole edilir.

1D ana trendi, 1H yapı/setup hizasını, 15M kapanmış mum giriş tetikleyicisini sağlar. Watchlist eşiği varsayılan 70, giriş eşiği 82'dir. Yüksek skor tek başına alım yaptırmaz; setup, bullish 1D yönü ve minimum 1.5 RR birlikte gerekir.

Varsayılan risk: işlem başına %0,5; pozisyon başına en fazla %20; %10 nakit rezervi; en fazla 4 açık pozisyon. Lot tam sayıdır. Stop mesafesi ATR ile aşırı dar/uzak olmaya karşı sınırlanır. Alış/satış komisyonu ve slippage ayrı kaydedilir ve PnL'e dahildir. Aynı mumda stop ve hedef dokunursa muhafazakâr politika gereği stop önce uygulanır. Emir idempotency anahtarı DB seviyesinde unique'tir.

## V6 Live Forward Paper Trading

V6, bugünden itibaren 5.000 TL sanal sermaye ile gerçek piyasa verisi üzerinde çalışan sürekli bir paper trading motorudur. Gerçek broker emri gönderilmez, gerçek para kullanılmaz.

- **İki Mod**: `LIVE_PAPER` (canlı kapanmış mumlarla çalışan gerçek zamanlı forward test) ve `REPLAY` (tarihsel veri). İki mod aynı analiz, strateji, risk ve portföy motorunu paylaşır.
- **Kalıcı Portföy**: 5.000 TL sanal sermaye, uygulama restart olsa bile DB'den korunur.
- **Provider Desteği**: Canlı modda varsayılan olarak Yahoo live data kullanılır. Twelve Data anahtarı tanımlandığında `MARKET_DATA_PROVIDER=twelvedata` ile strateji kodu değişmeden geçiş yapılabilir. Canlı modda mock veri ile işlem açılması kesinlikle engellenmiştir.
- **15M Zamanlayıcı ve Worker**: Her kapanmış 15 dakikalık mum için bir kez çalışır (`processed_candles` tablosu ile idempotency). Hafta sonu ve seans dışı saatlerde otomatik tarama ve yeni giriş engellenir.
- **BIST Seans & Fail-Safe**: Seans kapalıyken veya veri hatasında yeni giriş açılmaz; açık pozisyonlar ve stop/target değerleri korunur.
- **Run ID ve İmmutable Snapshot**: Her forward test `LIVE-YYYYMMDD-001` formatında izole edilir. Sıfırlama yapıldığında eski işlemler `ARCHIVED` olarak saklanır ve yeni 5.000 TL periyodu başlar.
- **Strateji Versiyonu Koruması**: Aktif forward test sırasında `V3_FROZEN_1` strateji parametreleri değiştirilemez (HTTP 409 koruması).
- **Groq AI İkinci Görüş**: Skoru en az 70 olan deterministic analizler `openai/gpt-oss-20b` modeline gönderilir. AI yalnızca `CONFIRM`, `WATCH` veya `AVOID` yorumu üretir; karar, skor, stop, hedef, lot ve emir yetkisi yoktur. Anahtar veya servis sorunu deterministic paper trading akışını durdurmaz.

### Yerel Geliştirme (Local Dev)

Tek komutla veya ayrı terminallerde:

```powershell
# 1. Terminal: Backend API
cd backend
..\.venv\Scripts\uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 2. Terminal: Forward Scanner Background Worker
cd backend
..\.venv\Scripts\python.exe -m scripts.forward_worker

# 3. Terminal: Frontend Dashboard
cd frontend
npm run dev
```

### Render Deployment Hazırlığı

`render.yaml` dosyası hazır durumdadır:
- **Backend**: FastAPI web servisi
- **Worker**: `scripts.forward_worker` background worker servisi
- **Frontend**: Next.js web servisi
- **Database**: PostgreSQL veritabanı

## API

- `GET /api/health`, `/api/data-health`
- `GET /api/portfolio`, `/api/portfolio/history`
- `GET /api/positions`, `/api/trades`, `/api/watchlist`
- `GET /api/scanner/results`, `/api/analysis/{symbol}`, `/api/candles/{symbol}`
- `GET /api/decisions`, `/api/settings`
- `GET /api/forward/status`, `/api/forward/daily-summaries`, `/api/forward/runs`
- `POST /api/forward/control` (Pause / Resume)
- `POST /api/scanner/run`, `/api/settings`
- `POST /api/portfolio/reset` — gövdede tam olarak `{"confirmation":"RESET PAPER PORTFOLIO"}` gerekir.

## Testler

```bash
cd backend
pytest -v
```

