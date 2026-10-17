"""Yeni 15 dakikalık erken uyarı şart seti: son 7 günün %10+ yükselişlerinden (bir sonraki mumda açılıştan en yükseğe %10+)
önceki kapanmış mumun indikatörlerine göre şart seti aranır (eğitim = son 7 gün), bir önceki 7 günde (görmediği hafta) sınanır.
Kural araması yukselis_profili.kural_ara ile aynı (Wilson alt sınırına göre en seçici şart birleşimleri, en çok DERINLIK şart).
Her set için: sinyal sayısı, yakalanan yükseliş, isabet, taban orana göre kat, sinyalden sonraki mum ve 4 saat (16 mum) hareketi.
Karşılaştırma: mevcut 8 şart (btr/erken.py). Aynı coinde 4 mum içindeki tekrar sinyaller tek sayılır.
Saatler TR, mum saati açılış saatidir. Yalnızca herkese açık mum verisi. Sonuç: analiz/sonuc/yeni_set.json
"""
from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import yukselis_profili as yp  # noqa: E402

IV = 900_000
ESIK = float(os.environ.get("ESIK") or 10.0)
DERINLIK = int(os.environ.get("DERINLIK") or 6)
MIN_GUNLUK = float(os.environ.get("MIN_GUNLUK") or 2.0)    # eğitimde günde en az bu kadar sinyal (tüm coinler)
MIN_POZ = int(os.environ.get("MIN_POZ") or 6)              # eğitimde en az bu kadar yakalanan yükseliş
SONUC = yp.KOK / "analiz" / "sonuc"


def oku(s, bas_ms, son_ms):
    rows = yp.mumlar(s["bn"], bas_ms - yp.ISINMA * IV, son_ms, IV)
    if len(rows) < yp.ISINMA:
        return None
    d = yp.ozellik(rows)
    o = np.array([float(r[1]) for r in rows])
    h = np.array([float(r[2]) for r in rows])
    l = np.array([float(r[3]) for r in rows])
    c = d["c"]
    n = len(c)
    sonraki = {k: np.full(n, np.nan) for k in ("yuk", "kap1", "dus1", "max16", "kap16", "min16")}
    sonraki["yuk"][:-1] = (h[1:] / o[1:] - 1) * 100
    sonraki["kap1"][:-1] = (c[1:] / c[:-1] - 1) * 100
    sonraki["dus1"][:-1] = (l[1:] / c[:-1] - 1) * 100
    for i in range(n - 16):
        sonraki["max16"][i] = (h[i + 1:i + 17].max() / c[i] - 1) * 100
        sonraki["min16"][i] = (l[i + 1:i + 17].min() / c[i] - 1) * 100
        sonraki["kap16"][i] = (c[i + 16] / c[i] - 1) * 100
    m = (d["ts"] + IV >= bas_ms) & ~np.isnan(sonraki["yuk"])
    return {"coin": s["s"], "ts": d["ts"][m] + IV, "F": {k: v[m] for k, v in d["F"].items()},
            "S": {k: v[m] for k, v in sonraki.items()}}


def ilk(m, coin, ts):
    """Aynı coinde 4 mum (1 saat) içindeki tekrar sinyalleri at."""
    idx = np.where(m)[0]
    sira = idx[np.lexsort((ts[idx], coin[idx]))]
    out, son = [], {}
    for i in sira:
        c = coin[i]
        if c in son and ts[i] - son[c] <= 4 * IV:
            son[c] = ts[i]
            continue
        son[c] = ts[i]
        out.append(i)
    z = np.zeros(len(m), bool)
    z[out] = True
    return z


