"""Olası hedeflerin (1.-4. hedef ve zarar-kes, btr/hedef.py) geriye dönük testi. Yalnızca herkese açık mum verisini okur.

Son GUN günde 15 dakikalık mumlarda üç grup sinyal için hedefler hesaplanır ve sonraki 4 ve 24 saatte ne olduğu ölçülür:
- yeni_kural: EMA9 son 3 mumda EMA21'i yukarı kesti ve %0,9 üstünde (yukselis_profili.py'deki "ani EMA kesişi")
- mevcut_erken: btr/erken.py'deki 8 şart
- rastgele: rastgele seçilmiş mumlar (kıyas için)
Her hedef için: o süre içinde ulaşıldı mı, zarar-kesten önce mi ulaşıldı. Aynı mumda ikisi birden olursa zarar-kes sayılır.
Sonuç: analiz/sonuc/hedef_15m.json
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np

KOK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KOK))
sys.path.insert(0, str(KOK / "analiz"))

import telegram_tarama as tt  # noqa: E402
import yukselis_profili as yp  # noqa: E402
from btr import hedef as hd  # noqa: E402

GUN = int(os.environ.get("GUN", "30"))
PENCERE = {"4s": 16, "24s": 96}
RASTGELE_ADIM = 150


def yeni_kural(F) -> np.ndarray:
    with np.errstate(invalid="ignore"):
        return (F["ema_kes"] == 1) & (F["e9_21"] >= 0.9)


def olcum(h, l, c, i, r) -> dict:
    out = {}
    for ad, k in PENCERE.items():
        if i + k >= len(c):
            out[ad] = None
            continue
        hh, ll = h[i + 1:i + k + 1], l[i + 1:i + k + 1]
        st = np.flatnonzero(ll <= r["stop"])
        st = int(st[0]) if len(st) else None
        ulasti, once = [], []
        for t in r["hedefler"]:
            j = np.flatnonzero(hh >= t)
            j = int(j[0]) if len(j) else None
            ulasti.append(j is not None)
            once.append(j is not None and (st is None or j < st))
        # basit plan: hedefte ya da zarar-keste sat, ikisi de olmazsa süre sonunda kapanıştan sat
        plan = {}
        for p, t in enumerate(r["hedefler"][:2], 1):
            j = np.flatnonzero(hh >= t)
            j = int(j[0]) if len(j) else None
            if j is not None and (st is None or j < st):
                plan[f"T{p}"] = (t / c[i] - 1) * 100
            elif st is not None:
                plan[f"T{p}"] = (r["stop"] / c[i] - 1) * 100
            else:
                plan[f"T{p}"] = (c[i + k] / c[i] - 1) * 100
        out[ad] = {"ulasti": ulasti, "once": once, "stop_once": st is not None and not once[0], "plan": plan}
    return out


def simdiye(h, l, c, ts, i, r) -> dict:
    """Süre dolmamış sinyaller için: şimdiye kadar (en çok 24 saat) ne oldu."""
    k = min(len(c) - 1, i + PENCERE["24s"])
    hh, ll = h[i + 1:k + 1], l[i + 1:k + 1]
    if not len(hh):
        return {"mum": 0}
    st = np.flatnonzero(ll <= r["stop"])
    st = int(st[0]) if len(st) else None
    hedef = []
    for t in r["hedefler"]:
        j = np.flatnonzero(hh >= t)
        j = int(j[0]) if len(j) else None
        hedef.append(None if j is None else {"mum": j + 1, "stoptan_once": st is None or j < st,
                                             "saat": datetime.fromtimestamp(int(ts[i + 1 + j]) / 1000 + 900, yp.TR).strftime("%d.%m %H:%M")})
    return {"mum": int(len(hh)), "max_pct": round(float((hh.max() / c[i] - 1) * 100), 2),
            "min_pct": round(float((ll.min() / c[i] - 1) * 100), 2), "son_pct": round(float((c[k] / c[i] - 1) * 100), 2),
            "stop_mum": None if st is None else st + 1, "hedef": hedef}


def ozet(kayit: list[dict]) -> dict:
    if not kayit:
        return {"n": 0}
    o = {"n": len(kayit),
         "hedef_pct_medyan": [round(statistics.median(x["hedef_pct"][k] for x in kayit), 2) for k in range(hd.ADET)],
         "direnc_payi_pct": [round(sum(x["kaynak"][k] == "direnç" for x in kayit) / len(kayit) * 100) for k in range(hd.ADET)],
         "stop_pct_medyan": round(statistics.median(x["stop_pct"] for x in kayit), 2)}
    for ad in PENCERE:
        v = [x[ad] for x in kayit if x[ad]]
        if not v:
            continue
        o[ad] = {"n": len(v),
                 "ulasti_pct": [round(sum(x["ulasti"][k] for x in v) / len(v) * 100, 1) for k in range(hd.ADET)],
                 "stoptan_once_pct": [round(sum(x["once"][k] for x in v) / len(v) * 100, 1) for k in range(hd.ADET)],
                 "once_stop_pct": round(sum(x["stop_once"] for x in v) / len(v) * 100, 1),
                 "plan_T1_ort_pct": round(statistics.fmean(x["plan"]["T1"] for x in v), 2),
                 "plan_T2_ort_pct": round(statistics.fmean(x["plan"]["T2"] for x in v), 2)}
    return o


def analiz_sembol(sym: str, rows: list, bas_ms: int, rng) -> list[dict]:
    D = yp.ozellik(rows)
    F = D["F"]
    h = np.array([float(r[2]) for r in rows])
    l = np.array([float(r[3]) for r in rows])
    c = np.array([float(r[4]) for r in rows])
    hl, ll, cl = h.tolist(), l.tolist(), c.tolist()
    gruplar = {"yeni_kural": yeni_kural(F), "mevcut_erken": yp.erken_kurali(F)}
    rast = np.zeros(len(c), bool)
    rast[rng.integers(0, RASTGELE_ADIM):: RASTGELE_ADIM] = True
    gruplar["rastgele"] = rast
    out = []
    for grup, m in gruplar.items():
        son = -10**9
        for i in np.flatnonzero(m):
            if D["ts"][i] < bas_ms:
                continue
            if grup != "rastgele" and i - son <= yp.YAKIN:      # aynı coinde 1 saat içinde tekrar eden sinyal
                son = i
                continue
            son = i
            a = max(0, i - hd.GERI - 20)
            r = hd.hesapla(hl[a:i + 1], ll[a:i + 1], cl[a:i + 1])
            if not r:
                continue
            ek = {}
            if grup == "yeni_kural":
                k = min(len(c) - 1, i + PENCERE["24s"])
                ek = {"fiyat": float(c[i]), "hedefler": r["hedefler"], "stop": r["stop"], "simdiye": simdiye(h, l, c, D["ts"], i, r),
                      "max24_pct": float((h[i + 1:k + 1].max() / c[i] - 1) * 100) if k > i else None,
                      "min24_pct": float((l[i + 1:k + 1].min() / c[i] - 1) * 100) if k > i else None}
            out.append({"grup": grup, "s": sym, "ts": int(D["ts"][i]), "hedef_pct": r["hedef_pct"], "stop_pct": r["stop_pct"],
                        "kaynak": r["kaynak"], **ek, **olcum(h, l, c, i, r)})
    return out


def main():
    t0 = time.time()
    iv = yp.IV
    bit_ms = int(time.time() * 1000) // iv * iv
    bas_ms = bit_ms - GUN * 86_400_000
    ayrim = bas_ms + int((bit_ms - bas_ms) * 2 / 3)
    sem = [s for s in tt.semboller() if s["s"].split("_")[0] not in yp.STABIL]
    kayit, hata = [], []
    rng = np.random.default_rng(7)

    def bir(s):
        try:
            rows = yp.mumlar(s["bn"], bas_ms - yp.ISINMA * iv, bit_ms)
            kayit.extend(analiz_sembol(s["s"], rows, bas_ms, rng))
        except Exception as e:
            hata.append(f"{s['s']}: {e}")

    with ThreadPoolExecutor(8) as ex:
        list(ex.map(bir, sem))
    sonuc = {"olusturma": datetime.now(yp.TR).strftime("%d.%m.%Y %H:%M"), "gun": GUN, "coin": len(sem), "hata": len(hata),
             "hata_ornek": hata[:5], "yontem": hd.__doc__, "gruplar": {}}
    for g in ("yeni_kural", "mevcut_erken", "rastgele"):
        x = [k for k in kayit if k["grup"] == g]
        sonuc["gruplar"][g] = {"hepsi": ozet(x), "ilk_20_gun": ozet([k for k in x if k["ts"] < ayrim]),
                               "son_10_gun": ozet([k for k in x if k["ts"] >= ayrim])}
    sonuc["yeni_kural_liste"] = [{"s": k["s"], "zaman": datetime.fromtimestamp(k["ts"] / 1000, yp.TR).strftime("%d.%m %H:%M"),
                                  "fiyat": k["fiyat"], "hedefler": [float(f"{v:.6g}") for v in k["hedefler"]],
                                  "stop": float(f"{k['stop']:.6g}"), "kaynak": k["kaynak"],
                                  "hedef_pct": [round(v, 1) for v in k["hedef_pct"]], "stop_pct": round(k["stop_pct"], 1),
                                  "max24_pct": k["max24_pct"] and round(k["max24_pct"], 1),
                                  "min24_pct": k["min24_pct"] and round(k["min24_pct"], 1),
                                  "4s": k["4s"] and {"ulasti": k["4s"]["ulasti"], "once": k["4s"]["once"], "stop_once": k["4s"]["stop_once"]},
                                  "24s": k["24s"] and {"ulasti": k["24s"]["ulasti"], "once": k["24s"]["once"], "stop_once": k["24s"]["stop_once"]}}
                                 for k in sorted((k for k in kayit if k["grup"] == "yeni_kural"), key=lambda k: k["ts"])]
    takip_dosya = KOK / "analiz" / "takip.json"
    if takip_dosya.exists():
        istek = {(t["s"], t["zaman"]) for t in json.loads(takip_dosya.read_text(encoding="utf-8"))}
        sonuc["takip"] = [{"s": k["s"], "zaman": datetime.fromtimestamp(k["ts"] / 1000, yp.TR).strftime("%d.%m %H:%M"),
                           "fiyat": k["fiyat"], "hedefler": [float(f"{v:.6g}") for v in k["hedefler"]],
                           "stop": float(f"{k['stop']:.6g}"), "simdiye": k["simdiye"]}
                          for k in kayit if k["grup"] == "yeni_kural"
                          and (k["s"], datetime.fromtimestamp(k["ts"] / 1000, yp.TR).strftime("%d.%m %H:%M")) in istek]
    sonuc["sure_sn"] = round(time.time() - t0)
    out = KOK / "analiz" / "sonuc"
    out.mkdir(parents=True, exist_ok=True)
    (out / "hedef_15m.json").write_text(json.dumps(sonuc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(sonuc["gruplar"], ensure_ascii=False, indent=1)[:5000])


if __name__ == "__main__":
    main()
