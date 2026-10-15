"""Erken uyarı (15 dakikalık) geriye dönük başarı testi. Yalnızca herkese açık mum verisini okur.

Her TRY çiftinin son GUN günlük 15 dakikalık mumlarında, canlı taramayla aynı kuralı (btr/erken.py, 8 şartın 8'i)
her kapanmış mumda uygular. Sinyal mumunun kapanışından sonraki fiyat hareketini ölçer ve tüm mumlarla (taban) karşılaştırır.
Sonuç: analiz/sonuc/erken_15m.json
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KOK))

import telegram_tarama as tt  # noqa: E402
from btr import erken as er  # noqa: E402
from btr import indikator as ind  # noqa: E402

IV = 900_000
GUN = int(os.environ.get("GUN", "30"))
PENCERE = 163          # canlı tarayıcının okuduğu kapanmış mum sayısı (EMA/MACD ısınması aynı olsun)
ILERI = 16             # sinyalden sonra 16 mum = 4 saat izlenir
TEKRAR = 4             # aynı coinde 4 mum (1 saat) içinde tekrar eden sinyal yeni sinyal sayılmaz
TR = timezone(timedelta(hours=3))
STABIL = {"USDT", "USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "EUR", "EURI", "AEUR", "BFUSD", "USDE", "USD1", "RLUSD", "XUSD"}


def mumlar(bn: str, bas_ms: int, bit_ms: int) -> list:
    out, t = {}, bas_ms
    while t < bit_ms:
        rows = tt._api("/api/v3/klines", {"symbol": bn, "interval": "15m", "startTime": t, "limit": 1000})
        if not rows:
            break
        for r in rows:
            out[int(r[0])] = r
        t = int(rows[-1][0]) + IV
        if len(rows) < 1000:
            break
    return [out[k] for k in sorted(out) if int(out[k][6]) < bit_ms]


def ileri(o, h, l, c, q, i):
    """Sinyal mumu i'nin kapanışından sonraki hareket. Eksik ileri veri varsa olanla hesaplanır."""
    n = len(c)
    m = {}
    for ad, k in (("1s", 4), ("4s", 16)):
        j = min(n - 1, i + k)
        if j <= i:
            m[f"max_{ad}"] = m[f"kap_{ad}"] = m[f"dd_{ad}"] = None
            continue
        m[f"max_{ad}"] = (max(h[i + 1:j + 1]) / c[i] - 1) * 100
        m[f"dd_{ad}"] = (min(l[i + 1:j + 1]) / c[i] - 1) * 100
        m[f"kap_{ad}"] = (c[j] / c[i] - 1) * 100 if j == i + k else None
    # sert yükseliş: sonraki 4 mumdan birinde +%5 ve üstü mum, hacim 20 mum ortalamasının 5 katı ve üstü
    sert = False
    for j in range(i + 1, min(n, i + 5)):
        ort = sum(q[j - 20:j]) / 20
        if o[j] > 0 and c[j] / o[j] - 1 >= 0.05 and ort > 0 and q[j] >= 5 * ort:
            sert = True
    m["sert"] = sert
    m["tam"] = i + ILERI < n
    return m


def analiz(sym: str, rows: list, bas_ms: int):
    o = [float(r[1]) for r in rows]
    h = [float(r[2]) for r in rows]
    l = [float(r[3]) for r in rows]
    c = [float(r[4]) for r in rows]
    q = [float(r[7]) for r in rows]
    ts = [int(r[0]) for r in rows]
    sinyal, taban = [], []
    son = -10**9
    for i in range(PENCERE - 1, len(rows)):
        if ts[i] < bas_ms or c[i] <= 0:
            continue
        if i + ILERI < len(rows):
            taban.append(((max(h[i + 1:i + ILERI + 1]) / c[i] - 1) * 100, (c[i + ILERI] / c[i] - 1) * 100,
                          (max(h[i + 1:i + 5]) / c[i] - 1) * 100))
        if c[i] <= o[i]:                     # hızlı ön eleme: yeşil mum ve %B 0,80-1,10 (kuralın 2 şartı)
            continue
        b = ind.bollinger(c[i - 20:i + 1], 21, 2.0)
        if not b or b[1] <= b[2]:
            continue
        yb = (c[i] - b[2]) / (b[1] - b[2])
        if not 0.80 <= yb <= 1.10:
            continue
        a = i - PENCERE + 1
        r = er.hesapla(c[a:i + 1], q[a:i + 1], o[a:i + 1])
        if not (r and r["ok"]):
            continue
        tekrar = i - son <= TEKRAR
        son = i
        sinyal.append({"s": sym, "ts": ts[i], "fiyat": c[i], "hacim": q[i], "tekrar": tekrar, **ileri(o, h, l, c, q, i)})
    return sinyal, taban


