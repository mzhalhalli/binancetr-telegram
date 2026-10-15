"""Cüzdan denemesi: belli saatler arasında 15 dakikalık erken uyarı (btr/erken.py, 8 şart) veren coinlere alım yapılsaydı
cüzdan ne olurdu. Yalnızca herkese açık mum verisini okur; gerçek alım yapılmaz.

Senaryolar analiz/cuzdan_senaryo.json dosyasında. Her senaryo için:
- bas ile bit arasında kapanan her 15 dakikalık mumda tüm coinler taranır; erken uyarı veren coin o mumun kapanışından alınır.
- Her alım başlangıç cüzdanının `pay` oranı kadar. Nakit yetmezse sinyal atlanır. Aynı mumda birden çok sinyal varsa
  alfabetik sırayla alınır.
- `satis`: [kâr %, eldeki miktarın satılan %] adımları. Fiyat alıştan o kadar yükselince (mumun en yükseği) eldekinin o kadarı
  hedef fiyattan satılır. Aynı mumda birden çok adıma ulaşılırsa hepsi o mumda yapılır.
- `stop`: alıştan bu kadar % düşünce (mumun en düşüğü) eldekinin tamamı satılır. Mum zarar-kesin altında açılırsa açılış
  fiyatından satılır. Aynı mumda hem zarar-kese hem hedefe değilirse önce zarar-kes sayılır (temkinli).
- Bir coin elde varken gelen sinyalde ek alım yapılmaz; tamamen satıldıktan sonra yeni sinyalde tekrar alınır.
- Elde kalanlar son kapanan mumun fiyatıyla değerlenir. Her alım ve satışta %0,1 komisyon.
- Yüzdeler TRY fiyatıyla hesaplanır (dolar/TL kurunun bir gün içinde değişmediği varsayılır).
Sonuç: analiz/sonuc/cuzdan_<ad>.json ve analiz/sonuc/cuzdan_<ad>.md
"""
from __future__ import annotations

import json
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

IV = yp.IVS["15m"]
KOMISYON = 0.001


def saat(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, yp.TR).strftime("%d.%m %H:%M")


def simule(veri: dict, sen: dict, son_ms: int) -> dict:
    """Mum mum ilerler: önce eldeki coinlerde zarar-kes ve kademeli satış, sonra mum kapanışında yeni sinyallere alım."""
    bas_ms, bit_ms = yp.zaman_ms(sen["bas"]), yp.zaman_ms(sen["bit"])
    alim_usd = sen["cuzdan"] * sen["pay"]
    stop = sen.get("stop")
    nakit = float(sen["cuzdan"])
    acik: dict[str, dict] = {}
    islemler, atlanan = [], []

    def sat(p, miktar, fiyat, t, neden):
        nonlocal nakit
        para = float(miktar * fiyat * (1 - KOMISYON))
        nakit += para
        p["nakit"] += para
        p["kalan"] -= miktar
        p["satislar"].append({"saat": saat(t + IV), "neden": neden, "oran_pct": round(miktar / p["adet"] * 100, 1),
                              "fiyat": float(fiyat), "usd": round(para, 2)})

    for t in range(bas_ms, son_ms, IV):
        for s, p in list(acik.items()):
            v = veri[s]
            i = v["idx"].get(t)
            if i is None:
                continue
            if stop is not None and v["l"][i] <= p["giris"] * (1 - stop / 100):
                sat(p, p["kalan"], min(p["giris"] * (1 - stop / 100), v["o"][i]), t, f"zarar-kes –%{stop:g}")
            else:
                for k, (kar, oran) in enumerate(sen["satis"]):
                    if k < p["adim"] or v["h"][i] < p["giris"] * (1 + kar / 100):
                        continue
                    sat(p, p["kalan"] * oran / 100, p["giris"] * (1 + kar / 100), t, f"+%{kar:g} hedef")
                    p["adim"] = k + 1
            if p["kalan"] <= p["adet"] * 1e-9:
                p["kalan"] = 0.0
                islemler.append(kapat(p, v, i))
                del acik[s]
        if t + IV > bit_ms:
            continue
        for s in sorted(k for k, v in veri.items() if t in v["sinyal"]):
            if s in acik:
                continue
            if nakit < alim_usd - 1e-9:
                atlanan.append({"s": s, "sinyal": saat(t + IV)})
                continue
            i = veri[s]["idx"][t]
            giris = float(veri[s]["c"][i])
            adet = alim_usd * (1 - KOMISYON) / giris
            nakit -= alim_usd
            acik[s] = {"s": s, "alis": saat(t + IV), "i": i, "giris": giris, "para": alim_usd, "adet": adet, "kalan": adet,
                       "nakit": 0.0, "adim": 0, "satislar": []}
    for s, p in acik.items():
        islemler.append(kapat(p, veri[s], len(veri[s]["c"]) - 1))
    islemler.sort(key=lambda x: (x["alis_ms"], x["s"]))
    elde = sum(x["kalan_deger"] for x in islemler)
    return {"nakit": nakit, "elde_deger": elde, "toplam": nakit + elde, "islemler": islemler, "atlanan": atlanan}


