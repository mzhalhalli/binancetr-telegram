"""Cüzdan denemesi: belli saatler arasında 15 dakikalık erken uyarı (btr/erken.py, 8 şart) veren coinlere alım yapılsaydı
cüzdan ne olurdu. Yalnızca herkese açık mum verisini okur; gerçek alım yapılmaz.

Kurallar:
- BAS ile BIT arasında kapanan her 15 dakikalık mumda tüm coinler taranır; erken uyarı veren coin o mumun kapanışından alınır.
- Her alım başlangıç cüzdanının %10'u (1000 USD için 100 USD). Nakit yetmezse sinyal atlanır. Aynı mumda birden çok sinyal
  varsa alfabetik sırayla alınır.
- Fiyat alıştan %10 yükselince yarısı, %20 yükselince kalanı satılır (mumun en yükseğine bakılır; aynı mumda ikisi de olursa
  ikisi de o mumda satılmış sayılır). Zarar-kes yok. Satıştan dönen para sonraki alımlarda kullanılır.
- Bir coin tamamen satıldıktan sonra yeni sinyal verirse tekrar alınır; elde varken gelen sinyalde ek alım yapılmaz.
- Elde kalanlar son kapanan mumun fiyatıyla değerlenir. Her alım ve satışta %0,1 komisyon.
- Yüzdeler TRY fiyatıyla hesaplanır (dolar/TL kurunun birkaç saatte değişmediği varsayılır).
Ayarlar (ortam değişkeni): BAS, BIT ("15.10.2026 15:00", TR saati), CUZDAN (USD), PAY (alım başına cüzdan oranı).
Sonuç: analiz/sonuc/cuzdan_15m.json ve analiz/sonuc/cuzdan_15m.md
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

IV = yp.IVS["15m"]
BAS = os.environ.get("BAS") or "15.10.2026 15:00"
BIT = os.environ.get("BIT") or "15.10.2026 19:00"
CUZDAN = float(os.environ.get("CUZDAN") or 1000)
PAY = float(os.environ.get("PAY") or 0.10)
KOMISYON = 0.001
YARI, TAM = 0.10, 0.20


def saat(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, yp.TR).strftime("%d.%m %H:%M")


def simule(veri: dict, bas_ms: int, bit_ms: int, son_ms: int) -> dict:
    """Mum mum ilerler: önce eldeki coinlerde mumun en yükseğine göre satış, sonra mum kapanışında yeni sinyallere alım."""
    alim_usd = CUZDAN * PAY
    nakit = CUZDAN
    acik: dict[str, dict] = {}
    islemler, atlanan = [], []
    for t in range(bas_ms, son_ms, IV):
        for s, p in list(acik.items()):
            i = veri[s]["idx"].get(t)
            if i is None:
                continue
            h = veri[s]["h"][i]
            if not p["yari"] and h >= p["giris"] * (1 + YARI):
                para = p["adet"] / 2 * p["giris"] * (1 + YARI) * (1 - KOMISYON)
                nakit += para
                p["kalan"] -= p["adet"] / 2
                p["nakit"] += para
                p["yari"] = saat(t + IV)
            if p["yari"] and h >= p["giris"] * (1 + TAM):
                para = p["kalan"] * p["giris"] * (1 + TAM) * (1 - KOMISYON)
                nakit += para
                p["nakit"] += para
                p["kalan"] = 0.0
                p["tam"] = saat(t + IV)
                islemler.append(kapat(p, veri[s], i))
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
            giris = veri[s]["c"][i]
            nakit -= alim_usd
            acik[s] = {"s": s, "alis": saat(t + IV), "i": i, "giris": float(giris), "para": alim_usd,
                       "adet": alim_usd * (1 - KOMISYON) / giris, "kalan": alim_usd * (1 - KOMISYON) / giris,
                       "nakit": 0.0, "yari": None, "tam": None}
    for s, p in acik.items():
        islemler.append(kapat(p, veri[s], len(veri[s]["c"]) - 1))
    islemler.sort(key=lambda x: (x["alis_ms"], x["s"]))
    elde = sum(x["kalan_deger"] for x in islemler)
    return {"nakit": nakit, "elde_deger": elde, "toplam": nakit + elde, "islemler": islemler, "atlanan": atlanan}


def kapat(p: dict, v: dict, j: int) -> dict:
    """İşlemin sonucu; elde kalan kısım j. mumun kapanışıyla değerlenir."""
    son = v["c"][-1]
    kalan_deger = p["kalan"] * son * (1 - KOMISYON)
    deger = p["nakit"] + kalan_deger
    hh, ll = v["h"][p["i"] + 1:j + 1], v["l"][p["i"] + 1:j + 1]
    return {"s": p["s"], "alis": p["alis"], "alis_ms": int(v["ts"][p["i"]]), "giris": p["giris"], "para": round(p["para"], 2),
            "yari_satis": p["yari"], "tam_satis": p["tam"], "elde": bool(p["kalan"] > 0), "kalan_deger": round(kalan_deger, 2),
            "deger": round(deger, 2), "kar": round(deger - p["para"], 2), "getiri_pct": round((deger / p["para"] - 1) * 100, 2),
            "en_yuksek_pct": round(float((hh.max() / p["giris"] - 1) * 100), 2) if len(hh) else 0.0,
            "en_dusuk_pct": round(float((ll.min() / p["giris"] - 1) * 100), 2) if len(ll) else 0.0,
            "simdi_pct": round(float((son / p["giris"] - 1) * 100), 2)}


def main():
    t0 = time.time()
    bas_ms = yp.zaman_ms(BAS)
    bit_ms = yp.zaman_ms(BIT)
    son_ms = int(time.time() * 1000) // IV * IV          # bu andan önce kapanan mumlar
    sem = [s for s in tt.semboller() if s["s"].split("_")[0] not in yp.STABIL]
    veri, hata = {}, []

    def bir(s):
        try:
            rows = yp.mumlar(s["bn"], bas_ms - yp.ISINMA * IV, son_ms, IV)
            if len(rows) < yp.ISINMA:
                return
            sinyal = yp.erken_kurali(yp.ozellik(rows)["F"])
            ts = np.array([int(r[0]) for r in rows], dtype=np.int64)
            pencere = (ts + IV > bas_ms) & (ts + IV <= bit_ms)
            veri[s["s"]] = {"ts": ts, "idx": {int(x): k for k, x in enumerate(ts)},
                            "h": np.array([float(r[2]) for r in rows]), "l": np.array([float(r[3]) for r in rows]),
                            "c": np.array([float(r[4]) for r in rows]),
                            "sinyal": {int(x) for x in ts[sinyal & pencere]}}
        except Exception as e:
            hata.append(f"{s['s']}: {type(e).__name__}: {e}")

    with ThreadPoolExecutor(8) as ex:
        list(ex.map(bir, sem))

    S = simule(veri, bas_ms, bit_ms, son_ms)
    # piyasa geneli: BAS anında bütün coinlerin fiyatı şimdiye kadar ortalama ne yaptı
    degisim = [float(v["c"][-1] / v["c"][v["idx"][bas_ms] - 1] - 1) * 100 for v in veri.values()
               if bas_ms in v["idx"] and v["idx"][bas_ms] > 0]
    sonuc = {"olusturma": datetime.now(yp.TR).strftime("%d.%m.%Y %H:%M"), "bas": BAS, "bit": BIT, "cuzdan": CUZDAN,
             "alim_usd": CUZDAN * PAY, "son_mum": saat(son_ms), "coin": len(sem), "veri_olan": len(veri),
             "hata": len(hata), "hata_ornek": hata[:5],
             "sinyal_sayisi": sum(len(v["sinyal"]) for v in veri.values()),
             "sinyal_coin": sum(bool(v["sinyal"]) for v in veri.values()),
             "alim_sayisi": len(S["islemler"]), "atlanan_sayisi": len(S["atlanan"]),
             "yari_satis_olan": sum(x["yari_satis"] is not None for x in S["islemler"]),
             "tam_satis_olan": sum(x["tam_satis"] is not None for x in S["islemler"]),
             "elde_kalan": sum(x["elde"] for x in S["islemler"]),
             "nakit": round(S["nakit"], 2), "elde_deger": round(S["elde_deger"], 2), "son_deger": round(S["toplam"], 2),
             "kar": round(S["toplam"] - CUZDAN, 2), "getiri_pct": round((S["toplam"] / CUZDAN - 1) * 100, 2),
             "piyasa_coin": len(degisim), "piyasa_ort_pct": round(statistics.fmean(degisim), 2) if degisim else None,
             "piyasa_medyan_pct": round(statistics.median(degisim), 2) if degisim else None,
             "islemler": S["islemler"], "atlanan": S["atlanan"], "sure_sn": round(time.time() - t0)}
    out = KOK / "analiz" / "sonuc"
    out.mkdir(parents=True, exist_ok=True)
    (out / "cuzdan_15m.json").write_text(json.dumps(sonuc, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "cuzdan_15m.md").write_text(rapor_md(sonuc), encoding="utf-8")
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


def rapor_md(d: dict) -> str:
    md = [f"# Erken uyarı cüzdan denemesi ({d['bas']} – {d['bit'][-5:]})", "",
          f"Hesaplama: {d['olusturma']} (TR saati); son kapanan 15 dakikalık mum {d['son_mum']}. Gerçek alım yapılmadı; "
          "yalnızca borsa verisiyle 'yapsaydık ne olurdu' hesabı.", "",
          "## Kurallar", "",
          f"- {d['bas']} ile {d['bit'][-5:]} arasında kapanan her 15 dakikalık mumda {d['coin']} TRY çifti erken uyarı şartlarıyla tarandı.",
          f"- Erken uyarı veren coin, sinyal mumunun kapanış fiyatından alındı. Her alım {usd(d['alim_usd'])} "
          f"(cüzdanın %{sayi(d['alim_usd'] / d['cuzdan'] * 100, 0)}'u). Nakit yetmeyince sinyal atlandı.",
          "- Fiyat alıştan %10 yükselince yarısı, %20 yükselince kalanı satıldı. Zarar-kes yok. Satıştan dönen para yeni alımlarda kullanıldı.",
          "- Tamamen satılan coin yeni sinyal verirse tekrar alındı. Elde kalanlar son kapanan mumun fiyatıyla değerlendi.",
          "- Her alım ve satışta %0,1 komisyon düşüldü.", "",
          "## Sonuç", ""]
    if not d["alim_sayisi"]:
        md += [f"Bu saatler arasında erken uyarı veren coin olmadı; cüzdan {usd(d['cuzdan'])} olarak kaldı.", ""]
        return "\n".join(md)
    md += [f"- Cüzdanın şimdiki değeri: **{usd(d['son_deger'])}** ({yuzde(d['getiri_pct'])}, {usd(d['kar'])})",
           f"  - Nakit: {usd(d['nakit'])}; elde kalan {d['elde_kalan']} coinin değeri: {usd(d['elde_deger'])}",
           f"- Erken uyarı: {d['sinyal_coin']} coinde {d['sinyal_sayisi']} sinyal; yapılan alım {d['alim_sayisi']}, "
           f"nakit yetmediği için alınamayan sinyal {d['atlanan_sayisi']}, coin zaten eldeyken gelen "
           f"{d['sinyal_sayisi'] - d['alim_sayisi'] - d['atlanan_sayisi']} sinyalde ek alım yapılmadı",
           f"- %10'a ulaşıp yarısı satılan: {d['yari_satis_olan']}; %20'ye ulaşıp tamamı satılan: {d['tam_satis_olan']}"]
    if d.get("piyasa_ort_pct") is not None:
        md += [f"- Karşılaştırma: {d['bas'][-5:]}'ten bu yana {d['piyasa_coin']} coinin fiyatı ortalama {yuzde(d['piyasa_ort_pct'])} "
               f"(ortanca {yuzde(d['piyasa_medyan_pct'])}) değişti."]
    md += ["", "## Alımlar", "",
           "| Coin | Alış (TR) | Alış fiyatı | Yarı satış (%10) | Tam satış (%20) | En yüksek | En düşük | Şimdi | Değer | Kâr/zarar |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for a in d["islemler"]:
        md.append(f"| {a['s'].replace('_TRY', '')} | {a['alis']} | {fiyat(a['giris'])} | {a['yari_satis'] or '–'} | {a['tam_satis'] or '–'} | "
                  f"{yuzde(a['en_yuksek_pct'])} | {yuzde(a['en_dusuk_pct'])} | {yuzde(a['simdi_pct'])} | {usd(a['deger'])} | {usd(a['kar'])} |")
    if d["atlanan"]:
        ilk = {}
        for a in d["atlanan"]:
            ilk.setdefault(a["s"], a["sinyal"])
        md += ["", f"Nakit yetmediği için alınamayan {len(ilk)} coin (ilk sinyal saati): "
               + ", ".join(f"{s.replace('_TRY', '')} ({z})" for s, z in ilk.items()) + "."]
    md += ["", "'Şimdi': alış fiyatına göre son kapanan mumdaki fiyat. Yatırım tavsiyesi değildir; birkaç saatlik sonuç geleceği göstermez.", ""]
    return "\n".join(md)


if __name__ == "__main__":
    main()