def degerlendir(m, y, S, gun):
    n = int(m.sum())
    k = int((m & y).sum())
    r = {"sinyal": n, "gunluk": round(n / gun, 1), "yakalanan": k, "olay": int(y.sum()),
         "kapsama_pct": round(k / y.sum() * 100, 1) if y.sum() else None,
         "isabet_pct": round(k / n * 100, 2) if n else None}
    for ad in ("kap1", "dus1", "max16", "kap16", "min16"):
        x = S[ad][m]
        x = x[~np.isnan(x)]
        r[ad + "_med"] = round(float(np.median(x)), 2) if len(x) else None
    x = S["max16"][m]
    r["max16_5_pct"] = round(float((x >= 5).mean()) * 100, 1) if n else None
    x = S["min16"][m]
    r["min16_5_pct"] = round(float((x <= -5).mean()) * 100, 1) if n else None
    return r


def main():
    t0 = time.time()
    son_ms = int(os.environ.get("SON_MS") or int(time.time() * 1000) // IV * IV)
    ayrim_ms = son_ms - 7 * 86_400_000          # eğitim: son 7 gün (87 yükselişin haftası)
    bas_ms = ayrim_ms - 7 * 86_400_000          # sınama: ondan önceki 7 gün
    sem = [s for s in yp.tt.semboller() if s["s"].split("_")[0] not in yp.STABIL]

    def guvenli(s):
        try:
            return oku(s, bas_ms, son_ms)
        except Exception:
            return None

    with ThreadPoolExecutor(8) as ex:
        parca = [p for p in ex.map(guvenli, sem) if p]
    ts = np.concatenate([p["ts"] for p in parca])
    coin = np.concatenate([[p["coin"]] * len(p["ts"]) for p in parca])
    X = {k: np.concatenate([p["F"][k] for p in parca]) for k in yp.ADLAR}
    S = {k: np.concatenate([p["S"][k] for p in parca]) for k in parca[0]["S"]}
    y = S["yuk"] >= ESIK
    egit = ts >= ayrim_ms
    test = ~egit

    setler = yp.kural_ara(X, y, egit, 7.0, MIN_GUNLUK, MIN_POZ, derinlik=DERINLIK)
    gorulen, sonuc_set = set(), []
    for kos in setler:
        a = frozenset(kos)
        if a in gorulen:
            continue
        gorulen.add(a)
        m = ilk(yp.kosul_maske(X, list(kos)) & ~np.isnan(S["yuk"]), coin, ts)
        sonuc_set.append({"kosullar": [list(k) for k in kos], "metin": yp.kosul_metin(kos),
                          "egitim": degerlendir(m & egit, y & egit, S, 7.0),
                          "sinama": degerlendir(m & test, y & test, S, 7.0)})
    # sıra eğitimdeki seçiciliğe göre (kural_ara sırası); sınama haftası seçimde kullanılmaz
    sekiz = ilk(yp.erken_kurali(X), coin, ts)
    hepsi = np.ones(len(ts), bool)
    sonuc = {"bas": int(bas_ms), "ayrim": int(ayrim_ms), "son": int(son_ms), "coin": len(parca), "mum": int(len(ts)),
             "olay_egitim": int((y & egit).sum()), "olay_sinama": int((y & test).sum()),
             "taban": {"egitim": degerlendir(hepsi & egit, y & egit, S, 7.0), "sinama": degerlendir(hepsi & test, y & test, S, 7.0)},
             "sekiz": {"egitim": degerlendir(sekiz & egit, y & egit, S, 7.0), "sinama": degerlendir(sekiz & test, y & test, S, 7.0)},
             "setler": sonuc_set, "ayar": {"derinlik": DERINLIK, "min_gunluk": MIN_GUNLUK, "min_poz": MIN_POZ},
             "sure_sn": round(time.time() - t0)}
    SONUC.mkdir(parents=True, exist_ok=True)
    (SONUC / "yeni_set.json").write_text(json.dumps(sonuc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"coin {len(parca)}, mum {len(ts)}, olay eğitim {sonuc['olay_egitim']} sınama {sonuc['olay_sinama']}, {sonuc['sure_sn']} sn")
    print("8 şart", sonuc["sekiz"])
    for r in sonuc_set[:10]:
        print(r["metin"], "| E", r["egitim"], "| S", r["sinama"])


if __name__ == "__main__":
    main()
