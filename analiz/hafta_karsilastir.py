"""Son 7 gün, Binance TR'deki tüm TRY coinleri: 15 dakikalık erken uyarı ile formasyon kırılımı girişleri (anında, 2 yeşil mum
onayı, retest) aynı al-sat kuralıyla karşılaştırılır. Yalnızca herkese açık mum verisi; gerçek alım yapılmaz.

Al-sat kuralı (gerçek cüzdan ayarı): sinyal mumunun kapanışından al, +%2 kâr al, -%5 zarar kes, süre sınırı yok.
- Satışlar alıştan sonraki 15 dakikalık mumlarla izlenir: mumun en yükseği hedefe değerse hedeften, en düşüğü zarar-kese
  değerse zarar-kesten (mum zarar-kesin altında açılırsa açılıştan) satılır. Aynı mumda ikisi birden: zarar-kes sayılır.
- Hafta sonunda kapanmamış işlem son kapanış fiyatıyla değerlenir ("açık").
- Her alım ve satışta %0,1 komisyon.
İki hesap:
  1) Her sinyale 200 TL (sermaye sınırı yok): işlem başı sonuç.
  2) Botun cüzdanı: 1.000 TL, her alım 200 TL, en çok 3 açık işlem, elde olan coine ek alım yok; aynı mumdaki sinyaller
     alfabetik sırayla. Nakit ya da yer yoksa sinyal atlanır.
Girişler:
  erken  = btr/erken.py 8 şartın tamamı (botun 15 dk erken uyarısı), sinyal mumunun kapanışı
  K0     = formasyon kırılım mumunun kapanışı (botun formasyon kuralı)
  Y2     = kırılımdan sonra 2 yeşil mum, ikisi de çizgi üstünde kapanınca 2. mumun kapanışı
  RT     = retest: kırılımdan sonra 10 mum içinde mum dibi çizgiye 0,25 ATR yaklaşır, kapanış çizgi altına inmez,
           ardından ilk yeşil mumun kapanışı (onay_testi.py ile aynı)
Formasyonlar 15m (boğa bayrağı, genişleyen, dikdörtgen, yükselen üçgen, ikili dip), 1h (boğa bayrağı, genişleyen),
4h (genişleyen, dikdörtgen, yükselen üçgen, ikili dip). 1h/4h girişleri o mumun kapanış saatindeki 15 dakikalık mumdan izlenir.
Karşılaştırma: aynı haftada her coinden 20 rastgele 15 dakikalık mumda aynı kuralla giriş; tüm coinlerin haftalık değişimi.
Sonuç: analiz/sonuc/hafta_karsilastir.json ve .md
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import formasyon as fm  # noqa: E402
import onay_testi as ot  # noqa: E402
import yukselis_profili as yp  # noqa: E402

IV = 900_000
GUN = int(os.environ.get("HAFTA_GUN") or 7)
GECMIS_GUN = {"15m": 30, "1h": 60, "4h": 240}    # formasyon dönüş noktaları için önceden okunan geçmiş
KAR, ZARAR, KOM = 2.0, 5.0, 0.001
ALIM, CUZDAN, EN_COK = 200.0, 1000.0, 3
RASTGELE = 20
YONTEM_AD = {"erken": "15 dk erken uyarı", "K0": "kırılım mumunda", "Y2": "2 yeşil mum onayı", "RT": "retest sonrası"}


def saat(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, fm.TR).strftime("%d.%m %H:%M")


def cik(o, h, l, c, e):
    """e mumunun kapanışında alınan işlemin çıkışı: (çıkış mumu, fiyat, neden). Hafta sonuna kadar kapanmazsa açık."""
    g = c[e]
    hedef, stop = g * (1 + KAR / 100), g * (1 - ZARAR / 100)
    for i in range(e + 1, len(c)):
        if o[i] <= stop:
            return i, o[i], "zarar-kes"
        if l[i] <= stop:
            return i, stop, "zarar-kes"
        if h[i] >= hedef:
            return i, max(hedef, o[i]), "kâr al"
    return len(c) - 1, c[-1], "açık"


def net_pct(giris, cikis):
    return ((cikis * (1 - KOM)) / (giris * (1 + KOM)) - 1) * 100


def formasyon_girisleri(rows, aralik, secili):
    """(kapanış ms, yöntem, formasyon) listesi: formasyon girişlerinin mum kapanış saatleri."""
    o = np.array([float(r[1]) for r in rows])
    h = np.array([float(r[2]) for r in rows])
    l = np.array([float(r[3]) for r in rows])
    c = np.array([float(r[4]) for r in rows])
    ts = np.array([int(r[0]) for r in rows], dtype=np.int64)
    n = len(c)
    if n < 60:
        return []
    iv = fm.IVS[aralik]
    a = fm.atr_dizi(h, l, c)
    P = fm.zigzag(h, l, a)
    out = []
    for f, t, yon, hedef, stop, hp, sp_, H in fm.kirilimlar(h, l, c, P):
        if yon != 1 or f["ad"] not in secili:
            continue
        girisler = {"K0": t}
        if t + 2 < n and all(c[t + i] > o[t + i] and c[t + i] > f["ust"](t + i) for i in (1, 2)):
            girisler["Y2"] = t + 2
        r = ot.retest_giris(o, l, c, a, f["ust"], t, n)
        if r is not None:
            girisler["RT"] = r
        for y, e in girisler.items():
            out.append((int(ts[e]) + iv, y, f["ad"]))
    return out


def main():
    t0 = time.time()
    son_ms = int(time.time() * 1000) // IV * IV          # bu andan önce kapanan mumlar
    bas_ms = son_ms - GUN * 86_400_000                  # haftanın ilk mumunun açılışı
    sem = [s for s in fm.tt.semboller() if s["s"].split("_")[0] not in fm.STABIL]
    ham, hata = {}, []
    kilit = threading.Lock()

    def bir(s):
        try:
            r15 = yp.mumlar(s["bn"], bas_ms - GECMIS_GUN["15m"] * 86_400_000, son_ms, IV)
            if len(r15) < yp.ISINMA:
                return
            ts = np.array([int(r[0]) for r in r15], dtype=np.int64)
            d = {"ts": ts, "o": np.array([float(r[1]) for r in r15]), "h": np.array([float(r[2]) for r in r15]),
                 "l": np.array([float(r[3]) for r in r15]), "c": np.array([float(r[4]) for r in r15])}
            kap = {int(x) + IV: k for k, x in enumerate(ts)}        # kapanış ms -> 15 dk mum
            sinyal = []
            erk = yp.erken_kurali(yp.ozellik(r15)["F"])
            for j in np.flatnonzero(erk):
                sinyal.append((int(ts[j]) + IV, "erken", "15m", "erken"))
            for y_ms, y, ad in formasyon_girisleri(r15, "15m", ot.SECILI["15m"]):
                sinyal.append((y_ms, y, "15m", ad))
            for a in ("1h", "4h"):
                iv = fm.IVS[a]
                rows = fm.mumlar(s["bn"], a, bas_ms - GECMIS_GUN[a] * 86_400_000, son_ms)
                for y_ms, y, ad in formasyon_girisleri(rows, a, ot.SECILI[a]):
                    sinyal.append((y_ms, y, a, ad))
            d["sinyal"] = [(kap[m], y, a, ad) for m, y, a, ad in sinyal if bas_ms < m <= son_ms and m in kap]
            rng = np.random.default_rng(sum(map(ord, s["s"])))
            ilk = int(np.searchsorted(ts, bas_ms))
            d["rastgele"] = sorted(set(int(x) for x in rng.integers(ilk, len(ts) - 1, RASTGELE))) if len(ts) - 1 > ilk else []
            d["hafta_pct"] = float(d["c"][-1] / d["c"][ilk - 1] - 1) * 100 if ilk > 0 else None
            with kilit:
                ham[s["s"]] = d
        except Exception as e:
            hata.append(f"{s['s']}: {type(e).__name__}: {e}")

    with ThreadPoolExecutor(8) as ex:
        list(ex.map(bir, sem))

    # ---- her sinyal için işlem sonucu (sermaye sınırı yok)
    islem = []
    for s, d in ham.items():
        o, h, l, c, ts = d["o"], d["h"], d["l"], d["c"], d["ts"]
        for e, y, a, ad in d["sinyal"]:
            i, p, neden = cik(o, h, l, c, e)
            islem.append({"s": s, "yontem": y, "aralik": a, "formasyon": ad, "giris_ms": int(ts[e]) + IV, "e": e, "cikis_i": i,
                          "cikis_ms": int(ts[i]) + IV, "giris": float(c[e]), "cikis": float(p), "neden": neden,
                          "net_pct": net_pct(c[e], p), "sure_dk": (i - e) * 15})
    rnd = []
    for s, d in ham.items():
        for e in d["rastgele"]:
            i, p, neden = cik(d["o"], d["h"], d["l"], d["c"], e)
            rnd.append({"neden": neden, "net_pct": net_pct(d["c"][e], p)})

    def ozet(L):
        if not L:
            return {"islem": 0}
        n = len(L)
        return {"islem": n, "coin": len({x.get("s") for x in L}),
                "kar_al": sum(x["neden"] == "kâr al" for x in L), "zarar_kes": sum(x["neden"] == "zarar-kes" for x in L),
                "acik": sum(x["neden"] == "açık" for x in L),
                "kar_al_pct": round(sum(x["neden"] == "kâr al" for x in L) / n * 100, 1),
                "ort_net_pct": round(statistics.fmean(x["net_pct"] for x in L), 3),
                "medyan_net_pct": round(statistics.median(x["net_pct"] for x in L), 3),
                "toplam_tl_200": round(sum(x["net_pct"] for x in L) * ALIM / 100, 2),
                "kapanan_sure_dk_medyan": (statistics.median(x["sure_dk"] for x in L if x["neden"] != "açık")
                                           if any(x["neden"] != "açık" for x in L) else None)}

    # ---- botun cüzdanı (1.000 TL, 200 TL, en çok 3 açık)
    def cuzdan(L):
        olay = {}
        for x in L:
            olay.setdefault(x["giris_ms"], []).append(x)
        nakit, acik, alinan, atlanan = CUZDAN, [], [], 0
        zamanlar = sorted(set(olay) | {x["cikis_ms"] for x in L})
        for z in zamanlar:
            for p in [p for p in acik if p["cikis_ms"] <= z and p["neden"] != "açık"]:   # bu mumda çıkanlar (alımdan önce)
                nakit += p["adet"] * p["cikis"] * (1 - KOM)
                acik.remove(p)
            for x in sorted(olay.get(z, []), key=lambda x: x["s"]):
                if len(acik) >= EN_COK or nakit < ALIM or any(p["s"] == x["s"] for p in acik):
                    atlanan += 1
                    continue
                p = {**x, "adet": ALIM * (1 - KOM) / x["giris"]}
                nakit -= ALIM
                acik.append(p)
                alinan.append(p)
        deger = sum(p["adet"] * p["cikis"] * (1 - KOM) for p in acik)      # açıklar son fiyatla
        return {"alim": len(alinan), "atlanan": atlanan, "kar_al": sum(p["neden"] == "kâr al" for p in alinan),
                "zarar_kes": sum(p["neden"] == "zarar-kes" for p in alinan), "acik": len(acik),
                "nakit": round(nakit, 2), "acik_deger": round(deger, 2), "son": round(nakit + deger, 2),
                "kar_tl": round(nakit + deger - CUZDAN, 2), "kar_pct": round((nakit + deger) / CUZDAN * 100 - 100, 2),
                "islemler": [{k: p[k] for k in ("s", "formasyon", "neden")} | {"giris": saat(p["giris_ms"]),
                              "cikis": saat(p["cikis_ms"]) if p["neden"] != "açık" else "-", "net_pct": round(p["net_pct"], 2)}
                             for p in alinan]}

    gruplar = {"erken_15m": [x for x in islem if x["yontem"] == "erken"]}
    for a in ("15m", "1h", "4h"):
        for y in ("K0", "Y2", "RT"):
            gruplar[f"{y}_{a}"] = [x for x in islem if x["yontem"] == y and x["aralik"] == a]
    sonuc = {"olusturma": datetime.now(fm.TR).strftime("%d.%m.%Y %H:%M"), "bas": saat(bas_ms), "bit": saat(son_ms),
             "coin": len(sem), "veri_olan": len(ham), "hata": len(hata), "hata_ornek": hata[:5],
             "kural": {"kar_al": KAR, "zarar_kes": ZARAR, "komisyon": KOM, "alim": ALIM, "cuzdan": CUZDAN, "en_cok": EN_COK},
             "rastgele": ozet(rnd),
             "piyasa_ort_pct": round(statistics.fmean(v["hafta_pct"] for v in ham.values() if v["hafta_pct"] is not None), 2),
             "piyasa_medyan_pct": round(statistics.median(v["hafta_pct"] for v in ham.values() if v["hafta_pct"] is not None), 2),
             "gruplar": {}}
    gunler = sorted({saat(x["giris_ms"])[:5] for x in islem})
    for g, L in gruplar.items():
        bf = {}
        for ad in sorted({x["formasyon"] for x in L}):
            bf[ad] = ozet([x for x in L if x["formasyon"] == ad])
        sonuc["gruplar"][g] = {"ozet": ozet(L), "formasyon": bf, "cuzdan": cuzdan(L),
                               "gun": {d: ozet([x for x in L if saat(x["giris_ms"])[:5] == d]) for d in gunler},
                               "en_iyi": sorted(({"s": x["s"], "giris": saat(x["giris_ms"]), "net_pct": round(x["net_pct"], 2),
                                                  "neden": x["neden"]} for x in L), key=lambda z: -z["net_pct"])[:5],
                               "en_kotu": sorted(({"s": x["s"], "giris": saat(x["giris_ms"]), "net_pct": round(x["net_pct"], 2),
                                                   "neden": x["neden"]} for x in L), key=lambda z: z["net_pct"])[:5]}
    sonuc["sure_sn"] = round(time.time() - t0)
    out = fm.KOK / "analiz" / "sonuc"
    out.mkdir(parents=True, exist_ok=True)
    (out / "hafta_karsilastir.json").write_text(json.dumps(sonuc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({g: v["ozet"] | {"cuzdan_son": v["cuzdan"]["son"]} for g, v in sonuc["gruplar"].items()}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
