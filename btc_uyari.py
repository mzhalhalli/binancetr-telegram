"""BTC "GÜÇLÜ AL" / "GÜÇLÜ SAT" Telegram uyarısı (16.10.2026, kullanıcı isteği).

BTC_TRY'nin 15 dk, 1 saat ve 4 saat göstergesi (PC botundaki btr/btc_trend.py ile aynı hesap) üçü birden GÜÇLÜ AL ya da
ÇOK GÜÇLÜ AL (tutarlılık >= %60) olduğunda Telegram'a "🚨 BTC GERÇEK GÜÇLÜ AL DURUMUNDA" mesajı gider; üçü birden GÜÇLÜ SAT
ya da ÇOK GÜÇLÜ SAT olduğunda "🚨 BTC GERÇEK GÜÇLÜ SAT DURUMUNDA".
Mesaj yalnızca durum YENİ oluşunca bir kez gider; üçü aynı kaldıkça tekrar gitmez, bozulup yeniden oluşursa yine gider.
Durum btc_uyari.json'da tutulur (yalnızca değişince yazılır, iş akışı commit eder).

Gösterge (btc_trend.py):
- Her zaman diliminde son kapanmış muma göre 8 sinyal (+1 / -1 / 0): kapanış-EMA20, EMA20-EMA50, EMA50-EMA200,
  MACD-sinyal, RSI14 (>55 / <45), Bollinger orta bandı, 20 mum en yüksek / en düşük kırılımı, formasyon yukarı kırılımı.
- Ağırlık = (geçmiş yön isabeti - 0,5) x 2, işaretli (15 dk son 90 gün, 1 saat ve 4 saat son 365 gün; isabet = sonraki H mum).
- Skor = toplam(ağırlık x sinyal) / toplam(|ağırlık|). >= 0 AL, < 0 SAT. Güç = |skor| x 100:
  %0-39 çok zayıf, %40-59 zayıf, %60-99 güçlü, %100 çok güçlü.
Ağırlıklar her çalıştırmada yeniden hesaplanır (PC botu günde bir kez hesaplar; aynı gün içinde fark çok küçüktür).
Yalnızca herkese açık piyasa verisini okur; cüzdan bilgisi içermez.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime

from btr import formasyon as fo
from btr.indikator import ema, macd, rsi, sma
import telegram_tarama as tt

ARALIKLAR = ("15m", "1h", "4h")
AD = {"15m": "15 dk", "1h": "1 saat", "4h": "4 saat"}
MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}
GUN = {"15m": 90, "1h": 365, "4h": 365}
H = {"15m": 4, "1h": 4, "4h": 6}
ISINMA = 210
EN_AZ_ORNEK = 50
FM_GECERLI = 20
GUCLU = 60                # GÜÇLÜ AL eşiği (%)
# PC botundaki formasyon taramasıyla aynı (bulut formasyon.py'de 15 dakikalık yok; bu gösterge için PC'deki liste kullanılır)
SECILI = {"15m": ("boga_bayragi", "genisleyen", "dikdortgen", "yukselen_ucgen", "ikili_dip"),
          "1h": ("boga_bayragi", "genisleyen"), "4h": ("genisleyen", "dikdortgen", "yukselen_ucgen", "ikili_dip")}
DURUM = "btc_uyari.json"
SINYALLER = [("ema20", "Kapanış / EMA20"), ("ema20_50", "EMA20 / EMA50"), ("ema50_200", "EMA50 / EMA200"),
             ("macd", "MACD / sinyal"), ("rsi", "RSI 14"), ("bb", "Bollinger orta bandı"),
             ("kirilim20", "20 mum kırılımı"), ("formasyon", "Formasyon yukarı kırılımı")]


# ---------------------------------------------------------------------- hesap (btr/btc_trend.py ile aynı)
def _isaret(a, b) -> int:
    if a is None or b is None:
        return 0
    return 1 if a > b else -1 if a < b else 0


def formasyon_olaylari(h, l, c, aralik):
    secili = SECILI.get(aralik)
    n = len(c)
    if not secili or n < 60:
        return []
    a = fo.atr(h, l, c)
    P = fo.zigzag(h, l, a)
    out, gorulen = [], set()
    for j in range(len(P)):
        t0 = P[j][3]
        for f in fo.formasyonlar(P, j):
            if f["ad"] not in secili:
                continue
            W, _ = fo._pencere(P, j, f)
            kir = None
            for t in range(P[j][0] + 1, min(n, t0 + W + 1)):
                if c[t] > f["ust"](t):
                    kir = t
                    break
                if f.get("alt") is not None and c[t] < f["alt"](t):
                    break
                if "gecersiz_alt" in f and c[t] < f["gecersiz_alt"]:
                    break
            if kir is None:
                continue
            t = kir
            if t < t0:
                if c[t0] <= f["ust"](t0):
                    continue
                t = t0
            if (f["ad"], t) in gorulen:
                continue
            gorulen.add((f["ad"], t))
            seviye = f["ust"](t)
            out.append((t, seviye, f["stop"], seviye + f["yuk"]))
    return out


def sinyaller(h, l, c, aralik):
    n = len(c)
    e20, e50, e200 = ema(c, 20), ema(c, 50), ema(c, 200)
    m, ms = macd(c)
    r = rsi(c, 14)
    orta = sma(c, 20)
    out = {
        "ema20": [_isaret(c[i], e20[i]) for i in range(n)],
        "ema20_50": [_isaret(e20[i], e50[i]) for i in range(n)],
        "ema50_200": [_isaret(e50[i], e200[i]) for i in range(n)],
        "macd": [_isaret(m[i], ms[i]) for i in range(n)],
        "rsi": [0 if r[i] is None else 1 if r[i] > 55 else -1 if r[i] < 45 else 0 for i in range(n)],
        "bb": [_isaret(c[i], orta[i]) for i in range(n)],
    }
    k20 = [0] * n
    for i in range(20, n):
        if c[i] > max(h[i - 20:i]):
            k20[i] = 1
        elif c[i] < min(l[i - 20:i]):
            k20[i] = -1
    out["kirilim20"] = k20
    fm = [0] * n
    for t, seviye, stop, hedef in formasyon_olaylari(h, l, c, aralik):
        for i in range(t, min(n, t + FM_GECERLI + 1)):
            if c[i] > seviye and c[i] > stop and c[i] < hedef:
                fm[i] = 1
    out["formasyon"] = fm
    return out


def isabet(sinyal, c, ufuk, bas=0):
    dogru = adet = 0
    for i in range(bas, len(c) - ufuk):
        s = sinyal[i]
        if not s:
            continue
        adet += 1
        if (c[i + ufuk] - c[i]) * s > 0:
            dogru += 1
    return (dogru / adet if adet else None), adet


def agirlik(oran, adet):
    if oran is None or adet < EN_AZ_ORNEK:
        return 0.0
    return round((oran - 0.5) * 2, 4)


def _agirlik_tablosu(sy, c, ufuk, bas, son=None):
    out = {}
    for k, _ in SINYALLER:
        o, n = isabet(sy[k][:son], c[:son], ufuk, bas)
        out[k] = {"agirlik": agirlik(o, n), "isabet": None if o is None else round(o * 100, 1), "n": n}
    return out


def skor(sinyal, agr):
    w = {k: (agr.get(k) or {}).get("agirlik", 0.0) for k in sinyal}
    top = sum(abs(v) for v in w.values())
    if top <= 0:
        return None
    return sum(w[k] * v for k, v in sinyal.items()) / top


def dogrulama(sy, c, ufuk, bas):
    n = len(c)
    orta = bas + (n - bas) // 2
    w = _agirlik_tablosu(sy, c, ufuk, bas, orta)
    dogru = adet = yukari = toplam = 0
    for i in range(orta, n - ufuk):
        toplam += 1
        yukari += c[i + ufuk] > c[i]
        s = skor({k: sy[k][i] for k in w}, w)
        if not s:
            continue
        adet += 1
        dogru += (c[i + ufuk] - c[i]) * s > 0
    isa = round(100 * dogru / adet, 1) if adet >= EN_AZ_ORNEK else None
    hep = round(100 * max(yukari, toplam - yukari) / toplam, 1) if toplam else None
    return {"isabet": isa, "hep_ayni": hep, "hep_yon": "alis" if 2 * yukari >= toplam else "satis"}


def seviye(yon, guc):
    if yon not in ("alis", "satis") or guc is None:
        return None
    k = "ÇOK GÜÇLÜ" if guc >= 100 else "GÜÇLÜ" if guc >= 60 else "ZAYIF" if guc >= 40 else "ÇOK ZAYIF"
    return f"{k} {'AL' if yon == 'alis' else 'SAT'}"


def hesapla(rows: list, aralik: str) -> dict:
    """Kapanmış mumlardan (eskiden yeniye) son mumun durumu: yon, guc, seviye, kapanis, mum_ts, dogrulama."""
    h, l, c = ([float(r[i]) for r in rows] for i in (2, 3, 4))
    sy = sinyaller(h, l, c, aralik)
    agr = _agirlik_tablosu(sy, c, H[aralik], ISINMA)
    s = skor({k: v[-1] for k, v in sy.items()}, agr)
    if s is None:
        return {"yon": "yok", "guc": None, "seviye": None, "kapanis": c[-1], "mum_ts": int(float(rows[-1][0]))}
    yon = "alis" if s >= 0 else "satis"
    guc = round(abs(s) * 100)
    return {"yon": yon, "guc": guc, "seviye": seviye(yon, guc), "kapanis": c[-1], "mum_ts": int(float(rows[-1][0])),
            "dogrulama": dogrulama(sy, c, H[aralik], ISINMA)}


def guclu(d: dict, yon: str) -> bool:
    return d.get("yon") == yon and (d.get("guc") or 0) >= GUCLU


# ---------------------------------------------------------------------- veri ve mesaj
def gecmis(aralik: str, now_ms: int) -> list:
    """Son GUN[aralik] gün + ISINMA kapanmış BTCTRY mumu (1000'lik sayfalar, eskiden yeniye)."""
    p = MS[aralik]
    mum = GUN[aralik] * 86_400_000 // p + ISINMA
    bas = int(now_ms - (mum + 1) * p)
    tekil = {}
    while bas < now_ms - p:
        parca = tt._api("/api/v3/klines", {"symbol": "BTCTRY", "interval": aralik, "startTime": bas, "limit": 1000})
        parca = [r for r in parca if isinstance(r, list) and len(r) >= 7]
        if not parca:
            break
        for r in parca:
            if int(r[6]) < now_ms:
                tekil[int(r[0])] = r
        yeni = int(parca[-1][0]) + p
        if yeni <= bas:
            break
        bas = yeni
        time.sleep(0.2)
    return [tekil[k] for k in sorted(tekil)]


def _tl(x: float) -> str:
    return f"{x:,.0f}".replace(",", ".") if x >= 1000 else f"{x:.2f}".replace(".", ",")


def _y(x) -> str:
    return "-" if x is None else f"%{x:.1f}".replace(".", ",")


def mesaj(d: dict[str, dict], now_ms: int, durum: str = "al") -> str:
    zaman = datetime.fromtimestamp(now_ms / 1000, tt.TR).strftime("%d.%m.%Y %H:%M")
    satir = [f"🚨🔴 BTC GERÇEK GÜÇLÜ {durum.upper()} DURUMUNDA 🔴🚨", f"BTC_TRY · {zaman} (TR)", ""]
    for a in ARALIKLAR:
        satir.append(f"🔴 {AD[a]}: {d[a]['seviye']} %{d[a]['guc']}")
    satir += ["", f"Fiyat: {_tl(d['15m']['kapanis'])} TL (son kapanan 15 dk mum)", "",
              "Geçmiş isabet (örnek dışı deneme; parantezde hep aynı yön):"]
    for a in ARALIKLAR:
        g = d[a].get("dogrulama") or {}
        satir.append(f"• {AD[a]}: {_y(g.get('isabet'))} ({_y(g.get('hep_ayni'))})")
    satir.append("Güç %: göstergelerin aynı yönü gösterme oranı.")
    return "\n".join(satir)


def _durum_oku() -> dict:
    try:
        with open(DURUM, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def ortak_durum(d: dict[str, dict]) -> str | None:
    """"al": üçü GÜÇLÜ/ÇOK GÜÇLÜ AL, "sat": üçü GÜÇLÜ/ÇOK GÜÇLÜ SAT, None: ikisi de değil."""
    if all(guclu(d[a], "alis") for a in ARALIKLAR):
        return "al"
    if all(guclu(d[a], "satis") for a in ARALIKLAR):
        return "sat"
    return None


def main():
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    now_ms = int(time.time() * 1000)
    d = {}
    for a in ARALIKLAR:
        rows = gecmis(a, now_ms)
        if len(rows) < ISINMA + 100:
            print(f"BTC {AD[a]}: yeterli mum yok ({len(rows)}), uyarıya bakılmadı")
            return
        d[a] = hesapla(rows, a)
        print(f"BTC {AD[a]}: {d[a]['seviye']} %{d[a]['guc']} (mum {d[a]['mum_ts']}, {len(rows)} mum)")
    simdi = ortak_durum(d)
    onceki = _durum_oku().get("durum")
    if simdi == onceki:
        print(f"BTC ortak durum değişmedi ({simdi or 'yok'}), mesaj atılmadı")
        return
    if simdi:
        m = mesaj(d, now_ms, simdi)
        print(m)
        if not token or not chat:
            print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID tanımlı değil; mesaj gönderilmedi.", file=sys.stderr)
            sys.exit(1)
        tt.gonder(token, chat, m)
    else:
        print(f"BTC güçlü {onceki} durumu sona erdi (mesaj yok)")
    with open(DURUM, "w", encoding="utf-8") as f:
        json.dump({"durum": simdi, "ts": now_ms // 1000,
                   "kutular": {a: {"seviye": d[a]["seviye"], "guc": d[a]["guc"]} for a in ARALIKLAR}}, f, ensure_ascii=False)


if __name__ == "__main__":
    main()
