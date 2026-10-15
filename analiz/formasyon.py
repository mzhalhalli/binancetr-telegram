"""Grafik formasyonları ve mum formasyonları: long (yukarı) ve short (aşağı) kırılımlardan sonra ne oluyor?
Yalnızca herkese açık mum verisini okur. Binance TR'de açığa satış yok; "short" = aşağı kırılım (satış/uzak dur sinyali).

Grafik formasyonları (dönüş noktalarından otomatik bulunur):
  ikili tepe/dip, üçlü tepe/dip, OBO/TOBO, yükselen/alçalan/simetrik üçgen, yükselen/alçalan kama, dikdörtgen,
  genişleyen formasyon, yükselen/alçalan kanal, boğa/ayı bayrağı, fincan-kulp / ters fincan-kulp.
Dönüş noktası (zigzag): fiyat son uç noktadan K_ATR x ATR(14) ters giderse uç nokta onaylanır. Formasyon ancak son dönüş
noktası onaylandıktan sonra "bilinir"; kırılım bundan sonraki bir mumun kapanışıyla aranır (geleceğe bakılmaz).
Her kırılım için: hedef (formasyon yüksekliği kadar ölçülü hareket), zarar-kes (formasyonun karşı ucu).
Başarı = hedefe zarar-kesten önce ulaşmak. Aynı coinde rastgele mumlardan aynı hedef/zarar-kes uzaklığıyla girilseydi
ne olurdu diye karşılaştırılır (formasyonun gerçek katkısı = fark).

Mum formasyonları (yutan, çekiç, kayan yıldız, doji, sabah/akşam yıldızı, üç asker/karga, harami, delen, kara bulut,
cımbız, marubozu): sinyal mumunun kapanışından sonra 10 mum içinde önce 1 ATR lehte mi aleyhte mi gidildi.
Piyasa durumu: kırılım anında coinlerin kaçta kaçı kendi 50 mumluk ortalamasının üstünde (genişlik) ve BTC_TRY ortalamanın üstünde mi.
Sonuç: analiz/sonuc/formasyon.json (CIKTI ile değiştirilebilir)
"""
from __future__ import annotations

import json
import math
import os
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view as pencere

KOK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KOK))

import telegram_tarama as tt  # noqa: E402

