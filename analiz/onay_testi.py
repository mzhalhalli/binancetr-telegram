"""Formasyon kırılımından sonra onay: kırılım mumunda almak ile 1-2 yeşil mum ya da retest (çizgiye geri dönüş) bekleyip almak karşılaştırılır.

Kırılımlar analiz/formasyon.py ile aynı yolla bulunur (aynı zigzag, aynı formasyonlar, geleceğe bakılmaz).
Yalnızca botta kullanılan yukarı kırılımlar: 15m beşi de (boğa bayrağı, genişleyen, dikdörtgen, yükselen üçgen, ikili dip);
1h boğa bayrağı ve genişleyen; 4h genişleyen, dikdörtgen, yükselen üçgen, ikili dip. Süre: 15m 30 gün, 1h 180 gün, 4h 730 gün.

Girişler:
  K0 = kırılım mumunun kapanışı (botun bugünkü kuralı)
  Y1 = kırılımdan sonraki 1 mum yeşil (kapanış > açılış) ve kapanış hâlâ kırılım çizgisinin üstünde: o mumun kapanışı
  Y2 = kırılımdan sonraki 2 mum da yeşil ve 2. mumun kapanışı hâlâ çizginin üstünde: 2. mumun kapanışı
  RT = kırılımdan sonra retest: RT_PENCERE mum içinde bir mumun dibi kırılım çizgisine RT_TOL x ATR kadar yaklaşır
       (dokunur) ama kapanış çizginin altına inmez; dokunuştan sonra (dokunan mum dahil) ilk yeşil ve çizgi üstü kapanış = giriş.
       Dokunuştan önce ya da sonra bir kapanış çizginin altına inerse kırılım bozulmuş sayılır, işlem yok.
  Onay gelmezse işlem yok (sayılır: "onay_yok"). Onay geldiğinde fiyat hedefi geçmişse işlem yok ("hedef_gecti").
Çıkışlar:
  F = formasyonun kendi hedefi ve zarar-kesi (aynı fiyat seviyeleri; yüzde girişe göre yeniden hesaplanır)
  S = sabit kâr al %2 / zarar kes %5 (gerçek cüzdan ayarı)
Her ikisinde süre sınırı H mum (formasyon boyuna göre, en çok 150). Aynı mumda ikisi birden: kayıp. Ücret dahil değil.
Karşılaştırma: aynı coinde rastgele mumlardan aynı hedef/zarar-kes uzaklığıyla giriş (RASTGELE adet).
Piyasa: kırılım anında coinlerin kaçta kaçı kendi MA50'sinin üstünde (genişlik >= %50 = güçlü).
Sonuç: analiz/sonuc/onay_testi.json
"""
from __future__ import annotations

import json
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

SECILI = {"15m": ["boga_bayragi", "genisleyen", "dikdortgen", "yukselen_ucgen", "ikili_dip"],
          "1h": ["boga_bayragi", "genisleyen"], "4h": ["genisleyen", "dikdortgen", "yukselen_ucgen", "ikili_dip"]}
KAR, ZARAR = 2.0, 5.0
RT_PENCERE = 10      # retest en çok bu kadar mum içinde aranır
RT_TOL = 0.25        # dokunuş: mum dibi <= çizgi + RT_TOL x ATR
GIRISLER = ("K0", "Y1", "Y2", "RT")


def retest_giris(o, l, c, a, cizgi, t, n):
    """Retest sonrası giriş mumu ya da None (bozuldu / pencerede tamamlanmadı)."""
    dokundu = False
    for i in range(t + 1, min(n, t + RT_PENCERE + 1)):
        cz = cizgi(i)
        if c[i] < cz:
            return None
        if not dokundu and l[i] <= cz + RT_TOL * a[i]:
            dokundu = True
        if dokundu and c[i] > o[i]:
            return i
    return None


def olc(h, l, c, e, hedef, stop, H, rng):
    s = fm.sonuc_olc(h, l, c, e, 1, hedef, stop, H)
    if s is None:
        return None
    hp, sp_ = (hedef / c[e] - 1) * 100, (1 - stop / c[e]) * 100
    rs = fm.rastgele_kiyas(h, l, c, 1, hp, sp_, H, rng)
    if not rs:
        return None
    return {"sonuc": s[0], "getiri": s[1], "r_kazanc": statistics.fmean(1 if x[0] == 1 else 0 for x in rs),
            "r_getiri": statistics.fmean(x[1] for x in rs), "hedef_pct": hp, "stop_pct": sp_}


def olaylar(rows, rng):
    o = np.array([float(r[1]) for r in rows])
    h = np.array([float(r[2]) for r in rows])
    l = np.array([float(r[3]) for r in rows])
    c = np.array([float(r[4]) for r in rows])
    ts = np.array([int(r[0]) for r in rows], dtype=np.int64)
    n = len(c)
    if n < 60:
        return []
    a = fm.atr_dizi(h, l, c)
    P = fm.zigzag(h, l, a)
    out = []
    for f, t, yon, hedef, stop, hp, sp_, H in fm.kirilimlar(h, l, c, P):
        if yon != 1:
            continue
        x = {"ad": f["ad"], "ts": int(ts[t])}
        for ad, k in (("K0", 0), ("Y1", 1), ("Y2", 2), ("RT", None)):
            if k is None:
                e = retest_giris(o, l, c, a, f["ust"], t, n)
                if e is None:
                    x[ad] = "onay_yok"
                    continue
            else:
                e = t + k
            if e + H >= n:
                x[ad] = None
                continue
            if k and not all(c[t + i] > o[t + i] and c[t + i] > f["ust"](t + i) for i in range(1, k + 1)):
                x[ad] = "onay_yok"
                continue
            if c[e] >= hedef:
                x[ad] = "hedef_gecti"
                continue
            if c[e] <= stop:
                x[ad] = "onay_yok"
                continue
            fF = olc(h, l, c, e, hedef, stop, H, rng)
            fS = olc(h, l, c, e, c[e] * (1 + KAR / 100), c[e] * (1 - ZARAR / 100), H, rng)
            x[ad] = {"F": fF, "S": fS} if fF and fS else None
        if x["K0"] is None or isinstance(x["K0"], str):
            continue
        out.append(x)
    return out


