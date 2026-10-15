"""Tek coinin belirli saatlerdeki 15 dakikalık mumlarını botun erken uyarı ve uygunluk kurallarından geçirir.
Kullanıcı bir yükselişi sorduğunda "neden yakalanmadı" sorusuna cevap için. Ortam: COIN (INIT_TRY), BAS, BIT (TR saati,
"2026-10-15 21:00"), ARALIK (15m). Sonuç: analiz/sonuc/coin_<COIN>_<ARALIK>.md ve .json
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KOK / "analiz" / "bot_yeni"))
from btr import erken, uygunluk  # noqa: E402

TR = timezone(timedelta(hours=3))
IV_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}


def klines(bn: str, aralik: str, bas_ms: int, bit_ms: int) -> list:
    out, t = [], bas_ms
    while t < bit_ms:
        u = f"https://data-api.binance.vision/api/v3/klines?symbol={bn}&interval={aralik}&startTime={t}&endTime={bit_ms}&limit=1000"
        with urllib.request.urlopen(u, timeout=30) as r:
            d = json.loads(r.read())
        if not d:
            break
        out += d
        t = d[-1][0] + IV_MS[aralik]
    return out


def main():
    coin = os.environ.get("COIN", "INIT_TRY")
    aralik = os.environ.get("ARALIK", "15m")
    bas = datetime.strptime(os.environ.get("BAS", "2026-10-15 21:00"), "%Y-%m-%d %H:%M").replace(tzinfo=TR)
    bit = datetime.strptime(os.environ.get("BIT", "2026-10-16 01:00"), "%Y-%m-%d %H:%M").replace(tzinfo=TR)
    iv = IV_MS[aralik]
    bas_ms, bit_ms = int(bas.timestamp() * 1000), int(bit.timestamp() * 1000)
    k = klines(coin.replace("_", ""), aralik, bas_ms - 200 * iv, bit_ms)
    satir, tablo = [], []
    for i, m in enumerate(k):
        if m[0] < bas_ms or m[0] > bit_ms or m[6] > datetime.now(TR).timestamp() * 1000:
            continue
        acl = [float(x[1]) for x in k[:i + 1]]
        kap = [float(x[4]) for x in k[:i + 1]]
        hac = [float(x[7]) for x in k[:i + 1]]
        e = erken.hesapla(kap, hac, acl)
        u = uygunluk.ozet(uygunluk.coin(acl, kap, hac))
        ort = sum(hac[-21:-1]) / 20
        t0 = datetime.fromtimestamp(m[0] / 1000, TR)
        kayit = {"mum": f"{t0:%d.%m %H:%M}–{(t0 + timedelta(milliseconds=iv)):%H:%M}", "acilis": acl[-1], "yuksek": float(m[2]),
                 "dusuk": float(m[3]), "kapanis": kap[-1], "degisim_pct": round((kap[-1] / acl[-1] - 1) * 100, 2),
                 "tepe_pct": round((float(m[2]) / acl[-1] - 1) * 100, 2), "hacim_try": round(hac[-1]), "hacim_kat": round(hac[-1] / ort, 2) if ort else None,
                 "erken_puan": e["puan"] if e else None, "erken_detay": e["detay"] if e else "veri yetmedi",
                 "uygunluk_pct": u["pct"], "uygunluk": " · ".join(("✓ " if x["ok"] else "✗ ") + x["ad"] + f" ({x['deger']})" for x in u["kontroller"])}
        tablo.append(kayit)
        satir.append(f"| {kayit['mum']} | {kayit['acilis']:.6g} | {kayit['yuksek']:.6g} | {kayit['kapanis']:.6g} | {kayit['degisim_pct']:+.2f} | "
                     f"{kayit['tepe_pct']:+.2f} | {kayit['hacim_kat']} | {kayit['erken_puan']}/8 | {kayit['uygunluk_pct']} |")
    md = [f"# {coin} {aralik} mumları {bas:%d.%m %H:%M} – {bit:%d.%m %H:%M} (TR saati)", "",
          "| mum | açılış | yüksek | kapanış | % | tepe % | hacim kat | erken | uygunluk % |", "|---|---|---|---|---|---|---|---|---|", *satir, "",
          "## Erken uyarı ayrıntısı", ""]
    md += [f"- **{x['mum']}** {x['erken_puan']}/8: {x['erken_detay']}" for x in tablo]
    md += ["", "## Uygunluk ayrıntısı", ""] + [f"- **{x['mum']}** %{x['uygunluk_pct']}: {x['uygunluk']}" for x in tablo]
    d = KOK / "analiz" / "sonuc"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"coin_{coin}_{aralik}.md").write_text("\n".join(md) + "\n", "utf-8")
    (d / f"coin_{coin}_{aralik}.json").write_text(json.dumps(tablo, ensure_ascii=False, indent=1), "utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
