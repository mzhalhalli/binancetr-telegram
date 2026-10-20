"""Grafik formasyonu kırılımları (yalnızca yukarı). Yalnızca mum verisiyle çalışır.

15.10.2026 geçmiş testinde (Binance TR TRY çiftleri; 1 saatlik 180 gün, 4 saatlik 730 gün) 19 formasyonun yukarı ve aşağı
kırılımları denendi. Hedefe zarar-kesten önce ulaşma oranı da işlem başı ortalama sonuç da aynı coinde rastgele girişten
iyi çıkan 6 yukarı kırılım burada kullanılır (GECMIS). Günlük ve haftalık mumlarda, mum formasyonlarında ve aşağı
kırılımlarda üstünlük bulunmadı; onlar kullanılmaz. 15 dakikalık mumlarda aynı 5 formasyon kullanıcı isteğiyle aranır
(16.10.2026) ama geçmiş testte hiçbiri rastgeleden iyi değildi: hepsi "uygun değil" (⚠) işaretlenir (KANITLI dışında).

Algılama analiz/formasyon.py ile aynıdır:
- Dönüş noktası (zigzag): fiyat son uç noktadan 3 x ATR(14) ters giderse uç nokta onaylanır.
- Formasyon son dönüş noktası onaylanınca bilinir. Kırılım = bundan sonraki ilk kapanışın kırılım çizgisinin üstünde olması
  (formasyon bilinmeden kırılmışsa onay mumunda hâlâ üstündeyse onay mumu sayılır).
- Hedef = kırılım seviyesi + formasyon yüksekliği (ölçülü hareket). Zarar-kes = formasyonun alt ucu.
- Kırılım ancak formasyon boyuna göre belli bir süre içinde (en çok 150 mum) aranır.
Erken uyarı (yaklasan): formasyon bilinmiş, henüz kırılmamış ve bozulmamış, kapanış kırılım çizgisine 1 ATR'den yakın.
Bu "yaklaşıyor" listesi geçmiş testte ölçülmedi; kırılımı beklemek için izleme listesidir.
"""
from __future__ import annotations

import math
import statistics

K_ATR = 3.0          # dönüş noktası eşiği (ATR katı)
TOL = 0.8            # "aynı seviye" toleransı (ATR katı)
ATR_N = 14
GEREKLI_MUM = 480    # zigzag'ın uzun geçmişle aynı dönüş noktalarına oturması için
YAKIN_ATR = 1.0      # erken uyarı: kapanış kırılım çizgisine bu kadar ATR'den yakın
PIYASA_MA = 50       # piyasa genişliği: kapanışı kendi 50 mumluk ortalamasının üstündeki coin oranı
GUCLU_PIYASA = 50.0  # genişlik bu yüzdenin üstündeyse piyasa güçlü

AD = {"boga_bayragi": "Boğa bayrağı", "genisleyen": "Genişleyen formasyon", "dikdortgen": "Dikdörtgen",
      "yukselen_ucgen": "Yükselen üçgen", "ikili_dip": "İkili dip"}
SECILI = {"15m": ("boga_bayragi", "genisleyen", "dikdortgen", "yukselen_ucgen", "ikili_dip"),
          "1h": ("boga_bayragi", "genisleyen"), "4h": ("genisleyen", "dikdortgen", "yukselen_ucgen", "ikili_dip")}
KANITLI = ("1h", "4h")   # geçmiş testte rastgeleden iyi çıkan zaman dilimleri

