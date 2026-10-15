"""Sert yükseliş profili: yükselişten önce ve yükseliş sırasında MA, EMA, RSI, MACD, Bollinger ve hacim neye benziyor?
Yalnızca herkese açık mum verisini okur.

1) Son GUN günde tüm TRY çiftlerinin ARALIK mumlarını okur, her mum için indikatörleri hesaplar.
2) Sert yükseliş mumu: gövde +%5 ve üstü, hacim önceki 20 mumun ortalamasının 5 katı ve üstü (erken_backtest ile aynı).
   Yükselişten 4, 2, 1 mum önceki ve yükseliş mumundaki değerler tüm mumlarla (taban) karşılaştırılır.
3) Her indikatörün her dilimi için "sonraki 4 mumda sert yükseliş" oranı tabanla kıyaslanır (kat = kaldıraç).
4) Kural araması: ilk 2/3 günde kural seçilir, son 1/3 günde (kuralın hiç görmediği veride) sınanır.
5) analiz/ornekler.json'daki örneklerin (kullanıcının gösterdiği yükselişler) mum mum değerleri yazılır.
Sonuç: analiz/sonuc/yukselis_<ARALIK>.json
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view as pencere

KOK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KOK))

import telegram_tarama as tt  # noqa: E402
from btr import indikator as ind  # noqa: E402

IVS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}
ARALIK = os.environ.get("ARALIK", "15m")
IV = IVS[ARALIK]
GUN = int(os.environ.get("GUN", "30"))
ISINMA = 320           # EMA200 ve 100 mumluk bant geçmişi için önceden okunan mum
BB_N = 21              # bottaki Bollinger (21, 2) ile aynı
YAKIN, UZAK = 4, 16    # sonraki 4 mum (15 dk'da 1 saat) ve 16 mum (15 dk'da 4 saat)
TR = timezone(timedelta(hours=3))
STABIL = {"USDT", "USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "EUR", "EURI", "AEUR", "BFUSD", "USDE", "USD1", "RLUSD", "XUSD"}

ADLAR = {
    "deg": "mum değişimi %",
    "yb": "Bollinger %B (0 alt bant, 1 üst bant)",
    "bw": "Bollinger bant genişliği %",
    "bwp": "bant genişliği yüzdeliği (son 100 mum içinde, düşük = sıkışma)",
    "rsi": "RSI 14",
    "rsi_d": "RSI'nin son 4 mumdaki değişimi",
    "hist": "MACD histogramı (fiyatın %'si)",
    "e9_21": "EMA9 / EMA21 farkı %",
    "e21_50": "EMA21 / EMA50 farkı %",
    "c_e200": "fiyat / EMA200 farkı %",
    "c_ma20": "fiyat / MA20 farkı %",
    "c_ma50": "fiyat / MA50 farkı %",
    "c_ma200": "fiyat / MA200 farkı %",
    "hk": "hacim / önceki 20 mum ortalaması (kat)",
    "hk3": "son 3 mumun hacmi / ondan önceki 20 mum ortalaması (kat)",
    "islem_k": "işlem sayısı / önceki 20 mum ortalaması (kat)",
    "alici": "alıcı payı (bu mum, 0-1)",
    "alici4": "alıcı payı (son 4 mum, 0-1)",
    "aralik36": "son 36 mumun fiyat aralığı % (15 dk'da 9 saat)",
    "tepe36": "önceki 36 mumun tepesine uzaklık % (artı = tepeyi aştı)",
    "tepe96": "önceki 96 mumun tepesine uzaklık % (15 dk'da 24 saat)",
    "get16": "son 16 mum getirisi % (15 dk'da 4 saat)",
    "get96": "son 96 mum getirisi % (15 dk'da 24 saat)",
    "likit": "son 96 mumun TRY hacmi (log10; 6 = 1 milyon, 7 = 10 milyon)",
    "hist_art": "MACD histogramı artıyor",
    "hist_don": "MACD histogramı son 3 mumda artıya döndü",
    "ema_kes": "EMA9 EMA21'i son 3 mumda yukarı kesti",
    "ema_sira": "EMA9 > EMA21 > EMA50",
    "ma_sira": "MA20 > MA50 > MA200",
    "yesil": "yeşil mum",
}
MANTIK = ["hist_art", "hist_don", "ema_kes", "ema_sira", "ma_sira", "yesil"]
SAYISAL = [k for k in ADLAR if k not in MANTIK]
HEDEFLER = {"sert4": "sonraki 4 mumda sert yükseliş mumu (+%5 gövde, 5 kat hacim)",
            "max1_5": "sonraki 4 mumda en yüksek fiyat +%5 ve üstü"}


# ---------------------------------------------------------------------- veri
def mumlar(bn: str, bas_ms: int, bit_ms: int, iv: int = IV) -> list:
    out, t = {}, bas_ms
    aralik = next(k for k, v in IVS.items() if v == iv)
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


# ---------------------------------------------------------------------- yardımcı diziler
def dizi(lst) -> np.ndarray:
    return np.array([np.nan if v is None else v for v in lst], dtype=float)


def ort(x, n):
    """out[i] = x[i-n+1..i] ortalaması."""
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        cs = np.concatenate(([0.0], np.cumsum(x)))
        out[n - 1:] = (cs[n:] - cs[:-n]) / n
    return out


def geri(x, k):
    """out[i] = x[i-k] (k > 0 geçmiş, k < 0 gelecek)."""
    out = np.full(len(x), np.nan)
    if 0 < k < len(x):
        out[k:] = x[:len(x) - k]
    elif -len(x) < k < 0:
        out[:k] = x[-k:]
    return out


def pmax(x, n):
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1:] = pencere(x, n).max(1)
    return out


def pmin(x, n):
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1:] = pencere(x, n).min(1)
    return out


def ileri_max(x, k):
    """out[i] = max(x[i+1..i+k])."""
    out = np.full(len(x), np.nan)
    if len(x) > k:
        out[:len(x) - k] = pencere(x[1:], k).max(1)
    return out


def ileri_min(x, k):
    out = np.full(len(x), np.nan)
    if len(x) > k:
        out[:len(x) - k] = pencere(x[1:], k).min(1)
    return out


# ---------------------------------------------------------------------- indikatörler
def ozellik(rows: list) -> dict:
    """Her mum için (yalnızca o muma kadarki veriyle) indikatörler, sert yükseliş işareti ve sonraki hareket."""
    a = np.array([[float(r[j]) for j in (1, 2, 3, 4, 7, 8, 10)] for r in rows]) if rows else np.zeros((0, 7))
    o, h, l, c, q, nis, tb = a.T
    ts = np.array([int(r[0]) for r in rows], dtype=np.int64)
    n = len(c)
    F = {}
    with np.errstate(all="ignore"):
        F["deg"] = (c / o - 1) * 100
        orta = ort(c, BB_N)
        sd = np.full(n, np.nan)
        if n >= BB_N:
            sd[BB_N - 1:] = pencere(c, BB_N).std(1)
        ust, alt = orta + 2 * sd, orta - 2 * sd
        F["yb"] = (c - alt) / (ust - alt)
        bw = (ust - alt) / orta * 100
        F["bw"] = bw
        bwp = np.full(n, np.nan)
        if n > 100:
            w = pencere(bw, 101)
            sira = (w[:, :-1] < w[:, -1:]).sum(1) / 100 * 100
            bwp[100:] = np.where(np.isnan(w).any(1), np.nan, sira)
        F["bwp"] = bwp
        rsi = dizi(ind.rsi(list(c), 14)) if n else np.zeros(0)
        F["rsi"] = rsi
        F["rsi_d"] = rsi - geri(rsi, 4)
        if n:
            m, s = ind.macd(list(c))
            hist = dizi(m) - dizi(s)
        else:
            hist = np.zeros(0)
        F["hist"] = hist / c * 100
        F["hist_art"] = hist > geri(hist, 1)
        F["hist_don"] = (hist > 0) & (pmin(geri(hist, 1), 3) <= 0)
        e9, e21, e50, e200 = (dizi(ind.ema(list(c), k)) if n else np.zeros(0) for k in (9, 21, 50, 200))
        F["e9_21"] = (e9 / e21 - 1) * 100
        F["e21_50"] = (e21 / e50 - 1) * 100
        F["c_e200"] = (c / e200 - 1) * 100
        F["ema_sira"] = (e9 > e21) & (e21 > e50)
        fark = e9 - e21
        F["ema_kes"] = (fark > 0) & (pmin(geri(fark, 1), 3) <= 0)
        ma20, ma50, ma200 = ort(c, 20), ort(c, 50), ort(c, 200)
        F["c_ma20"] = (c / ma20 - 1) * 100
        F["c_ma50"] = (c / ma50 - 1) * 100
        F["c_ma200"] = (c / ma200 - 1) * 100
        F["ma_sira"] = (ma20 > ma50) & (ma50 > ma200)
        q20 = geri(ort(q, 20), 1)                      # önceki 20 mumun ortalaması (bu mum hariç)
        F["hk"] = q / q20
        F["hk3"] = ort(q, 3) / geri(ort(q, 20), 3)
        F["islem_k"] = nis / geri(ort(nis, 20), 1)
        F["alici"] = np.where(q > 0, tb / q, np.nan)
        F["alici4"] = ort(tb, 4) / ort(q, 4)
        F["aralik36"] = (pmax(h, 36) - pmin(l, 36)) / c * 100
        F["tepe36"] = (c / geri(pmax(h, 36), 1) - 1) * 100
        F["tepe96"] = (c / geri(pmax(h, 96), 1) - 1) * 100
        F["get16"] = (c / geri(c, 16) - 1) * 100
        F["get96"] = (c / geri(c, 96) - 1) * 100
        F["likit"] = np.log10(ort(q, 96) * 96 + 1)
        F["yesil"] = c > o
        sert = (F["deg"] >= 5) & (q20 > 0) & (q >= 5 * q20)
        H = {
            "sert4": ileri_max(sert.astype(float), YAKIN) > 0,
            "max1": (ileri_max(h, YAKIN) / c - 1) * 100,
            "max4": (ileri_max(h, UZAK) / c - 1) * 100,
            "kap4": (geri(c, -UZAK) / c - 1) * 100,
            "dd4": (ileri_min(l, UZAK) / c - 1) * 100,
        }
    for k in MANTIK:
        F[k] = F[k].astype(float)
    return {"ts": ts, "c": c, "F": F, "sert": sert, "H": H, "tam": np.arange(n) + UZAK < n}


# ---------------------------------------------------------------------- özetler
def yuvarla(x, k=3):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), k)


def dagilim(x: np.ndarray, mantik=False) -> dict | None:
    x = x[~np.isnan(x)]
    if not len(x):
        return None
    if mantik:
        return {"n": int(len(x)), "evet_pct": yuvarla(x.mean() * 100, 1)}
    p = np.percentile(x, [10, 25, 50, 75, 90])
    return {"n": int(len(x)), "p10": yuvarla(p[0]), "p25": yuvarla(p[1]), "medyan": yuvarla(p[2]),
            "p75": yuvarla(p[3]), "p90": yuvarla(p[4])}


def wilson_alt(k, n, z=1.64):
    """İsabet oranının alt güven sınırı: az sayıda sinyale dayanan şanslı kuralları cezalandırır."""
    if n == 0:
        return 0.0
    p = k / n
    d = 1 + z * z / n
    m = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (m - r) / d


def kosul_maske(X, kos, sec=None):
    m = None
    for f, op, t in kos:
        x = X[f] if sec is None else X[f][sec]
        with np.errstate(invalid="ignore"):
            k = x >= t if op == ">=" else x <= t if op == "<=" else x == t
        m = k if m is None else (m & k)
    return m


def kosul_metin(kos) -> str:
    out = []
    for f, op, t in kos:
        if op == "==":
            out.append(ADLAR[f])
        else:
            out.append(f"{f} {'≥' if op == '>=' else '≤'} {t:.4g}")
    return " VE ".join(out)


def kural_ara(X, y, egit, gun, min_gunluk, min_poz, derinlik=4, isin=8):
    Xe = {f: X[f][egit] for f in X}
    ye = y[egit]
    taban = ye.mean()
    adaylar = []
    for f in SAYISAL:
        v = Xe[f][~np.isnan(Xe[f])]
        esikler = sorted({float(t) for t in np.percentile(v, [5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 95])})
        adaylar += [(f, op, t) for t in esikler for op in (">=", "<=")]
    adaylar += [(f, "==", 1.0) for f in MANTIK]
    kosul_cache = {}

    def km(a):
        if a not in kosul_cache:
            kosul_cache[a] = kosul_maske(Xe, [a])
        return kosul_cache[a]

    isinlar = [((), np.ones(len(ye), bool))]
    secilen = []
    for _ in range(derinlik):
        yeni = []
        for kos, m in isinlar:
            for a in adaylar:
                if any(k[0] == a[0] and k[1] == a[1] for k in kos):
                    continue
                mm = m & km(a)
                nn = int(mm.sum())
                if nn < min_gunluk * gun:
                    continue
                kk = int(ye[mm].sum())
                if kk < min_poz:
                    continue
                yeni.append((wilson_alt(kk, nn) / taban, kos + (a,), mm))
        if not yeni:
            break
        yeni.sort(key=lambda x: -x[0])
        gorulen, isinlar = set(), []
        for s, kos, mm in yeni:
            anahtar = frozenset(kos)
            if anahtar in gorulen:
                continue
            gorulen.add(anahtar)
            isinlar.append((kos, mm))
            if len(isinlar) == isin:
                break
        secilen += [kos for kos, _ in isinlar[:3]]
    del kosul_cache
    return secilen


def ilk_sinyaller(idx: np.ndarray, sym: np.ndarray, tekrar=YAKIN) -> np.ndarray:
    """Aynı coinde 4 mum içinde tekrar eden sinyaller atılır (Telegram'a gidecek olanlar)."""
    out, son = [], {}
    for i in idx:
        s = sym[i]
        if s in son and i - son[s] <= tekrar:
            son[s] = i
            continue
        son[s] = i
        out.append(i)
    return np.array(out, dtype=np.int64)


def degerlendir(m: np.ndarray, D: dict, sec: np.ndarray, gun: float, olaylar: np.ndarray) -> dict:
    """m: tüm satırlar için sinyal maskesi. sec: değerlendirilecek dönem (satır maskesi)."""
    mm = m & sec & D["tam"]
    idx = np.flatnonzero(mm)
    ilk = ilk_sinyaller(idx, D["sym"])
    H = D["H"]
    sonuc = {"sinyal": int(len(idx)), "gunluk": round(len(idx) / gun, 1), "ilk_sinyal": int(len(ilk)),
             "ilk_gunluk": round(len(ilk) / gun, 1)}
    for ad, x in (("ham", idx), ("ilk", ilk)):
        if not len(x):
            continue
        sonuc[ad] = {
            "sert4_pct": yuvarla(H["sert4"][x].mean() * 100, 2),
            "max1_5_pct": yuvarla((H["max1"][x] >= 5).mean() * 100, 1),
            "max4_5_pct": yuvarla((H["max4"][x] >= 5).mean() * 100, 1),
            "max4_10_pct": yuvarla((H["max4"][x] >= 10).mean() * 100, 1),
            "kap4_yukari_pct": yuvarla((H["kap4"][x] > 0).mean() * 100, 1),
            "kap4_ort": yuvarla(np.nanmean(H["kap4"][x]), 2),
            "dd4_medyan": yuvarla(np.nanmedian(H["dd4"][x]), 2),
        }
    ol = olaylar[sec[olaylar]]
    yak = sum(1 for j in ol if m[j - YAKIN:j].any())
    sonuc["olay"] = int(len(ol))
    sonuc["onceden_yakalanan"] = int(yak)
    sonuc["yakalama_pct"] = yuvarla(yak / len(ol) * 100, 1) if len(ol) else None
    return sonuc


def erken_kurali(X):
    """btr/erken.py'deki 8 şartın aynısı (mevcut erken uyarı)."""
    with np.errstate(invalid="ignore"):
        return ((X["bwp"] <= 40) & (X["yb"] >= 0.80) & (X["yb"] <= 1.10) & (X["hist"] > 0) & (X["hist_art"] == 1)
                & ((X["hist_don"] == 1) | (X["ema_kes"] == 1)) & (X["e9_21"] > 0) & (X["rsi"] >= 50) & (X["rsi"] <= 70)
                & (X["hk"] < 3) & (X["yesil"] == 1))


# ---------------------------------------------------------------------- kullanıcının örnekleri
def zaman_ms(metin: str) -> int:
    for bic in ("%d.%m.%Y %H:%M", "%d.%m.%y %H:%M", "%Y-%m-%d %H:%M"):
        try:
            return int(datetime.strptime(metin.strip(), bic).replace(tzinfo=TR).timestamp() * 1000)
        except ValueError:
            pass
    raise ValueError(f"tarih okunamadı: {metin}")


def ornek_analiz(o: dict) -> dict:
    iv = IVS[o.get("aralik", "15m")]
    t = zaman_ms(o["zaman"]) // iv * iv
    bn = o["s"].replace("_", "").replace("/", "").upper()
    rows = mumlar(bn, t - ISINMA * iv, t + 9 * iv, iv)
    D = ozellik(rows)
    k = int(np.searchsorted(D["ts"], t))
    if k >= len(D["ts"]) or D["ts"][k] != t:
        return {**o, "hata": "bu saatte mum bulunamadı"}
    tablo = []
    for j in range(max(0, k - 8), min(len(D["ts"]), k + 4)):
        satir = {"fark": j - k, "zaman": datetime.fromtimestamp(D["ts"][j] / 1000, TR).strftime("%d.%m %H:%M"),
                 "kapanis": yuvarla(D["c"][j], 8), "sert": bool(D["sert"][j])}
        satir.update({f: yuvarla(D["F"][f][j]) for f in ADLAR})
        tablo.append(satir)
    X1 = {f: D["F"][f][k - 1:k + 1] for f in ADLAR}
    return {**o, "erken_kurali_once": bool(erken_kurali(X1)[0]), "erken_kurali_mumda": bool(erken_kurali(X1)[1]),
            "mumlar": tablo}


def ornekler() -> list:
    dosya = KOK / "analiz" / "ornekler.json"
    out = []
    for o in json.loads(dosya.read_text(encoding="utf-8")) if dosya.exists() else []:
        try:
            out.append(ornek_analiz(o))
        except Exception as e:
            out.append({**o, "hata": str(e)})
    return out


# ---------------------------------------------------------------------- ana akış
def main():
    t0 = time.time()
    kayit = KOK / "analiz" / "sonuc"
    kayit.mkdir(parents=True, exist_ok=True)
    if os.environ.get("SADECE_ORNEK") == "1":         # yalnızca kullanıcının örnekleri (piyasa taraması yok)
        (kayit / "yukselis_ornekler.json").write_text(json.dumps(ornekler(), ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"örnekler yazıldı ({round(time.time() - t0)} sn)")
        return
    bit_ms = int(time.time() * 1000) // IV * IV
    bas_ms = bit_ms - GUN * 86_400_000
    sem = [s for s in tt.semboller() if s["s"].split("_")[0] not in STABIL]
    parcalar, hata = {}, []

    def bir(s):
        try:
            rows = mumlar(s["bn"], bas_ms - ISINMA * IV, bit_ms)
            parcalar[s["s"]] = ozellik(rows)
        except Exception as e:
            hata.append(f"{s['s']}: {e}")

    with ThreadPoolExecutor(8) as ex:
        list(ex.map(bir, sem))
    sonuc = analiz(parcalar, bas_ms, bit_ms)
    sonuc.update({"coin": len(sem), "hata": len(hata), "hata_ornek": hata[:5]})
    sonuc["ornekler"] = ornekler()
    sonuc["sure_sn"] = round(time.time() - t0)
    (kayit / f"yukselis_{ARALIK}.json").write_text(json.dumps(sonuc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in sonuc.items() if k in ("coin", "hata", "satir", "olay", "sure_sn", "kurallar")},
                     ensure_ascii=False, indent=1)[:6000])


def analiz(parcalar: dict, bas_ms: int, bit_ms: int) -> dict:
    """parcalar: sembol -> ozellik() çıktısı. Yalnızca bas_ms sonrası mumlar sayılır (öncesi ısınma)."""
    semler = sorted(parcalar)
    F = {f: [] for f in ADLAR}
    H = {k: [] for k in ("sert4", "max1", "max4", "kap4", "dd4")}
    ts, sym, tam, olay = [], [], [], []
    ofs = 0
    for si, s in enumerate(semler):
        D = parcalar[s]
        w = int(np.searchsorted(D["ts"], bas_ms))
        n = len(D["ts"]) - w
        if n <= 0:
            continue
        for f in ADLAR:
            F[f].append(D["F"][f][w:])
        for k in H:
            H[k].append(D["H"][k][w:])
        ts.append(D["ts"][w:])
        sym.append(np.full(n, si, dtype=np.int32))
        tam.append(D["tam"][w:])
        sert = D["sert"]
        for j in np.flatnonzero(sert[w:]) + w:
            if j - w >= YAKIN and j >= ISINMA // 2 and not sert[j - 1]:
                olay.append(ofs + j - w)
        ofs += n
    X = {f: np.concatenate(v) for f, v in F.items()}
    H = {k: np.concatenate(v) for k, v in H.items()}
    D = {"ts": np.concatenate(ts), "sym": np.concatenate(sym), "tam": np.concatenate(tam), "H": H}
    olay = np.array(olay, dtype=np.int64)
    N = len(D["ts"])
    gun = (bit_ms - bas_ms) / 86_400_000
    ayrim = bas_ms + int((bit_ms - bas_ms) * 2 / 3)
    egit = (D["ts"] < ayrim) & D["tam"]
    test = D["ts"] >= ayrim
    gun_e, gun_t = (ayrim - bas_ms) / 86_400_000, (bit_ms - ayrim) / 86_400_000
    taban = D["tam"]
    Y = {"sert4": H["sert4"], "max1_5": H["max1"] >= 5}

    # 1) profil: yükselişten önce ve yükseliş mumunda değerler, tabanla yan yana
    profil = {}
    for f in ADLAR:
        mt = f in MANTIK
        profil[f] = {"ad": ADLAR[f], "taban": dagilim(X[f][taban], mt)}
        for k in (-4, -2, -1, 0, 1):
            j = olay + k
            j = j[(j >= 0) & (j < N)]
            profil[f][str(k)] = dagilim(X[f][j], mt)

    # 2) kaldıraç: her indikatörün 10 diliminde sonraki 4 mumda sert yükseliş oranı
    oran_sert = H["sert4"][taban].mean()
    oran_m1 = Y["max1_5"][taban].mean()
    kaldirac = {}
    for f in SAYISAL:
        x = X[f][taban]
        ys, ym = H["sert4"][taban], Y["max1_5"][taban]
        gecerli = ~np.isnan(x)
        kenar = np.unique(np.percentile(x[gecerli], np.arange(0, 101, 10)))
        dilim = np.clip(np.searchsorted(kenar, x, side="right") - 1, 0, len(kenar) - 2)
        satirlar = []
        for b in range(len(kenar) - 1):
            sel = gecerli & (dilim == b)
            if not sel.any():
                continue
            satirlar.append({"alt": yuvarla(kenar[b]), "ust": yuvarla(kenar[b + 1]), "n": int(sel.sum()),
                             "sert4_pct": yuvarla(ys[sel].mean() * 100, 3), "sert4_kat": yuvarla(ys[sel].mean() / oran_sert, 2),
                             "max1_5_pct": yuvarla(ym[sel].mean() * 100, 2), "max1_5_kat": yuvarla(ym[sel].mean() / oran_m1, 2)})
        kaldirac[f] = satirlar
    for f in MANTIK:
        x = X[f][taban]
        kaldirac[f] = [{"deger": v, "n": int((x == v).sum()),
                        "sert4_pct": yuvarla(H["sert4"][taban][x == v].mean() * 100, 3),
                        "sert4_kat": yuvarla(H["sert4"][taban][x == v].mean() / oran_sert, 2),
                        "max1_5_pct": yuvarla(Y["max1_5"][taban][x == v].mean() * 100, 2),
                        "max1_5_kat": yuvarla(Y["max1_5"][taban][x == v].mean() / oran_m1, 2)} for v in (0.0, 1.0)]

    # 3) mevcut erken uyarı ve şartlarının yükselişten önceki mumda tutma oranı
    erk = erken_kurali(X)
    with np.errstate(invalid="ignore"):
        sartlar = {"bant dar (yüzdelik ≤ 40)": X["bwp"] <= 40, "%B 0,80-1,10": (X["yb"] >= 0.8) & (X["yb"] <= 1.1),
                   "MACD hist artı ve artıyor": (X["hist"] > 0) & (X["hist_art"] == 1),
                   "yeni dönüş (MACD/EMA)": (X["hist_don"] == 1) | (X["ema_kes"] == 1), "EMA9 > EMA21": X["e9_21"] > 0,
                   "RSI 50-70": (X["rsi"] >= 50) & (X["rsi"] <= 70), "hacim < 3 kat": X["hk"] < 3, "yeşil mum": X["yesil"] == 1}
    once = olay - 1
    erken_sart = {k: {"yukselis_oncesi_pct": yuvarla(v[once].mean() * 100, 1), "taban_pct": yuvarla(v[taban].mean() * 100, 1)}
                  for k, v in sartlar.items()}
    kiyas = {"erken_uyari_mevcut": {"egitim": degerlendir(erk, D, egit, gun_e, olay), "test": degerlendir(erk, D, test, gun_t, olay)},
             "hepsi": {"egitim": degerlendir(np.ones(N, bool), D, egit, gun_e, olay),
                       "test": degerlendir(np.ones(N, bool), D, test, gun_t, olay)}}

    # 4) kural araması (eğitim: ilk 2/3, test: son 1/3)
    kurallar = {}
    for hedef, (min_gunluk, min_poz) in {"sert4": (2, 12), "max1_5": (5, 40)}.items():
        liste = []
        for kos in kural_ara(X, Y[hedef], egit, gun_e, min_gunluk, min_poz):
            m = kosul_maske(X, kos)
            liste.append({"kural": kosul_metin(kos), "kosullar": [list(k) for k in kos],
                          "egitim": degerlendir(m, D, egit, gun_e, olay), "test": degerlendir(m, D, test, gun_t, olay)})
        kurallar[hedef] = liste

    son = sorted(olay, key=lambda j: D["ts"][j])[-60:]
    return {
        "olusturma": datetime.now(TR).strftime("%d.%m.%Y %H:%M"), "aralik": ARALIK, "gun": GUN,
        "bas": datetime.fromtimestamp(bas_ms / 1000, TR).strftime("%d.%m.%Y %H:%M"),
        "ayrim": datetime.fromtimestamp(ayrim / 1000, TR).strftime("%d.%m.%Y %H:%M"),
        "bit": datetime.fromtimestamp(bit_ms / 1000, TR).strftime("%d.%m.%Y %H:%M"),
        "satir": int(N), "olay": int(len(olay)), "hedefler": HEDEFLER,
        "taban_oran": {"sert4_pct": yuvarla(oran_sert * 100, 3), "max1_5_pct": yuvarla(oran_m1 * 100, 2)},
        "profil": profil, "kaldirac": kaldirac, "erken_sart": erken_sart, "kiyas": kiyas, "kurallar": kurallar,
        "son_olaylar": [{"s": semler[D["sym"][j]], "zaman": datetime.fromtimestamp(D["ts"][j] / 1000, TR).strftime("%d.%m %H:%M"),
                         "deg": yuvarla(X["deg"][j], 1), "hk": yuvarla(X["hk"][j], 1), "erken_once": bool(erk[j - YAKIN:j].any())}
                        for j in son],
    }


if __name__ == "__main__":
    main()