def ozet(ol, ad, plan):
    v = [x[ad][plan] for x in ol if isinstance(x[ad], dict)]
    n_hepsi = len(ol)
    if not v:
        return {"kirilim": n_hepsi, "islem": 0}
    return {"kirilim": n_hepsi, "islem": len(v),
            "onay_yok": sum(x[ad] == "onay_yok" for x in ol), "hedef_gecti": sum(x[ad] == "hedef_gecti" for x in ol),
            "basari_pct": round(sum(y["sonuc"] == 1 for y in v) / len(v) * 100, 1),
            "stop_pct": round(sum(y["sonuc"] == -1 for y in v) / len(v) * 100, 1),
            "rastgele_basari_pct": round(statistics.fmean(y["r_kazanc"] for y in v) * 100, 1),
            "ort_pct": round(statistics.fmean(y["getiri"] for y in v), 2),
            "rastgele_ort_pct": round(statistics.fmean(y["r_getiri"] for y in v), 2),
            "hedef_pct_medyan": round(statistics.median(y["hedef_pct"] for y in v), 2),
            "stop_pct_medyan": round(statistics.median(y["stop_pct"] for y in v), 2)}


def tablo(ol, orta):
    d = {}
    for ad in GIRISLER:
        for plan in ("F", "S"):
            d[f"{ad}_{plan}"] = {**ozet(ol, ad, plan),
                                 "ilk_yari": ozet([x for x in ol if x["ts"] < orta], ad, plan),
                                 "ikinci_yari": ozet([x for x in ol if x["ts"] >= orta], ad, plan)}
    return d


def main():
    t0 = time.time()
    sem = [s for s in fm.tt.semboller() if s["s"].split("_")[0] not in fm.STABIL]
    sonuc = {"olusturma": datetime.now(fm.TR).strftime("%d.%m.%Y %H:%M"), "coin": len(sem), "kar_al": KAR, "zarar_kes": ZARAR,
             "araliklar": {}}
    for aralik, secili in SECILI.items():
        iv = fm.IVS[aralik]
        bit_ms = int(time.time() * 1000) // iv * iv
        bas_ms = bit_ms - fm.GUNLER[aralik] * 86_400_000
        hepsi, piyasa_ham, hata = [], [], []
        kilit = threading.Lock()

        def bir(s):
            try:
                rows = fm.mumlar(s["bn"], aralik, bas_ms, bit_ms)
                rng = np.random.default_rng(sum(map(ord, s["s"])) * 7 + len(aralik))
                pb = fm.piyasa_bayrak(rows)
                ol = [x for x in olaylar(rows, rng) if x["ad"] in secili]
                with kilit:
                    if pb:
                        piyasa_ham.append(pb)
                    hepsi.extend(ol)
            except Exception as e:
                hata.append(f"{s['s']}: {type(e).__name__}: {e}")

        with ThreadPoolExecutor(8) as ex:
            list(ex.map(bir, sem))
        genislik = fm.genislik_haritasi(piyasa_ham)
        for x in hepsi:
            x["genislik"] = genislik.get(x["ts"])
        tsl = sorted(x["ts"] for x in hepsi)
        orta = tsl[len(tsl) // 2] if tsl else 0
        A = {}
        for ad in secili:
            ol = [x for x in hepsi if x["ad"] == ad]
            A[ad] = {"hepsi": tablo(ol, orta),
                     "piyasa_guclu": tablo([x for x in ol if x["genislik"] is not None and x["genislik"] >= 0.5], orta)}
        A["_toplam"] = {"hepsi": tablo(hepsi, orta),
                        "piyasa_guclu": tablo([x for x in hepsi if x["genislik"] is not None and x["genislik"] >= 0.5], orta)}
        sonuc["araliklar"][aralik] = {
            "bas": datetime.fromtimestamp(bas_ms / 1000, fm.TR).strftime("%d.%m.%Y"),
            "bit": datetime.fromtimestamp(bit_ms / 1000, fm.TR).strftime("%d.%m.%Y"),
            "orta": datetime.fromtimestamp(orta / 1000, fm.TR).strftime("%d.%m.%Y") if orta else None,
            "hata": len(hata), "hata_ornek": hata[:3], "formasyon": A}
        print(aralik, len(hepsi), "kırılım", round(time.time() - t0), "sn", flush=True)
    sonuc["sure_sn"] = round(time.time() - t0)
    out = fm.KOK / "analiz" / "sonuc"
    out.mkdir(parents=True, exist_ok=True)
    (out / "onay_testi.json").write_text(json.dumps(sonuc, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
