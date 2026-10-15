"""Canlı izleme: geçmiş testte (formasyon.py) rastgeleden iyi çıkan yukarı kırılımları yeni günlerde takip eder.
Yalnızca herkese açık mum verisini okur; bota ve Telegram'a bir şey eklemez.

Takip edilenler: 1 saatlik boğa bayrağı ve genişleyen formasyon; 4 saatlik genişleyen formasyon, dikdörtgen,
yükselen üçgen ve ikili dip yukarı kırılımları. Kırılım, hedef ve zarar-kes formasyon.py ile aynı kurallarla bulunur.
Her kırılım için durum: hedef (zarar-kesten önce), zarar-kes, süre doldu ya da açık (şu ana kadarki en yüksek/en düşük).
Özetteki başarı oranı yalnızca süresi tamamen dolmuş kırılımlardan hesaplanır.
TAKIP_BAS (TR saati) sonrasında kapanan kırılımlar ileri testtir; geçmiş testte görülmemiş veridir.
Piyasa: kırılım mumunda coinlerin yüzde kaçı kendi 50 mumluk ortalamasının üstünde. Geçmiş testte bu oran %50'nin altındayken
yukarı kırılımlar rastgeleden iyi değildi; özet bu yüzden güçlü/zayıf piyasaya göre de bölünür.
Sonuç: analiz/sonuc/formasyon_canli.json ve Türkçe liste analiz/sonuc/formasyon_canli.md
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

import formasyon as fm  # noqa: E402
import telegram_tarama as tt  # noqa: E402

SECIM = {"1h": ("boga_bayragi", "genisleyen"), "4h": ("genisleyen", "dikdortgen", "yukselen_ucgen", "ikili_dip")}
GECMIS_GUN = {"1h": 60, "4h": 240}
TAKIP_BAS = datetime.strptime(os.environ.get("TAKIP_BAS", "15.10.2026 19:00"), "%d.%m.%Y %H:%M").replace(tzinfo=fm.TR)
LISTE_GUN = int(os.environ.get("LISTE_GUN", "7"))
GUCLU = 0.5          # piyasa güçlü: coinlerin en az yarısı 50 mumluk ortalamasının üstünde


def saat(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, fm.TR).strftime("%d.%m %H:%M")


def durum(h, l, c, ts, t, hedef, stop, H, iv) -> dict:
    """Kırılım mumundan sonra ne oldu. Aynı mumda hedef ve zarar-kes birlikte: zarar-kes sayılır."""
    n = len(c)
    k = min(n - 1, t + H)
    hh, ll = h[t + 1:k + 1], l[t + 1:k + 1]
    kz, kb = np.flatnonzero(hh >= hedef), np.flatnonzero(ll <= stop)
    iz = int(kz[0]) if len(kz) else None
    ib = int(kb[0]) if len(kb) else None
    o = {"gecen_mum": int(len(hh)), "sure_mum": H,
         "en_yuksek_pct": round(float((hh.max() / c[t] - 1) * 100), 2) if len(hh) else 0.0,
         "en_dusuk_pct": round(float((ll.min() / c[t] - 1) * 100), 2) if len(ll) else 0.0,
         "simdi_pct": round(float((c[k] / c[t] - 1) * 100), 2)}
    if iz is not None and (ib is None or iz < ib):
        o.update(durum="hedef", zaman=saat(int(ts[t + 1 + iz]) + iv), getiri_pct=round(float((hedef / c[t] - 1) * 100), 2))
    elif ib is not None:
        o.update(durum="zarar-kes", zaman=saat(int(ts[t + 1 + ib]) + iv), getiri_pct=round(float((stop / c[t] - 1) * 100), 2))
    elif t + H < n:
        o.update(durum="süre doldu", getiri_pct=o["simdi_pct"])
    else:
        o.update(durum="açık")
    return o


def ozet(liste: list[dict]) -> dict:
    """Başarı yalnızca süresi tamamen dolmuş kırılımlardan hesaplanır. Süresi dolmamışlarda zarar-kes hedeften
    çabuk geldiği için, "sonuçlananlar"a bakmak başarıyı olduğundan düşük gösterir."""
    tam = [x for x in liste if x["gecen_mum"] >= x["sure_mum"]]
    acik = [x for x in liste if x["gecen_mum"] < x["sure_mum"]]
    o = {"n": len(liste), "suresi_dolan": len(tam), "suresi_dolmayan": len(acik),
         "suresi_dolmayan_hedef": sum(x["durum"] == "hedef" for x in acik),
         "suresi_dolmayan_zarar_kes": sum(x["durum"] == "zarar-kes" for x in acik)}
    if tam:
        o.update(hedef=sum(x["durum"] == "hedef" for x in tam), zarar_kes=sum(x["durum"] == "zarar-kes" for x in tam),
                 sure_doldu=sum(x["durum"] == "süre doldu" for x in tam),
                 basari_pct=round(sum(x["durum"] == "hedef" for x in tam) / len(tam) * 100, 1),
                 rastgele_basari_pct=round(statistics.fmean(x["rastgele_basari_pct"] for x in tam), 1),
                 ort_getiri_pct=round(statistics.fmean(x["getiri_pct"] for x in tam), 2))
    return o


def bolumler(liste: list[dict]) -> dict:
    return {"piyasa_guclu": ozet([x for x in liste if x["genislik"] is not None and x["genislik"] >= GUCLU]),
            "piyasa_zayif": ozet([x for x in liste if x["genislik"] is not None and x["genislik"] < GUCLU])}


# ---------------------------------------------------------------------- Türkçe rapor
ARALIK_AD = {"1h": "1 saat", "4h": "4 saat"}
DURUM_AD = {"hedef": "Hedef ✓", "zarar-kes": "Zarar-kes ✗", "süre doldu": "Süre doldu", "açık": "Açık"}


def sayi(x: float, k: int = 2) -> str:
    """Türkçe sayı biçimi: 1.234,56"""
    return f"{abs(x):,.{k}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def yuzde(x: float) -> str:
    return ("+" if x > 0 else "–" if x < 0 else "") + "%" + sayi(x)


def fiyat(x: float) -> str:
    s = sayi(x, 2 if x >= 100 else 4 if x >= 1 else 6)
    return s.rstrip("0").rstrip(",")


def tablo(liste: list[dict]) -> str:
    out = ["| Coin | Zaman | Formasyon | Kırılım (TR) | Piyasa | Giriş | Hedef | Zarar-kes | Durum | En yüksek | En düşük | Şimdi |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for x in liste:
        durum_ = DURUM_AD[x["durum"]] + (f" ({x['zaman']})" if x.get("zaman") else "")
        out.append(f"| {x['s'].replace('_TRY', '')} | {ARALIK_AD[x['aralik']]} | {x['formasyon']} | {x['kirilim']} | {piyasa_metin(x['genislik'])} | {fiyat(x['fiyat'])} | "
                   f"{fiyat(x['hedef'])} ({yuzde(x['hedef_pct'])}) | {fiyat(x['stop'])} ({yuzde(x['stop_pct'])}) | {durum_} | "
                   f"{yuzde(x['en_yuksek_pct'])} | {yuzde(x['en_dusuk_pct'])} | {yuzde(x['simdi_pct'])} |")
    return "\n".join(out)


def piyasa_metin(g: float | None) -> str:
    if g is None:
        return "?"
    return ("güçlü" if g >= GUCLU else "zayıf") + f" %{g * 100:.0f}"


def ozet_satir(o: dict) -> str:
    if not o["n"]:
        return "Henüz kırılım yok."
    s = f"{o['n']} kırılım."
    if o.get("suresi_dolan"):
        s += (f" Süresi dolan {o['suresi_dolan']} kırılımdan {o['hedef']} tanesi hedefe zarar-kesten önce ulaştı, {o['zarar_kes']} tanesinde "
              f"zarar-kes geldi, {o['sure_doldu']} tanesinde ikisi de olmadı. Başarı %{sayi(o['basari_pct'], 1)}; aynı coinde rastgele "
              f"girişte beklenen %{sayi(o['rastgele_basari_pct'], 1)}. İşlem başına ortalama {yuzde(o['ort_getiri_pct'])}.")
    if o.get("suresi_dolmayan"):
        s += (f" Süresi dolmayan {o['suresi_dolmayan']} kırılımdan şimdiye kadar {o['suresi_dolmayan_hedef']} tanesi hedefe, "
              f"{o['suresi_dolmayan_zarar_kes']} tanesi zarar-kese ulaştı; diğerleri açık.")
    return s


def rapor_md(d: dict) -> str:
    ileri = [x for x in d["liste"] if x["ileri_test"]]
    gecmis = sorted((x for x in d["liste"] if not x["ileri_test"]), key=lambda x: -x["kirilim_ms"])
    md = ["# Formasyon kırılımları: canlı izleme",
          "",
          f"Güncelleme: {d['olusturma']} (TR saati). Bota ve Telegram'a bir şey eklenmedi; bu liste yalnızca burada takip için.",
          "",
          "İzlenenler (geçmiş testte rastgeleden iyi çıkan yukarı kırılımlar): 1 saatlik boğa bayrağı ve genişleyen formasyon; "
          "4 saatlik genişleyen formasyon, dikdörtgen, yükselen üçgen ve ikili dip. Hedef ve zarar-kes geçmiş testteki kuralla hesaplandı. "
          "Aynı mumda ikisine birden değerse zarar-kes sayılır.",
          "",
          "**Şu anki piyasa:** " + ", ".join(f"{ARALIK_AD[a]}lik grafikte {piyasa_metin(g)}" for a, g in d["simdiki_piyasa"].items())
          + " (coinlerin yüzde kaçı 50 mumluk ortalamasının üstünde). Geçmiş testte piyasa zayıfken (%50'nin altı) 4 saatlik genişleyen "
          "formasyon, dikdörtgen ve yükselen üçgen ile 1 saatlik genişleyen formasyon ortalamada kaybettirdi; 1 saatlik boğa bayrağı ve "
          "4 saatlik ikili dip piyasadan pek etkilenmedi.",
          "",
          f"## Yeni kırılımlar ({d['takip_bas']} sonrası)",
          "",
          "Bu kırılımlar geçmiş testte kullanılmayan yeni veridir; asıl deneme bunlar.",
          "",
          "**Özet:** " + ozet_satir(d["ileri_test"]["hepsi"]),
          "",
          "**Yalnızca güçlü piyasada:** " + ozet_satir(d["ileri_test"]["piyasa_guclu"]),
          ""]
    if ileri:
        md += [tablo(ileri), ""]
    md += [f"## Son {d['son_gunler']['gun']} günün kırılımları (karşılaştırma için)", "",
           "**Özet:** " + ozet_satir(d["son_gunler"]["hepsi"]), "",
           "**Yalnızca güçlü piyasada:** " + ozet_satir(d["son_gunler"]["piyasa_guclu"]), "",
           "**Zayıf piyasada:** " + ozet_satir(d["son_gunler"]["piyasa_zayif"]), "",
           "Not: süresi dolmamış kırılımlarda zarar-kes hedeften daha çabuk geldiği için, başarı oranı yalnızca süresi dolanlardan hesaplandı.", ""]
    if gecmis:
        md += [tablo(gecmis), ""]
    md += ["'Şimdi': kırılımdan bu yana ya da süre bittiyse süre sonundaki fiyat değişimi. Komisyon hariç. Yatırım tavsiyesi değildir.", ""]
    return "\n".join(md)


def main():
    t0 = time.time()
    sem = [s for s in tt.semboller() if s["s"].split("_")[0] not in fm.STABIL]
    takip_ms = int(TAKIP_BAS.timestamp() * 1000)
    liste_ms = min(takip_ms, int(time.time() * 1000) - LISTE_GUN * 86_400_000)
    kayit, hata, simdiki = [], [], {}
    for aralik, secilen in SECIM.items():
        iv = fm.IVS[aralik]
        bit_ms = int(time.time() * 1000) // iv * iv
        bas_ms = bit_ms - GECMIS_GUN[aralik] * 86_400_000
        bayraklar, olaylar = [], []

        def bir(s):
            try:
                rows = fm.mumlar(s["bn"], aralik, bas_ms, bit_ms)
                if len(rows) < 60:
                    return
                pb = fm.piyasa_bayrak(rows)
                if pb:
                    bayraklar.append(pb)
                h = np.array([float(r[2]) for r in rows])
                l = np.array([float(r[3]) for r in rows])
                c = np.array([float(r[4]) for r in rows])
                ts = np.array([int(r[0]) for r in rows], dtype=np.int64)
                P = fm.zigzag(h, l, fm.atr_dizi(h, l, c))
                rng = np.random.default_rng(sum(map(ord, s["s"])) * 7 + len(aralik))
                for f, t, yon, hedef, stop, hp, sp_, H in fm.kirilimlar(h, l, c, P):
                    kapanis = int(ts[t]) + iv
                    if yon < 0 or f["ad"] not in secilen or kapanis < liste_ms:
                        continue
                    rs = fm.rastgele_kiyas(h, l, c, yon, hp, sp_, H, rng)
                    olaylar.append({"s": s["s"], "aralik": aralik, "ts": int(ts[t]), "ad": f["ad"], "formasyon": fm.ADLAR[f["ad"]][0],
                                  "kirilim": saat(kapanis), "kirilim_ms": kapanis, "ileri_test": kapanis >= takip_ms,
                                  "fiyat": float(f"{c[t]:.6g}"), "hedef": float(f"{hedef:.6g}"), "stop": float(f"{stop:.6g}"),
                                  "hedef_pct": round(hp, 2), "stop_pct": round(-sp_, 2),
                                  "rastgele_basari_pct": round(statistics.fmean(1 if x[0] == 1 else 0 for x in rs) * 100, 1) if rs else None,
                                  **durum(h, l, c, ts, t, hedef, stop, H, iv)})
            except Exception as e:
                hata.append(f"{aralik} {s['s']}: {type(e).__name__}: {e}")

        with ThreadPoolExecutor(8) as ex:
            list(ex.map(bir, sem))
        genislik = fm.genislik_haritasi(bayraklar)
        for x in olaylar:
            x["genislik"] = genislik.get(x.pop("ts"))
        kayit.extend(olaylar)
        simdiki[aralik] = genislik[max(genislik)] if genislik else None
    kayit.sort(key=lambda x: (x["kirilim_ms"], x["s"]))
    for x in kayit:
        if x["rastgele_basari_pct"] is None:
            x["rastgele_basari_pct"] = 0.0
    ileri = [x for x in kayit if x["ileri_test"]]
    sonuc = {"olusturma": datetime.now(fm.TR).strftime("%d.%m.%Y %H:%M"), "takip_bas": TAKIP_BAS.strftime("%d.%m.%Y %H:%M"),
             "coin": len(sem), "hata": len(hata), "hata_ornek": hata[:5],
             "simdiki_piyasa": simdiki,
             "ileri_test": {"hepsi": ozet(ileri), **bolumler(ileri),
                            **{f"{a} {ad}": ozet([x for x in ileri if x["aralik"] == a and x["ad"] == ad])
                               for a, adlar in SECIM.items() for ad in adlar}},
             "son_gunler": {"gun": LISTE_GUN, "hepsi": ozet(kayit), **bolumler(kayit)},
             "liste": kayit, "sure_sn": round(time.time() - t0)}
    out = KOK / "analiz" / "sonuc"
    out.mkdir(parents=True, exist_ok=True)
    (out / "formasyon_canli.json").write_text(json.dumps(sonuc, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "formasyon_canli.md").write_text(rapor_md(sonuc), encoding="utf-8")
    print(json.dumps({k: sonuc[k] for k in ("ileri_test", "son_gunler", "hata", "sure_sn")}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
