"""Binance TR hazır taramalarını son kapanan 1 saatlik (her saat) ve 4 saatlik (4 saatte bir) mumda çalıştırır,
sonucu Telegram kanalına yollar. Elle çalıştırmada (Run workflow) ikisi de gönderilir.

Yalnızca herkese açık piyasa verisini okur: borsa API anahtarı, hesap ve emir yoktur.
Gerekenler (GitHub > Settings > Secrets and variables > Actions):
  TELEGRAM_BOT_TOKEN  BotFather'ın verdiği anahtar
  TELEGRAM_CHAT_ID    kanal adı (@kanalim) ya da -100 ile başlayan kanal numarası
Taramalar (bilgisayardaki bottaki hazır taramalarla aynı):
  1) Bollinger (21, 2): son 3 mum ardışık yeşil ve orta bandın üstünde kapattı
  2) Süpriz: WAL_TRY 14.10 (4s) ve BLUR_TRY 15.10 (15dk) yükselişlerine benzeyenler (7 şartın en az 6'sı)
  3) Erken uyarı: BLUR_TRY 15.10 11:45 gibi patlama öncesi sessiz hazırlık mumu (8 şartın 8'i)
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
from btr import indikator as ind
from btr import supriz as sp

ARALIK_MS = {"1h": 3_600_000, "4h": 14_400_000}
ARALIK_AD = {"1h": "1 saatlik", "4h": "4 saatlik"}
LIMIT = 200
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


def degerlendir(rows: list) -> dict | None:
    if len(rows) < 60:
        return None
    kap = [float(r[4]) for r in rows]
    acl = [float(r[1]) for r in rows]
    hq = [float(r[7]) for r in rows]
    bb, bb_acik = bb_uc_yesil(kap, acl)
    sup = sp.hesapla(kap, hq, acl)
    erk = er.hesapla(kap, hq, acl)
    return {"fiyat": kap[-1], "mum_ts": int(rows[-1][0]), "hacim": hq[-1], "bb": bb, "bb_acik": bb_acik,
            "supriz": sup["puan"] if sup else 0, "supriz_detay": sup["detay"] if sup else "",
            "erken": bool(erk and erk["ok"]), "erken_detay": erk["detay"] if erk else ""}


def tara(now_ms: int | None = None, aralik="4h", paralel=10, sem: list | None = None) -> dict:
    now_ms = now_ms or int(time.time() * 1000)
    sem = sem if sem is not None else semboller()
    sonuc, hata = [], []

    def bir(s):
        try:
            r = degerlendir(mumlar(s["bn"], now_ms, aralik))
            if r and r["mum_ts"] >= now_ms - 2 * ARALIK_MS[aralik]:      # işlemden kalkmış coinleri atla
                sonuc.append({"s": s["s"], **r})
        except Exception as e:
            hata.append(f"{s['s']}: {e}")

    with ThreadPoolExecutor(paralel) as ex:
        list(ex.map(bir, sem))
    son_mum = max((x["mum_ts"] for x in sonuc), default=None)
    sonuc = [x for x in sonuc if x["mum_ts"] == son_mum]
    return {"aralik": aralik, "taranan": len(sem), "son_mum": son_mum, "hata": len(hata), "hata_ornek": hata[:3], "sonuc": sonuc}


def calistirilacak(now_ms: int, elle=False) -> list[str]:
    """Her saat 1 saatlik; son kapanan saat 4 saatlik mumun da kapanışıysa (UTC 00/04/08/12/16/20) 4 saatlik de."""
    if elle:
        return ["1h", "4h"]
    son_kapanis = now_ms // ARALIK_MS["1h"] * ARALIK_MS["1h"]
    return ["1h"] + (["4h"] if son_kapanis % ARALIK_MS["4h"] == 0 else [])


def _sayi(x: float) -> str:
    return f"{x:,.6g}".replace(",", "X").replace(".", ",").replace("X", ".")


def _hacim(x: float) -> str:
    return f"{x / 1e6:.1f} mn" if x >= 1e6 else f"{x / 1e3:.0f} bin"


def mesaj(r: dict) -> str:
    aralik = r.get("aralik", "4h")
    if r["son_mum"] is None:
        return f"⚠️ Binance TR {ARALIK_AD[aralik]} tarama: veri alınamadı ({r['hata']} hata). {r['hata_ornek'][:1]}"
    t0 = datetime.fromtimestamp(r["son_mum"] / 1000, TR)
    t1 = t0 + timedelta(milliseconds=ARALIK_MS[aralik])
    bas = f"📊 Binance TR · {ARALIK_AD[aralik]} kapanış {t0:%d.%m %H:%M}–{t1:%H:%M} (TR saati)\n{r['taranan']} TRY çifti tarandı"
    if r["hata"]:
        bas += f", {r['hata']} coinde veri alınamadı"
    bb = sorted((x for x in r["sonuc"] if x["bb"]), key=lambda x: (-x["supriz"], -x["hacim"]))
    sup = sorted((x for x in r["sonuc"] if x["supriz"] >= sp.TOPLAM - 1), key=lambda x: (-x["supriz"], -x["hacim"]))
    erk = sorted((x for x in r["sonuc"] if x["erken"]), key=lambda x: -x["hacim"])

    def etiket(x, erken=True):
        e = " 🟠 ERKEN UYARI" if erken and x["erken"] else ""
        return e + (" 🟣 SÜPRİZ" if x["supriz"] >= sp.TOPLAM else (" 🟪 süpriz adayı 6/7" if x["supriz"] == sp.TOPLAM - 1 else ""))

    satir = [bas, "", f"🟢 Orta bandın üstünde ardışık 3 yeşil mum (BOLL 21,2): {len(bb)}"]
    satir += [f"• {x['s']}  {_sayi(x['fiyat'])}  ({x['bb_acik']})  hacim {_hacim(x['hacim'])} TRY{etiket(x)}" for x in bb] or ["• yok"]
    satir += ["", f"🟣 Süpriz (WAL_TRY 14.10 / BLUR_TRY 15.10 benzeri, 7 şartın en az 6'sı): {len(sup)}"]
    satir += [f"• {x['s']}  {_sayi(x['fiyat'])}  {x['supriz']}/7{etiket(x)}\n   {x['supriz_detay']}" for x in sup] or ["• yok"]
    satir += ["", f"🟠 Erken uyarı (BLUR_TRY 15.10 patlama öncesi benzeri, 8/8; izlemeye al): {len(erk)}"]
    satir += [f"• {x['s']}  {_sayi(x['fiyat'])}  hacim {_hacim(x['hacim'])} TRY{etiket(x, False)}\n   {x['erken_detay']}"
              for x in erk] or ["• yok"]
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
    araliklar = calistirilacak(now_ms, elle)
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
        if not elle and r["son_mum"] is not None and r["son_mum"] <= int(onceki.get(aralik, 0)):
            print(f"{aralik}: bu mum daha önce gönderildi (dış zamanlayıcı ya da yedek çalıştırma), atlandı\n")
            continue
        m = mesaj(r)
        print(m + "\n")
        mesajlar.append((aralik, m, r["son_mum"]))
        if aralik == "4h":   # depo aktif kalsın diye 4 saatte bir kaydedilir (her saat commit atılmasın)
            with open("son_sonuc.json", "w", encoding="utf-8") as f:
                json.dump({"ts": int(time.time()), "son_mum": r["son_mum"], "taranan": r["taranan"], "hata": r["hata"],
                           "bb": [x["s"] for x in r["sonuc"] if x["bb"]],
                           "erken": [x["s"] for x in r["sonuc"] if x["erken"]],
                           "supriz": {x["s"]: x["supriz"] for x in r["sonuc"] if x["supriz"] >= sp.TOPLAM - 1}},
                          f, ensure_ascii=False, indent=1)
    if not token or not chat:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID tanımlı değil; mesaj gönderilmedi.", file=sys.stderr)
        sys.exit(1)
    gonderilen = dict(onceki)
    for aralik, m, son_mum in mesajlar:
        gonder(token, chat, m)
        if son_mum is not None:
            gonderilen[aralik] = max(son_mum, int(gonderilen.get(aralik, 0)))
            with open(GONDERILEN, "w", encoding="utf-8") as f:
                json.dump(gonderilen, f)


if __name__ == "__main__":
    main()