def kapat(p: dict, v: dict, j: int) -> dict:
    """İşlemin sonucu; elde kalan kısım son kapanan mumun fiyatıyla değerlenir. En yüksek/en düşük j. muma kadar."""
    son = v["c"][-1]
    kalan_deger = float(p["kalan"] * son * (1 - KOMISYON))
    deger = float(p["nakit"] + kalan_deger)
    hh, ll = v["h"][p["i"] + 1:j + 1], v["l"][p["i"] + 1:j + 1]
    return {"s": p["s"], "alis": p["alis"], "alis_ms": int(v["ts"][p["i"]]), "giris": p["giris"], "para": round(p["para"], 2),
            "satislar": p["satislar"], "zarar_kes": any(x["neden"].startswith("zarar") for x in p["satislar"]),
            "hedef_adim": sum(x["neden"].endswith("hedef") for x in p["satislar"]),
            "elde": bool(p["kalan"] > 0), "elde_oran_pct": round(p["kalan"] / p["adet"] * 100, 1), "kalan_deger": round(kalan_deger, 2),
            "deger": round(deger, 2), "kar": round(deger - p["para"], 2), "getiri_pct": round((deger / p["para"] - 1) * 100, 2),
            "en_yuksek_pct": round(float((hh.max() / p["giris"] - 1) * 100), 2) if len(hh) else 0.0,
            "en_dusuk_pct": round(float((ll.min() / p["giris"] - 1) * 100), 2) if len(ll) else 0.0,
            "simdi_pct": round(float((son / p["giris"] - 1) * 100), 2)}


def main():
    t0 = time.time()
    senaryolar = json.loads((KOK / "analiz" / "cuzdan_senaryo.json").read_text(encoding="utf-8"))
    ilk_ms = min(yp.zaman_ms(s["bas"]) for s in senaryolar)
    son_ms = int(time.time() * 1000) // IV * IV          # bu andan önce kapanan mumlar
    sem = [s for s in tt.semboller() if s["s"].split("_")[0] not in yp.STABIL]
    ham, hata = {}, []

    def bir(s):
        try:
            rows = yp.mumlar(s["bn"], ilk_ms - yp.ISINMA * IV, son_ms, IV)
            if len(rows) < yp.ISINMA:
                return
            ts = np.array([int(r[0]) for r in rows], dtype=np.int64)
            ham[s["s"]] = {"ts": ts, "idx": {int(x): k for k, x in enumerate(ts)},
                           "o": np.array([float(r[1]) for r in rows]), "h": np.array([float(r[2]) for r in rows]),
                           "l": np.array([float(r[3]) for r in rows]), "c": np.array([float(r[4]) for r in rows]),
                           "erken": yp.erken_kurali(yp.ozellik(rows)["F"])}
        except Exception as e:
            hata.append(f"{s['s']}: {type(e).__name__}: {e}")

    with ThreadPoolExecutor(8) as ex:
        list(ex.map(bir, sem))

    out = KOK / "analiz" / "sonuc"
    out.mkdir(parents=True, exist_ok=True)
    for sen in senaryolar:
        bas_ms, bit_ms = yp.zaman_ms(sen["bas"]), yp.zaman_ms(sen["bit"])
        veri = {k: {**v, "sinyal": {int(x) for x in v["ts"][v["erken"] & (v["ts"] + IV > bas_ms) & (v["ts"] + IV <= bit_ms)]}}
                for k, v in ham.items()}
        S = simule(veri, sen, son_ms)
        degisim = [float(v["c"][-1] / v["c"][v["idx"][bas_ms] - 1] - 1) * 100 for v in veri.values()
                   if bas_ms in v["idx"] and v["idx"][bas_ms] > 0]
        isl = S["islemler"]
        sonuc = {"olusturma": datetime.now(yp.TR).strftime("%d.%m.%Y %H:%M"), **sen, "alim_usd": sen["cuzdan"] * sen["pay"],
                 "son_mum": saat(son_ms), "coin": len(sem), "veri_olan": len(veri), "hata": len(hata), "hata_ornek": hata[:5],
                 "sinyal_sayisi": sum(len(v["sinyal"]) for v in veri.values()),
                 "sinyal_coin": sum(bool(v["sinyal"]) for v in veri.values()),
                 "alim_sayisi": len(isl), "alim_coin": len({x["s"] for x in isl}), "atlanan_sayisi": len(S["atlanan"]),
                 "hedef_adim_sayisi": [sum(x["hedef_adim"] > k for x in isl) for k in range(len(sen["satis"]))],
                 "zarar_kes_sayisi": sum(x["zarar_kes"] for x in isl), "elde_kalan": sum(x["elde"] for x in isl),
                 "karda": sum(x["kar"] > 0 for x in isl), "zararda": sum(x["kar"] < 0 for x in isl),
                 "nakit": round(S["nakit"], 2), "elde_deger": round(S["elde_deger"], 2), "son_deger": round(S["toplam"], 2),
                 "kar": round(S["toplam"] - sen["cuzdan"], 2), "getiri_pct": round((S["toplam"] / sen["cuzdan"] - 1) * 100, 2),
                 "piyasa_coin": len(degisim), "piyasa_ort_pct": round(statistics.fmean(degisim), 2) if degisim else None,
                 "piyasa_medyan_pct": round(statistics.median(degisim), 2) if degisim else None,
                 "islemler": isl, "atlanan": S["atlanan"], "sure_sn": round(time.time() - t0)}
        (out / f"cuzdan_{sen['ad']}.json").write_text(json.dumps(sonuc, ensure_ascii=False, indent=1), encoding="utf-8")
        (out / f"cuzdan_{sen['ad']}.md").write_text(rapor_md(sonuc), encoding="utf-8")
        print(json.dumps({k: v for k, v in sonuc.items() if k not in ("islemler", "atlanan")}, ensure_ascii=False, indent=1))


