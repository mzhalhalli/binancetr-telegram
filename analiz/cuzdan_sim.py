"""Cüzdan denemesi: belli saatler arasında 15 dakikalık erken uyarı (btr/erken.py, 8 şart) ya da süpriz (btr/supriz.py,
7 şart) veren coinlere alım yapılsaydı cüzdan ne olurdu. Yalnızca herkese açık mum verisini okur; gerçek alım yapılmaz.

Senaryolar analiz/cuzdan_senaryo.json dosyasında. Her senaryo için:
- bas ile bit arasında kapanan her `aralik` mumunda (15m, 1h ya da 4h; varsayılan 15m) tüm coinler taranır; erken uyarı veren
  coin o mumun kapanışından alınır. `bas_dahil` true ise tam bas saatinde kapanan mum da sayılır.
- `sinyal`: "erken" (varsayılan) ya da "supriz" (yalnızca 15m). Süpriz, bilgisayardaki botun Telegram'a yolladığı gibi son
  ~162 kapanmış mumla hesaplanır; `supriz_min` şart sayısına (varsayılan 6, yani 7 şartın en az 6'sı) ulaşan coin alınır.
- Satışlar (hedef, zarar-kes) alıştan sonra 15 dakikalık mumlarla izlenir.
- Her alım başlangıç cüzdanının `pay` oranı kadar. Nakit yetmezse sinyal atlanır. Aynı mumda birden çok sinyal varsa
  alfabetik sırayla alınır.
- `satis`: [kâr %, eldeki miktarın satılan %] adımları. Fiyat alıştan o kadar yükselince (mumun en yükseği) eldekinin o kadarı
  hedef fiyattan satılır. Aynı mumda birden çok adıma ulaşılırsa hepsi o mumda yapılır.
- `stop`: alıştan bu kadar % düşünce (mumun en düşüğü) eldekinin tamamı satılır. Mum zarar-kesin altında açılırsa açılış
  fiyatından satılır. Aynı 15 dakikalık mumda hem zarar-kese hem hedefe değilirse hangisinin önce olduğuna o mumun
  1 dakikalık mumlarından bakılır; aynı 1 dakikalık mumda ikisi birden varsa ya da 1 dakikalık veri alınamazsa önce
  zarar-kes sayılır (temkinli).
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
from btr import supriz as sp  # noqa: E402

IV = yp.IVS["15m"]                  # satışlar her zaman 15 dakikalık mumlarla izlenir
DK = 60_000
yp.IVS.setdefault("1m", DK)         # yp.mumlar aralık adını IVS'ten bulur
KOMISYON = 0.001
ARALIK_AD = {"15m": "15 dakikalık", "1h": "1 saatlik", "4h": "4 saatlik"}
SINYAL_AD = {"erken": "Erken uyarı", "supriz": "Süpriz"}
SUPRIZ_PENCERE = sp.GEREKLI_MUM + 2     # btr/tarayici.py: limit = gerekli_mum + 2, açık mum atılınca ~162 kapanmış mum


def saat(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, yp.TR).strftime("%d.%m %H:%M")


def dakika(bn: str, t: int) -> list:
    """t'de açılan 15 dakikalık mumun 1 dakikalık mumları (o, h, l, kapanış zamanı); alınamazsa boş liste."""
    try:
        rows = yp.mumlar(bn, t, t + IV, DK)
    except Exception:
        return []
    return [(float(r[1]), float(r[2]), float(r[3]), int(r[0]) + DK) for r in rows if t <= int(r[0]) < t + IV]