# Geçmiş test (yukarı kırılım): n, hedefe ulaşma %, aynı coinde rastgele giriş %, işlem başı ortalama % (rastgele),
# hedef/zarar-kes uzaklığı ortancası %. kosul: üstünlüğün görüldüğü piyasa durumu
#   "genislik" = coinlerin en az yarısı kendi MA50'sinin üstünde (zayıf piyasada rastgeleden iyi değildi)
#   "btc" = BTC_TRY kendi MA50'sinin üstünde (altındayken ortalama zarardaydı), None = iki durumda da iyiydi
#   "yok" = rastgeleden iyi değildi (15 dakikalık: 15.09-15.10.2026, işlem başı ortalama rastgeleden düşük, iki yarıda tutarsız)
GECMIS = {
    ("15m", "boga_bayragi"): {"n": 1086, "basari": 24.1, "rastgele": 19.1, "ort": 0.44, "r_ort": 0.40, "hedef": 5.36, "stop": 2.48, "kosul": "yok"},
    ("15m", "genisleyen"): {"n": 792, "basari": 44.1, "rastgele": 44.4, "ort": 0.44, "r_ort": 0.50, "hedef": 3.22, "stop": 5.0, "kosul": "yok"},
    ("15m", "dikdortgen"): {"n": 1184, "basari": 52.1, "rastgele": 50.4, "ort": 0.13, "r_ort": 0.22, "hedef": 1.74, "stop": 2.29, "kosul": "yok"},
    ("15m", "yukselen_ucgen"): {"n": 834, "basari": 40.0, "rastgele": 41.3, "ort": -0.10, "r_ort": 0.28, "hedef": 2.7, "stop": 2.31, "kosul": "yok"},
    ("15m", "ikili_dip"): {"n": 2036, "basari": 57.4, "rastgele": 56.2, "ort": 0.05, "r_ort": 0.16, "hedef": 1.56, "stop": 2.33, "kosul": "yok"},
    ("1h", "boga_bayragi"): {"n": 770, "basari": 34.0, "rastgele": 19.1, "ort": 1.59, "r_ort": 0.55, "hedef": 11.7, "stop": 5.76, "kosul": "btc"},
    ("1h", "genisleyen"): {"n": 784, "basari": 52.0, "rastgele": 41.6, "ort": 1.80, "r_ort": 0.77, "hedef": 7.27, "stop": 11.19, "kosul": "genislik"},
    ("4h", "genisleyen"): {"n": 383, "basari": 60.1, "rastgele": 45.7, "ort": 4.06, "r_ort": 0.55, "hedef": 15.87, "stop": 25.74, "kosul": "genislik"},
    ("4h", "dikdortgen"): {"n": 1003, "basari": 53.5, "rastgele": 46.1, "ort": 0.82, "r_ort": 0.09, "hedef": 10.32, "stop": 11.99, "kosul": "genislik"},
    ("4h", "yukselen_ucgen"): {"n": 764, "basari": 45.8, "rastgele": 39.3, "ort": 0.63, "r_ort": 0.02, "hedef": 14.23, "stop": 11.45, "kosul": "genislik"},
    ("4h", "ikili_dip"): {"n": 2068, "basari": 57.8, "rastgele": 53.3, "ort": 0.40, "r_ort": 0.06, "hedef": 9.15, "stop": 12.34, "kosul": None},
}


# ---------------------------------------------------------------------- yardımcılar
def atr(h: list[float], l: list[float], c: list[float], n: int = ATR_N) -> list[float]:
    """Wilder ATR; ilk n mum NaN."""
    out = [math.nan] * len(c)
    if len(c) <= n:
        return out
    tr = [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]
    a = sum(tr[:n]) / n
    out[n] = a
    for i in range(n, len(tr)):
        a = (a * (n - 1) + tr[i]) / n
        out[i + 1] = a
    return out


def zigzag(h: list[float], l: list[float], a: list[float], k: float = K_ATR) -> list[tuple]:
    """(idx, fiyat, 'H'/'L', onay_idx, onaydaki ATR) listesi, sırayla H ve L değişir."""
    n = len(h)
    piv: list[tuple] = []
    bas = next((i for i in range(n) if not math.isnan(a[i])), None)
    if bas is None:
        return piv
    yon = 0
    hi_i, hi_p, lo_i, lo_p = bas, h[bas], bas, l[bas]
    ex_i, ex_p = bas, h[bas]
    for i in range(bas + 1, n):
        x = a[i]
        if yon == 0:
            if h[i] > hi_p:
                hi_i, hi_p = i, h[i]
            if l[i] < lo_p:
                lo_i, lo_p = i, l[i]
            if h[i] - lo_p >= k * x and lo_i < i:
                piv.append((lo_i, lo_p, "L", i, x))
                yon, ex_i, ex_p = 1, i, h[i]
            elif hi_p - l[i] >= k * x and hi_i < i:
                piv.append((hi_i, hi_p, "H", i, x))
                yon, ex_i, ex_p = -1, i, l[i]
        elif yon == 1:
            if h[i] > ex_p:
                ex_i, ex_p = i, h[i]
            elif ex_p - l[i] >= k * x:
                piv.append((ex_i, ex_p, "H", i, x))
                yon, ex_i, ex_p = -1, i, l[i]
        else:
            if l[i] < ex_p:
                ex_i, ex_p = i, l[i]
            elif h[i] - ex_p >= k * x:
                piv.append((ex_i, ex_p, "L", i, x))
                yon, ex_i, ex_p = 1, i, h[i]
    return piv


