"""Sert yükselişleri zamanında yakalama denemesi. Yalnızca herkese açık mum verisini okur.

Soru: 15 dakikalık mum kapanışını beklemeden (1 dakikalık mumlarla) ya da farklı kurallarla sert yükselişe
zamanında girilebilir mi, girilirse kârlı mı?

Veri: son GUN günde tüm TRY çiftlerinin 1 dakikalık mumları; 15 dakikalık mumlar bunlardan toplanır.
Kurallar (sinyalden sonra aynı coinde 60 dakika yeni sinyal sayılmaz):
- ani_<k>dk_<x>_<m>x : son k dakikada fiyat +%x ve üstü, bu k dakikanın hacmi önceki 60 dakikanın dakika ortalamasının
  m katı ve üstü (bot her dakika bakabilir: mum kapanışını beklemez)
- tepe24_<m>x : fiyat son 24 saatin tepesini aştı ve bu dakikanın hacmi ortalamanın m katı
- momentum_hacim / trend_hacim : 15 dakikalık mum kapanışında (yukselis_profili.py ELLE kuralları)
- rastgele : rastgele dakikalar (kıyas)
Ölçüm (giriş = sinyal dakikasının kapanışı, 240 dakika içinde, 1 dakikalık mumlarla):
- kâr al / zarar kes çiftleri: hangisi önce; aynı dakikada ikisi birden = zarar kes; hiçbiri = 240. dakika kapanışı.
  Sonuçtan %0,2 işlem ücreti düşülür.
- sert yükseliş yakalama: 15 dakikalık sert mum (kapanış: gövde +%5; fitil: tepe +%5; ikisinde de hacim 5 kat)
  içinde ya da 15 dakika öncesinde sinyal var mı, girişten sonra yükselişin ne kadarı kaldı.
İlk 2/3 gün (eğitim) ve son 1/3 gün (deneme) ayrı yazılır: iki dönemde de iyi olan kural tesadüf olma ihtimali düşük.
Sonuç: analiz/sonuc/yakalama.json ve .md
"""
from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Lock

import numpy as np

KOK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KOK))
sys.path.insert(0, str(KOK / "analiz"))

import telegram_tarama as tt  # noqa: E402
import yukselis_profili as yp  # noqa: E402

DK = 60_000
IV15 = 900_000
GUN = int(os.environ.get("GUN", "30"))
ISINMA_DK = 5000          # 15 dakikalık indikatörler (320 mum) ve 24 saatlik tepe için
UFUK = 240                # dakika
BEKLE = 60                # aynı coinde iki sinyal arası en az (dakika)
KOMISYON = 0.2            # alış + satış, %
YOLLAR = {"kar2_zarar5": (2, 5), "kar3_zarar2": (3, 2), "kar5_zarar3": (5, 3)}
# sabit hedef yerine: tepeden geri çekilince sat (iz süren zarar-kes; başta -%5) ya da süre dolunca sat
CIKISLAR = {"iz2": 2.0, "iz3": 3.0, "iz5": 5.0, "sure15": 15, "sure60": 60}
ANI = [(k, x, m) for k in (1, 3, 5) for x in (1.5, 2.0, 3.0) for m in (5, 10)]
TEPE = (5, 10)
KURALLAR = [f"ani_{k}dk_{x:g}_{m}x" for k, x, m in ANI] + [f"tepe24_{m}x" for m in TEPE] + ["momentum_hacim", "trend_hacim", "rastgele"]
OLAYLAR = {"kapanis": "15 dk mum gövdesi +%5 ve üstü, hacim 5 kat", "fitil": "15 dk mumun tepesi açılışın +%5 üstü, hacim 5 kat"}


def mumlar_1m(bn: str, bas: int, bit: int) -> np.ndarray:
    out, t = {}, bas
    while t < bit:
        for deneme in range(5):
            try:
                rows = tt._api("/api/v3/klines", {"symbol": bn, "interval": "1m", "startTime": t, "limit": 1000})
                break
            except Exception:
                if deneme == 4:
                    raise
                time.sleep(2 * (deneme + 1))
        if not rows:
            break
        for r in rows:
            out[int(r[0])] = r
        t = int(rows[-1][0]) + DK
        if len(rows) < 1000:
            break
    rows = [out[k] for k in sorted(out) if int(out[k][6]) < bit]
    return np.array([[float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[7])] for r in rows]) if rows else np.zeros((0, 6))


