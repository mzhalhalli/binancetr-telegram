"""Binance TR taramalarını çalıştırıp sonucu Telegram kanalına yollar. 15.10.2026'dan beri kanala yalnızca şunlar gider:
  1) 4 saatlik: Bollinger (21, 2) orta bandının üstünde ardışık 3 yeşil mum
  2) 4 saatlik: formasyon kırılımı (15.10.2026 geçmiş testinde rastgele girişten iyi çıkan yukarı kırılımlar:
     genişleyen formasyon, dikdörtgen, yükselen üçgen, ikili dip; btr/formasyon.py)
  3) 15 dakikalık erken uyarı (patlama öncesi sessiz hazırlık mumu, 8 şartın 8'i).
     Dış zamanlayıcı (cron-job.org) her 15 dakikada bir otomatik=evet ile çalıştırır; iş akışında ONBES_DK=evet.
     Eşleşen coin yoksa mesaj atılmaz. Mum kapanışından 10 dakikadan geç başlayan çalıştırma bu mumu göndermez
     (eski uyarı gitmesin, aynı mum iki kez gitmesin). Depo herkese açık olmalı: gizli depoda GitHub'ın aylık ücretsiz
     dakikası 15 dakikada bir çalışmaya yetmez.
Elle çalıştırmada (Run workflow) son 4 saatlik mum her zaman gönderilir.

Yalnızca herkese açık piyasa verisini okur: borsa API anahtarı, hesap ve emir yoktur.
Gerekenler (GitHub > Settings > Secrets and variables > Actions):
  TELEGRAM_BOT_TOKEN  BotFather'ın verdiği anahtar
  TELEGRAM_CHAT_ID    kanal adı (@kanalim) ya da -100 ile başlayan kanal numarası
1 saatlik mesajlar, 4 saatlik erken uyarı ve formasyon kırılımına yaklaşanlar listesi 15.10.2026'da kullanıcının isteğiyle
kaldırıldı; süpriz taraması da aynı gün kaldırıldı.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from btr import erken as er
from btr import formasyon as fo
from btr import indikator as ind

ARALIK_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}
ARALIK_AD = {"15m": "15 dakikalık", "1h": "1 saatlik", "4h": "4 saatlik"}
LIMIT = 490          # formasyon dönüş noktaları için ~480 kapanmış mum (istek ağırlığı 500'e kadar aynı)
LISTE_EN_COK = 25    # bir bölümde gösterilen en çok coin
TR = timezone(timedelta(hours=3))
# piyasa verisi kaynakları (sırayla denenir); Binance TR'deki coinlerin mumları Binance defterinden gelir
API = ["https://data-api.binance.vision", "https://api.binance.me", "https://api.binance.com"]
TR_SEMBOL = ["https://www.binance.tr/open/v1/common/symbols", "https://www.trbinance.com/open/v1/common/symbols"]
BB_N, BB_K, BB_SON = 21, 2.0, 3
GONDERILEN = "gonderilen.json"   # aralık -> en son gönderilen mumun açılışı (aynı mum iki kez gönderilmesin)


def _get(url: str, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "binancetr-tarama/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _api(path: str, q: dict):
    hata = None
    for host in API:
        try:
            return _get(f"{host}{path}?{urllib.parse.urlencode(q)}")
        except Exception as e:  # sıradaki kaynağı dene
            hata = e
    raise RuntimeError(f"{path}: {hata}")


def semboller(quote="TRY") -> list[dict]:
    """Binance TR listesi; ulaşılamazsa Binance'teki TRY çiftleri (Binance TR bu defterleri kullanır)."""
    for url in TR_SEMBOL:
        try:
            d = _get(url)
            lst = d.get("data", {}).get("list", []) if isinstance(d, dict) else []
            out = [{"s": x["symbol"], "bn": x["symbol"].replace("_", "")} for x in lst
                   if str(x.get("quoteAsset", "")).upper() == quote and x.get("type") in (1, "1")]
            if out:
                return out
        except Exception:
            pass
    d = _api("/api/v3/exchangeInfo", {"permissions": "SPOT"})
    return [{"s": f"{x['baseAsset']}_{x['quoteAsset']}", "bn": x["symbol"]} for x in d["symbols"]
            if x["quoteAsset"] == quote and x["status"] == "TRADING"]


def mumlar(bn: str, now_ms: int, aralik="4h") -> list:
    start = now_ms - (LIMIT + 1) * ARALIK_MS[aralik]
    rows = _api("/api/v3/klines", {"symbol": bn, "interval": aralik, "startTime": start, "limit": LIMIT + 2})
    tekil = {int(r[0]): r for r in rows if isinstance(r, list) and len(r) >= 8}
    return [tekil[k] for k in sorted(tekil) if int(tekil[k][6]) < now_ms]   # yalnızca kapanmış mumlar