def simule(veri: dict, sen: dict, son_ms: int) -> dict:
    """Mum mum ilerler: önce eldeki coinlerde zarar-kes ve kademeli satış, sonra mum kapanışında yeni sinyallere alım."""
    bas_ms, bit_ms = yp.zaman_ms(sen["bas"]), yp.zaman_ms(sen["bit"])
    alim_usd = sen["cuzdan"] * sen["pay"]
    stop = sen.get("stop")
    nakit = float(sen["cuzdan"])
    acik: dict[str, dict] = {}
    islemler, atlanan, belirsiz = [], [], []

    def sat(p, miktar, fiyat, kapanis, neden, dk1=False):
        nonlocal nakit
        para = float(miktar * fiyat * (1 - KOMISYON))
        nakit += para
        p["nakit"] += para
        p["kalan"] -= miktar
        p["satislar"].append({"saat": saat(kapanis), "neden": neden, "oran_pct": round(miktar / p["adet"] * 100, 1),
                              "fiyat": float(fiyat), "usd": round(para, 2), "dk1": dk1})

    def isle(p, o, h, l, kapanis, dk1=False):
        """Bir mumda önce zarar-kes, sonra hedef adımları."""
        if p["kalan"] <= p["adet"] * 1e-9:
            return
        if stop is not None and l <= p["giris"] * (1 - stop / 100):
            sat(p, p["kalan"], min(p["giris"] * (1 - stop / 100), o), kapanis, f"zarar-kes –%{stop:g}", dk1)
            return
        for k, (kar, oran) in enumerate(sen["satis"]):
            if k < p["adim"] or h < p["giris"] * (1 + kar / 100):
                continue
            sat(p, p["kalan"] * oran / 100, p["giris"] * (1 + kar / 100), kapanis, f"+%{kar:g} hedef", dk1)
            p["adim"] = k + 1

    # bas_dahil: tam bas saatinde kapanan sinyal de alınır (o anda kapanan 15 dakikalık mum bas'tan bir mum önce açılır)
    for t in range(bas_ms - (IV if sen.get("bas_dahil") else 0), son_ms, IV):
        for s, p in list(acik.items()):
            v = veri[s]
            i = v["idx"].get(t)
            if i is None:
                continue
            ikisi = (stop is not None and v["l"][i] <= p["giris"] * (1 - stop / 100)
                     and any(k >= p["adim"] and v["h"][i] >= p["giris"] * (1 + kar / 100) for k, (kar, _) in enumerate(sen["satis"])))
            dk = dakika(v["bn"], t) if ikisi else []
            if dk:
                # aynı 15 dakikalık mumda hem hedef hem zarar-kes: sırayı 1 dakikalık mumlar belirler
                n0 = len(p["satislar"])
                for o1, h1, l1, k1 in dk:
                    isle(p, o1, h1, l1, k1, True)
                belirsiz.append({"s": s, "saat": saat(t + IV), "once": p["satislar"][n0]["neden"] if len(p["satislar"]) > n0 else "yok"})
            else:
                isle(p, v["o"][i], v["h"][i], v["l"][i], t + IV)
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
                       "nakit": 0.0, "adim": 0, "satislar": [], "puan": veri[s]["puan"].get(t)}
    for s, p in acik.items():
        islemler.append(kapat(p, veri[s], len(veri[s]["c"]) - 1))
    islemler.sort(key=lambda x: (x["alis_ms"], x["s"]))
    elde = sum(x["kalan_deger"] for x in islemler)
    return {"nakit": nakit, "elde_deger": elde, "toplam": nakit + elde, "islemler": islemler, "atlanan": atlanan, "belirsiz": belirsiz}