def ani_kosul(o, c, q, k, x, m):
    with np.errstate(all="ignore"):
        ret = (c / yp.geri(c, k) - 1) * 100
        hacim_k = yp.ort(q, k) * k
        taban = yp.geri(yp.ort(q, 60), k)               # sinyal penceresinden önceki 60 dakika
        yesil = c > (yp.geri(o, k - 1) if k > 1 else o)       # pencere boyunca yükselmiş
        return (ret >= x) & (taban > 0) & (hacim_k >= m * k * taban) & yesil


def tepe_kosul(c, h, q, m):
    with np.errstate(all="ignore"):
        tepe = yp.geri(yp.pmax(h, 1440), 1)
        taban = yp.geri(yp.ort(q, 60), 1)
        return (c > tepe) & (taban > 0) & (q >= m * taban)


def seyrelt(idx: np.ndarray) -> list[int]:
    out, son = [], -10**9
    for i in idx:
        if i - son >= BEKLE:
            out.append(int(i))
            son = i
    return out


def sonuc(h, l, c, i) -> dict | None:
    if i + UFUK >= len(c):
        return None
    g = c[i]
    hh, ll = h[i + 1:i + UFUK + 1], l[i + 1:i + UFUK + 1]
    r = {"max60": (hh[:60].max() / g - 1) * 100}
    for ad, (tp, sl) in YOLLAR.items():
        a = np.flatnonzero(hh >= g * (1 + tp / 100))
        z = np.flatnonzero(ll <= g * (1 - sl / 100))
        a = a[0] if len(a) else UFUK + 1
        z = z[0] if len(z) else UFUK + 1
        if a < z:
            r[ad] = (1, tp - KOMISYON)
        elif z <= UFUK:
            r[ad] = (-1, -sl - KOMISYON)
        else:
            r[ad] = (0, (c[i + UFUK] / g - 1) * 100 - KOMISYON)
    for ad, v in CIKISLAR.items():
        if ad.startswith("sure"):
            deg = (c[i + v] / g - 1) * 100 - KOMISYON
        else:
            deg = iz_sur(hh, ll, c[i + UFUK], g, v)
        r[ad] = (1 if deg > 0 else -1 if deg < 0 else 0, deg)
    return r


def iz_sur(hh, ll, son, g, geri_pct, ilk_sl=5.0) -> float:
    """Giriş g; her dakika zarar-kes = max(girişin -%ilk_sl'si, görülen en yüksek fiyatın -%geri_pct'si).
    Dakikanın düşüğü zarar-kese değerse o fiyattan satılır (o dakikanın tepesi henüz sayılmaz: temkinli)."""
    tepe = g
    for x, y in zip(hh, ll):
        stop = max(g * (1 - ilk_sl / 100), tepe * (1 - geri_pct / 100))
        if y <= stop:
            return (stop / g - 1) * 100 - KOMISYON
        if x > tepe:
            tepe = x
    return (son / g - 1) * 100 - KOMISYON


def bos_toplam():
    return {"n": 0, "gun": set(), "max60": 0.0, "max60_5": 0, **{ad: {"kar": 0, "zarar": 0, "toplam": 0.0} for ad in (*YOLLAR, *CIKISLAR)}}