def _cizgi(i1, p1, i2, p2):
    e = (p2 - p1) / (i2 - i1) if i2 != i1 else 0.0
    return lambda t: p1 + e * (t - i1)


def _yatay(i, p):
    return _cizgi(i, p, i + 1, p)


# ---------------------------------------------------------------------- formasyonlar (analiz/formasyon.py ile aynı)
def formasyonlar(P: list, j: int) -> list[dict]:
    """P[:j+1] onaylı dönüş noktaları; son nokta P[j]. Bu anda tamamlanmış formasyonlar (yalnızca yukarı kırılım alanları)."""
    out = []
    tol = TOL * P[j][4]
    son = P[j]

    def nok(m):
        return P[j - m + 1:j + 1] if j - m + 1 >= 0 else None

    q = nok(4)
    if q and son[2] == "L":
        H0, L1, H2, L3 = q
        if abs(L1[1] - L3[1]) <= tol and H0[1] > H2[1]:
            out.append({"ad": "ikili_dip", "ust": _yatay(H2[0], H2[1]), "gecersiz_alt": min(L1[1], L3[1]),
                        "yuk": H2[1] - min(L1[1], L3[1]), "stop": min(L1[1], L3[1]), "bas": H0[0]})
    q = nok(6)
    if q and son[2] == "L":
        H0, L1, H2, L3, H4, L5 = q
        dipler = [L1[1], L3[1], L5[1]]
        if max(dipler) - min(dipler) <= 1.5 * tol and H0[1] > max(H2[1], H4[1]):
            out = [x for x in out if x["ad"] != "ikili_dip"]      # üçlü dip ikili dibin yerini alır (kullanılmıyor)

    # dikdörtgen, genişleyen, yükselen üçgen: son 4 nokta (2 tepe, 2 dip)
    q = nok(4)
    if q:
        H = [x for x in q if x[2] == "H"]
        L = [x for x in q if x[2] == "L"]
        Ha, Hb = H
        La, Lb = L
        ust, alt = _cizgi(Ha[0], Ha[1], Hb[0], Hb[1]), _cizgi(La[0], La[1], Lb[0], Lb[1])
        du, dl = Hb[1] - Ha[1], Lb[1] - La[1]
        b0, b1 = q[0][0], q[-1][0]
        g0, g1 = ust(b0) - alt(b0), ust(b1) - alt(b1)
        if g0 > 0 and g1 > 0:
            yatay_u, yatay_l = abs(du) <= tol, abs(dl) <= tol
            ad = None
            if yatay_u and dl > tol:
                ad = "yukselen_ucgen"
            elif yatay_u and yatay_l:
                ad = "dikdortgen"
            elif du > tol and dl < -tol:
                ad = "genisleyen"
            if ad:
                out.append({"ad": ad, "ust": ust, "alt": alt, "yuk": max(Ha[1], Hb[1]) - min(La[1], Lb[1]),
                            "stop": Lb[1], "bas": b0})

    # boğa bayrağı / flama: güçlü direk, ardından direğin yarısından az geri çekilme
    q = nok(3)
    if q and j >= 8:
        bacaklar = [abs(P[m][1] - P[m - 1][1]) for m in range(max(1, j - 10), j)]
        ort_bacak = statistics.median(bacaklar) if bacaklar else 0
        A, B, C = q
        direk = abs(B[1] - A[1])
        if (A[2] == "L" and direk >= 2.0 * ort_bacak and abs(C[1] - B[1]) <= 0.5 * direk
                and (C[0] - B[0]) <= 1.5 * (B[0] - A[0]) + 5):
            out.append({"ad": "boga_bayragi", "ust": _yatay(B[0], B[1]), "gecersiz_alt": C[1], "yuk": direk,
                        "stop": C[1], "bas": A[0]})
    return out


