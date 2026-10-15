"""Bota eklenen formasyon bulucusunu (bot-guncelleme dalındaki btr/formasyon.py, kopyası analiz/bot_yeni) gerçek Binance
verisiyle analiz/formasyon.py'deki geçmiş testle karşılaştırır ve yeni Telegram mesajının şu anki halini üretir.
- Her coin için 1500 kapanmış mum alınır; geçmiş test kırılımları tüm geçmişle bulunur.
- Son SON_MUM mumun her biri için bot, o muma kadarki son 480 mumla çalıştırılır; aynı kırılımları bulmalı.
Sonuç: analiz/sonuc/formasyon_bot_kontrol.json ve .md
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np

KOK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KOK / "analiz" / "bot_yeni"))       # yeni telegram_tarama ve btr önce
sys.path.insert(1, str(KOK / "analiz"))

import formasyon as an  # noqa: E402  (geçmiş test)
import telegram_tarama as tt  # noqa: E402  (yeni Telegram taraması)
from btr import formasyon as fo  # noqa: E402

N_MUM = 1500
SON_MUM = 200


def main():
    t0 = time.time()
    now = int(time.time() * 1000)
    sem = [s for s in tt.semboller() if s["s"].split("_")[0] not in an.STABIL]
    sonuc = {"olusturma": datetime.now(an.TR).strftime("%d.%m.%Y %H:%M"), "coin": len(sem), "aralik": {}}
    for aralik in ("1h", "4h"):
        iv = an.IVS[aralik]
        sec = set(fo.SECILI[aralik])
        kayit = {"kontrol": 0, "beklenen": 0, "bulunan": 0, "eksik": [], "fazla": [], "olay_ad": {}, "hata": 0}

        def bir(s):
            try:
                rows = an.mumlar(s["bn"], aralik, now - (N_MUM + 1) * iv, now)
            except Exception:
                kayit["hata"] += 1
                return None
            if len(rows) < 700:
                return None
            o, h, l, c = (np.array([float(r[k]) for r in rows]) for k in (1, 2, 3, 4))
            P = an.zigzag(h, l, an.atr_dizi(h, l, c))
            olay = {}
            for f, t, yon, *_ in an.kirilimlar(h, l, c, P):
                if yon > 0 and f["ad"] in sec:
                    olay.setdefault(t, set()).add(f["ad"])
            out = []
            ol, hl, ll, cl = o.tolist(), h.tolist(), l.tolist(), c.tolist()
            for t in range(len(c) - SON_MUM, len(c)):
                s0 = t - fo.GEREKLI_MUM + 1
                r = fo.bul(ol[s0:t + 1], hl[s0:t + 1], ll[s0:t + 1], cl[s0:t + 1], aralik)
                out.append((s["s"], int(rows[t][0]), olay.get(t, set()), {x["ad"] for x in r["kirilim"]}))
            return out

        with ThreadPoolExecutor(8) as ex:
            for out in ex.map(bir, sem):
                for s, ts, bek, got in out or []:
                    kayit["kontrol"] += 1
                    kayit["beklenen"] += len(bek)
                    kayit["bulunan"] += len(bek & got)
                    for a in bek:
                        kayit["olay_ad"][a] = kayit["olay_ad"].get(a, 0) + 1
                    zaman = datetime.fromtimestamp(ts / 1000, an.TR).strftime("%d.%m %H:%M")
                    for a in bek - got:
                        kayit["eksik"].append(f"{s} {zaman} {a}")
                    for a in got - bek:
                        kayit["fazla"].append(f"{s} {zaman} {a}")
        kayit["eksik_sayi"], kayit["fazla_sayi"] = len(kayit["eksik"]), len(kayit["fazla"])
        kayit["eksik"], kayit["fazla"] = kayit["eksik"][:30], kayit["fazla"][:30]
        # yeni Telegram mesajı: şu an son kapanan mum
        r = tt.tara(now, aralik)
        kayit["mesaj"] = tt.mesaj(r)
        kayit["piyasa"] = r.get("piyasa")
        kayit["simdi_kirilim"] = {x["s"]: [f["ad"] for f in x["fm_kirilim"]] for x in r["sonuc"] if x["fm_kirilim"]}
        kayit["simdi_yaklasan"] = {x["s"]: [f["ad"] for f in x["fm_yaklasan"]] for x in r["sonuc"] if x["fm_yaklasan"]}
        sonuc["aralik"][aralik] = kayit
        print(aralik, {k: v for k, v in kayit.items() if k not in ("mesaj", "eksik", "fazla")}, flush=True)
    sonuc["sure_sn"] = round(time.time() - t0)
    out = KOK / "analiz" / "sonuc"
    out.mkdir(parents=True, exist_ok=True)
    (out / "formasyon_bot_kontrol.json").write_text(json.dumps(sonuc, ensure_ascii=False, indent=1), encoding="utf-8")
    md = [f"# Formasyon bulucusu gerçek veri kontrolü ({sonuc['olusturma']})", ""]
    for a, k in sonuc["aralik"].items():
        md += [f"## {a}", "", f"- Kontrol edilen coin-mum: {k['kontrol']}; geçmiş testteki kırılım: {k['beklenen']}; botun bulduğu: {k['bulunan']}; "
               f"eksik {k['eksik_sayi']}, fazla {k['fazla_sayi']}", f"- Formasyonlara göre: {k['olay_ad']}", "",
               "Eksik örnekler: " + ", ".join(k["eksik"][:10]), "", "Fazla örnekler: " + ", ".join(k["fazla"][:10]), "",
               "### Şu anki mesaj", "", "```", k["mesaj"], "```", ""]
    (out / "formasyon_bot_kontrol.md").write_text("\n".join(md), encoding="utf-8")


if __name__ == "__main__":
    main()