def analiz(a: np.ndarray, bas_ms: int, ayrim: int, rng) -> tuple[dict, dict]:
    ts, o, h, l, c, q = a.T
    n = len(c)
    T = {kural: {"egitim": bos_toplam(), "deneme": bos_toplam()} for kural in KURALLAR}
    Y = {ev: {"egitim": {"olay": 0, **{k: {"zamaninda": 0, "once": 0, "kalan": 0.0, "tam": 0.0} for k in KURALLAR}},
              "deneme": {"olay": 0, **{k: {"zamaninda": 0, "once": 0, "kalan": 0.0, "tam": 0.0} for k in KURALLAR}}}
         for ev in OLAYLAR}
    if n < ISINMA_DK:
        return T, Y
    kosul = {}
    for k, x, m in ANI:
        kosul[f"ani_{k}dk_{x:g}_{m}x"] = ani_kosul(o, c, q, k, x, m)
    for m in TEPE:
        kosul[f"tepe24_{m}x"] = tepe_kosul(c, h, q, m)
    # 15 dakikalık mumlar (yalnızca 15 dakikası tam olanlar)
    bas15 = (ts // IV15) * IV15
    gruplar = {}
    for j, b in enumerate(bas15):
        gruplar.setdefault(int(b), []).append(j)
    rows15, son_dk = [], []
    for b in sorted(gruplar):
        js = gruplar[b]
        if len(js) < 15:
            continue
        rows15.append([b, o[js[0]], h[js].max(), l[js].min(), c[js[-1]], 0, b + IV15 - 1, q[js].sum(), 0, 0, 0])
        son_dk.append(js[-1])
    son_dk = np.array(son_dk)
    F = yp.ozellik(rows15)["F"] if len(rows15) > 400 else None
    for ad in ("momentum_hacim", "trend_hacim"):
        dk = np.zeros(n, dtype=bool)
        if F is not None:
            with np.errstate(invalid="ignore"):
                s = np.ones(len(rows15), dtype=bool)
                for alan, op, deg in yp.ELLE[ad]:
                    s &= (F[alan] >= deg) if op == ">=" else (F[alan] <= deg) if op == "<=" else (F[alan] == deg)
            dk[son_dk[s]] = True
        kosul[ad] = dk
    rast = np.zeros(n, dtype=bool)
    rast[rng.integers(0, 97) + np.arange(0, n, 97)[:-1]] = True
    kosul["rastgele"] = rast
    gecerli = ts >= bas_ms
    for kural, kos in kosul.items():
        for i in seyrelt(np.flatnonzero(kos & gecerli)):
            r = sonuc(h, l, c, i)
            if r is None:
                continue
            t = T[kural]["egitim" if ts[i] < ayrim else "deneme"]
            t["n"] += 1
            t["gun"].add(int(ts[i] // 86_400_000))
            t["max60"] += r["max60"]
            t["max60_5"] += r["max60"] >= 5
            for ad in (*YOLLAR, *CIKISLAR):
                kod, deg = r[ad]
                t[ad]["kar"] += kod == 1
                t[ad]["zarar"] += kod == -1
                t[ad]["toplam"] += deg
    # sert yükseliş olayları
    q15 = np.array([r[7] for r in rows15])
    o15, h15, c15 = (np.array([r[j] for r in rows15]) for j in (1, 2, 4))
    q20 = yp.geri(yp.ort(q15, 20), 1)
    with np.errstate(invalid="ignore"):
        olay = {"kapanis": ((c15 / o15 - 1) * 100 >= 5) & (q15 >= 5 * q20),
                "fitil": ((h15 / o15 - 1) * 100 >= 5) & (q15 >= 5 * q20)}
    ilk_dk = son_dk - 14
    for ev, ok in olay.items():
        for e in np.flatnonzero(ok):
            if rows15[e][0] < bas_ms or e == 0:
                continue
            y = Y[ev]["egitim" if rows15[e][0] < ayrim else "deneme"]
            y["olay"] += 1
            s0, s1 = ilk_dk[e], son_dk[e]
            tam = (h15[e] / o15[e] - 1) * 100
            for kural, kos in kosul.items():
                if kural == "rastgele":
                    continue
                if kos[max(0, s0 - 15):s0].any():
                    y[kural]["once"] += 1
                    continue
                w = np.flatnonzero(kos[s0:s1 + 1])
                if kural in ("momentum_hacim", "trend_hacim") or not len(w):
                    continue            # 15 dakikalık kurallar ancak mum kapanınca sinyal verir: o zaman yükseliş bitmiştir
                j = s0 + w[0]
                y[kural]["zamaninda"] += 1
                y[kural]["kalan"] += (h15[e] / c[j] - 1) * 100
                y[kural]["tam"] += tam
    return T, Y


def birlestir(A, B):
    for k, v in B.items():
        if isinstance(v, dict):
            birlestir(A[k], v)
        elif isinstance(v, set):
            A[k] |= v
        else:
            A[k] += v


def ozet_t(t) -> dict:
    n = t["n"]
    out = {"sinyal": n, "gunluk": round(n / max(1, len(t["gun"])), 1), "max60_ort": round(t["max60"] / n, 2) if n else None,
           "max60_5_pct": round(t["max60_5"] / n * 100, 1) if n else None}
    for ad in (*YOLLAR, *CIKISLAR):
        x = t[ad]
        out[ad] = {"kar_pct": round(x["kar"] / n * 100, 1) if n else None, "zarar_pct": round(x["zarar"] / n * 100, 1) if n else None,
                   "ort_sonuc": round(x["toplam"] / n, 2) if n else None}
    return out


def main():
    t0 = time.time()
    bit_ms = int(time.time() // 60) * DK
    bas_ms = bit_ms - GUN * 86_400_000
    ayrim = bas_ms + int((bit_ms - bas_ms) * 2 / 3)
    sem = [s for s in tt.semboller() if s["s"].split("_")[0] not in yp.STABIL]
    T = {kural: {"egitim": bos_toplam(), "deneme": bos_toplam()} for kural in KURALLAR}
    Y = {ev: {"egitim": {"olay": 0, **{k: {"zamaninda": 0, "once": 0, "kalan": 0.0, "tam": 0.0} for k in KURALLAR}},
              "deneme": {"olay": 0, **{k: {"zamaninda": 0, "once": 0, "kalan": 0.0, "tam": 0.0} for k in KURALLAR}}}
         for ev in OLAYLAR}
    kilit, hata = Lock(), []

    def bir(ix_s):
        ix, s = ix_s
        try:
            a = mumlar_1m(s["bn"], bas_ms - ISINMA_DK * DK, bit_ms)
            t, y = analiz(a, bas_ms, ayrim, np.random.default_rng(ix))
            with kilit:
                birlestir(T, t)
                birlestir(Y, y)
        except Exception as e:
            hata.append(f"{s['s']}: {type(e).__name__}: {e}"[:200])

    with ThreadPoolExecutor(8) as ex:
        list(ex.map(bir, enumerate(sem)))
    tarih = lambda ms: datetime.fromtimestamp(ms / 1000, yp.TR).strftime("%d.%m.%Y %H:%M")
    out = {"olusturma": datetime.now(yp.TR).strftime("%d.%m.%Y %H:%M"), "gun": GUN, "coin": len(sem), "hata": len(hata),
           "hata_ornek": hata[:5], "egitim": f"{tarih(bas_ms)} – {tarih(ayrim)}", "deneme": f"{tarih(ayrim)} – {tarih(bit_ms)}",
           "yollar": YOLLAR, "cikislar": CIKISLAR, "olaylar": OLAYLAR, "kurallar": {}, "yakalama": {}}
    for kural in KURALLAR:
        out["kurallar"][kural] = {d: ozet_t(T[kural][d]) for d in ("egitim", "deneme")}
    for ev in OLAYLAR:
        out["yakalama"][ev] = {}
        for d in ("egitim", "deneme"):
            y = Y[ev][d]
            e = {"olay": y["olay"]}
            for kural in KURALLAR:
                if kural == "rastgele":
                    continue
                x = y[kural]
                e[kural] = {"zamaninda_pct": round(x["zamaninda"] / y["olay"] * 100, 1) if y["olay"] else None,
                            "once_pct": round(x["once"] / y["olay"] * 100, 1) if y["olay"] else None,
                            "kalan_ort": round(x["kalan"] / x["zamaninda"], 2) if x["zamaninda"] else None,
                            "tam_ort": round(x["tam"] / x["zamaninda"], 2) if x["zamaninda"] else None}
            out["yakalama"][ev][d] = e
    out["sure_sn"] = round(time.time() - t0)
    d = KOK / "analiz" / "sonuc"
    d.mkdir(parents=True, exist_ok=True)
    (d / "yakalama.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k not in ("kurallar", "yakalama")}, ensure_ascii=False))
    for kural, v in out["kurallar"].items():
        print(kural, json.dumps(v, ensure_ascii=False))


if __name__ == "__main__":
    main()