def _pencere(P: list, j: int, f: dict) -> tuple[int, int]:
    span = max(5, P[j][0] - f["bas"])
    return int(min(max(10, span), 150)), int(min(max(10, 2 * span), 150))


def bul(acilis: list[float], yuksek: list[float], dusuk: list[float], kapanis: list[float], aralik: str) -> dict | None:
    """Son mum için: {"kirilim": [...], "yaklasan": [...]}. Bu aralıkta seçili formasyon yoksa ya da veri azsa None.
    Her öğe: ad, baslik, seviye, hedef, stop, hedef_pct, stop_pct (son kapanışa göre), uzaklik_pct (yalnızca yaklaşan)."""
    secili = SECILI.get(aralik)
    n = len(kapanis)
    if not secili or n < 60 or len(yuksek) < n or len(dusuk) < n:
        return None
    h, l, c = yuksek[-n:], dusuk[-n:], kapanis
    a = atr(h, l, c)
    P = zigzag(h, l, a)
    son = n - 1
    kir, yak, gorulen = {}, {}, set()
    for j in range(len(P)):
        t0 = P[j][3]
        if t0 + 150 < son:                 # bu dönüş noktasının formasyonları son muma ulaşamaz
            continue
        for f in formasyonlar(P, j):
            W, _ = _pencere(P, j, f)
            kirilim, bozuldu = None, False
            for t in range(P[j][0] + 1, min(n, t0 + W + 1)):
                if c[t] > f["ust"](t):
                    kirilim = t
                    break
                if f.get("alt") is not None and c[t] < f["alt"](t):
                    bozuldu = True                # aşağı kırıldı (yukarı kırılım aranmaz)
                    break
                if "gecersiz_alt" in f and c[t] < f["gecersiz_alt"]:
                    bozuldu = True
                    break
            if f["ad"] not in secili:
                if kirilim is not None:
                    gorulen.add((f["ad"], kirilim if kirilim >= t0 else t0))
                continue
            if kirilim is not None:
                t = kirilim
                if t < t0:                        # formasyon bilinmeden kırıldı: onay mumunda hâlâ üstünde mi?
                    if c[t0] <= f["ust"](t0):
                        continue
                    t = t0
                if (f["ad"], t) in gorulen:
                    continue
                gorulen.add((f["ad"], t))
                if t != son:
                    continue
                o = _ogeyi_yap(f, t, c[son])
                if o:
                    kir.setdefault(f["ad"], o)
            elif not bozuldu and t0 <= son <= t0 + W:
                seviye = f["ust"](son)
                if c[son] >= seviye - YAKIN_ATR * a[son]:
                    o = _ogeyi_yap(f, son, c[son])
                    if o:
                        o["uzaklik_pct"] = round((seviye / c[son] - 1) * 100, 2)
                        o["uzak_atr"] = round((seviye - c[son]) / a[son], 3) if a[son] else None
                        yak[f["ad"]] = o          # aynı formasyon tipinde en yeni dönüş noktası kalır
    return {"kirilim": list(kir.values()), "yaklasan": list(yak.values())}


# ---------------------------------------------------------------------- onaylı girişler (otomatik al-sat, 16.10.2026)
GIRISLER = {"retest": "retest sonrası", "y2": "2 yeşil mum onayı", "kirilim_trend": "yükselen trendde ilk kırılım",
            "retest_y2": "retest ardından 2 yeşil mum"}
RETEST_MUM = 10      # retest kırılımdan sonra en çok bu kadar mum içinde tamamlanmalı
RETEST_ATR = 0.25    # mumun dibi kırılım çizgisine bu kadar ATR yaklaşınca retest sayılır


def _ema(x: list[float], n: int) -> list[float]:
    k, out, e = 2 / (n + 1), [], None
    for v in x:
        e = v if e is None else e + k * (v - e)
        out.append(e)
    return out


