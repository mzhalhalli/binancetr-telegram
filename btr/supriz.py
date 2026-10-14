"""Süpriz profili: WAL_TRY'nin 14.10.2026 yükselişine benzeyen mumları bulur. Yalnızca mum verisiyle çalışır.

Referans (WAL_TRY, 4 saatlik, TR saati, Binance verisi):
  14.10 03:00 kırılım mumu   kapanış 1,968  +%3,3   hacim  21 mn TRY (5,2x)   RSI 62  (henüz süpriz değil)
  14.10 07:00 yükseliş başı   kapanış 2,399  +%21,7  hacim 169 mn TRY (35,5x)  RSI 79  BB üst 2,133  bant %26,6
  14.10 19:00 ikinci dalga    kapanış 2,983  +%14,7  hacim 348 mn TRY (10,8x)  RSI 85  BB üst 2,675  bant %65,7
  Öncesi: 13.10 19:00'de BB(21,2) bant genişliği %9,3'e daralmıştı (sıkışma), MACD histogramı 13.10 15:00'te
  artıya dönmüştü, EMA9 > EMA21 > EMA50 sıralaması 11.10'dan beri sürüyordu. Alıcı payı her mumda ~%50.
Eşikler bu iki mumun ikisini de yakalayacak, ama sıradan mumları elemek için gevşek tutulmadan seçildi.
"""
from __future__ import annotations

from . import indikator as ind

REFERANS = {
    "sembol": "WAL_TRY", "aralik": "4h", "tarih": "14.10.2026",
    "mumlar": [
        {"saat": "07:00", "kapanis": 2.399, "degisim_pct": 21.7, "hacim_try": 169_356_555, "hacim_kat": 35.5,
         "rsi": 79, "bb_ust": 2.133, "bant_pct": 26.6, "macd_hist": 0.0381},
        {"saat": "19:00", "kapanis": 2.983, "degisim_pct": 14.7, "hacim_try": 348_165_356, "hacim_kat": 10.8,
         "rsi": 85, "bb_ust": 2.675, "bant_pct": 65.7, "macd_hist": 0.1113},
    ],
    "oncesi": {"en_dar_bant_pct": 9.3, "macd_hist_artiya": "13.10 15:00", "ema_sirasi": "EMA9 > EMA21 > EMA50"},
}

ESIK = {"degisim_pct": 5.0, "hacim_kat": 5.0, "hacim_n": 20, "rsi": 70.0, "bb_n": 21, "bb_k": 2.0,
        "bant_acilma_kat": 2.0, "bant_geri": 12}
GEREKLI_MUM = 160
TOPLAM = 7


def _bant(kap: list[float], j: int) -> float | None:
    b = ind.bollinger(kap[:j + 1], ESIK["bb_n"], ESIK["bb_k"])
    return (b[1] - b[2]) / b[0] * 100 if b and b[0] else None


def hesapla(kap: list[float], hacim: list[float], acilis: list[float]) -> dict | None:
    """Son mum için 7 şartı kontrol eder. Veri yetmezse None. hacim tercihen TRY (karşı para) hacmidir."""
    n = len(kap)
    if n < 60 or len(hacim) < n or len(acilis) < n:
        return None
    sartlar = []
    o, c = acilis[-1], kap[-1]
    d = (c / o - 1) * 100 if o else 0
    sartlar.append((d >= ESIK["degisim_pct"], f"mum {d:+.1f}%"))
    hn = ESIK["hacim_n"]
    ort = sum(hacim[-hn - 1:-1]) / hn
    kat = hacim[-1] / ort if ort > 0 else 0
    sartlar.append((kat >= ESIK["hacim_kat"], f"hacim {kat:.1f}x"))
    r = ind.rsi(kap, 14)[-1]
    sartlar.append((r is not None and r >= ESIK["rsi"], f"RSI {r:.0f}" if r is not None else "RSI -"))
    b = ind.bollinger(kap, ESIK["bb_n"], ESIK["bb_k"])
    sartlar.append((c > b[1], f"BB üst {b[1]:.6g} ({(c / b[1] - 1) * 100:+.1f}%)"))
    simdi = _bant(kap, n - 1)
    onceki = [x for x in (_bant(kap, j) for j in range(n - 1 - ESIK["bant_geri"], n - 1)) if x is not None]
    en_dar = min(onceki) if onceki else None
    acildi = bool(en_dar) and simdi >= ESIK["bant_acilma_kat"] * en_dar
    sartlar.append((acildi, f"bant %{en_dar:.1f}→%{simdi:.1f}" if en_dar else "bant -"))
    m, s = ind.macd(kap)
    h1 = m[-1] - s[-1] if s[-1] is not None else None
    h0 = m[-2] - s[-2] if s[-2] is not None else None
    sartlar.append((h1 is not None and h0 is not None and h1 > 0 and h1 > h0,
                    f"MACD hist {h1:.4g}" if h1 is not None else "MACD -"))
    e9, e21, e50 = ind.ema(kap, 9)[-1], ind.ema(kap, 21)[-1], ind.ema(kap, 50)[-1]
    sartlar.append((None not in (e9, e21, e50) and e9 > e21 > e50, "EMA 9>21>50" if None not in (e9, e21, e50) and e9 > e21 > e50 else "EMA sırası yok"))
    puan = sum(1 for ok, _ in sartlar if ok)
    return {"puan": puan, "toplam": TOPLAM, "ok": puan == TOPLAM,
            "detay": " · ".join(("✓ " if ok else "✗ ") + t for ok, t in sartlar)}
