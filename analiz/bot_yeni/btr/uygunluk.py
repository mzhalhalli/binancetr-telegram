"""İşlem uygunluk yüzdesi: tarama sonucundaki coinde yükseliş yönündeki göstergelerden kaçı olumlu.

Her coin için aynı kontrol listesi (seçilen mum üzerinde): mum rengi, Bollinger orta bant, EMA9/EMA21, EMA50, MACD,
RSI 50-70, hacim, piyasa genişliği, BTC_TRY ve (1 saat / 4 saatte) formasyon kırılımı.
Yüzde = olumlu kontrol / hesaplanabilen kontrol. Bir özet göstergedir; başarı olasılığı değildir, geçmişte ölçülmedi.
"""
from __future__ import annotations

from . import indikator as ind
from .formasyon import GUCLU_PIYASA

RSI_MIN, RSI_MAX = 50.0, 70.0
HACIM_N = 20


def _k(kod: str, ad: str, ok: bool | None, deger: str) -> dict:
    return {"k": kod, "ad": ad, "ok": ok, "deger": deger}


def _yuzde(x: float, isaret=True) -> str:
    return (f"{x:+.1f}" if isaret else f"{x:.1f}").replace(".", ",") + "%"


def coin(acilis: list[float], kapanis: list[float], hacim: list[float]) -> list[dict]:
    """Coinin kendi mumlarıyla hesaplanan kontroller (son mum = seçilen mum)."""
    f, o = kapanis[-1], acilis[-1]
    out = [_k("yesil", "Son mum yeşil", f > o, f"mum {_yuzde((f / o - 1) * 100)}" if o else "mum -")]
    b = ind.bollinger(kapanis, 21, 2.0)
    out.append(_k("bb_orta", "Bollinger orta bandının üstünde", f > b[0] if b else None,
                  f"ortaya göre {_yuzde((f / b[0] - 1) * 100)}" if b else "yetersiz mum"))
    e9, e21 = ind.ema(kapanis, 9)[-1], ind.ema(kapanis, 21)[-1]
    out.append(_k("ema_kisa", "EMA9 EMA21'in üstünde (kısa vade yükselişte)", e9 > e21 if e21 is not None else None,
                  ("EMA9 > EMA21" if e9 > e21 else "EMA9 < EMA21") if e21 is not None else "yetersiz mum"))
    e50 = ind.ema(kapanis, 50)[-1]
    out.append(_k("ema50", "Fiyat EMA50'nin üstünde (orta vade yükselişte)", f > e50 if e50 is not None else None,
                  f"EMA50'ye göre {_yuzde((f / e50 - 1) * 100)}" if e50 else "yetersiz mum"))
    m, s = ind.macd(kapanis)
    out.append(_k("macd", "MACD sinyalin üstünde", m[-1] > s[-1] if s[-1] is not None else None,
                  ("MACD > sinyal" if m[-1] > s[-1] else "MACD < sinyal") if s[-1] is not None else "yetersiz mum"))
    r = ind.rsi(kapanis, 14)[-1]
    out.append(_k("rsi", "RSI 50-70 arası (güçlü, aşırı alımda değil)", RSI_MIN <= r <= RSI_MAX if r is not None else None,
                  f"RSI {r:.0f}" if r is not None else "yetersiz mum"))
    if len(hacim) >= HACIM_N + 1:
        ort = sum(hacim[-HACIM_N - 1:-1]) / HACIM_N
        kat = hacim[-1] / ort if ort > 0 else 0.0
        out.append(_k("hacim", "Hacim 20 mum ortalamasının üstünde", kat > 1, f"hacim {kat:.1f}x".replace(".", ",")))
    else:
        out.append(_k("hacim", "Hacim 20 mum ortalamasının üstünde", None, "yetersiz mum"))
    return out


def piyasa(genislik_pct: float | None, btc_ust: bool | None) -> list[dict]:
    return [
        _k("piyasa", "Piyasa güçlü (coinlerin yarısından çoğu MA50 üstünde)",
           genislik_pct >= GUCLU_PIYASA if genislik_pct is not None else None,
           f"MA50 üstündeki coin oranı %{genislik_pct:.0f}" if genislik_pct is not None else "hesaplanamadı"),
        _k("btc", "BTC_TRY MA50'nin üstünde", btc_ust, "üstünde" if btc_ust else "altında" if btc_ust is False else "bilinmiyor"),
    ]


def formasyon(fm: dict | None) -> dict:
    """1 saat / 4 saat: geçmişte rastgeleden iyi çıkan formasyonlardan biri, piyasa koşulu uyarken yukarı kırıldı mı."""
    kir = (fm or {}).get("kirilim") or []
    iyi = [f for f in kir if f.get("uygun") is not False]
    if iyi:
        return _k("formasyon", "Formasyon kırılımı (piyasa koşulu uygun)", True, ", ".join(f["baslik"] for f in iyi))
    return _k("formasyon", "Formasyon kırılımı (piyasa koşulu uygun)", False,
              ", ".join(f["baslik"] for f in kir) + " (piyasa koşulu uymuyor)" if kir else "yok")


def ozet(kontroller: list[dict]) -> dict:
    hesaplanan = [x for x in kontroller if x["ok"] is not None]
    olumlu = sum(1 for x in hesaplanan if x["ok"])
    return {"pct": round(olumlu / len(hesaplanan) * 100) if hesaplanan else None, "olumlu": olumlu,
            "toplam": len(hesaplanan), "kontroller": kontroller}