def yukselen_trend(c: list[float]) -> bool:
    """Kapanış EMA50'nin üstünde, EMA20 > EMA50 ve EMA50 son 10 mumda yükselmiş."""
    if len(c) < 60:
        return False
    e20, e50 = _ema(c, 20), _ema(c, 50)
    return c[-1] > e50[-1] and e20[-1] > e50[-1] and e50[-1] > e50[-11]


def _giris_mumu(o, h, l, c, a, f: dict, t: int, son: int, giris: str) -> int | None:
    """t mumunda kırılan formasyon için giriş koşulunun tamamlandığı mum (yoksa None)."""
    if giris == "kirilim_trend":
        return t
    if giris == "y2":
        if t + 2 > son:
            return None
        for k in (t + 1, t + 2):
            if not (c[k] > o[k] and c[k] > f["ust"](k) and l[k] > f["stop"]):
                return None
        return t + 2
    if giris == "retest_y2":
        # 20.10.2026 (manuel/otomatik 4. satır): kırılımdan sonra en çok RETEST_MUM mum içinde retest (dip kırılım çizgisine
        # RETEST_ATR yaklaşır, kapanış çizginin altına inmez, dip zarar-kesin üstünde); retest mumundan sonraki ilk ardışık
        # 2 yeşil mumun ikincisi giriş mumu.
        d = None
        for k in range(t + 1, min(son, t + RETEST_MUM + 2) + 1):
            L = f["ust"](k)
            if c[k] <= L or l[k] <= f["stop"]:
                return None
            if d is None:
                if k <= t + RETEST_MUM and l[k] <= L + RETEST_ATR * a[k]:
                    d = k
                continue
            if k >= d + 2 and c[k] > o[k] and c[k - 1] > o[k - 1]:
                return k
        return None
    deg = False                                   # retest
    for k in range(t + 1, min(son, t + RETEST_MUM) + 1):
        L = f["ust"](k)
        if c[k] <= L or l[k] <= f["stop"]:        # çizginin altına kapandı / zarar-kes: kırılım bozuldu
            return None
        if not deg and l[k] <= L + RETEST_ATR * a[k]:
            deg = True
        if deg and c[k] > o[k]:
            return k
    return None


def onay(acilis: list[float], yuksek: list[float], dusuk: list[float], kapanis: list[float], aralik: str, giris: str,
         adlar: list[str]) -> list[dict] | None:
    """Son (kapanmış) mumda giriş koşulu tamamlanan yukarı kırılımlar; yalnızca `adlar` formasyonları. Veri azsa None.
    - retest: kırılımdan sonra en çok 10 mum içinde bir mumun dibi kırılım çizgisine 0,25 ATR yaklaşır, hiçbir kapanış
      çizginin altına inmez; ardından gelen ilk yeşil mum son mumsa giriş.
    - y2: kırılım iki mum önce; sonraki 2 mum yeşil, kapanışları çizginin üstünde, dipleri zarar-kesin üstünde.
    - kirilim_trend: son mum ilk yukarı kırılım mumu ve yükselen trend (yukselen_trend).
    Kırılım algılaması bul() ile aynıdır. Öğeler _ogeyi_yap gibi (hedef / zarar-kes formasyonun), artı giris ve giris_ad."""
    n = len(kapanis)
    if giris not in GIRISLER or n < 60 or len(yuksek) < n or len(dusuk) < n or len(acilis) < n:
        return None
    o, h, l, c = acilis[-n:], yuksek[-n:], dusuk[-n:], kapanis
    if giris == "kirilim_trend" and not yukselen_trend(c):
        return []
    a = atr(h, l, c)
    P = zigzag(h, l, a)
    son = n - 1
    out, gorulen = {}, set()
    for j in range(len(P)):
        t0 = P[j][3]
        if t0 + 150 + RETEST_MUM < son:
            continue
        for f in formasyonlar(P, j):
            W, _ = _pencere(P, j, f)
            kir = None
            for t in range(P[j][0] + 1, min(n, t0 + W + 1)):
                if c[t] > f["ust"](t):
                    kir = t
                    break
                if f.get("alt") is not None and c[t] < f["alt"](t):
                    break
                if "gecersiz_alt" in f and c[t] < f["gecersiz_alt"]:
                    break
            if kir is None:
                continue
            t = kir
            if t < t0:                            # formasyon bilinmeden kırıldı: onay mumunda hâlâ üstünde mi?
                if c[t0] <= f["ust"](t0):
                    continue
                t = t0
            if (f["ad"], t) in gorulen:
                continue
            gorulen.add((f["ad"], t))
            if f["ad"] not in adlar or _giris_mumu(o, h, l, c, a, f, t, son, giris) != son:
                continue
            x = _ogeyi_yap(f, t, c[son])
            if x:
                x.update(giris=giris, giris_ad=GIRISLER[giris], kirilim_mum_once=son - t)
                out.setdefault(f["ad"], x)
    return list(out.values())


