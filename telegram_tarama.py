"""GitHub Actions: botun SON HALİNİN otomatik alım taramalarını çalıştırır, botun alacağı sinyalleri Telegram'a yollar
(bilgisayar kapalıyken de). 20.10.2026'da bilgisayardaki botla (binancetr_arastirma) aynı kurallara getirildi.

Tarama kodu bilgisayardaki botun btr/ klasöründen kopyadır (tarayici, formasyon, benim_sartlar, uygunluk ...).
Kurallar (al_sat.MODELLER ve AlSatAyar varsayılanlarıyla aynı):
  1. Alt banttan orta banda değdi, ardından kırmızı mumla alt banda temas (15 dk)
  2. RSI6, RSI12 ve RSI24'ü yukarı kesti → RSI12'ye retest → orta bant üstünde 2 yeşil mum (1 saat, 4 saat)
  3. Boğa bayrağı ve retest, ardından 2 yeşil mum (15 dk, 1 saat, 4 saat)
  4. Benim şartlarım (btr/benim_sartlarim.json) (15 dk, 1 saat, 4 saat)
  Coinler: stabil/altın hariç, 5 USD üstü ve 0,0001 USD altı hariç, arzı en çok arzın yarısından az olanlar hariç, kalanlardan
  24 saatlik TL hacmi en yüksek 100. Açık (kapanmamış) mumla taranır; aynı açık mumdaki sinyal bir kez gönderilir.
  Alım süzgeçleri: uygunluk %0-40 ya da %50-69; aynı coin 24 saatte bir; altcoin 7 günlük ortanca değişim artıda değilse
  sinyal gönderilmez (piyasa yönü değişince bir kez bilgi mesajı gider).
Gerekenler (GitHub > Settings > Secrets and variables > Actions): TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.
Durum dosyaları depoya kaydedilir: durum.json (gönderilen sinyaller, piyasa yönü), data/arz.json (günde bir yenilenir).
Yalnızca herkese açık piyasa verisini okur; borsa API anahtarı, hesap, emir yoktur. Yatırım tavsiyesi değildir.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from btr import istemci as ist
from btr.arz import Arz
from btr.istemci import Istemci
from btr.piyasa_rejim import AltRejim
from btr.tarayici import Tarayici

KOK = Path(__file__).resolve().parent
TR = timezone(timedelta(hours=3))
MODELLER = {
    "m1_bb": {"ad": "1. Alt-orta-alt bant + kırmızı mumla alt banda temas", "araliklar": ["15m"],
              "kosullar": [{"ind": "bb", "kosul": "dip_3_kirmizi", "n": 21, "k": 2, "son": 3}]},
    "m2_formasyon": {"ad": "2. RSI6 kesişim → RSI12 retest → orta bant üstünde 2 yeşil", "araliklar": ["1h", "4h"],
                     "kosullar": [{"ind": "rsi", "kosul": "retest_2_yesil", "n": 14, "deger": 30}]},
    # 3. model (kural 1 ardından kural 2) 20.10.2026'da otomatik alımdan kaldırıldı: gönderilmez
    "m4_yaklasan": {"ad": "3. Boğa bayrağı ve retest + 2 yeşil", "araliklar": ["15m", "1h", "4h"],
                    "kosullar": [{"ind": "formasyon", "kosul": "boga_retest_y2"}]},
    "m5_benim": {"ad": "4. Benim şartlarım", "araliklar": ["15m", "1h", "4h"], "kosullar": [{"ind": "benim", "kosul": "tam"}]},
}
UYGUNLUK = [(0, 40), (50, 69)]
TEKRAR_SN = 24 * 3600
ARALIK_AD = {"15m": "15 dk", "1h": "1 saat", "4h": "4 saat"}
# GitHub sunucuları ABD'de: Binance verisi önce data-api.binance.vision'dan (Binance'in herkese açık veri sunucusu)
ist.API_HOSTS[:0] = ["https://data-api.binance.vision"]


def durum_oku() -> dict:
    try:
        return json.loads((KOK / "durum.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {"gonderilen": {}, "son_coin": {}, "rejim_acik": None}


async def ticker(i: Istemci) -> dict:
    out = {}
    for r in await i.get("api", "/api/v3/ticker/24hr"):
        s = r.get("symbol", "")
        for q in ("TRY", "USDT"):
            if s.endswith(q) and s != q:
                out[f"{s[:-len(q)]}_{q}"] = {"c": float(r["lastPrice"]), "q": float(r["quoteVolume"])}
    return out


async def semboller(i: Istemci) -> dict:
    """Binance TR sembol listesi; GitHub'dan Binance TR'ye ulaşılamazsa Binance'teki TRY çiftleri (Binance TR'deki
    1. tip coinler bu defterleri kullanır)."""
    try:
        sem = {s["symbol"]: s for s in await i.semboller() if str(s.get("quote", "")).upper() == "TRY"}
        if sem:
            return sem
    except Exception as e:
        print(f"Binance TR sembol listesi alınamadı ({e}); Binance TRY çiftleri kullanılıyor", file=sys.stderr)
    req = urllib.request.Request("https://data-api.binance.vision/api/v3/exchangeInfo?permissions=SPOT",
                                 headers={"User-Agent": "binancetr-tarama/2.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read().decode())
    return {f"{x['baseAsset']}_TRY": {"symbol": f"{x['baseAsset']}_TRY", "bn": x["symbol"], "base": x["baseAsset"], "quote": "TRY",
                                      "type": 1} for x in d["symbols"] if x["quoteAsset"] == "TRY" and x["status"] == "TRADING"}


def gonder(token: str, chat: str, metin: str):
    parca, cur = [], ""
    for line in metin.split("\n"):
        if len(cur) + len(line) + 1 > 3900 and cur:
            parca.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        parca.append(cur)
    for p in parca:
        data = urllib.parse.urlencode({"chat_id": chat, "text": p, "disable_web_page_preview": "true"}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                cevap = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:      # anahtar yazdırılmaz
            cevap = json.loads(e.read().decode() or "{}")
        if not cevap.get("ok"):
            raise SystemExit(f"Telegram mesajı gönderilemedi: {cevap.get('description', '?')}")


async def tara() -> tuple[list[str], dict]:
    simdi = time.time()
    d = durum_oku()
    i = Istemci(min_gap=0.05)
    sem, tick = await semboller(i), await ticker(i)
    arz = Arz(KOK)
    if simdi - arz.guncelleme > 24 * 3600:
        await arz.yenile()
    tr = Tarayici(i, lambda: sem, lambda: tick, arz=arz)
    tr.anlik_onbellek_sn = 600          # aynı zaman dilimindeki taramalar mumları bir kez indirsin
    rejim = AltRejim(tr)
    await rejim.yenile()
    mesajlar = []
    acik = rejim.acik_mi()
    if d.get("rejim_acik") is not None and acik != d.get("rejim_acik"):
        mesajlar.append(f"{'🟢' if acik else '🔴'} Altcoin piyasa yönü (taranan 100 coinin 7 günlük ortanca değişimi): "
                        f"{'hesaplanamadı' if rejim.deger is None else f'%{rejim.deger:+.2f}'} → "
                        + ("otomatik alım sinyalleri yeniden açık." if acik else "piyasa düşüşte, alım sinyali gönderilmiyor."))
    d["rejim_acik"], d["rejim_deger"] = acik, rejim.deger
    if not acik:
        return mesajlar, d
    yeni = []
    for kod, m in MODELLER.items():
        for a in m["araliklar"]:
            try:
                r = await tr.tara(a, m["kosullar"], "acik", ["TRY"], 0, ana_ag_sart=False)
            except Exception as e:
                print(f"{kod} {a}: {e}", file=sys.stderr)
                continue
            for x in r["sonuc"]:
                if x["mum_bitis"] < simdi * 1000:                  # yalnızca şu an açık mum
                    continue
                pct = (x.get("uygunluk") or {}).get("pct")
                if pct is None or not any(lo <= pct <= hi for lo, hi in UYGUNLUK):
                    continue
                anahtar = f"{kod}|{a}|{x['s']}|{x['mum_ts']}"
                if anahtar in d["gonderilen"] or simdi - d["son_coin"].get(x["s"], 0) < TEKRAR_SN:
                    continue
                d["gonderilen"][anahtar] = simdi
                d["son_coin"][x["s"]] = simdi
                yeni.append((m["ad"], a, x, pct))
    d["gonderilen"] = {k: v for k, v in d["gonderilen"].items() if simdi - v < 3 * 86400}
    d["son_coin"] = {k: v for k, v in d["son_coin"].items() if simdi - v < 2 * 86400}
    if yeni:
        s = [f"📊 Binance TR · botun alım sinyalleri · {datetime.fromtimestamp(simdi, TR):%d.%m %H:%M} (TR)",
             f"Altcoin 7 gün ortancası %{rejim.deger:+.2f} · {rejim.coin} coin"]
        for ad, a, x, pct in yeni:
            onc = " ★" if any("ÖNCELİKLİ" in str(z) for z in x.get("aciklama", [])) else ""
            s.append(f"• {x['s']} · {ARALIK_AD[a]} · {x['fiyat']:.8g} · uygunluk %{pct}{onc}\n   {ad}")
        s += ["", "Satış (bottaki sahte cüzdan kuralları): +%4'te yarısı, kalanı en yüksekten −%4 iz süren; zarar kes yazılan yüzde.",
              "Yalnızca borsa verisi; yatırım tavsiyesi değildir."]
        mesajlar.append("\n".join(s))
    return mesajlar, d


def main():
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    try:
        mesajlar, d = asyncio.run(tara())
    except Exception as e:
        mesajlar, d = [f"⚠️ Binance TR tarama yapılamadı: {str(e)[:200]}"], durum_oku()
    (KOK / "durum.json").write_text(json.dumps(d, ensure_ascii=False), "utf-8")
    for m in mesajlar:
        print(m)
    if not mesajlar:
        print("Yeni sinyal yok.")
        return
    if not token or not chat:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID tanımlı değil; mesaj gönderilmedi.", file=sys.stderr)
        sys.exit(1)
    for m in mesajlar:
        gonder(token, chat, m)


if __name__ == "__main__":
    main()
