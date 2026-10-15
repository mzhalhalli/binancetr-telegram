"""İndikatör hesapları (saf Python). Girdi: kapanış fiyatları listesi (eskiden yeniye)."""
from __future__ import annotations

import math


def sma(x: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(x)
    s = 0.0
    for i, v in enumerate(x):
        s += v
        if i >= n:
            s -= x[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def ema(x: list[float], n: int) -> list[float | None]:
    """İlk değer ilk n kapanışın ortalamasıyla başlar (TradingView ile aynı)."""
    out: list[float | None] = [None] * len(x)
    if len(x) < n:
        return out
    k = 2 / (n + 1)
    e = sum(x[:n]) / n
    out[n - 1] = e
    for i in range(n, len(x)):
        e = x[i] * k + e * (1 - k)
        out[i] = e
    return out


def bollinger(x: list[float], n=20, k=2.0):
    """(orta, üst, alt) son değerleri; veri yetmezse None. Standart sapma popülasyon (TradingView ile aynı)."""
    if len(x) < n:
        return None
    w = x[-n:]
    m = sum(w) / n
    sd = math.sqrt(sum((v - m) ** 2 for v in w) / n)
    return m, m + k * sd, m - k * sd


def rsi(x: list[float], n=14) -> list[float | None]:
    """Wilder RSI."""
    out: list[float | None] = [None] * len(x)
    if len(x) <= n:
        return out
    g = l = 0.0
    for i in range(1, n + 1):
        d = x[i] - x[i - 1]
        g += max(d, 0)
        l += max(-d, 0)
    g /= n
    l /= n
    out[n] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    for i in range(n + 1, len(x)):
        d = x[i] - x[i - 1]
        g = (g * (n - 1) + max(d, 0)) / n
        l = (l * (n - 1) + max(-d, 0)) / n
        out[i] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    return out


def macd(x: list[float], fast=12, slow=26, sig=9):
    """(macd, sinyal) serileri."""
    ef, es = ema(x, fast), ema(x, slow)
    m = [a - b if a is not None and b is not None else None for a, b in zip(ef, es)]
    vals = [v for v in m if v is not None]
    s_part = ema(vals, sig)
    s: list[float | None] = [None] * (len(m) - len(vals)) + s_part
    return m, s
