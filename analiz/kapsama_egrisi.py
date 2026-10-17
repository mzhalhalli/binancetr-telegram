"""Kaç yükselişi yakalamak için kaç sinyal gerekir? (kapsama eğrisi)
Olay: bir sonraki 15 dakikalık mumda açılıştan en yükseğe %10+. Şartlar olaydan bir önceki kapanmış mumda.
Seçim haftası 10.10–17.10 (eşikler buradaki olayların %50/%75/%90/%100'ünü kapsayacak şekilde seçilir),
sınama haftası 03.10–10.10 (aynı eşiklerle kaç olay yakalandı, kaç sinyal çıktı).
1) Tek indikatör: her indikatör için en iyi yön (>= ya da <=).
2) Mevcut 8 şartın gevşetilmesi: "8 şarttan en az k tanesi" (k = 8..0).
3) Seçim haftasındaki her olayı yakalayan en az sinyalli "şartlardan biri" birleşimi (açgözlü küme örtüsü, tek eşikli şartlardan).
Sonuç: analiz/sonuc/kapsama_egrisi.json
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
SONUC = yp.KOK / "analiz" / "sonuc"
HEDEF = (0.5, 0.75, 0.9, 1.0)


def main():
    t0 = time.time()
    son_ms = int(os.environ.get("SON_MS") or int(time.time() * 1000) // IV * IV)
    ayrim_ms = son_ms - 7 * 86_400_000
    bas_ms = ayrim_ms - 7 * 86_400_000
    sem = [s for s in yp.tt.semboller() if s["s"].split("_")[0] not in yp.STABIL]

    def guvenli(s):
        try:
            return ys.oku(s, bas_ms, son_ms)
        except Exception:
            return None

    with ThreadPoolExecutor(8) as ex:
        parca = [p for p in ex.map(guvenli, sem) if p]
    ts = np.concatenate([p["ts"] for p in parca])
    X = {k: np.concatenate([p["F"][k] for p in parca]) for k in yp.ADLAR}
    S = {k: np.concatenate([p["S"][k] for p in parca]) for k in parca[0]["S"]}
    y = S["yuk"] >= ys.ESIK
    E, T = ts >= ayrim_ms, ts < ayrim_ms
    nE, nT = int((y & E).sum()), int((y & T).sum())

    def olc(m):
        return {"secim_yakalanan": int((m & y & E).sum()), "secim_sinyal": int((m & E).sum()),
                "sinama_yakalanan": int((m & y & T).sum()), "sinama_sinyal": int((m & T).sum())}

    tek = []
    for k in yp.SAYISAL:
        v = X[k][y & E]
        v = v[~np.isnan(v)]
        if len(v) < 20:
            continue
        for p in HEDEF:
            en = None
            for yon, t in ((">=", float(np.percentile(v, (1 - p) * 100))), ("<=", float(np.percentile(v, p * 100)))):
                with np.errstate(invalid="ignore"):
                    m = (X[k] >= t) if yon == ">=" else (X[k] <= t)
                r = {"indikator": k, "ad": yp.ADLAR[k], "yon": yon, "esik": round(t, 4), "hedef": p, **olc(m)}
                if en is None or r["secim_sinyal"] < en["secim_sinyal"]:
                    en = r
            tek.append(en)
    en_iyi = {}
    for p in HEDEF:
        en_iyi[str(p)] = sorted([r for r in tek if r["hedef"] == p], key=lambda r: r["secim_sinyal"])[:5]

    with np.errstate(invalid="ignore"):
        sartlar = [X["bwp"] <= 40, (X["yb"] >= 0.8) & (X["yb"] <= 1.1), (X["hist"] > 0) & (X["hist_art"] == 1),
                   (X["hist_don"] == 1) | (X["ema_kes"] == 1), X["e9_21"] > 0, (X["rsi"] >= 50) & (X["rsi"] <= 70),
                   X["hk"] < 3, X["yesil"] == 1]
    puan = sum(m.astype(int) for m in sartlar)
    gevsek = [{"en_az": k, **olc(puan >= k)} for k in range(8, -1, -1)]

    # açgözlü küme örtüsü: her adımda, kalan olaylardan en çoğunu sinyal başına en ucuza yakalayan tek eşikli şart
    adaylar = []
    for k in yp.SAYISAL:
        v = X[k][y & E]
        v = v[~np.isnan(v)]
        for q in (5, 10, 20, 30, 50, 70, 80, 90, 95):
            t = float(np.percentile(v, q))
            for yon in (">=", "<="):
                with np.errstate(invalid="ignore"):
                    m = (X[k] >= t) if yon == ">=" else (X[k] <= t)
                adaylar.append((f"{yp.ADLAR[k]} {'≥' if yon == '>=' else '≤'} {t:.4g}", m & ~np.isnan(X[k])))
    kalan = (y & E).copy()
    birlesim = np.zeros(len(ts), bool)
    adimlar = []
    while kalan.any() and len(adimlar) < 40:
        en, puan_en = None, -1.0
        for ad, m in adaylar:
            yeni = int((m & kalan).sum())
            if not yeni:
                continue
            ek = int((m & ~birlesim & E).sum())
            s = yeni / (ek + 1)
            if s > puan_en:
                en, puan_en = (ad, m), s
        if en is None:
            break
        birlesim |= en[1]
        kalan &= ~en[1]
        adimlar.append({"sart": en[0], **olc(birlesim)})
    sonuc = {"secim_olay": nE, "sinama_olay": nT, "secim_mum": int(E.sum()), "sinama_mum": int(T.sum()),
             "tek": en_iyi, "gevsek8": gevsek, "birlesim": adimlar, "sure_sn": round(time.time() - t0)}
    SONUC.mkdir(parents=True, exist_ok=True)
    (SONUC / "kapsama_egrisi.json").write_text(json.dumps(sonuc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: sonuc[k] for k in ("secim_olay", "sinama_olay", "secim_mum", "sinama_mum", "sure_sn")}))
    for g in gevsek:
        print("8 şarttan en az", g)
    for a in adimlar:
        print(a)


if __name__ == "__main__":
    main()