def bb_uc_yesil(kap: list[float], acl: list[float]) -> tuple[bool, str]:
    """Son 3 mum ardışık yeşil ve her biri kendi Bollinger orta bandının üstünde kapattı."""
    if len(kap) < BB_N + BB_SON - 1:
        return False, ""
    parca, ok = [], True
    for j in range(BB_SON - 1, -1, -1):
        seri = kap[:len(kap) - j]
        o, c = acl[len(kap) - 1 - j], seri[-1]
        m = ind.bollinger(seri, BB_N, BB_K)[0]
        ok = ok and c > o and c > m
        parca.append(f"{(c / o - 1) * 100:+.1f}%")
    return ok, " ".join(parca)


def degerlendir(rows: list, aralik: str = "4h") -> dict | None:
    if len(rows) < 60:
        return None
    kap = [float(r[4]) for r in rows]
    acl = [float(r[1]) for r in rows]
    yuk = [float(r[2]) for r in rows]
    dus = [float(r[3]) for r in rows]
    hq = [float(r[7]) for r in rows]
    bb, bb_acik = bb_uc_yesil(kap, acl)
    erk = er.hesapla(kap, hq, acl)
    fm = fo.bul(acl, yuk, dus, kap, aralik) or {"kirilim": [], "yaklasan": []}
    return {"fiyat": kap[-1], "mum_ts": int(rows[-1][0]), "hacim": hq[-1], "bb": bb, "bb_acik": bb_acik,
            "erken": bool(erk and erk["ok"]), "erken_detay": erk["detay"] if erk else "",
            "fm_kirilim": fm["kirilim"], "fm_yaklasan": fm["yaklasan"], "ma_ustu": fo.ma_ustu(kap)}


def tara(now_ms: int | None = None, aralik="4h", paralel=10, sem: list | None = None) -> dict:
    now_ms = now_ms or int(time.time() * 1000)
    sem = sem if sem is not None else semboller()
    sonuc, hata = [], []

    def bir(s):
        try:
            r = degerlendir(mumlar(s["bn"], now_ms, aralik), aralik)
            if r and r["mum_ts"] >= now_ms - 2 * ARALIK_MS[aralik]:      # işlemden kalkmış coinleri atla
                sonuc.append({"s": s["s"], **r})
        except Exception as e:
            hata.append(f"{s['s']}: {e}")

    with ThreadPoolExecutor(paralel) as ex:
        list(ex.map(bir, sem))
    son_mum = max((x["mum_ts"] for x in sonuc), default=None)
    sonuc = [x for x in sonuc if x["mum_ts"] == son_mum]
    # piyasa genişliği (geçmiş testteki gibi en az 50 coin): kapanışı kendi MA50'sinin üstündeki coin oranı; BTC_TRY durumu
    ma = [x["ma_ustu"] for x in sonuc if x["ma_ustu"] is not None]
    btc = next((x["ma_ustu"] for x in sonuc if x["s"] == "BTC_TRY"), None)
    piyasa = {"genislik_pct": round(sum(ma) / len(ma) * 100, 1) if len(ma) >= 50 else None, "coin": len(ma), "btc_ust": btc}
    for x in sonuc:
        for f in x["fm_kirilim"] + x["fm_yaklasan"]:
            f["uygun"], f["uygun_not"] = fo.uygunluk(aralik, f["ad"], piyasa["genislik_pct"], btc)
    return {"aralik": aralik, "taranan": len(sem), "son_mum": son_mum, "hata": len(hata), "hata_ornek": hata[:3],
            "piyasa": piyasa, "sonuc": sonuc}


ONBES_GEC_MS = 10 * 60_000   # 15 dakikalık mum kapanışından bu kadar sonra başlayan çalıştırma o mumu göndermez


def calistirilacak(now_ms: int, elle=False, onbes=False) -> list[str]:
    """4 saatlik mum UTC 00/04/08/12/16/20'de kapanır: o saatin içindeki çalıştırma 4 saatliği tarar (aynı mum iki kez
    gitmez). onbes: dış zamanlayıcının çalıştırması ve ONBES_DK=evet; son kapanan 15 dakikalık mumda erken uyarıya bakar."""
    if elle:
        return ["4h"]
    son_saat = now_ms // ARALIK_MS["1h"] * ARALIK_MS["1h"]
    return (["4h"] if son_saat % ARALIK_MS["4h"] == 0 else []) + (["15m"] if onbes else [])


def _sayi(x: float) -> str:
    return f"{x:,.6g}".replace(",", "X").replace(".", ",").replace("X", ".")


def _hacim(x: float) -> str:
    return f"{x / 1e6:.1f} mn" if x >= 1e6 else f"{x / 1e3:.0f} bin"


