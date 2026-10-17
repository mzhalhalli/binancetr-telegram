"""Son 7 gün, Binance TR'deki tüm TRY coinleri (stabil coinler hariç, 200 TL sınırı yok): tek bir 15 dakikalık mum içinde
en yükseği açılışının %10 ve daha fazla üstüne çıkan mumlar (en yüksek / açılış - 1 >= %10). Yalnızca herkese açık mum verisi.

Liste 1: tek 15 dakikalık mumda %10+ (açılıştan en yükseğe).
Liste 2 (bilgi için): 4 mum (1 saat) içinde, ilk mumun açılışından sonraki 4 mumun en yükseğine %10+; aynı coinde 4 mum içinde
         tek kayıt. Liste 1'de olan mumu içerenler işaretlenir.
Liste 1'deki her mum için en yükseğe ulaşılan dakika 1 dakikalık mumlardan bulunur.
Saatler TR saati, mum saati açılış saatidir. Sonuç: analiz/sonuc/yuzde10_hafta.json ve .md
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import yukselis_profili as yp  # noqa: E402

IV = 900_000
GUN = int(os.environ.get("HAFTA_GUN") or 7)
ESIK = float(os.environ.get("ESIK") or 10.0)
ONCE = 20          # hacim katı için önceki 20 mum
SONUC = yp.KOK / "analiz" / "sonuc"


def saat(ms: int, bicim="%d.%m.%Y %H:%M") -> str:
    return datetime.fromtimestamp(ms / 1000, yp.TR).strftime(bicim)


def pct(a, b):
    return round((a / b - 1) * 100, 2) if b else None


def tara(s, bas_ms, son_ms):
    rows = yp.mumlar(s["bn"], bas_ms - (ONCE + 1) * IV, son_ms, IV)
    if not rows:
        return s["s"], None, [], []
    ts = [int(r[0]) for r in rows]
    o, h, c = ([float(r[k]) for r in rows] for k in (1, 2, 4))
    q = [float(r[7]) for r in rows]
    bir, dort = [], []
    for i, t in enumerate(ts):
        if t < bas_ms or o[i] <= 0:
            continue
        y = pct(h[i], o[i])
        if y >= ESIK:
            once = q[max(0, i - ONCE):i]
            ort = sum(once) / len(once) if once else 0
            bir.append({"coin": s["s"], "ts": t, "acilis": o[i], "yuksek": h[i], "kapanis": c[i], "yuksek_pct": y,
                        "kapanis_pct": pct(c[i], o[i]), "onceki_degisim_pct": pct(c[i], c[i - 1]) if i else None,
                        "hacim_try": round(q[i]), "hacim_kat": round(q[i] / ort, 2) if ort else None})
    i = 0
    while i < len(ts):
        if ts[i] < bas_ms or o[i] <= 0:
            i += 1
            continue
        j = min(len(ts), i + 4)
        tepe = max(range(i, j), key=lambda k: h[k])
        y = pct(h[tepe], o[i])
        if y >= ESIK:
            dort.append({"coin": s["s"], "ts": ts[i], "acilis": o[i], "tepe": h[tepe], "tepe_ts": ts[tepe],
                         "yukselis_pct": y, "son_kapanis_pct": pct(c[j - 1], o[i]),
                         "tek_mumda": any(pct(h[k], o[k]) >= ESIK for k in range(i, j))})
            i = j
        else:
            i += 1
    return s["s"], len(rows), bir, dort


def tepe_dakika(kayit, bn):
    try:
        r1 = yp.tt._api("/api/v3/klines", {"symbol": bn, "interval": "1m", "startTime": kayit["ts"], "limit": 15})
        r1 = [r for r in r1 if kayit["ts"] <= int(r[0]) < kayit["ts"] + IV]
        if r1:
            en = max(r1, key=lambda r: float(r[2]))
            return saat(int(en[0]), "%H:%M"), (int(en[0]) - kayit["ts"]) // 60_000 + 1
    except Exception:
        pass
    return None, None


def _f(x) -> str:
    """Türkçe sayı: ondalık virgül."""
    return format(x, "g").replace(".", ",")


def _p(x, isaret=True) -> str:
    return "-" if x is None else ("%" + format(x, "+.2f" if isaret else ".2f")).replace(".", ",").replace("%+", "+%").replace("%-", "−%")


def md(sonuc) -> str:
    sat = [f"# Son {GUN} gün: 15 dakikalık mumda %{ESIK:g} ve üstü yükselenler", "",
           f"Dönem: {sonuc['bas']} – {sonuc['son']} (TR saati, mum saati açılış saatidir). Binance TR'deki TRY coinleri, "
           "stabil coinler hariç, fiyat sınırı yok.",
           f"Tanım: tek bir 15 dakikalık mumda en yüksek fiyat, o mumun açılış fiyatının en az %{ESIK:g} üstüne çıktı.", "",
           "## Özet", "",
           f"- Taranan coin: {sonuc['taranan']} (veri alınamayan: {len(sonuc['hata'])})",
           f"- Kayıt (mum): {len(sonuc['liste1'])}",
           f"- Farklı coin: {len({k['coin'] for k in sonuc['liste1']})}", "",
           "| Gün | Kayıt |", "|---|---|"]
    sat += [f"| {g} | {n} |" for g, n in sonuc["gunluk"]]
    sat += ["", "| Coin | Kayıt |", "|---|---|"]
    sat += [f"| {c} | {n} |" for c, n in sonuc["coin_sayisi"]]
    sat += ["", "## Liste 1: tek 15 dakikalık mumda açılıştan en yükseğe %10+", "",
            "Yüksek %: en yüksek / açılış. Kapanış %: kapanış / açılış. Önceki %: kapanış / bir önceki mumun kapanışı. "
            "Tepe dakikası: en yükseğin görüldüğü 1 dakikalık mum ve mumun kaçıncı dakikası. Hacim katı: önceki 20 mumun ortalamasına göre.", "",
            "| # | Coin | Tarih | Saat | Açılış | En yüksek | Kapanış | Yüksek % | Kapanış % | Önceki % | Tepe dakikası | Hacim TRY | Hacim katı |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for n, k in enumerate(sonuc["liste1"], 1):
        td = f"{k['tepe_saat']} ({k['tepe_dk']}. dk)" if k.get("tepe_saat") else "-"
        sat.append(f"| {n} | {k['coin']} | {saat(k['ts'], '%d.%m.%Y')} | {saat(k['ts'], '%H:%M')} | {_f(k['acilis'])} | {_f(k['yuksek'])} | "
                   f"{_f(k['kapanis'])} | {_p(k['yuksek_pct'])} | {_p(k['kapanis_pct'])} | {_p(k['onceki_degisim_pct'])} | {td} | "
                   f"{format(k['hacim_try'], ',').replace(',', '.')} | {'-' if k['hacim_kat'] is None else _f(k['hacim_kat']) + 'x'} |")
    sat += ["", "## Liste 2 (bilgi için): 4 mum (1 saat) içinde başlangıçtan %10+", "",
            "Başlangıç: ilk mumun açılışı. Tepe: sonraki 4 mumun en yükseği. Son kapanış %: 4. mumun kapanışı / başlangıç. "
            "Tek mumda: bu 4 mumdan biri Liste 1'de var mı.", "",
            "| # | Coin | Tarih | Başlangıç saati | Başlangıç | Tepe | Tepe saati | Yükseliş % | Son kapanış % | Tek mumda |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for n, k in enumerate(sonuc["liste2"], 1):
        sat.append(f"| {n} | {k['coin']} | {saat(k['ts'], '%d.%m.%Y')} | {saat(k['ts'], '%H:%M')} | {_f(k['acilis'])} | {_f(k['tepe'])} | "
                   f"{saat(k['tepe_ts'], '%H:%M')} | {_p(k['yukselis_pct'])} | {_p(k['son_kapanis_pct'])} | {'evet' if k['tek_mumda'] else 'hayır'} |")
    if sonuc["hata"]:
        sat += ["", "Veri alınamayan coinler: " + ", ".join(sonuc["hata"])]
    return "\n".join(sat) + "\n"


def main():
    t0 = time.time()
    son_ms = int(time.time() * 1000) // IV * IV
    bas_ms = son_ms - GUN * 86_400_000
    sem = [s for s in yp.tt.semboller() if s["s"].split("_")[0] not in yp.STABIL]
    bn = {s["s"]: s["bn"] for s in sem}
    l1, l2, hata, taranan = [], [], [], 0

    def guvenli(s):
        try:
            return tara(s, bas_ms, son_ms)
        except Exception as e:
            return s["s"], None, [], [repr(e)]

    with ThreadPoolExecutor(8) as ex:
        for ad, n, bir, dort in ex.map(guvenli, sem):
            if n is None:
                hata.append(ad)
                continue
            taranan += 1
            l1 += bir
            l2 += dort
    l1.sort(key=lambda k: (k["ts"], k["coin"]))
    l2.sort(key=lambda k: (k["ts"], k["coin"]))
    with ThreadPoolExecutor(8) as ex:
        for k, (ts_, dk) in zip(l1, ex.map(lambda k: tepe_dakika(k, bn[k["coin"]]), l1)):
            k["tepe_saat"], k["tepe_dk"] = ts_, dk
    gunluk = sorted(Counter(saat(k["ts"], "%d.%m.%Y") for k in l1).items(),
                    key=lambda x: datetime.strptime(x[0], "%d.%m.%Y"))
    coin_sayisi = Counter(k["coin"] for k in l1).most_common()
    sonuc = {"bas": saat(bas_ms), "son": saat(son_ms), "esik_pct": ESIK, "taranan": taranan, "hata": sorted(hata),
             "gunluk": gunluk, "coin_sayisi": coin_sayisi, "liste1": l1, "liste2": l2, "sure_sn": round(time.time() - t0)}
    SONUC.mkdir(parents=True, exist_ok=True)
    (SONUC / "yuzde10_hafta.json").write_text(json.dumps(sonuc, ensure_ascii=False, indent=1), encoding="utf-8")
    (SONUC / "yuzde10_hafta.md").write_text(md(sonuc), encoding="utf-8")
    print(f"taranan {taranan}, hata {len(hata)}, liste1 {len(l1)} kayıt / {len(coin_sayisi)} coin, liste2 {len(l2)}, {sonuc['sure_sn']} sn")


if __name__ == "__main__":
    main()
