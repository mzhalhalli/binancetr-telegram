"""Binance TR salt okunur istemci.

- Yalnızca herkese açık piyasa verisi okunur. API anahtarı, imza, hesap, emir uç noktası YOKTUR.
- İzin listesi dışındaki hiçbir istek ağa çıkamaz.
- Binance TR iki tür sembol listeler: type 1 (Binance ana defterine bağlı; veri Binance API sunucusundan)
  ve diğerleri (yalnızca Binance TR defteri; veri www.trbinance.com/open/v1/market altından).
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Callable
from urllib.parse import urlencode

# Sunucular: ilki çalışmazsa (bağlantı hatası, 403/404/451) sıradaki denenir ve çalışan hatırlanır.
TR_HOSTS = ["https://www.binance.tr", "https://www.trbinance.com"]
# Binance TR belgelerinde type 1 semboller için piyasa verisi sunucusu.
API_HOSTS = ["https://api.binance.me", "https://api.binance.cc"]

# (sunucu türü, yol) -> istek ağırlığı. Bu listede olmayan hiçbir istek atılamaz.
IZINLI = {
    ("tr", "/open/v1/common/time"): 1,
    ("tr", "/open/v1/common/symbols"): 1,
    ("tr", "/open/v1/market/klines"): 1,
    ("api", "/api/v3/ticker/24hr"): 80,
    ("api", "/api/v3/klines"): 2,
    ("api", "/api/v3/ticker/price"): 4,     # otomatik al-sat: açık işlemlerin anlık fiyatı
    ("api", "/api/v3/aggTrades"): 4,        # alım alarmı: herkese açık işlem akışı (20.10.2026)
}


class BtrHata(Exception):
    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status = status


class HizSiniri(BtrHata):
    pass


class YasakliIstek(Exception):
    pass


def _default_transport(url: str, timeout: float):
    import requests
    r = requests.get(url, timeout=timeout, headers={"Accept": "application/json", "User-Agent": "btr-arastirma/1.0"})
    return r.status_code, dict(r.headers), r.text


class Istemci:
    def __init__(self, transport: Callable | None = None, clock=time.time, min_gap=0.25, timeout=10.0):
        self.transport = transport or _default_transport
        self.clock = clock
        self.min_gap = min_gap
        self.timeout = timeout
        self.hosts = {"tr": list(TR_HOSTS), "api": list(API_HOSTS)}
        self.secili = {"tr": TR_HOSTS[0], "api": API_HOSTS[0]}
        self.calls = 0
        self.agirlik_dk: list[tuple[float, int]] = []
        self.rl_hits = 0
        self.last_error = ""
        self.last_ok_ts = 0.0
        self._last = 0.0
        self._ban_until = 0.0
        self._lock = asyncio.Lock()

    def agirlik_son_dk(self) -> int:
        now = self.clock()
        self.agirlik_dk = [(t, w) for t, w in self.agirlik_dk if now - t < 60]
        return sum(w for _, w in self.agirlik_dk)

    async def get(self, kind: str, path: str, query: dict | None = None):
        if (kind, path) not in IZINLI:
            raise YasakliIstek(f"Bu istek yapılamaz: {kind} {path}")
        last = None
        for host in [self.secili[kind]] + [h for h in self.hosts[kind] if h != self.secili[kind]]:
            try:
                out = await self._get(host, kind, path, query)
                self.secili[kind] = host
                return out
            except HizSiniri:
                raise
            except BtrHata as e:
                last = e
                if e.status not in (None, 403, 404, 451, "json"):
                    raise
        raise last

    @property
    def api_host(self) -> str:
        return self.secili["api"]

    async def _get(self, host, kind, path, query):
        async with self._lock:
            now = time.monotonic()
            if now < self._ban_until:
                raise HizSiniri(f"Binance hız sınırı: {int(self._ban_until - now)} sn bekleniyor", 429)
            wait = self._last + self.min_gap - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()
        url = host + path + ("?" + urlencode(query) if query else "")
        self.calls += 1
        self.agirlik_dk.append((self.clock(), IZINLI[(kind, path)]))
        try:
            status, headers, text = await asyncio.to_thread(self.transport, url, self.timeout)
        except Exception as e:  # ağ hatası
            self.last_error = f"Bağlantı hatası ({host}): {type(e).__name__}: {e}"[:300]
            raise BtrHata(self.last_error)
        if status in (418, 429):
            self.rl_hits += 1
            try:
                ra = float({k.lower(): v for k, v in headers.items()}.get("retry-after", 60))
            except ValueError:
                ra = 60.0
            self._ban_until = time.monotonic() + max(5.0, ra)
            self.last_error = f"Binance hız sınırı ({status}), {int(ra)} sn beklenecek"
            raise HizSiniri(self.last_error, status)
        if status != 200:
            self.last_error = f"HTTP {status} ({host}{path}): {text[:150]}"
            raise BtrHata(self.last_error, status)
        try:
            data = json.loads(text)
        except ValueError:
            self.last_error = f"JSON değil ({host}{path}): {text[:150]}"
            raise BtrHata(self.last_error, "json")
        if isinstance(data, dict) and "code" in data and data.get("code") not in (0, "0", 200):
            self.last_error = f"Binance TR hata kodu {data.get('code')}: {str(data.get('msg'))[:150]}"
            raise BtrHata(self.last_error, status)
        self.last_ok_ts = self.clock()
        return data

    # ------------------------------------------------------------------ uç noktalar
    async def sunucu_saati(self):
        return await self.get("tr", "/open/v1/common/time")

    async def semboller(self) -> list[dict]:
        d = await self.get("tr", "/open/v1/common/symbols")
        rows = _ic(d, "list")
        out = []
        for r in rows if isinstance(rows, list) else []:
            if not isinstance(r, dict):
                continue
            base, quote = str(r.get("baseAsset") or ""), str(r.get("quoteAsset") or "")
            sym = str(r.get("symbol") or (base + "_" + quote))
            if not base or not quote:
                if "_" in sym:
                    base, quote = sym.split("_", 1)
                else:
                    continue
            out.append({"symbol": sym, "bn": (base + quote).upper(), "base": base, "quote": quote,
                        "type": r.get("type"), "ham": r})
        return out

    async def ticker_24s(self) -> list[dict]:
        d = await self.get("api", "/api/v3/ticker/24hr")
        return d if isinstance(d, list) else []

    async def mumlar(self, s: dict, interval="1m", start_ms: int | None = None, limit=120) -> list:
        """type 1 sembol -> Binance API, diğerleri -> trbinance open/v1/market/klines."""
        if s.get("type") in (1, "1"):
            q = {"symbol": s["bn"], "interval": interval, "limit": limit}
            if start_ms:
                q["startTime"] = start_ms
            d = await self.get("api", "/api/v3/klines", q)
        else:
            q = {"symbol": s["symbol"], "interval": interval, "limit": limit}
            if start_ms:
                q["startTime"] = start_ms
            d = await self.get("tr", "/open/v1/market/klines", q)
        rows = _ic(d, "list")
        return rows if isinstance(rows, list) else []


def _ic(d, key):
    """Binance TR yanıtları {"code":0,"data":{...}} ile sarılıdır; içteki listeyi bulur."""
    if isinstance(d, dict) and "data" in d:
        d = d["data"]
    if isinstance(d, dict) and key in d:
        d = d[key]
    return d