def kapat(p: dict, v: dict, j: int) -> dict:
    """İşlemin sonucu; elde kalan kısım son kapanan mumun fiyatıyla değerlenir. En yüksek/en düşük j. muma kadar."""
    son = v["c"][-1]
    kalan_deger = float(p["kalan"] * son * (1 - KOMISYON))
    deger = float(p["nakit"] + kalan_deger)
    hh, ll = v["h"][p["i"] + 1:j + 1], v["l"][p["i"] + 1:j + 1]
    return {"s": p["s"], "alis": p["alis"], "alis_ms": int(v["ts"][p["i"]]), "puan": p.get("puan"), "giris": p["giris"], "para": round(p["para"], 2),
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
    araliklar = {s.get("aralik", "15m") for s in senaryolar}
    sup = [s for s in senaryolar if s.get("sinyal") == "supriz"]
    assert all(s.get("aralik", "15m") == "15m" for s in sup), "süpriz yalnızca 15 dakikalık"
    sup_bit = max((yp.zaman_ms(s["bit"]) for s in sup), default=0)
    ham, hata = {}, []

    def bir(s):
        try:
            rows = yp.mumlar(s["bn"], ilk_ms - yp.ISINMA * IV, son_ms, IV)
            if len(rows) < yp.ISINMA:
                return
            ts = np.array([int(r[0]) for r in rows], dtype=np.int64)
            d = {"bn": s["bn"], "ts": ts, "idx": {int(x): k for k, x in enumerate(ts)},
                 "o": np.array([float(r[1]) for r in rows]), "h": np.array([float(r[2]) for r in rows]),
                 "l": np.array([float(r[3]) for r in rows]), "c": np.array([float(r[4]) for r in rows]),
                 "kapanis": {"15m": ts + IV}, "erken": {"15m": yp.erken_kurali(yp.ozellik(rows)["F"])}}
            if sup:
                # süpriz puanı: her mum için, o mumda biten son SUPRIZ_PENCERE kapanmış mumla (botun gördüğü pencere)
                kap, acl, hq = d["c"].tolist(), d["o"].tolist(), [float(r[7]) for r in rows]
                puan = np.zeros(len(rows), dtype=int)
                for j in range(SUPRIZ_PENCERE - 1, len(rows)):
                    if ilk_ms - IV < ts[j] + IV <= sup_bit:
                        w = slice(j - SUPRIZ_PENCERE + 1, j + 1)
                        r = sp.hesapla(kap[w], hq[w], acl[w])
                        puan[j] = r["puan"] if r else 0
                d["supriz"] = {"15m": puan}
            for a in araliklar - {"15m"}:
                iv = yp.IVS[a]
                r2 = yp.mumlar(s["bn"], ilk_ms - yp.ISINMA * iv, son_ms // iv * iv, iv)
                if len(r2) < yp.ISINMA:          # bu aralıkta geçmişi kısa olan yeni coin: bu aralıkta sinyal yok sayılır
                    d["kapanis"][a], d["erken"][a] = np.zeros(0, dtype=np.int64), np.zeros(0, dtype=bool)
                    continue
                d["kapanis"][a] = np.array([int(r[0]) for r in r2], dtype=np.int64) + iv
                d["erken"][a] = yp.erken_kurali(yp.ozellik(r2)["F"])
            ham[s["s"]] = d
        except Exception as e:
            hata.append(f"{s['s']}: {type(e).__name__}: {e}")

    with ThreadPoolExecutor(8) as ex:
        list(ex.map(bir, sem))

    out = KOK / "analiz" / "sonuc"
    out.mkdir(parents=True, exist_ok=True)
    for sen in senaryolar:
        bas_ms, bit_ms = yp.zaman_ms(sen["bas"]), yp.zaman_ms(sen["bit"])
        a = sen.get("aralik", "15m")
        alt = bas_ms - (1 if sen.get("bas_dahil") else 0)

        tur = sen.get("sinyal", "erken")

        def sinyaller(v):
            kap = v["kapanis"][a]
            if tur == "supriz":
                pu = v["supriz"][a]
                m = pu >= sen.get("supriz_min", sp.TOPLAM - 1)
            else:
                pu, m = None, v["erken"][a]
            # sinyal mumunun kapanışı = o anda biten 15 dakikalık mumun kapanışı; alım o 15 dakikalık mumun kapanış fiyatından
            sec = np.flatnonzero(m & (kap > alt) & (kap <= bit_ms))
            return {int(kap[j]) - IV: (int(pu[j]) if pu is not None else None) for j in sec if int(kap[j]) - IV in v["idx"]}
        veri = {}
        for k, v in ham.items():
            sn = sinyaller(v)
            veri[k] = {**v, "sinyal": set(sn), "puan": sn}
        S = simule(veri, sen, son_ms)
        degisim = [float(v["c"][-1] / v["c"][v["idx"][bas_ms] - 1] - 1) * 100 for v in veri.values()
                   if bas_ms in v["idx"] and v["idx"][bas_ms] > 0]
        isl = S["islemler"]
        kontrol = {}
        if tur == "supriz" and "BLUR_TRY" in veri:      # btr/supriz.py: BLUR 15.10 12:00 ve 12:15 mumları 7/7 olmalı
            v = veri["BLUR_TRY"]
            kontrol = {saat(t + IV): int(v["supriz"]["15m"][v["idx"][t]]) for t in (yp.zaman_ms("15.10.2026 12:00"),
                                                                                    yp.zaman_ms("15.10.2026 12:15")) if t in v["idx"]}
        sonuc = {"olusturma": datetime.now(yp.TR).strftime("%d.%m.%Y %H:%M"), **sen, "alim_usd": sen["cuzdan"] * sen["pay"],
                 "sinyal_dagilim": {str(n): sum(list(v["puan"].values()).count(n) for v in veri.values()) for n in range(1, sp.TOPLAM + 1)
                                    if tur == "supriz"}, "kontrol_blur": kontrol,
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
                 "belirsiz": S["belirsiz"], "islemler": isl, "atlanan": S["atlanan"], "sure_sn": round(time.time() - t0)}
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
    a = d.get("aralik", "15m")
    if d.get("sinyal") == "supriz":
        n = d.get("supriz_min", sp.TOPLAM - 1)
        sart = ("süpriz şartlarıyla (7 şartın hepsi)" if n >= sp.TOPLAM else
                f"süpriz şartlarıyla (7 şartın en az {n}'{'sı' if n == 6 else 'i'}; bilgisayardaki botun Telegram'a yolladığı gibi)")
    else:
        sart = "erken uyarı şartlarıyla"
    return [f"- {d['bas']} ile {d['bit'][-5:]} arasında kapanan her {ARALIK_AD[a]} mumda {d['coin']} TRY çifti {sart} tarandı"
            + (" (satışlar 15 dakikalık mumlarla izlendi)." if a != "15m" else "."),
            f"- {SINYAL_AD[d.get('sinyal', 'erken')]} veren coin, sinyal mumunun kapanış fiyatından alındı. Her alım {usd(d['alim_usd'])} "
            f"(cüzdanın %{sayi(d['pay'] * 100, 0)}'i). Nakit yetmeyince sinyal atlandı.",
            "- Satış: " + "; ".join(adimlar) + " satıldı. " + stop,
            "- Satıştan dönen para yeni alımlarda kullanıldı. Elde varken gelen sinyalde ek alım yapılmadı; tamamen satılan coin yeni sinyalde tekrar alındı.",
            "- Elde kalanlar son kapanan mumun fiyatıyla değerlendi. Her alım ve satışta %0,1 komisyon düşüldü."]


def rapor_md(d: dict) -> str:
    ad = SINYAL_AD[d.get("sinyal", "erken")]
    sup = d.get("sinyal") == "supriz"
    md = [f"# {ad} ({ARALIK_AD[d.get('aralik', '15m')]}) cüzdan denemesi ({d['bas']} – {d['bit'][-5:]})", "",
          f"Hesaplama: {d['olusturma']} (TR saati); son kapanan 15 dakikalık mum {d['son_mum']}. Veri Binance'in gerçek "
          + ("15 dakikalık TRY mumları" if d.get("aralik", "15m") == "15m"
             else f"{ARALIK_AD[d['aralik']]} (sinyal) ve 15 dakikalık (satış takibi) TRY mumları")
          + ". Gerçek alım yapılmadı; 'yapsaydık ne olurdu' hesabı.", "",
          "## Kurallar", "", *kural_metni(d), "", "## Sonuç", ""]
    if not d["alim_sayisi"]:
        md += [f"Bu saatler arasında {ad.lower()} veren coin olmadı; cüzdan {usd(d['cuzdan'])} olarak kaldı.", ""]
        return "\n".join(md)
    adim = "; ".join(f"%{kar:g} hedefine ulaşan: {n}" for (kar, _), n in zip(d["satis"], d["hedef_adim_sayisi"]))
    md += [f"- Cüzdanın şimdiki değeri: **{usd(d['son_deger'])}** ({yuzde(d['getiri_pct'])}, {usd(d['kar'])})",
           f"  - Nakit: {usd(d['nakit'])}; elde kalan {d['elde_kalan']} coinin değeri: {usd(d['elde_deger'])}",
           f"- {ad}: {d['sinyal_coin']} coinde {d['sinyal_sayisi']} sinyal"
           + (f" (7/7 SÜPRİZ: {d['sinyal_dagilim'].get('7', 0)}, 6/7 süpriz adayı: {d['sinyal_dagilim'].get('6', 0)})" if sup else "")
           + f". Yapılan alım {d['alim_sayisi']} ({d['alim_coin']} coin); "
           f"nakit yetmediği için alınamayan sinyal {d['atlanan_sayisi']}.",
           f"- {adim}; zarar-kese düşen: {d['zarar_kes_sayisi']}",
           f"- Alımların {d['karda']} tanesi kârda, {d['zararda']} tanesi zararda."]
    if d.get("belirsiz"):
        b = d["belirsiz"]
        hedef = sum(x["once"].endswith("hedef") for x in b)
        md += [f"- Aynı 15 dakikalık mumda hem hedefe hem zarar-kese değilen {len(b)} durumda hangisinin önce olduğu Binance'in 1 dakikalık "
               f"mumlarından bulundu: {hedef} kez önce hedef, {len(b) - hedef} kez önce zarar-kes."]
    if d.get("piyasa_ort_pct") is not None:
        md += [f"- Karşılaştırma: {d['bas'][-5:]}'ten bu yana {d['piyasa_coin']} coinin fiyatı ortalama {yuzde(d['piyasa_ort_pct'])} "
               f"(ortanca {yuzde(d['piyasa_medyan_pct'])}) değişti."]
    md += ["", "## Alımlar", "",
           "| Coin | Alış (TR) | " + ("Süpriz | " if sup else "") + "Alış fiyatı | Satışlar | Elde kalan | En yüksek | En düşük | Şimdi | Değer | Kâr/zarar |",
           "|---|---|" + ("---|" if sup else "") + "---|---|---|---|---|---|---|---|"]
    for a in d["islemler"]:
        satis = "<br>".join(f"{x['saat']} {x['neden']} (%{sayi(x['oran_pct'], 0)})" for x in a["satislar"]) or "–"
        md.append(f"| {a['s'].replace('_TRY', '')} | {a['alis']} | " + (f"{a['puan']}/7 | " if sup else "") + f"{fiyat(a['giris'])} | {satis} | %{sayi(a['elde_oran_pct'], 0)} | "
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