# ---------------------------------------------------------------------- Türkçe rapor
def sayi(x: float, k: int = 2) -> str:
    return f"{abs(x):,.{k}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def usd(x: float) -> str:
    return ("–" if x < 0 else "") + sayi(x) + " USD"


def yuzde(x: float) -> str:
    return ("+" if x > 0 else "–" if x < 0 else "") + "%" + sayi(x)


def fiyat(x: float) -> str:
    return sayi(x, 2 if x >= 100 else 4 if x >= 1 else 6).rstrip("0").rstrip(",")


def kural_metni(d: dict) -> list[str]:
    adimlar = []
    for k, (kar, oran) in enumerate(d["satis"]):
        if oran >= 100:
            ne = "tamamı" if k == 0 else "elde kalanın tamamı"
        else:
            ne = f"%{oran:g}'si" if k == 0 else f"elde kalanın %{oran:g}'si"
        adimlar.append(f"%{kar:g} kârda {ne}")
    stop = (f"Alıştan %{d['stop']:g} düşünce elde kalanın tamamı satıldı (zarar-kes)." if d.get("stop") is not None
            else "Zarar-kes yok.")
    return [f"- {d['bas']} ile {d['bit'][-5:]} arasında kapanan her 15 dakikalık mumda {d['coin']} TRY çifti erken uyarı şartlarıyla tarandı.",
            f"- Erken uyarı veren coin, sinyal mumunun kapanış fiyatından alındı. Her alım {usd(d['alim_usd'])} "
            f"(cüzdanın %{sayi(d['pay'] * 100, 0)}'i). Nakit yetmeyince sinyal atlandı.",
            "- Satış: " + "; ".join(adimlar) + " satıldı. " + stop,
            "- Satıştan dönen para yeni alımlarda kullanıldı. Elde varken gelen sinyalde ek alım yapılmadı; tamamen satılan coin yeni sinyalde tekrar alındı.",
            "- Elde kalanlar son kapanan mumun fiyatıyla değerlendi. Her alım ve satışta %0,1 komisyon düşüldü."]


