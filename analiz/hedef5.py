"""+%5 hedefi: sinyal mumunun kapanışında alınırsa, fiyat (a) sonraki 15 dakikalık mumda, (b) sonraki 1 saatte (4 mum)
alış fiyatının en az %5 üstüne çıkıyor mu? Hangi şart birleşimleri bunu en yüksek oranla (isabet) sağlıyor?
Ayrıca aynı süre içinde fiyatın önce alışın %5 altına inip inmediği (zarar) sayılır; aynı mumda ikisi de olursa sıra bilinmez.
Seçim haftası 10.10–17.10, sınama haftası 03.10–10.10 (seçimde kullanılmaz). Kural araması yukselis_profili.kural_ara
(Wilson alt sınırına göre), en çok DERINLIK şart. Aynı coinde 1 saat içindeki tekrar sinyaller tek sayılır.
Yalnızca herkese açık mum verisi. Sonuç: analiz/sonuc/hedef5.json
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
import yeni_set as ys  # noqa: E402
import yukselis_profili as yp  # noqa: E402

IV = 900_000
KAR = float(os.environ.get("KAR") or 5.0)
DERINLIK = int(os.environ.get("DERINLIK") or 6)
SONUC = yp.KOK / "analiz" / "sonuc"
SURE = {"15dk": 1, "1saat": 4}


def oku(s, bas_ms, son_ms):
    rows = yp.mumlar(s["bn"], bas_ms - yp.ISINMA * IV, son_ms, IV)
    if len(rows) < yp.ISINMA:
        return None
    d = yp.ozellik(rows)
    h = np.array([float(r[2]) for r in rows])
    l = np.array([float(r[3]) for r in rows])
    c = d["c"]
    n = len(c)
    S = {}
    for ad, k in SURE.items():
        yuk, dus, ilk_kar, ilk_zarar = (np.full(n, np.nan) for _ in range(4))
        for i in range(n - k):
            hh, ll = h[i + 1:i + 1 + k], l[i + 1:i + 1 + k]
            yuk[i] = (hh.max() / c[i] - 1) * 100
            dus[i] = (ll.min() / c[i] - 1) * 100
            kar = np.where(hh >= c[i] * (1 + KAR / 100))[0]
            zarar = np.where(ll <= c[i] * (1 - KAR / 100))[0]
            ilk_kar[i] = kar[0] if len(kar) else 99
            ilk_zarar[i] = zarar[0] if len(zarar) else 99
        S[ad + "_yuk"], S[ad + "_dus"] = yuk, dus
        S[ad + "_kar"] = (ilk_kar < 99) & (ilk_kar < ilk_zarar)          # +%5, %5 zarardan önce (aynı mum = zarar)
        S[ad + "_temiz"] = (ilk_kar < 99) & (ilk_zarar == 99)           # +%5 ve süre boyunca -%5 yok
    S["kap4"] = np.full(n, np.nan)
    S["kap4"][:-4] = (c[4:] / c[:-4] - 1) * 100
    m = (d["ts"] + IV >= bas_ms) & ~np.isnan(S["1saat_yuk"])
    return {"coin": s["s"], "ts": d["ts"][m] + IV, "F": {k: v[m] for k, v in d["F"].items()},
            "S": {k: v[m] for k, v in S.items()}}


def olc(m, S, sec):
    mm = m & sec
    n = int(mm.sum())
    r = {"sinyal": n}
    for ad in SURE:
        for t in ("_yuk", "_kar", "_temiz"):
            if t == "_yuk":
                x = S[ad + t][mm] >= KAR
            else:
                x = S[ad + t][mm].astype(bool)
            r[ad + t + "_pct"] = round(float(x.mean()) * 100, 1) if n else None
            r[ad + t + "_adet"] = int(x.sum())
    x = S["kap4"][mm]
    r["kap4_med"] = round(float(np.nanmedian(x)), 2) if n else None
    return r


def main():
    t0 = time.time()
    son_ms = int(os.environ.get("SON_MS") or int(time.time() * 1000) // IV * IV)
    ayrim_ms = son_ms - 7 * 86_400_000
    bas_ms = ayrim_ms - 7 * 86_400_000
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
    E, T = ts >= ayrim_ms, ts < ayrim_ms
    hepsi = np.ones(len(ts), bool)
    sonuc = {"coin": len(parca), "mum": int(len(ts)), "kar_pct": KAR,
             "taban": {"secim": olc(hepsi, S, E), "sinama": olc(hepsi, S, T)},
             "sekiz": {}, "hedefler": {}}
    sekiz = ys.ilk(yp.erken_kurali(X), coin, ts)
    sonuc["sekiz"] = {"secim": olc(sekiz, S, E), "sinama": olc(sekiz, S, T)}
    for ad in SURE:
        y = S[ad + "_kar"].astype(bool)
        sets = yp.kural_ara(X, y, E, 7.0, 0.5, 5, derinlik=DERINLIK)
        gorulen, liste = set(), []
        for kos in sets:
            a = frozenset(kos)
            if a in gorulen:
                continue
            gorulen.add(a)
            m = ys.ilk(yp.kosul_maske(X, list(kos)), coin, ts)
            liste.append({"kosullar": [list(k) for k in kos], "metin": yp.kosul_metin(kos),
                          "secim": olc(m, S, E), "sinama": olc(m, S, T)})
        sonuc["hedefler"][ad] = liste
    sonuc["sure_sn"] = round(time.time() - t0)
    SONUC.mkdir(parents=True, exist_ok=True)
    (SONUC / "hedef5.json").write_text(json.dumps(sonuc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"taban": sonuc["taban"], "sekiz": sonuc["sekiz"]}, ensure_ascii=False))
    for ad, liste in sonuc["hedefler"].items():
        for r in liste[:12]:
            print(ad, "|", r["metin"], "| S", r["secim"], "| T", r["sinama"])


if __name__ == "__main__":
    main()
