"""Erken uyarı profili: sert yükselişten hemen önceki sessiz hazırlık mumunu arar. Yalnızca mum verisiyle çalışır.

Referans (BLUR_TRY, 15 dakikalık, TR saati, Binance verisi): 11:45 mumu (12:00'de kapandı), yükselişten önceki son mum.
  9 saat 0,49-0,53 aralığında sıkışma. Kapanış 0,519 (+%1,8), BB(21,2) bant %4,7, üst banda %0,3 uzaklık,
  MACD histogramı bu mumda artıya döndü, EMA9 EMA21'i yukarı kesti, RSI 58, hacim normal (1,2x).
  Sonraki mum (12:00) +%10 ve 20 kat hacimle patladı.
Aynı şartlar WAL_TRY 4 saatlikte 13.10 23:00 mumunu da yakalar (14.10 03:00'teki kırılımdan bir önceki mum).
Bu bir öncü sinyaldir: patlamadan önce yakalar ama süprizden çok daha sık ve daha çok yanlış sinyal verir.
"""
from __future__ import annotations

from . import indikator as ind

REFERANS = {
    "sembol": "BLUR_TRY", "aralik": "15m", "tarih": "15.10.2026", "mum": "11:45 (12:00'de kapanır)",
    "kapanis": 0.519, "degisim_pct": 1.8, "bant_pct": 4.7, "bant_yuzdelik": 22, "yuzde_b": 0.94,
    "macd_hist": 0.0006, "ema9": 0.5089, "ema21": 0.5084, "rsi": 58, "hacim_kat": 1.2,
}

ESIK = {"bb_n": 21, "bb_k": 2.0, "bant_gecmis": 100, "bant_yuzdelik": 40.0, "yuzde_b_min": 0.80, "yuzde_b_max": 1.10,
        "yeni_donus_mum": 3, "rsi_min": 50.0, "rsi_max": 70.0, "hacim_n": 20, "hacim_kat_max": 3.0}
GEREKLI_MUM = 160
TOPLAM = 8


def hesapla(kap: list[float], hacim: list[float], acilis: list[float]) -> dict | None:
    """Son mum için 8 şartı kontrol eder. Veri yetmezse None. hacim tercihen TRY (karşı para) hacmidir."""
    n, g = len(kap), ESIK["bant_gecmis"]
    if n < ESIK["bb_n"] + 61 or len(hacim) < n or len(acilis) < n:   # en az 60 mumluk bant geçmişi
        return None
    bantlar = []
    for j in range(max(ESIK["bb_n"] - 1, n - g - 1), n):
        b = ind.bollinger(kap[:j + 1], ESIK["bb_n"], ESIK["bb_k"])
        bantlar.append(b)
    m, u, a = bantlar[-1]
    bw = (u - a) / m * 100
    gecmis = [(b[1] - b[2]) / b[0] * 100 for b in bantlar[:-1]]
    yuzdelik = sum(1 for x in gecmis if x < bw) / len(gecmis) * 100
    c, o = kap[-1], acilis[-1]
    yb = (c - a) / (u - a) if u > a else 0
    mm, s = ind.macd(kap)
    hist = [x - y if x is not None and y is not None else None for x, y in zip(mm, s)]
    e9, e21 = ind.ema(kap, 9), ind.ema(kap, 21)
    k = ESIK["yeni_donus_mum"]
    if None in hist[-k - 1:] or None in e21[-k - 1:]:
        return None
    macd_dondu = hist[-1] > 0 and any(h <= 0 for h in hist[-k - 1:-1])
    ema_kesti = e9[-1] > e21[-1] and any(x <= y for x, y in zip(e9[-k - 1:-1], e21[-k - 1:-1]))
    r = ind.rsi(kap, 14)[-1]
    hn = ESIK["hacim_n"]
    ort = sum(hacim[-hn - 1:-1]) / hn
    kat = hacim[-1] / ort if ort > 0 else 0
    sartlar = [
        (yuzdelik <= ESIK["bant_yuzdelik"], f"bant %{bw:.1f} (son {len(gecmis)} mumun en dar %{yuzdelik:.0f} dilimi)"),
        (ESIK["yuzde_b_min"] <= yb <= ESIK["yuzde_b_max"], f"üst banda yakın (%B {yb:.2f})"),
        (hist[-1] > 0 and hist[-1] > hist[-2], f"MACD hist {hist[-1]:.4g} artıyor" if hist[-1] > hist[-2] else f"MACD hist {hist[-1]:.4g}"),
        (macd_dondu or ema_kesti, "yeni dönüş: " + ("MACD artıya döndü" if macd_dondu else "EMA9 EMA21'i kesti" if ema_kesti else "yok")),
        (e9[-1] > e21[-1], "EMA9 > EMA21" if e9[-1] > e21[-1] else "EMA9 < EMA21"),
        (r is not None and ESIK["rsi_min"] <= r <= ESIK["rsi_max"], f"RSI {r:.0f}" if r is not None else "RSI -"),
        (kat < ESIK["hacim_kat_max"], f"hacim {kat:.1f}x (henüz patlamadı)" if kat < ESIK["hacim_kat_max"] else f"hacim {kat:.1f}x"),
        (c > o, f"mum {(c / o - 1) * 100:+.1f}%" if o else "mum -"),
    ]
    puan = sum(1 for ok, _ in sartlar if ok)
    return {"puan": puan, "toplam": TOPLAM, "ok": puan == TOPLAM,
            "detay": " · ".join(("✓ " if ok else "✗ ") + t for ok, t in sartlar)}