def _yuzde(x: float) -> str:
    return f"%{x:.1f}".replace(".", ",")


def _fm_etiket(x: dict, aralik: str) -> str:
    return "".join(f" 🔷 {fo.etiket(aralik)} {f['baslik'].replace('i', 'İ').upper()} ↑" for f in x["fm_kirilim"])   # Türkçe büyük harf


def _piyasa_satiri(p: dict | None) -> str:
    if not p:
        return ""
    g = p.get("genislik_pct")
    m = (f"Piyasa: MA50 üstündeki coin oranı {_yuzde(g)} ({'güçlü' if g >= fo.GUCLU_PIYASA else 'zayıf'})" if g is not None
         else f"Piyasa genişliği hesaplanamadı ({p.get('coin', 0)} coin)")
    if p.get("btc_ust") is not None:
        m += f" · BTC_TRY MA50 {'üstünde' if p['btc_ust'] else 'altında'}"
    return m


def _uygun_isareti(f: dict) -> str:
    return "✓" if f.get("uygun") else ("⚠" if f.get("uygun") is False else "?")


def _kisalt(satirlar: list[str], adet: int, satir_basina: int) -> list[str]:
    if adet <= LISTE_EN_COK:
        return satirlar
    return satirlar[:LISTE_EN_COK * satir_basina] + [f"• … ve {adet - LISTE_EN_COK} coin daha"]


def _baslik(r: dict) -> str:
    aralik = r.get("aralik", "4h")
    t0 = datetime.fromtimestamp(r["son_mum"] / 1000, TR)
    t1 = t0 + timedelta(milliseconds=ARALIK_MS[aralik])
    bas = f"{t0:%d.%m %H:%M}–{t1:%H:%M} (TR saati)\n{r['taranan']} TRY çifti tarandı"
    return bas + (f", {r['hata']} coinde veri alınamadı" if r["hata"] else "")


def mesaj(r: dict) -> str:
    """4 saatlik mesaj: orta bandın üstünde 3 yeşil mum ve formasyon kırılımı."""
    aralik = r.get("aralik", "4h")
    if r["son_mum"] is None:
        return f"⚠️ Binance TR {ARALIK_AD[aralik]} tarama: veri alınamadı ({r['hata']} hata). {r['hata_ornek'][:1]}"
    bb = sorted((x for x in r["sonuc"] if x["bb"]), key=lambda x: -x["hacim"])
    fmk = sorted(((x, f) for x in r["sonuc"] for f in x.get("fm_kirilim", [])), key=lambda xf: (not xf[1].get("uygun"), -xf[0]["hacim"]))

    satir = [f"📊 Binance TR · {ARALIK_AD[aralik]} kapanış {_baslik(r)}", "",
             f"🟢 Orta bandın üstünde ardışık 3 yeşil mum (BOLL 21,2): {len(bb)}"]
    satir += _kisalt([f"• {x['s']}  {_sayi(x['fiyat'])}  ({x['bb_acik']})  hacim {_hacim(x['hacim'])} TRY" + _fm_etiket(x, aralik)
                      for x in bb], len(bb), 1) or ["• yok"]
    if aralik in fo.SECILI:
        adlar = ", ".join(fo.AD[a].replace("İ", "i").lower() for a in fo.SECILI[aralik])
        satir += ["", f"🔷 Formasyon kırılımı (geçmişte rastgeleden iyi çıkan; {adlar}): {len(fmk)}", _piyasa_satiri(r.get("piyasa"))]
        fs = []
        for x, f in fmk:
            fs += [f"• {x['s']}  {_sayi(x['fiyat'])}  {f['baslik']} ↑  hedef {_sayi(f['hedef'])} (+{_yuzde(f['hedef_pct'])}) · "
                   f"zarar-kes {_sayi(f['stop'])} (–{_yuzde(f['stop_pct'])})",
                   f"   {fo.gecmis_metni(aralik, f['ad'])} · {_uygun_isareti(f)} {f['uygun_not']}"]
        satir += _kisalt(fs, len(fmk), 2) or ["• yok"]
    satir += ["", "Yalnızca borsa verisi; yatırım tavsiyesi değildir."]
    return "\n".join(satir)


def mesaj_erken(r: dict) -> str | None:
    """15 dakikalık erken uyarı mesajı; eşleşen coin yoksa ya da veri alınamadıysa None (mesaj atılmaz)."""
    if r["son_mum"] is None:
        return None
    erk = sorted((x for x in r["sonuc"] if x["erken"]), key=lambda x: -x["hacim"])
    if not erk:
        return None
    satir = [f"🟠 Binance TR · 15 dakikalık erken uyarı · kapanış {_baslik(r)}",
             "Patlama öncesi sessiz hazırlık, 8 şartın 8'i tutuyor; izlemeye al", ""]
    satir += _kisalt([f"• {x['s']}  {_sayi(x['fiyat'])}  hacim {_hacim(x['hacim'])} TRY\n   {x['erken_detay']}" for x in erk], len(erk), 1)
    satir += ["", "Yalnızca borsa verisi; yatırım tavsiyesi değildir."]
    return "\n".join(satir)