def yuvarla(x: float, basamak: int = 4) -> float:
    """Gösterim için: borsadaki fiyatlar gibi 4 anlamlı basamak (1000 üstünde tam sayı)."""
    if x <= 0:
        return x
    return round(x, max(basamak - 1 - math.floor(math.log10(x)), 0))


def _ogeyi_yap(f: dict, t: int, fiyat: float) -> dict | None:
    seviye = f["ust"](t)
    hedef, stop = seviye + f["yuk"], f["stop"]
    if hedef <= fiyat or stop >= fiyat:
        return None
    hp, sp = (hedef / fiyat - 1) * 100, (1 - stop / fiyat) * 100
    if sp > 40 or hp > 300:
        return None
    return {"ad": f["ad"], "baslik": AD[f["ad"]], "seviye": yuvarla(seviye), "hedef": yuvarla(hedef), "stop": yuvarla(stop),
            "hedef_pct": round(hp, 2), "stop_pct": round(sp, 2)}


# ---------------------------------------------------------------------- piyasa durumu ve metinler
def ma_ustu(kapanis: list[float]) -> bool | None:
    """Son kapanış kendi PIYASA_MA ortalamasının üstünde mi (veri azsa None)."""
    if len(kapanis) < PIYASA_MA:
        return None
    return kapanis[-1] > sum(kapanis[-PIYASA_MA:]) / PIYASA_MA


def uygunluk(aralik: str, ad: str, genislik_pct: float | None, btc_ust: bool | None) -> tuple[bool | None, str]:
    """Geçmiş testte üstünlüğün görüldüğü piyasa durumu şu an var mı. (uygun mu, kısa açıklama)"""
    g = GECMIS.get((aralik, ad))
    if g and g["kosul"] == "yok":
        return False, "15 dakikalıkta geçmiş testte rastgeleden iyi değildi"
    if not g or g["kosul"] is None:
        return True, "piyasa durumundan bağımsız"
    if g["kosul"] == "genislik":
        if genislik_pct is None:
            return None, "piyasa genişliği bilinmiyor"
        if genislik_pct >= GUCLU_PIYASA:
            return True, f"piyasa güçlü (MA50 üstündeki coin oranı %{genislik_pct:.0f})"
        return False, f"piyasa zayıf (MA50 üstündeki coin oranı %{genislik_pct:.0f}): geçmişte bu durumda rastgeleden iyi değildi"
    if btc_ust is None:
        return None, "BTC_TRY durumu bilinmiyor"
    return (True, "BTC_TRY MA50 üstünde") if btc_ust else (False, "BTC_TRY MA50 altında: geçmişte bu durumda ortalama zarardaydı")


def _yuzde(x: float) -> str:
    return f"%{x:.1f}".replace(".", ",")


def gecmis_metni(aralik: str, ad: str) -> str:
    g = GECMIS[(aralik, ad)]
    return (f"geçmiş test ({g['n']} kırılım): hedefe ulaşma {_yuzde(g['basari'])} (rastgele {_yuzde(g['rastgele'])}), "
            f"işlem başı ort. {_isaretli(g['ort'])} (rastgele {_isaretli(g['r_ort'])})")


def _isaretli(x: float) -> str:
    return ("+" if x >= 0 else "-") + _yuzde(abs(x))


def etiket(aralik: str) -> str:
    return {"15m": "15dk", "1h": "1s", "4h": "4s"}.get(aralik, aralik)
