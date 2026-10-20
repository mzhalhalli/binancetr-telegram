"""Arz süzgeci (20.10.2026, kullanıcı isteği): dolaşımdaki arzı en çok arzın yarısından az olan coinler taranmaz
(ileride çok sayıda yeni token piyasaya çıkabilir: satış baskısı riski).

- Veri: CoinGecko herkese açık API (/coins/markets: circulating_supply, max_supply, total_supply), piyasa değerine göre
  ilk SAYFA x 250 coin. Günde bir yenilenir, data/arz.json'da saklanır (bot açılınca önce kayıttan okunur).
- Oran = dolaşımdaki / en çok arz. En çok arz yoksa (sınırsız arz, ör. ETH) toplam arz kullanılır.
- Coin CoinGecko'da bulunamazsa ya da arzı bilinmiyorsa TARANIR (veri yokken tahminle elenmez).
- Aynı sembol birden çok coinde varsa piyasa değeri en büyük olan alınır.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Callable

URL = "https://api.coingecko.com/api/v3/coins/markets"
SAYFA = 6                # 6 x 250 = piyasa değerine göre ilk 1.500 coin
YENILE_SN = 24 * 3600
ESIK = 0.5


def _indir(sayfa: int) -> list:
    import requests
    r = requests.get(URL, params={"vs_currency": "usd", "order": "market_cap_desc", "per_page": 250, "page": sayfa},
                     timeout=30, headers={"User-Agent": "Mozilla/5.0 (BinanceTR-arastirma)"})
    r.raise_for_status()
    return r.json()


def oran_hesapla(c: dict) -> float | None:
    dol = c.get("circulating_supply")
    ust = c.get("max_supply") or c.get("total_supply")
    if not dol or not ust or ust <= 0:
        return None
    return float(dol) / float(ust)


class Arz:
    def __init__(self, dizin: Path, indir: Callable[[int], list] | None = None, clock=time.time, bekle=asyncio.sleep):
        self.dosya = Path(dizin) / "data" / "arz.json"
        self.indir, self.clock, self.bekle = indir or _indir, clock, bekle
        self.veri: dict[str, dict] = {}
        self.guncelleme, self.son_hata = 0.0, ""
        with contextlib.suppress(OSError, ValueError):
            d = json.loads(self.dosya.read_text("utf-8"))
            self.veri, self.guncelleme = dict(d.get("veri") or {}), float(d.get("guncelleme") or 0)

    def oran(self, base: str) -> float | None:
        return (self.veri.get(str(base).upper()) or {}).get("oran")

    def disi(self, base: str) -> bool:
        o = self.oran(base)
        return o is not None and o < ESIK

    async def yenile(self) -> int:
        veri = {}
        for p in range(1, SAYFA + 1):
            liste = None
            for deneme in range(3):              # ücretsiz API dakikalık sınırı (429): bekleyip yeniden dene
                try:
                    liste = await asyncio.to_thread(self.indir, p)
                    break
                except Exception as e:
                    self.son_hata = f"CoinGecko sayfa {p}: {type(e).__name__}"[:200]
                    await self.bekle(65)
            if liste is None:
                if not veri:
                    return 0
                break
            for c in liste:
                s = str(c.get("symbol", "")).upper()
                if s and s not in veri:          # liste piyasa değerine göre sıralı: ilk görülen en büyük
                    veri[s] = {"oran": oran_hesapla(c), "dolasim": c.get("circulating_supply"), "max": c.get("max_supply"),
                               "toplam": c.get("total_supply"), "ad": c.get("name")}
            if p < SAYFA:
                await self.bekle(8)              # ücretsiz API sınırı
        self.veri, self.guncelleme = veri, self.clock()
        if len(veri) >= SAYFA * 200:
            self.son_hata = ""
        with contextlib.suppress(OSError):
            self.dosya.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.dosya.parent, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"guncelleme": self.guncelleme, "veri": veri}, f, ensure_ascii=False)
            os.replace(tmp, self.dosya)
        return len(veri)

    async def run(self):
        while True:
            if self.clock() - self.guncelleme >= YENILE_SN:
                with contextlib.suppress(Exception):
                    await self.yenile()
            await self.bekle(3600)