def rapor_md(d: dict) -> str:
    md = [f"# Erken uyarı cüzdan denemesi ({d['bas']} – {d['bit'][-5:]})", "",
          f"Hesaplama: {d['olusturma']} (TR saati); son kapanan 15 dakikalık mum {d['son_mum']}. Veri Binance'in gerçek 15 dakikalık "
          "TRY mumları. Gerçek alım yapılmadı; 'yapsaydık ne olurdu' hesabı.", "",
          "## Kurallar", "", *kural_metni(d), "", "## Sonuç", ""]
    if not d["alim_sayisi"]:
        md += [f"Bu saatler arasında erken uyarı veren coin olmadı; cüzdan {usd(d['cuzdan'])} olarak kaldı.", ""]
        return "\n".join(md)
    adim = "; ".join(f"%{kar:g} hedefine ulaşan: {n}" for (kar, _), n in zip(d["satis"], d["hedef_adim_sayisi"]))
    md += [f"- Cüzdanın şimdiki değeri: **{usd(d['son_deger'])}** ({yuzde(d['getiri_pct'])}, {usd(d['kar'])})",
           f"  - Nakit: {usd(d['nakit'])}; elde kalan {d['elde_kalan']} coinin değeri: {usd(d['elde_deger'])}",
           f"- Erken uyarı: {d['sinyal_coin']} coinde {d['sinyal_sayisi']} sinyal. Yapılan alım {d['alim_sayisi']} ({d['alim_coin']} coin); "
           f"nakit yetmediği için alınamayan sinyal {d['atlanan_sayisi']}.",
           f"- {adim}; zarar-kese düşen: {d['zarar_kes_sayisi']}",
           f"- Alımların {d['karda']} tanesi kârda, {d['zararda']} tanesi zararda."]
    if d.get("piyasa_ort_pct") is not None:
        md += [f"- Karşılaştırma: {d['bas'][-5:]}'ten bu yana {d['piyasa_coin']} coinin fiyatı ortalama {yuzde(d['piyasa_ort_pct'])} "
               f"(ortanca {yuzde(d['piyasa_medyan_pct'])}) değişti."]
    md += ["", "## Alımlar", "",
           "| Coin | Alış (TR) | Alış fiyatı | Satışlar | Elde kalan | En yüksek | En düşük | Şimdi | Değer | Kâr/zarar |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for a in d["islemler"]:
        satis = "<br>".join(f"{x['saat']} {x['neden']} (%{sayi(x['oran_pct'], 0)})" for x in a["satislar"]) or "–"
        md.append(f"| {a['s'].replace('_TRY', '')} | {a['alis']} | {fiyat(a['giris'])} | {satis} | %{sayi(a['elde_oran_pct'], 0)} | "
                  f"{yuzde(a['en_yuksek_pct'])} | {yuzde(a['en_dusuk_pct'])} | {yuzde(a['simdi_pct'])} | {usd(a['deger'])} | {usd(a['kar'])} |")
    if d["atlanan"]:
        ilk = {}
        for a in d["atlanan"]:
            ilk.setdefault(a["s"], a["sinyal"])
        md += ["", f"Nakit yetmediği için alınamayan {len(ilk)} coin (ilk sinyal saati): "
               + ", ".join(f"{s.replace('_TRY', '')} ({z})" for s, z in ilk.items()) + "."]
    elde = [a for a in d["islemler"] if a["elde"]]
    md += ["", f"## Cüzdanın son durumu ({d['son_mum']})", ""]
    if elde:
        md += ["| Coin | Alış (TR) | Alış fiyatı | Şimdiki fiyat | Elde kalan | Elde kalanın değeri | İşlemin kâr/zararı |",
               "|---|---|---|---|---|---|---|"]
        for a in elde:
            md.append(f"| {a['s'].replace('_TRY', '')} | {a['alis']} | {fiyat(a['giris'])} | {fiyat(a['giris'] * (1 + a['simdi_pct'] / 100))} "
                      f"({yuzde(a['simdi_pct'])}) | %{sayi(a['elde_oran_pct'], 0)} | {usd(a['kalan_deger'])} | {usd(a['kar'])} ({yuzde(a['getiri_pct'])}) |")
        md += [""]
    else:
        md += ["Elde coin kalmadı.", ""]
    md += ["| | Tutar |", "|---|---|",
           f"| Nakit | {usd(d['nakit'])} |",
           f"| Eldeki {len(elde)} coinin değeri | {usd(d['elde_deger'])} |",
           f"| **Cüzdan toplamı** | **{usd(d['son_deger'])}** |",
           f"| **Kâr/zarar** | **{usd(d['kar'])} ({yuzde(d['getiri_pct'])})** |", "",
           "'Elde kalan': ilk alınan miktarın yüzde kaçı hâlâ elde. 'İşlemin kâr/zararı': o coinde yapılan satışlar ve elde kalanın şimdiki "
           "değeri birlikte. Satışlardaki yüzde, ilk alınan miktara göre. Yatırım tavsiyesi değildir; bir günlük sonuç geleceği göstermez.", ""]
    return "\n".join(md)


if __name__ == "__main__":
    main()
