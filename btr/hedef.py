"""Olası hedefler: uyarı veren coin için yukarıdaki direnç seviyeleri (1.-4. hedef) ve zarar-kes seviyesi.
Yalnızca mum verisiyle (yüksek, düşük, kapanış) çalışır. Hedef bir tahmin değil, fiyatın önündeki seviyelerdir.

Yöntem:
- ATR(14): ortalama mum boyu (Wilder). Seviyeler arası uzaklık buna göre ayarlanır.
- Direnç: son GERI mumdaki tepe noktaları (yükseği iki yanındaki 3'er mumun yükseğinden düşük olmayan mum)
  ve dönemin en yükseği. Fiyatın en az 0,5 ATR üstündekiler, aralarında en az 1 ATR olacak şekilde sırayla seçilir.
- Yukarıda yeterli direnç yoksa (fiyat dönemin zirvesindeyse) kalan hedefler bir öncekinin 1,5 ATR üstüne konur.
- Zarar-kes: son 24 mumdaki en yakın dip (fiyatın 0,5-3 ATR altında); yoksa fiyatın 1,5 ATR altı.
"""
from __future__ import annotations

GERI = 288            # 15 dakikalıkta 3 gün
GEREKLI_MUM = GERI
ATR_N = 14
TEPE_YAN = 3
ILK_ATR, ARA_ATR, EK_ATR = 0.5, 1.0, 1.5
STOP_GERI, STOP_MIN_ATR, STOP_MAX_ATR, STOP_ATR = 24, 0.5, 3.0, 1.5
ADET = 4


def atr(yuk: list[float], dus: list[float], kap: list[float], n: int = ATR_N) -> float | None:
    if len(kap) < n + 1:
        return None
    tr = [max(yuk[i] - dus[i], abs(yuk[i] - kap[i - 1]), abs(dus[i] - kap[i - 1])) for i in range(1, len(kap))]
    a = sum(tr[:n]) / n
    for x in tr[n:]:
        a = (a * (n - 1) + x) / n
    return a


def hesapla(yuk: list[float], dus: list[float], kap: list[float], adet: int = ADET) -> dict | None:
    """Son mumun kapanışına göre hedefler. Veri yetmezse None."""
    n = len(kap)
    a = atr(yuk, dus, kap)
    if not a or a <= 0 or n < 2 * TEPE_YAN + 2:
        return None
    c = kap[-1]
    bas = max(0, n - GERI)
    k = TEPE_YAN
    seviye = {max(yuk[bas:])}
    for i in range(bas + k, n - k):
        if yuk[i] >= max(yuk[i - k:i]) and yuk[i] >= max(yuk[i + 1:i + k + 1]):
            seviye.add(yuk[i])
    hedef, kaynak, son = [], [], c
    for t in sorted(x for x in seviye if x >= c + ILK_ATR * a):
        if len(hedef) == adet:
            break
        if not hedef or t >= son + ARA_ATR * a:
            hedef.append(t)
            kaynak.append("direnç")
            son = t
    while len(hedef) < adet:
        son = son + EK_ATR * a
        hedef.append(son)
        kaynak.append("ATR")
    dipler = [min(dus[-k:])]
    for i in range(max(k, n - STOP_GERI), n - k):
        if dus[i] <= min(dus[i - k:i]) and dus[i] <= min(dus[i + 1:i + k + 1]):
            dipler.append(dus[i])
    uygun = [d for d in dipler if c - STOP_MAX_ATR * a <= d <= c - STOP_MIN_ATR * a]
    stop = max(uygun) if uygun else c - STOP_ATR * a
    return {"fiyat": c, "atr": a, "hedefler": hedef, "kaynak": kaynak, "stop": stop,
            "hedef_pct": [(t / c - 1) * 100 for t in hedef], "stop_pct": (stop / c - 1) * 100}