# ---------------------------------------------------------------------- yeni erken uyarı (17.10.2026, yalnızca manuel tarama)
# Kayıtlı 87 coinin geçmişinde (15 dk 29.09–17.10, 1 saat 19.09–17.10) 4 mum içinde +%5 isabeti: 15 dk test yarısı %19,4
# (taban %1,0), 1 saat %19,8 (taban %4,7). Al-sat benzetiminde işlem başı ortalama ZARARDA (15 dk −%1,0 ile −%1,8):
# alım sinyali değil, yalnızca uyarı. Otomatik al-sat kaynağı (erken_15dk) eski 8 şartla (hesapla) kalır.
YENI_ARALIK_MUM = 36
YENI_ARALIK_PCT = 15.0


def hesapla_yeni(kap: list[float], yuksek: list[float], dusuk: list[float], acilis: list[float]) -> dict | None:
    """Son mum için 4 şart: son 36 mumun en yüksek/en düşük aralığı >= %15, EMA9 > EMA21, MACD histogramı artıda ve bir
    önceki mumdan büyük, mum yeşil. Veri yetmezse None."""
    n = len(kap)
    if n < 60 or len(yuksek) < n or len(dusuk) < n or len(acilis) < n:
        return None
    y, d = yuksek[-n:], dusuk[-n:]
    lo = min(d[-YENI_ARALIK_MUM:])
    aralik = (max(y[-YENI_ARALIK_MUM:]) / lo - 1) * 100 if lo > 0 else 0.0
    e9, e21 = ind.ema(kap, 9)[-1], ind.ema(kap, 21)[-1]
    mm, s = ind.macd(kap)
    if None in (e9, e21, s[-1], s[-2]):
        return None
    hist, hist_once = mm[-1] - s[-1], mm[-2] - s[-2]
    c, o = kap[-1], acilis[-1]
    sartlar = [
        (aralik >= YENI_ARALIK_PCT, f"36 mum aralığı %{aralik:.1f}"),
        (e9 > e21, "EMA9 > EMA21" if e9 > e21 else "EMA9 < EMA21"),
        (hist > 0 and hist > hist_once, f"MACD hist {hist:.4g}" + (" artıyor" if hist > hist_once else " artmıyor")),
        (c > o, f"mum {(c / o - 1) * 100:+.1f}%" if o else "mum -"),
    ]
    puan = sum(1 for ok, _ in sartlar if ok)
    return {"puan": puan, "toplam": 4, "ok": puan == 4, "detay": " · ".join(("✓ " if ok else "✗ ") + t for ok, t in sartlar)}