TR = timezone(timedelta(hours=3))
IVS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000, "1w": 604_800_000}
GUNLER = {"15m": 30, "1h": 180, "4h": 730, "1d": 1500, "1w": 3000}
ARALIKLAR = [a for a in (os.environ.get("ARALIKLAR") or "15m,1h,4h,1d,1w").split(",") if a]
CIKTI = os.environ.get("CIKTI") or "formasyon.json"
STABIL = {"USDT", "USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "EUR", "EURI", "AEUR", "BFUSD", "USDE", "USD1", "RLUSD", "XUSD"}
K_ATR = 3.0          # dönüş noktası eşiği
TOL = 0.8            # "aynı seviye" toleransı (ATR)
RASTGELE = 8         # her kırılım için rastgele karşılaştırma girişi
MUM_ILERI = 10
PIYASA_MA = 50       # piyasa genişliği: kapanışı kendi MA50'sinin üstünde olan coin oranı

ADLAR = {
    "ikili_tepe": ("İkili tepe", "short"), "ikili_dip": ("İkili dip", "long"),
    "uclu_tepe": ("Üçlü tepe", "short"), "uclu_dip": ("Üçlü dip", "long"),
    "obo": ("Omuz-baş-omuz (OBO)", "short"), "tobo": ("Ters omuz-baş-omuz (TOBO)", "long"),
    "yukselen_ucgen": ("Yükselen üçgen", "long"), "alcalan_ucgen": ("Alçalan üçgen", "short"),
    "simetrik_ucgen": ("Simetrik üçgen", "iki"), "yukselen_kama": ("Yükselen kama", "short"),
    "alcalan_kama": ("Alçalan kama", "long"), "dikdortgen": ("Dikdörtgen", "iki"),
    "genisleyen": ("Genişleyen formasyon", "iki"), "yukselen_kanal": ("Yükselen kanal", "iki"),
    "alcalan_kanal": ("Alçalan kanal", "iki"), "boga_bayragi": ("Boğa bayrağı / flama", "long"),
    "ayi_bayragi": ("Ayı bayrağı / flama", "short"), "fincan_kulp": ("Fincan-kulp", "long"),
    "ters_fincan_kulp": ("Ters fincan-kulp", "short"),
}
MUM_ADLAR = {
    "yutan_boga": ("Yutan boğa (bullish engulfing)", "long"), "yutan_ayi": ("Yutan ayı (bearish engulfing)", "short"),
    "cekic": ("Çekiç (hammer)", "long"), "ters_cekic": ("Ters çekiç (inverted hammer)", "long"),
    "kayan_yildiz": ("Kayan yıldız (shooting star)", "short"), "asili_adam": ("Asılı adam (hanging man)", "short"),
    "yusufcuk_doji": ("Yusufçuk doji (dragonfly)", "long"), "mezar_tasi_doji": ("Mezar taşı doji (gravestone)", "short"),
    "sabah_yildizi": ("Sabah yıldızı (morning star)", "long"), "aksam_yildizi": ("Akşam yıldızı (evening star)", "short"),
    "uc_asker": ("Üç beyaz asker", "long"), "uc_karga": ("Üç kara karga", "short"),
    "harami_boga": ("Boğa harami", "long"), "harami_ayi": ("Ayı harami", "short"),
    "delen": ("Delen mum (piercing line)", "long"), "kara_bulut": ("Kara bulut (dark cloud cover)", "short"),
    "cimbiz_dip": ("Cımbız dip (tweezer bottom)", "long"), "cimbiz_tepe": ("Cımbız tepe (tweezer top)", "short"),
    "marubozu_boga": ("Boğa marubozu", "long"), "marubozu_ayi": ("Ayı marubozu", "short"),
}


# ---------------------------------------------------------------------- veri
def mumlar(bn: str, aralik: str, bas_ms: int, bit_ms: int) -> list:
    iv = IVS[aralik]
    out, t = {}, bas_ms
    while t < bit_ms:
        rows = tt._api("/api/v3/klines", {"symbol": bn, "interval": aralik, "startTime": t, "limit": 1000})
        if not rows:
            break
        for r in rows:
            out[int(r[0])] = r
        t = int(rows[-1][0]) + iv
        if len(rows) < 1000:
            break
    return [out[k] for k in sorted(out) if int(out[k][6]) < bit_ms]


def atr_dizi(h, l, c, n=14) -> np.ndarray:
    out = np.full(len(c), np.nan)
    if len(c) <= n:
        return out
    tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
    a = tr[:n].mean()
    out[n] = a
    for i in range(n, len(tr)):
        a = (a * (n - 1) + tr[i]) / n
        out[i + 1] = a
    return out


# ---------------------------------------------------------------------- dönüş noktaları
def zigzag(h, l, atr, k=K_ATR) -> list[tuple]:
    """(idx, fiyat, 'H'/'L', onay_idx, onaydaki ATR) listesi, sırayla H ve L değişir."""
    n = len(h)
    piv = []
    bas = next((i for i in range(n) if not math.isnan(atr[i])), None)
    if bas is None:
        return piv
    yon = 0
    hi_i, hi_p, lo_i, lo_p = bas, h[bas], bas, l[bas]
    for i in range(bas + 1, n):
        a = atr[i]
        if yon == 0:
            if h[i] > hi_p:
                hi_i, hi_p = i, h[i]
            if l[i] < lo_p:
                lo_i, lo_p = i, l[i]
            if h[i] - lo_p >= k * a and lo_i < i:
                piv.append((lo_i, lo_p, "L", i, a))
                yon, ex_i, ex_p = 1, i, h[i]
            elif hi_p - l[i] >= k * a and hi_i < i:
                piv.append((hi_i, hi_p, "H", i, a))
                yon, ex_i, ex_p = -1, i, l[i]
        elif yon == 1:
            if h[i] > ex_p:
                ex_i, ex_p = i, h[i]
            elif ex_p - l[i] >= k * a:
                piv.append((ex_i, ex_p, "H", i, a))
                yon, ex_i, ex_p = -1, i, l[i]
        else:
            if l[i] < ex_p:
                ex_i, ex_p = i, l[i]
            elif h[i] - ex_p >= k * a:
                piv.append((ex_i, ex_p, "L", i, a))
                yon, ex_i, ex_p = 1, i, h[i]
    return piv


def cizgi(i1, p1, i2, p2):
    e = (p2 - p1) / (i2 - i1) if i2 != i1 else 0.0
    return lambda t: p1 + e * (t - i1)


# ---------------------------------------------------------------------- grafik formasyonları
def formasyonlar(P: list, j: int) -> list[dict]:
    """P[:j+1] onaylı dönüş noktaları; son nokta P[j]. Bu anda tamamlanmış formasyonlar.
    Her biri: ad, ust/alt çizgi (fonksiyon) ya da None, geçersiz seviye, yükseklik, zarar-kes, başlangıç."""
    out = []
    a = P[j][4]
    tol = TOL * a
    son = P[j]

    def nok(m):
        return P[j - m + 1:j + 1] if j - m + 1 >= 0 else None

    # ikili / üçlü tepe ve dip, OBO / TOBO
    q = nok(4)
    if q and son[2] == "H":
        L0, H1, L2, H3 = q
        if abs(H1[1] - H3[1]) <= tol and L0[1] < L2[1]:
            out.append({"ad": "ikili_tepe", "alt": cizgi(L2[0], L2[1], L2[0] + 1, L2[1]), "ust": None,
                        "gecersiz_ust": max(H1[1], H3[1]), "yuk": max(H1[1], H3[1]) - L2[1],
                        "stop_short": max(H1[1], H3[1]), "bas": L0[0]})
    if q and son[2] == "L":
        H0, L1, H2, L3 = q
        if abs(L1[1] - L3[1]) <= tol and H0[1] > H2[1]:
            out.append({"ad": "ikili_dip", "ust": cizgi(H2[0], H2[1], H2[0] + 1, H2[1]), "alt": None,
                        "gecersiz_alt": min(L1[1], L3[1]), "yuk": H2[1] - min(L1[1], L3[1]),
                        "stop_long": min(L1[1], L3[1]), "bas": H0[0]})
        # fincan-kulp: iki kenar aynı seviyede, derin çanak, sığ ve kısa kulp
        D = min(H0[1], H2[1]) - L1[1]
        if (abs(H0[1] - H2[1]) <= 1.5 * tol and D > 0 and H2[1] - L3[1] <= 0.5 * D and L3[1] > L1[1] + 0.5 * D
                and (H2[0] - H0[0]) >= 3 * (L3[0] - H2[0])):
            seviye = max(H0[1], H2[1])
            out.append({"ad": "fincan_kulp", "ust": cizgi(H2[0], seviye, H2[0] + 1, seviye), "alt": None,
                        "gecersiz_alt": L3[1], "yuk": seviye - L1[1], "stop_long": L3[1], "bas": H0[0]})
    if q and son[2] == "H":
        L0, H1, L2, H3 = q
        D = H1[1] - max(L0[1], L2[1])          # ters fincan: kenarlar dip, kubbe tepe, kulp küçük tepki
        if (abs(L0[1] - L2[1]) <= 1.5 * tol and D > 0 and H3[1] - L2[1] <= 0.5 * D and H3[1] < H1[1] - 0.5 * D
                and (L2[0] - L0[0]) >= 3 * (H3[0] - L2[0])):
            seviye = min(L0[1], L2[1])
            out.append({"ad": "ters_fincan_kulp", "alt": cizgi(L2[0], seviye, L2[0] + 1, seviye), "ust": None,
                        "gecersiz_ust": H3[1], "yuk": H1[1] - seviye, "stop_short": H3[1], "bas": L0[0]})
    q = nok(6)
    if q and son[2] == "H":
        L0, H1, L2, H3, L4, H5 = q
        tepeler = [H1[1], H3[1], H5[1]]
        if max(tepeler) - min(tepeler) <= 1.5 * tol and L0[1] < min(L2[1], L4[1]):
            sev = min(L2[1], L4[1])
            out = [x for x in out if x["ad"] != "ikili_tepe"]
            out.append({"ad": "uclu_tepe", "alt": cizgi(L4[0], sev, L4[0] + 1, sev), "ust": None,
                        "gecersiz_ust": max(tepeler), "yuk": statistics.fmean(tepeler) - sev, "stop_short": max(tepeler), "bas": L0[0]})
        elif (H3[1] > max(H1[1], H5[1]) + tol and abs(H1[1] - H5[1]) <= 2 * tol and L0[1] < min(L2[1], L4[1])
              and abs(L2[1] - L4[1]) <= 0.5 * (H3[1] - max(L2[1], L4[1]))):
            boyun = cizgi(L2[0], L2[1], L4[0], L4[1])
            out.append({"ad": "obo", "alt": boyun, "ust": None, "gecersiz_ust": H5[1], "yuk": H3[1] - boyun(H3[0]),
                        "stop_short": H5[1], "bas": L0[0]})
    if q and son[2] == "L":
        H0, L1, H2, L3, H4, L5 = q
        dipler = [L1[1], L3[1], L5[1]]
        if max(dipler) - min(dipler) <= 1.5 * tol and H0[1] > max(H2[1], H4[1]):
            sev = max(H2[1], H4[1])
            out = [x for x in out if x["ad"] != "ikili_dip"]
            out.append({"ad": "uclu_dip", "ust": cizgi(H4[0], sev, H4[0] + 1, sev), "alt": None,
                        "gecersiz_alt": min(dipler), "yuk": sev - statistics.fmean(dipler), "stop_long": min(dipler), "bas": H0[0]})
        elif (L3[1] < min(L1[1], L5[1]) - tol and abs(L1[1] - L5[1]) <= 2 * tol and H0[1] > max(H2[1], H4[1])
              and abs(H2[1] - H4[1]) <= 0.5 * (min(H2[1], H4[1]) - L3[1])):
            boyun = cizgi(H2[0], H2[1], H4[0], H4[1])
            out.append({"ad": "tobo", "ust": boyun, "alt": None, "gecersiz_alt": L5[1], "yuk": boyun(L3[0]) - L3[1],
                        "stop_long": L5[1], "bas": H0[0]})

    # üçgen, kama, dikdörtgen, genişleyen, kanal: son 4 nokta (2 tepe, 2 dip)
    q = nok(4)
    if q:
        H = [x for x in q if x[2] == "H"]
        L = [x for x in q if x[2] == "L"]
        Ha, Hb = H
        La, Lb = L
        ust, alt = cizgi(Ha[0], Ha[1], Hb[0], Hb[1]), cizgi(La[0], La[1], Lb[0], Lb[1])
        du, dl = Hb[1] - Ha[1], Lb[1] - La[1]
        b0, b1 = q[0][0], q[-1][0]
        g0, g1 = ust(b0) - alt(b0), ust(b1) - alt(b1)
        if g0 > 0 and g1 > 0:
            yatay_u, yatay_l = abs(du) <= tol, abs(dl) <= tol
            yaklasan, acilan = g1 < 0.75 * g0, g1 > 1.25 * g0
            ad = None
            if yatay_u and dl > tol:
                ad = "yukselen_ucgen"
            elif du < -tol and yatay_l:
                ad = "alcalan_ucgen"
            elif du < -tol and dl > tol:
                ad = "simetrik_ucgen"
            elif yatay_u and yatay_l:
                ad = "dikdortgen"
            elif du > tol and dl < -tol:
                ad = "genisleyen"
            elif du > tol and dl > tol:
                ad = "yukselen_kama" if yaklasan else (None if acilan else "yukselen_kanal")
            elif du < -tol and dl < -tol:
                ad = "alcalan_kama" if yaklasan else (None if acilan else "alcalan_kanal")
            if ad:
                out.append({"ad": ad, "ust": ust, "alt": alt, "yuk": max(Ha[1], Hb[1]) - min(La[1], Lb[1]),
                            "stop_long": Lb[1], "stop_short": Hb[1], "bas": b0})

    # bayrak / flama: güçlü direk, ardından direğin yarısından az geri çekilme
    q = nok(3)
    if q and j >= 8:
        bacaklar = [abs(P[m][1] - P[m - 1][1]) for m in range(max(1, j - 10), j)]
        ort_bacak = statistics.median(bacaklar) if bacaklar else 0
        A, B, C = q
        direk = abs(B[1] - A[1])
        if direk >= 2.0 * ort_bacak and abs(C[1] - B[1]) <= 0.5 * direk and (C[0] - B[0]) <= 1.5 * (B[0] - A[0]) + 5:
            if A[2] == "L":
                out.append({"ad": "boga_bayragi", "ust": cizgi(B[0], B[1], B[0] + 1, B[1]), "alt": None,
                            "gecersiz_alt": C[1], "yuk": direk, "stop_long": C[1], "bas": A[0]})
            else:
                out.append({"ad": "ayi_bayragi", "alt": cizgi(B[0], B[1], B[0] + 1, B[1]), "ust": None,
                            "gecersiz_ust": C[1], "yuk": direk, "stop_short": C[1], "bas": A[0]})
    return out


def sonuc_olc(h, l, c, e, yon, hedef, stop, H) -> tuple | None:
    """(kazandı 1 / kaybetti -1 / süre doldu 0, plan getirisi %). Aynı mumda ikisi birden: kayıp."""
    n = len(c)
    if e + H >= n:
        return None
    hh, ll = h[e + 1:e + H + 1], l[e + 1:e + H + 1]
    if yon > 0:
        kz, kb = np.flatnonzero(hh >= hedef), np.flatnonzero(ll <= stop)
    else:
        kz, kb = np.flatnonzero(ll <= hedef), np.flatnonzero(hh >= stop)
    iz = kz[0] if len(kz) else H + 1
    ib = kb[0] if len(kb) else H + 1
    if iz < ib:
        return 1, yon * (hedef / c[e] - 1) * 100
    if ib <= H:
        return -1, yon * (stop / c[e] - 1) * 100
    return 0, yon * (c[e + H] / c[e] - 1) * 100


def kirilimlar(h, l, c, P):
    """Formasyon kırılımları, bulundukları sırayla: (formasyon, kırılım mumu, yön, hedef, zarar-kes, hedef %, zarar-kes %, süre).
    Kırılım yalnızca formasyon bilindikten sonraki kapanışlarla aranır (geleceğe bakılmaz)."""
    n = len(c)
    gorulen = set()
    for j in range(len(P)):
        t0 = P[j][3]
        for f in formasyonlar(P, j):
            span = max(5, P[j][0] - f["bas"])
            W = int(min(max(10, span), 150))
            H = int(min(max(10, 2 * span), 150))
            kirilim = None
            for t in range(P[j][0] + 1, min(n, t0 + W + 1)):
                if f.get("ust") is not None and c[t] > f["ust"](t):
                    kirilim = (t, 1)
                    break
                if f.get("alt") is not None and c[t] < f["alt"](t):
                    kirilim = (t, -1)
                    break
                if "gecersiz_ust" in f and c[t] > f["gecersiz_ust"]:
                    break
                if "gecersiz_alt" in f and c[t] < f["gecersiz_alt"]:
                    break
            if not kirilim:
                continue
            t, yon = kirilim
            if t < t0:                        # kırılım formasyon bilinmeden oldu: onay mumunda hâlâ kırık mı?
                cizgi_f = f["ust"] if yon > 0 else f["alt"]
                if (yon > 0 and c[t0] <= cizgi_f(t0)) or (yon < 0 and c[t0] >= cizgi_f(t0)):
                    continue
                t = t0
            anahtar = (f["ad"], yon, t)
            if anahtar in gorulen:
                continue
            gorulen.add(anahtar)
            seviye = (f["ust"] if yon > 0 else f["alt"])(t)
            hedef = seviye + yon * f["yuk"]
            stop = f.get("stop_long" if yon > 0 else "stop_short")
            if stop is None or (hedef - c[t]) * yon <= 0 or (c[t] - stop) * yon <= 0:
                continue
            hp, sp_ = yon * (hedef / c[t] - 1) * 100, yon * (1 - stop / c[t]) * 100
            if sp_ > 40 or hp > 300:
                continue
            yield f, t, yon, hedef, stop, hp, sp_, H


def rastgele_kiyas(h, l, c, yon, hp, sp_, H, rng) -> list[tuple]:
    """Aynı coinde rastgele mumlardan aynı hedef/zarar-kes uzaklığıyla giriş sonuçları."""
    n = len(c)
    rs = []
    for r in rng.integers(30, n - H - 1, RASTGELE) if n - H - 1 > 30 else []:
        x = sonuc_olc(h, l, c, int(r), yon, c[r] * (1 + yon * hp / 100), c[r] * (1 - yon * sp_ / 100), H)
        if x:
            rs.append(x)
    return rs


def grafik_olaylari(rows: list, rng) -> list[dict]:
    o = np.array([float(r[1]) for r in rows])
    h = np.array([float(r[2]) for r in rows])
    l = np.array([float(r[3]) for r in rows])
    c = np.array([float(r[4]) for r in rows])
    q = np.array([float(r[7]) for r in rows])
    ts = np.array([int(r[0]) for r in rows], dtype=np.int64)
    n = len(c)
    if n < 60:
        return []
    atr = atr_dizi(h, l, c)
    P = zigzag(h, l, atr)
    q20 = np.full(n, np.nan)
    if n > 21:
        cs = np.concatenate(([0.0], np.cumsum(q)))
        q20[21:] = (cs[21:n] - cs[1:n - 20]) / 20      # önceki 20 mumun ortalaması
    olay = []
    for f, t, yon, hedef, stop, hp, sp_, H in kirilimlar(h, l, c, P):
        s = sonuc_olc(h, l, c, t, yon, hedef, stop, H)
        if s is None:
            continue
        rs = rastgele_kiyas(h, l, c, yon, hp, sp_, H, rng)
        if not rs:
            continue
        olay.append({"ad": f["ad"], "yon": yon, "ts": int(ts[t]), "sonuc": s[0], "getiri": s[1],
                     "r_kazanc": statistics.fmean(1 if x[0] == 1 else 0 for x in rs),
                     "r_getiri": statistics.fmean(x[1] for x in rs),
                     "hedef_pct": hp, "stop_pct": sp_, "hacim_kat": float(q[t] / q20[t]) if q20[t] and q20[t] > 0 else None,
                     "fiyat": float(c[t]), "hedef": float(hedef), "stop": float(stop), "H": H})
    return olay


# ---------------------------------------------------------------------- mum formasyonları
def mum_olaylari(rows: list) -> dict:
    o = np.array([float(r[1]) for r in rows])
    h = np.array([float(r[2]) for r in rows])
    l = np.array([float(r[3]) for r in rows])
    c = np.array([float(r[4]) for r in rows])
    n = len(c)
    if n < 40:
        return {}
    atr = atr_dizi(h, l, c)
    govde = c - o
    mg = np.abs(govde)
    boy = h - l
    ust_f = h - np.maximum(o, c)
    alt_f = np.minimum(o, c) - l
    yesil, kirmizi = govde > 0, govde < 0
    ort_govde = np.full(n, np.nan)
    cs = np.concatenate(([0.0], np.cumsum(mg)))
    ort_govde[10:] = (cs[10:n] - cs[:n - 10]) / 10
    getiri = np.full(n, np.nan)
    getiri[10:] = c[10:] / c[:-10] - 1          # önceki 10 mumun getirisi: trend
    dusus, yukselis = getiri < -0.0, getiri > 0.0

    def g(x, k):
        out = np.full(n, np.nan)
        if 0 < k < n:
            out[k:] = x[:-k]
        return out
    o1, c1, h1, l1, g1, mg1 = g(o, 1), g(c, 1), g(h, 1), g(l, 1), g(govde, 1), g(mg, 1)
    o2, c2, g2, mg2 = g(o, 2), g(c, 2), g(govde, 2), g(mg, 2)
    dus1, yuk1 = g(dusus.astype(float), 1) == 1, g(yukselis.astype(float), 1) == 1      # tek mumlu: önceki mumda trend
    dus2, yuk2 = g(dusus.astype(float), 2) == 1, g(yukselis.astype(float), 2) == 1      # iki mumlu
    dus3, yuk3 = g(dusus.astype(float), 3) == 1, g(yukselis.astype(float), 3) == 1      # üç mumlu
    with np.errstate(invalid="ignore", divide="ignore"):
        buyuk = mg >= ort_govde
        F = {
            "yutan_boga": dus2 & (g1 < 0) & yesil & (c >= o1) & (o <= c1) & (mg > mg1),
            "yutan_ayi": yuk2 & (g1 > 0) & kirmizi & (c <= o1) & (o >= c1) & (mg > mg1),
            "cekic": dus1 & (alt_f >= 2 * mg) & (ust_f <= 0.3 * np.maximum(mg, 1e-12) + 0.1 * boy) & (boy > 0),
            "asili_adam": yuk1 & (alt_f >= 2 * mg) & (ust_f <= 0.3 * np.maximum(mg, 1e-12) + 0.1 * boy) & (boy > 0),
            "ters_cekic": dus1 & (ust_f >= 2 * mg) & (alt_f <= 0.3 * np.maximum(mg, 1e-12) + 0.1 * boy) & (boy > 0),
            "kayan_yildiz": yuk1 & (ust_f >= 2 * mg) & (alt_f <= 0.3 * np.maximum(mg, 1e-12) + 0.1 * boy) & (boy > 0),
            "yusufcuk_doji": dus1 & (mg <= 0.1 * boy) & (alt_f >= 0.6 * boy) & (boy > 0),
            "mezar_tasi_doji": yuk1 & (mg <= 0.1 * boy) & (ust_f >= 0.6 * boy) & (boy > 0),
            "sabah_yildizi": dus3 & (g2 < 0) & (mg2 >= g(ort_govde, 2)) & (mg1 <= 0.3 * mg2) & yesil & (c > (o2 + c2) / 2),
            "aksam_yildizi": yuk3 & (g2 > 0) & (mg2 >= g(ort_govde, 2)) & (mg1 <= 0.3 * mg2) & kirmizi & (c < (o2 + c2) / 2),
            "uc_asker": dus3 & yesil & (g1 > 0) & (g2 > 0) & (c > c1) & (c1 > c2) & (o >= o1) & (o <= c1) & (ust_f <= 0.3 * mg) & buyuk,
            "uc_karga": yuk3 & kirmizi & (g1 < 0) & (g2 < 0) & (c < c1) & (c1 < c2) & (o <= o1) & (o >= c1) & (alt_f <= 0.3 * mg) & buyuk,
            "harami_boga": dus2 & (g1 < 0) & (mg1 >= g(ort_govde, 1)) & yesil & (o >= c1) & (c <= o1) & (mg < 0.5 * mg1),
            "harami_ayi": yuk2 & (g1 > 0) & (mg1 >= g(ort_govde, 1)) & kirmizi & (o <= c1) & (c >= o1) & (mg < 0.5 * mg1),
            "delen": dus2 & (g1 < 0) & (mg1 >= g(ort_govde, 1)) & yesil & (o <= c1) & (c > (o1 + c1) / 2) & (c < o1),
            "kara_bulut": yuk2 & (g1 > 0) & (mg1 >= g(ort_govde, 1)) & kirmizi & (o >= c1) & (c < (o1 + c1) / 2) & (c > o1),
            "cimbiz_dip": dus2 & (g1 < 0) & yesil & (np.abs(l - l1) <= 0.1 * atr),
            "cimbiz_tepe": yuk2 & (g1 > 0) & kirmizi & (np.abs(h - h1) <= 0.1 * atr),
            "marubozu_boga": yesil & (mg >= 0.95 * boy) & buyuk & (boy > 0),
            "marubozu_ayi": kirmizi & (mg >= 0.95 * boy) & buyuk & (boy > 0),
        }
        # sonraki 10 mumda önce +1 ATR mı, -1 ATR mı?
        kaz_l = np.zeros(n, np.int8)
        kaz_s = np.zeros(n, np.int8)
        if n > MUM_ILERI + 1:
            wh, wl = pencere(h[1:], MUM_ILERI), pencere(l[1:], MUM_ILERI)
            m = n - MUM_ILERI
            cc, aa = c[:m, None], atr[:m, None]
            yu, as_ = wh[:m] >= cc + aa, wl[:m] <= cc - aa
            iu = np.where(yu.any(1), yu.argmax(1), MUM_ILERI + 1)
            ia = np.where(as_.any(1), as_.argmax(1), MUM_ILERI + 1)
            kaz_l[:m] = np.where(iu < ia, 1, np.where(ia <= MUM_ILERI, -1, 0))
            kaz_s[:m] = np.where(ia < iu, 1, np.where(iu <= MUM_ILERI, -1, 0))
            ileri = np.full(n, np.nan)
            ileri[:m] = (c[MUM_ILERI:] / c[:m] - 1) * 100
        gecerli = ~np.isnan(atr) & (np.arange(n) < n - MUM_ILERI)
    out = {}
    for ad, m in F.items():
        yon = 1 if MUM_ADLAR[ad][1] == "long" else -1
        mm = m & gecerli
        kz = kaz_l if yon > 0 else kaz_s
        out[ad] = {"n": int(mm.sum()), "kazan": int((kz[mm] == 1).sum()), "kaybet": int((kz[mm] == -1).sum()),
                   "ileri_toplam": float(np.nansum(yon * ileri[mm]))}
    # karşılaştırma: aynı trend yönündeki tüm mumlar
    for ad, maske in (("taban_dusus", dus1 & gecerli), ("taban_yukselis", yuk1 & gecerli), ("taban_hepsi", gecerli)):
        out[ad] = {"n": int(maske.sum()), "kazan_l": int((kaz_l[maske] == 1).sum()), "kaybet_l": int((kaz_l[maske] == -1).sum()),
                   "kazan_s": int((kaz_s[maske] == 1).sum()), "kaybet_s": int((kaz_s[maske] == -1).sum()),
                   "ileri_toplam": float(np.nansum(ileri[maske]))}
    return out


# ---------------------------------------------------------------------- özet
def pct(a, b):
    return round(a / b * 100, 1) if b else None


def grafik_ozet(ol: list[dict]) -> dict:
    if not ol:
        return {"n": 0}
    n = len(ol)
    return {"n": n, "basari_pct": pct(sum(x["sonuc"] == 1 for x in ol), n), "stop_pct": pct(sum(x["sonuc"] == -1 for x in ol), n),
            "rastgele_basari_pct": round(statistics.fmean(x["r_kazanc"] for x in ol) * 100, 1),
            "plan_ort_pct": round(statistics.fmean(x["getiri"] for x in ol), 2),
            "rastgele_plan_ort_pct": round(statistics.fmean(x["r_getiri"] for x in ol), 2),
            "hedef_pct_medyan": round(statistics.median(x["hedef_pct"] for x in ol), 2),
            "stop_pct_medyan": round(statistics.median(x["stop_pct"] for x in ol), 2)}


def main():
    t0 = time.time()
    sem = [s for s in tt.semboller() if s["s"].split("_")[0] not in STABIL]
    sonuc = {"olusturma": datetime.now(TR).strftime("%d.%m.%Y %H:%M"), "coin": len(sem), "ayar": {"K_ATR": K_ATR, "TOL": TOL},
             "adlar": ADLAR, "mum_adlar": MUM_ADLAR, "araliklar": {}}
    for aralik in ARALIKLAR:
        t1 = time.time()
        iv = IVS[aralik]
        bit_ms = int(time.time() * 1000) // iv * iv
        bas_ms = bit_ms - GUNLER[aralik] * 86_400_000
        grafik, mum, hata, mum_say, piyasa_ham = [], {}, [], [], []
        btc = {}
        kilit = threading.Lock()

        def bir(s):
            try:
                rows = mumlar(s["bn"], aralik, bas_ms, bit_ms)
                rng = np.random.default_rng(sum(map(ord, s["s"])) * 7 + len(aralik))
                mum_say.append(len(rows))
                if len(rows) > PIYASA_MA:
                    kp = np.array([float(r[4]) for r in rows])
                    ma = np.convolve(kp, np.ones(PIYASA_MA) / PIYASA_MA, "valid")
                    ts_ = np.array([int(r[0]) for r in rows[PIYASA_MA - 1:]], dtype=np.int64)
                    ust = (kp[PIYASA_MA - 1:] > ma).astype(float)
                    piyasa_ham.append((ts_, ust))
                    if s["s"] == "BTC_TRY":
                        btc.update(zip(ts_.tolist(), (ust > 0).tolist()))
                for x in grafik_olaylari(rows, rng):
                    x["s"] = s["s"]
                    grafik.append(x)
                mo = mum_olaylari(rows)
                with kilit:
                    for ad, d in mo.items():
                        t = mum.setdefault(ad, {})
                        for k, v in d.items():
                            t[k] = t.get(k, 0) + v
            except Exception as e:
                hata.append(f"{s['s']}: {type(e).__name__}: {e}")

        with ThreadPoolExecutor(8) as ex:
            list(ex.map(bir, sem))
        genislik = {}
        if piyasa_ham:
            tum_ts = np.concatenate([a for a, _ in piyasa_ham])
            u, ters = np.unique(tum_ts, return_inverse=True)
            toplam = np.bincount(ters)
            yukari = np.bincount(ters, weights=np.concatenate([b for _, b in piyasa_ham]))
            genislik = {int(k): float(y / n) for k, y, n in zip(u, yukari, toplam) if n >= 50}
        for x in grafik:
            x["genislik"] = genislik.get(x["ts"])
            x["btc_ust"] = btc.get(x["ts"])
        ts_hepsi = sorted(x["ts"] for x in grafik)
        orta = ts_hepsi[len(ts_hepsi) // 2] if ts_hepsi else 0

        def yarili(ol):
            return {**grafik_ozet(ol), "ilk_yari": grafik_ozet([x for x in ol if x["ts"] < orta]),
                    "ikinci_yari": grafik_ozet([x for x in ol if x["ts"] >= orta])}
        G = {}
        for ad in ADLAR:
            for yon, yad in ((1, "long"), (-1, "short")):
                ol = [x for x in grafik if x["ad"] == ad and x["yon"] == yon]
                if not ol:
                    continue
                G.setdefault(ad, {})[yad] = {
                    **grafik_ozet(ol),
                    "ilk_yari": grafik_ozet([x for x in ol if x["ts"] < orta]),
                    "ikinci_yari": grafik_ozet([x for x in ol if x["ts"] >= orta]),
                    "hacimli": grafik_ozet([x for x in ol if (x["hacim_kat"] or 0) >= 1.5]),
                    "hacimsiz": grafik_ozet([x for x in ol if (x["hacim_kat"] or 0) < 1.5]),
                    "piyasa_guclu": yarili([x for x in ol if x["genislik"] is not None and x["genislik"] >= 0.5]),
                    "piyasa_zayif": yarili([x for x in ol if x["genislik"] is not None and x["genislik"] < 0.5]),
                    "btc_ust": yarili([x for x in ol if x["btc_ust"] is True]),
                    "btc_alt": yarili([x for x in ol if x["btc_ust"] is False]),
                }
        M = {}
        for ad, (isim, yad) in MUM_ADLAR.items():
            d = mum.get(ad)
            if not d:
                continue
            taban = mum["taban_dusus" if ad in ("yutan_boga", "cekic", "ters_cekic", "yusufcuk_doji", "sabah_yildizi",
                                                "uc_asker", "harami_boga", "delen", "cimbiz_dip") else
                        "taban_yukselis" if ad not in ("marubozu_boga", "marubozu_ayi") else "taban_hepsi"]
            k = "l" if yad == "long" else "s"
            M[ad] = {"n": d["n"], "kazan_pct": pct(d["kazan"], d["n"]), "kaybet_pct": pct(d["kaybet"], d["n"]),
                     "ileri10_ort_pct": round(d["ileri_toplam"] / d["n"], 3) if d["n"] else None,
                     "taban_kazan_pct": pct(taban[f"kazan_{k}"], taban["n"]), "taban_kaybet_pct": pct(taban[f"kaybet_{k}"], taban["n"]),
                     "taban_ileri10_ort_pct": round((1 if yad == "long" else -1) * taban["ileri_toplam"] / taban["n"], 3) if taban["n"] else None}
        son = sorted(grafik, key=lambda x: x["ts"])[-60:]
        sonuc["araliklar"][aralik] = {
            "bas": datetime.fromtimestamp(bas_ms / 1000, TR).strftime("%d.%m.%Y"), "bit": datetime.fromtimestamp(bit_ms / 1000, TR).strftime("%d.%m.%Y"),
            "mum": sum(mum_say), "hata": len(hata), "hata_ornek": hata[:3], "kirilim": len(grafik),
            "orta": datetime.fromtimestamp(orta / 1000, TR).strftime("%d.%m.%Y") if orta else None,
            "grafik": G, "mum_formasyonu": M,
            "son_ornekler": [{"s": x["s"], "ad": x["ad"], "yon": "long" if x["yon"] > 0 else "short",
                              "zaman": datetime.fromtimestamp(x["ts"] / 1000, TR).strftime("%d.%m.%Y %H:%M"),
                              "fiyat": x["fiyat"], "hedef": float(f"{x['hedef']:.6g}"), "stop": float(f"{x['stop']:.6g}"),
                              "sonuc": {1: "hedef", -1: "zarar-kes", 0: "süre doldu"}[x["sonuc"]]} for x in son],
            "sure_sn": round(time.time() - t1)}
        print(aralik, len(grafik), "kırılım", round(time.time() - t1), "sn", flush=True)
    sonuc["sure_sn"] = round(time.time() - t0)
    out = KOK / "analiz" / "sonuc"
    out.mkdir(parents=True, exist_ok=True)
    (out / CIKTI).write_text(json.dumps(sonuc, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
