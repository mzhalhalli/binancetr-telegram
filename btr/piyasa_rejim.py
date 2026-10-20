"""Altcoin piyasa yönü (20.10.2026, kullanıcı isteği): taranan coinlerin (en likit 100) son 7 günlük değişiminin ortancası.
Otomatik alım yalnızca bu değer artıdaysa yapılır (al_sat.AlSatAyar.alt_rejim).

90 günlük gerçek veri geri testinde (25.07-20.10.2026) iki ayrı 45 günlük dönemin ikisinde de zarar etmeyen tek piyasa
filtresiydi (yükseliş döneminde kazancı yarıya indirdi, zayıf dönemde zararı sıfırladı, en büyük düşüşü %8,9'dan %5'e indirdi).

Hesap: her coin için 4 saatlik mumlar; değişim = şu anki fiyat (açık mumun kapanışı) / 42 mum (7 gün) önceki mumun kapanışı.
YENILE_SN'de bir yenilenir. Hesaplanamazsa (veri yok) deger None olur: güvenli tarafta kalınır, otomatik alım yapılmaz."""
from __future__ import annotations

import asyncio
import contextlib
import statistics
import time

YENILE_SN = 1800
MUM_7G = 42
EN_AZ_COIN = 50


class AltRejim:
    def __init__(self, tarayici, clock=time.time, bekle=asyncio.sleep):
        self.tr, self.clock, self.bekle = tarayici, clock, bekle
        self.deger: float | None = None
        self.coin = 0
        self.guncelleme = 0.0
        self.son_hata = ""

    def acik_mi(self) -> bool:
        return self.deger is not None and self.deger > 0

    async def yenile(self) -> float | None:
        sem = self.tr.evren()
        kapi = asyncio.Semaphore(8)
        degisim = []

        async def bir(s):
            async with kapi:
                with contextlib.suppress(Exception):
                    r = await self.tr.client.mumlar(s, "4h", limit=MUM_7G + 2)
                    if len(r) >= MUM_7G + 1 and float(r[-1 - MUM_7G][4]):
                        degisim.append(float(r[-1][4]) / float(r[-1 - MUM_7G][4]) - 1)
        await asyncio.gather(*(bir(s) for s in sem))
        self.coin = len(degisim)
        if len(degisim) >= EN_AZ_COIN:
            self.deger, self.son_hata = round(statistics.median(degisim) * 100, 2), ""
        else:
            self.deger, self.son_hata = None, f"altcoin piyasa yönü hesaplanamadı ({len(degisim)} coin)"
        self.guncelleme = self.clock()
        return self.deger

    def ozet(self) -> dict:
        return {"deger": self.deger, "acik": self.acik_mi(), "coin": self.coin, "guncelleme": self.guncelleme, "son_hata": self.son_hata}

    async def run(self):
        await self.bekle(20)                        # bot açılınca ticker ve sembol listesi gelsin
        while True:
            try:
                await self.yenile()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.son_hata = f"{type(e).__name__}: {e}"[:200]
            await self.bekle(YENILE_SN)