def ozet(xs: list[dict] | list[tuple], al) -> dict:
    v = [al(x) for x in xs]
    v = [x for x in v if x is not None]
    if not v:
        return {"n": 0}
    return {"n": len(v), "ort": round(statistics.fmean(v), 2), "medyan": round(statistics.median(v), 2),
            "pozitif_pct": round(sum(1 for x in v if x > 0) / len(v) * 100, 1),
            "ge3_pct": round(sum(1 for x in v if x >= 3) / len(v) * 100, 1),
            "ge5_pct": round(sum(1 for x in v if x >= 5) / len(v) * 100, 1),
            "ge10_pct": round(sum(1 for x in v if x >= 10) / len(v) * 100, 1)}


def main():
    t0 = time.time()
    bit_ms = int(time.time() * 1000) // IV * IV
    bas_ms = bit_ms - GUN * 86_400_000
    sem = [s for s in tt.semboller() if s["s"].split("_")[0] not in STABIL]
    sinyaller, taban, hata = [], [], []

    def bir(s):
        try:
            rows = mumlar(s["bn"], bas_ms - PENCERE * IV, bit_ms)
            si, ta = analiz(s["s"], rows, bas_ms)
            sinyaller.extend(si)
            taban.extend(ta)
        except Exception as e:
            hata.append(f"{s['s']}: {e}")

    with ThreadPoolExecutor(8) as ex:
        list(ex.map(bir, sem))
    sinyaller.sort(key=lambda x: x["ts"])
    ilk = [x for x in sinyaller if not x["tekrar"]]
    tam = [x for x in ilk if x["tam"]]
    sonuc = {
        "olusturma": datetime.now(TR).strftime("%d.%m.%Y %H:%M"), "gun": GUN,
        "bas": datetime.fromtimestamp(bas_ms / 1000, TR).strftime("%d.%m.%Y %H:%M"),
        "bit": datetime.fromtimestamp(bit_ms / 1000, TR).strftime("%d.%m.%Y %H:%M"),
        "coin": len(sem), "hata": len(hata), "hata_ornek": hata[:5], "sure_sn": round(time.time() - t0),
        "sinyal_ham": len(sinyaller), "sinyal_ilk": len(ilk), "sinyal_tam": len(tam),
        "sinyal": {"max_1s": ozet(tam, lambda x: x["max_1s"]), "max_4s": ozet(tam, lambda x: x["max_4s"]),
                   "kap_1s": ozet(tam, lambda x: x["kap_1s"]), "kap_4s": ozet(tam, lambda x: x["kap_4s"]),
                   "dd_4s": ozet(tam, lambda x: x["dd_4s"]),
                   "sert_pct": round(sum(1 for x in tam if x["sert"]) / len(tam) * 100, 1) if tam else None},
        "taban": {"n": len(taban), "max_1s": ozet(taban, lambda x: x[2]), "max_4s": ozet(taban, lambda x: x[0]),
                  "kap_4s": ozet(taban, lambda x: x[1])},
        "liste": [{**x, "zaman": datetime.fromtimestamp(x["ts"] / 1000, TR).strftime("%d.%m %H:%M")} for x in ilk],
    }
    out = KOK / "analiz" / "sonuc"
    out.mkdir(parents=True, exist_ok=True)
    (out / "erken_15m.json").write_text(json.dumps(sonuc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in sonuc.items() if k != "liste"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
