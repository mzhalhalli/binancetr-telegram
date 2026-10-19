"""16.10.2026 gerçek cüzdan işlemleri: alıştan sonra fiyat ne yaptı, kapanıştan sonra ne oldu, piyasa ne yaptı.
Yalnızca herkese açık mum verisi. Sonuç: analiz/sonuc/islem_incele.json
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import yukselis_profili as yp  # noqa: E402
import formasyon as fm  # noqa: E402

IV = 900_000
# (coin, sinyal mumu kapanışı TR, kapanış TR, alış, satış)  — kullanıcının panelden yapıştırdığı satırlar
ISLEMLER = [
    ("PLUME_TRY", "16.10.2026 15:45", "16.10.2026 16:25", 0.985, 0.971),
    ("ALLO_TRY", "16.10.2026 13:15", "16.10.2026 16:24", 11.35, 11.07),
    ("HBAR_TRY", "16.10.2026 14:45", "16.10.2026 16:24", 4.301, 4.281),
    ("VIRTUAL_TRY", "16.10.2026 12:45", "16.10.2026 16:24", 34.25, 33.87),
    ("TRB_TRY", "16.10.2026 12:45", "16.10.2026 14:39", 916, 935),
    ("MANTRA_TRY", "16.10.2026 12:15", "16.10.2026 13:13", 0.2091, 0.207),
]
OZ = ("deg", "rsi", "yb", "bwp", "hk", "e9_21", "c_ma20", "c_ma50", "get16", "get96", "tepe96", "aralik36")


def saat(ms):
    return datetime.fromtimestamp(ms / 1000, yp.TR).strftime("%d.%m %H:%M")


def main():
    son = int(time.time() * 1000) // IV * IV
    gun_bas = yp.zaman_ms("16.10.2026 12:00")
    sem = {s["s"]: s for s in fm.tt.semboller()}
    out = {"olusturma": datetime.now(yp.TR).strftime("%d.%m.%Y %H:%M"), "son_mum": saat(son), "islemler": []}
    for s, sin, kap, giris, cikis in ISLEMLER:
        bn = sem[s]["bn"]
        t_sin = yp.zaman_ms(sin)                     # sinyal mumunun kapanışı = alış anı
        t_kap = yp.zaman_ms(kap)
        rows = yp.mumlar(bn, t_sin - (yp.ISINMA + 5) * IV, son, IV)
        ts = np.array([int(r[0]) for r in rows], dtype=np.int64)
        h = np.array([float(r[2]) for r in rows]); l = np.array([float(r[3]) for r in rows]); c = np.array([float(r[4]) for r in rows])
        F = yp.ozellik(rows)["F"]
        j = int(np.searchsorted(ts, t_sin - IV))     # sinyal mumu
        k = int(np.searchsorted(ts, t_kap, side="right")) - 1   # kapanışın olduğu mum
        ac = slice(j + 1, k + 1)                     # açık kaldığı mumlar
        sonra = slice(k + 1, len(c))
        d = {"s": s, "alis": sin, "kapanis": kap, "giris": giris, "cikis": cikis,
             "sonuc_pct": round((cikis / giris - 1) * 100, 2),
             "sinyal_mumu": {x: (None if np.isnan(F[x][j]) else round(float(F[x][j]), 2)) for x in OZ},
             "sinyal_kapanis": float(c[j]),
             "acikken_en_yuksek_pct": round((h[ac].max() / giris - 1) * 100, 2) if k > j else None,
             "acikken_en_dusuk_pct": round((l[ac].min() / giris - 1) * 100, 2) if k > j else None,
             "en_yuksek_saat": saat(int(ts[j + 1 + int(np.argmax(h[ac]))])) if k > j else None,
             "en_dusuk_saat": saat(int(ts[j + 1 + int(np.argmin(l[ac]))])) if k > j else None,
             "hedef_2_degdi_mi": bool(k > j and h[ac].max() >= giris * 1.02),
             "kapanistan_sonra_en_yuksek_pct": round((h[sonra].max() / giris - 1) * 100, 2) if len(c) > k + 1 else None,
             "kapanistan_sonra_en_dusuk_pct": round((l[sonra].min() / giris - 1) * 100, 2) if len(c) > k + 1 else None,
             "simdi_pct": round((c[-1] / giris - 1) * 100, 2),
             "yol": [[saat(int(ts[i]) + IV), round((c[i] / giris - 1) * 100, 2), round((h[i] / giris - 1) * 100, 2),
                      round((l[i] / giris - 1) * 100, 2)] for i in range(j, len(c))]}
        # kural sürseydi (+%2 / -%5): kapanıştan sonra hangisine önce değerdi
        once = "yok"
        for i in range(j + 1, len(c)):
            if l[i] <= giris * 0.95:
                once = f"zarar-kes {saat(int(ts[i]) + IV)}"; break
            if h[i] >= giris * 1.02:
                once = f"kâr al {saat(int(ts[i]) + IV)}"; break
        d["kural_surseydi"] = once
        out["islemler"].append(d)

    # piyasa: 12:00'den son muma tüm TRY coinlerinin değişimi ve BTC_TRY
    def bir(s):
        try:
            r = yp.mumlar(s["bn"], gun_bas - IV, son, IV)
            if len(r) < 2:
                return None
            return s["s"], float(r[-1][4]) / float(r[0][4]) - 1, [float(x[4]) for x in r]
        except Exception:
            return None
    coins = [s for s in sem.values() if s["s"].split("_")[0] not in yp.STABIL]
    with ThreadPoolExecutor(8) as ex:
        res = [x for x in ex.map(bir, coins) if x]
    deg = [x[1] * 100 for x in res]
    btc = next((x for x in res if x[0] == "BTC_TRY"), None)
    out["piyasa"] = {"bas": "16.10 12:00", "bit": saat(son), "coin": len(deg),
                     "ort_pct": round(statistics.fmean(deg), 2), "medyan_pct": round(statistics.median(deg), 2),
                     "yukselen": sum(x > 0 for x in deg), "dusen": sum(x < 0 for x in deg),
                     "btc_pct": round(btc[1] * 100, 2) if btc else None}
    # 15 dk'lık adımlarla piyasa ortalaması (12:00'e göre)
    L = min(len(x[2]) for x in res)
    out["piyasa"]["yol"] = [[saat(gun_bas + i * IV), round(statistics.fmean((x[2][i] / x[2][0] - 1) * 100 for x in res), 2),
                             round((btc[2][i] / btc[2][0] - 1) * 100, 2) if btc else None] for i in range(L)]
    p = fm.KOK / "analiz" / "sonuc"
    p.mkdir(parents=True, exist_ok=True)
    (p / "islem_incele.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "islemler"}, ensure_ascii=False)[:2000])


if __name__ == "__main__":
    main()