def parcala(metin: str, n=3900) -> list[str]:
    out, cur = [], ""
    for line in metin.split("\n"):
        if len(cur) + len(line) + 1 > n and cur:
            out.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        out.append(cur)
    return out


def gonder(token: str, chat: str, metin: str):
    for p in parcala(metin):
        data = urllib.parse.urlencode({"chat_id": chat, "text": p, "disable_web_page_preview": "true"}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                cevap = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:   # anahtar yazdırılmaz, yalnızca Telegram'ın açıklaması
            cevap = json.loads(e.read().decode() or "{}")
        if not cevap.get("ok"):
            raise SystemExit(f"Telegram mesajı gönderilemedi: {cevap.get('description', '?')}")


def _gonderilen_oku() -> dict:
    try:
        with open(GONDERILEN, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def main():
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    now_ms = int(time.time() * 1000)
    # dış zamanlayıcı (cron-job.org) "otomatik=evet" ile başlatır; Run workflow düğmesi elle çalıştırmadır
    elle = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch" and os.environ.get("OTOMATIK", "").lower() != "evet"
    # 15 dakikalık erken uyarı yalnızca dış zamanlayıcının çalıştırmasında (GitHub'ın gecikmeli yedeği ve elle çalıştırma bakmaz)
    onbes = (os.environ.get("ONBES_DK", "").lower() == "evet" and not elle
             and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch")
    araliklar = calistirilacak(now_ms, elle, onbes)
    if not araliklar:
        print("Bu çalıştırmada taranacak mum yok (4 saatlik kapanış saati değil, 15 dakikalık tarama kapalı).")
        return
    onceki = _gonderilen_oku()
    mesajlar = []
    try:
        sem = semboller()
    except Exception as e:
        sem, sem_hata = None, e
    for aralik in araliklar:
        try:
            if sem is None:
                raise sem_hata
            r = tara(now_ms, aralik, sem=sem)
        except Exception as e:   # veri hiç alınamadıysa da kanala haber ver (ör. 451 = bölge kısıtlaması)
            r = {"aralik": aralik, "taranan": 0, "son_mum": None, "hata": 1, "hata_ornek": [str(e)[:200]], "sonuc": []}
        if aralik == "15m":
            if r["son_mum"] is not None and now_ms - (r["son_mum"] + ARALIK_MS["15m"]) > ONBES_GEC_MS:
                print("15m: çalıştırma mum kapanışından 10 dakikadan geç başladı, eski uyarı gönderilmedi\n")
                continue
        elif not elle and r["son_mum"] is not None and r["son_mum"] <= int(onceki.get(aralik, 0)):
            print(f"{aralik}: bu mum daha önce gönderildi (dış zamanlayıcı ya da yedek çalıştırma), atlandı\n")
            continue
        m = mesaj_erken(r) if aralik == "15m" else mesaj(r)
        print((m or f"{aralik}: erken uyarı yok ya da veri alınamadı, mesaj atılmadı") + "\n")
        mesajlar.append((aralik, m, r["son_mum"]))
        if aralik == "4h":   # depo aktif kalsın diye 4 saatte bir kaydedilir (her saat commit atılmasın)
            with open("son_sonuc.json", "w", encoding="utf-8") as f:
                json.dump({"ts": int(time.time()), "son_mum": r["son_mum"], "taranan": r["taranan"], "hata": r["hata"],
                           "bb": [x["s"] for x in r["sonuc"] if x["bb"]],
                           "formasyon": {x["s"]: [f["ad"] for f in x["fm_kirilim"]] for x in r["sonuc"] if x["fm_kirilim"]},
                           "piyasa": r.get("piyasa")},
                          f, ensure_ascii=False, indent=1)
    if not token or not chat:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID tanımlı değil; mesaj gönderilmedi.", file=sys.stderr)
        sys.exit(1)
    gonderilen = dict(onceki)
    for aralik, m, son_mum in mesajlar:
        if m:
            gonder(token, chat, m)
        if son_mum is not None and aralik != "15m":      # 15 dakikalık her çalıştırmada commit atılmasın diye kaydedilmez
            gonderilen[aralik] = max(son_mum, int(gonderilen.get(aralik, 0)))
            with open(GONDERILEN, "w", encoding="utf-8") as f:
                json.dump(gonderilen, f)


if __name__ == "__main__":
    main()
