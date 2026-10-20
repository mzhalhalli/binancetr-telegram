"""Manuel tarama 4. satırı (17.10.2026): formasyon kırılımına yaklaşanlar + 9 şartın HEPSİ (kullanıcının 4 saat listesinden).

Şartlar: formasyon dikdörtgen · kapanış çizgiye ≤0,5 ATR · kapanış > EMA50 · kapanış > EMA200 · EMA50 > EMA200 ·
EMA9 > EMA21 · MACD histogramı > 0 ve bir önceki mumdan büyük · RSI 50–70 · yeşil mum. Temel 4. satır şartı (formasyon
bilinmiş, kırılmamış, bozulmamış, çizgiye ≤1 ATR, hedef/zarar-kes süzgeci) formasyon.bul()'dan gelir.
Yalnızca manuel tarama; otomatik al-sat kullanmaz. Göstergeler kayıtlı veri sınamasındaki hesapla aynı;
yakınlık = (çizgi - kapanış) / ATR14. 1 saatte bot dikdörtgen aramadığı için (formasyon.SECILI) 1 saat düğmesi sonuç vermez.
"""
from __future__ import annotations

from . import indikator as ind

SARTLAR = ["dikdortgen", "yakin05", "c_e50", "c_e200", "e50_200", "e9_21", "macd", "rsi", "yesil"]
SART_AD = {"dikdortgen": "dikdörtgen", "yakin05": "çizgiye ≤0,5 ATR", "c_e50": "kapanış > EMA50",
           "c_e200": "kapanış > EMA200", "e50_200": "EMA50 > EMA200", "e9_21": "EMA9 > EMA21",
           "macd": "MACD hist > 0 ve büyüyor", "rsi": "RSI 50–70", "yesil": "yeşil mum"}


def ozellikler(acilis, yuksek, dusuk, kapanis) -> dict | None:
    c = kapanis
    if len(c) < 60:
        return None
    e9, e21, e50, e200 = (ind.ema(c, k)[-1] for k in (9, 21, 50, 200))
    r = ind.rsi(c, 14)[-1]
    mm, ss = ind.macd(c)
    hist = mm[-1] - ss[-1] if ss[-1] is not None else None
    hist_o = mm[-2] - ss[-2] if ss[-2] is not None else None
    return {"yesil": c[-1] > acilis[-1], "e9_21": e21 is not None and e9 > e21,
            "c_e50": e50 is not None and c[-1] > e50, "c_e200": e200 is not None and c[-1] > e200,
            "e50_200": e200 is not None and e50 is not None and e50 > e200,
            "macd": hist is not None and hist_o is not None and hist > 0 and hist > hist_o,
            "rsi": r is not None and 50 <= r <= 70, "_rsi": r, "_hist": hist,
            "_e50": e50, "_e200": e200}


def sec(yaklasan: list[dict], oz: dict) -> str | None:
    """9 şartın hepsini sağlayan yaklaşan dikdörtgen varsa şart değerleri metni, yoksa None."""
    if not oz or not all(oz[k] for k in ("c_e50", "c_e200", "e50_200", "e9_21", "macd", "rsi", "yesil")):
        return None
    for f in yaklasan:
        if f["ad"] == "dikdortgen" and f.get("uzak_atr") is not None and f["uzak_atr"] <= 0.5:
            return (f"9/9 ✓ dikdörtgen · çizgiye {f['uzak_atr']:.2f} ATR (%{f.get('uzaklik_pct', 0):.1f}) · "
                    f"EMA50 {oz['_e50']:.6g} · EMA200 {oz['_e200']:.6g} · EMA9>EMA21 · MACD hist {oz['_hist']:.4g} büyüyor · "
                    f"RSI {oz['_rsi']:.0f} · yeşil mum")
    return None
