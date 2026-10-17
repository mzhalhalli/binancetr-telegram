"""9. şart araştırması: son 7 günde tek 15 dakikalık mumda açılıştan en yükseğe %10+ çıkan mumlardan (yuzde10_hafta.py Liste 1)
HEMEN ÖNCEKİ kapanmış mumun indikatörleri, haftanın tüm mumlarıyla karşılaştırılır. Tarama mum kapanışında çalıştığı için
uyarı ancak yükselişten önceki mumun kapanışında verilebilir; bu yüzden "önceki mum" değerleri esas alınır.
Yükseliş mumunun kendi değerleri (mum kapandıktan sonra) yalnızca bilgi için yazılır.

1) Her indikatör için olaylar (yükselişten önceki mum) ve taban (haftanın tüm mumları): medyan, %25, %75.
2) Her indikatör için tek eşikli şart adayları (>= x ya da <= x; x = olayların %50-%90'ını kapsayan değerler):
   kapsama (olayların % kaçı şartı sağlıyor), tabanda sağlama oranı, kaldıraç = kapsama / taban oranı, günlük sinyal sayısı.
   Eşik ilk 4 günde seçilir, son 3 günde (görmediği veride) aynı eşikle tekrar ölçülür.
3) Mevcut 8 şart (btr/erken.py) olayların öncesinde ve 9. şart adaylarıyla birlikte (8+1) kaç olayı yakalardı.
Saatler TR, mum saati açılış saatidir. Yalnızca herkese açık mum verisi. Sonuç: analiz/sonuc/sart9.json ve .md
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
GUN = int(os.environ.get("HAFTA_GUN") or 7)
ESIK = float(os.environ.get("ESIK") or 10.0)
EGITIM_GUN = 4
KAPSAMA = (0.5, 0.6, 0.7, 0.8, 0.9)
SONUC = yp.KOK / "analiz" / "sonuc"


def oku(s, bas_ms, son_ms):
    rows = yp.mumlar(s["bn"], bas_ms - yp.ISINMA * IV, son_ms, IV)
    if len(rows) < yp.ISINMA:
        return None
    d = yp.ozellik(rows)
    o = np.array([float(r[1]) for r in rows])
    h = np.array([float(r[2]) for r in rows])
    yuk = np.full(len(o), np.nan)
    yuk[:-1] = (h[1:] / o[1:] - 1) * 100          # sonraki mumun açılıştan en yükseğe yükselişi
    hafta = d["ts"] >= bas_ms
    sonraki_hafta = np.zeros(len(o), bool)
    sonraki_hafta[:-1] = d["ts"][1:] >= bas_ms     # sonraki mum hafta içinde
    m = sonraki_hafta & ~np.isnan(yuk)
    F = {k: v[m] for k, v in d["F"].items()}
    Fy = {k: v[np.roll(m, 1)] for k, v in d["F"].items()}   # yükseliş mumunun kendi değerleri
    return {"coin": s["s"], "ts": d["ts"][m] + IV, "yuk": yuk[m], "F": F, "Fy": Fy, "hafta_mum": int(hafta.sum())}


def dagilim(x):
    x = x[~np.isnan(x)]
    if not len(x):
        return None
    return [round(float(v), 3) for v in np.percentile(x, [25, 50, 75])]


def olc(X, olay, kos, gun):
    """kos = (ad, yon, eşik): kapsama, taban oranı, kaldıraç, günlük sinyal."""
    ad, yon, e = kos
    with np.errstate(invalid="ignore"):
        s = X[ad] >= e if yon == ">=" else X[ad] <= e
    s = s & ~np.isnan(X[ad])
    n_olay = int(olay.sum())
    kap = float(s[olay].mean()) if n_olay else 0.0
    taban = float(s.mean()) if len(s) else 0.0
    return {"kapsama": round(kap * 100, 1), "yakalanan": int(s[olay].sum()), "olay": n_olay,
            "taban_pct": round(taban * 100, 2), "kaldirac": round(kap / taban, 2) if taban else None,
            "gunluk_sinyal": round(int(s.sum()) / gun, 1),
            "isabet_pct": round(float(olay[s].mean()) * 100, 2) if s.sum() else None}


def main():
    t0 = time.time()
    son_ms = int(os.environ.get("SON_MS") or int(time.time() * 1000) // IV * IV)   # Liste 1 ile aynı dönem için sabitlenebilir
    bas_ms = son_ms - GUN * 86_400_000
    ayrim_ms = bas_ms + EGITIM_GUN * 86_400_000
    sem = [s for s in yp.tt.semboller() if s["s"].split("_")[0] not in yp.STABIL]

    def guvenli(s):
        try:
            return oku(s, bas_ms, son_ms)
        except Exception:
            return None

    with ThreadPoolExecutor(8) as ex:
        parca = [p for p in ex.map(guvenli, sem) if p]
    ts = np.concatenate([p["ts"] for p in parca])           # sonraki (yükseliş) mumunun açılışı
    yuk = np.concatenate([p["yuk"] for p in parca])
    coin = np.concatenate([[p["coin"]] * len(p["ts"]) for p in parca])
    X = {k: np.concatenate([p["F"][k] for p in parca]) for k in yp.ADLAR}
    Xy = {k: np.concatenate([p["Fy"][k] for p in parca]) for k in yp.ADLAR}
    olay = yuk >= ESIK
    egit = ts < ayrim_ms
    test = ~egit
    gun_e, gun_t = EGITIM_GUN, GUN - EGITIM_GUN

    profil = {}
    for k in yp.ADLAR:
        profil[k] = {"ad": yp.ADLAR[k], "olay_once": dagilim(X[k][olay]), "taban": dagilim(X[k]),
                     "olay_kendi": dagilim(Xy[k][olay])}
        if k in yp.MANTIK:
            profil[k]["olay_once_pct"] = round(float(np.nanmean(X[k][olay])) * 100, 1)
            profil[k]["taban_pct"] = round(float(np.nanmean(X[k])) * 100, 1)

    adaylar = []
    Xe = {k: v[egit] for k, v in X.items()}
    Xt = {k: v[test] for k, v in X.items()}
    for k in yp.ADLAR:
        x = X[k][olay & egit]
        x = x[~np.isnan(x)]
        if len(x) < 10:
            continue
        if k in yp.MANTIK:
            koslar = [(k, ">=", 1.0)]
        else:
            koslar = [(k, ">=", round(float(np.percentile(x, (1 - p) * 100)), 4)) for p in KAPSAMA] + \
                     [(k, "<=", round(float(np.percentile(x, p * 100)), 4)) for p in KAPSAMA]
        for kos in koslar:
            e = olc(Xe, olay[egit], kos, gun_e)
            if e["kaldirac"] is None or e["kapsama"] < 50:
                continue
            adaylar.append({"kosul": kos, "metin": yp.kosul_metin([kos]), "egitim": e, "test": olc(Xt, olay[test], kos, gun_t)})
    adaylar.sort(key=lambda a: -(a["egitim"]["kaldirac"] or 0))
    tek = {}
    for a in adaylar:                                   # her indikatörden en iyi aday
        tek.setdefault(a["kosul"][0], a)
    en_iyi = sorted(tek.values(), key=lambda a: -(a["egitim"]["kaldirac"] or 0))[:12]

    sekiz = yp.erken_kurali(X)
    sartlar_8 = {
        "1 bant dar (yüzdelik ≤40)": X["bwp"] <= 40, "2 %B 0,80-1,10": (X["yb"] >= 0.8) & (X["yb"] <= 1.1),
        "3 MACD hist artıda ve artıyor": (X["hist"] > 0) & (X["hist_art"] == 1),
        "4 yeni dönüş (MACD/EMA)": (X["hist_don"] == 1) | (X["ema_kes"] == 1), "5 EMA9>EMA21": X["e9_21"] > 0,
        "6 RSI 50-70": (X["rsi"] >= 50) & (X["rsi"] <= 70), "7 hacim <3x": X["hk"] < 3, "8 yeşil mum": X["yesil"] == 1}
    with np.errstate(invalid="ignore"):
        sekiz_detay = {ad: {"olay_pct": round(float(m[olay].mean()) * 100, 1), "taban_pct": round(float(m.mean()) * 100, 1)}
                       for ad, m in sartlar_8.items()}
        puan = sum(m.astype(int) for m in sartlar_8.values())
    puan_dag = {str(p): {"olay": int(((puan == p) & olay).sum()), "tum": int((puan == p).sum())} for p in range(9)}
    dokuz = []
    for a in en_iyi[:6]:
        ad, yon, e = a["kosul"]
        with np.errstate(invalid="ignore"):
            s9 = (X[ad] >= e if yon == ">=" else X[ad] <= e) & ~np.isnan(X[ad])
        birlikte = sekiz & s9
        dokuz.append({"kosul": a["metin"], "sekiz_olay": int((sekiz & olay).sum()), "sekiz_tum": int(sekiz.sum()),
                      "dokuz_olay": int((birlikte & olay).sum()), "dokuz_tum": int(birlikte.sum())})

    olaylar = [{"coin": str(coin[i]), "ts": int(ts[i]), "yuk": round(float(yuk[i]), 2), "puan8": int(puan[i]),
                **{k: (None if np.isnan(X[k][i]) else round(float(X[k][i]), 3)) for k in yp.ADLAR}}
               for i in np.where(olay)[0]]
    olaylar.sort(key=lambda o: o["ts"])
    sonuc = {"bas": int(bas_ms), "son": int(son_ms), "ayrim": int(ayrim_ms), "coin": len(parca), "mum": int(len(ts)),
             "olay": int(olay.sum()), "olay_egitim": int((olay & egit).sum()), "olay_test": int((olay & test).sum()),
             "taban_olay_pct": round(float(olay.mean()) * 100, 3), "profil": profil, "adaylar": en_iyi,
             "sekiz_detay": sekiz_detay, "puan8": puan_dag, "dokuz": dokuz, "olaylar": olaylar,
             "sure_sn": round(time.time() - t0)}
    SONUC.mkdir(parents=True, exist_ok=True)
    (SONUC / "sart9.json").write_text(json.dumps(sonuc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"coin {len(parca)}, mum {len(ts)}, olay {int(olay.sum())} (eğitim {sonuc['olay_egitim']}, test {sonuc['olay_test']}), "
          f"{sonuc['sure_sn']} sn")
    for a in en_iyi[:8]:
        print(a["metin"], a["egitim"], a["test"])


if __name__ == "__main__":
    main()
