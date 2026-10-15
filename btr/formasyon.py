"""Grafik formasyonu kırılımları (yalnızca yukarı). Yalnızca mum verisiyle çalışır.

15.10.2026 geçmiş testinde (Binance TR TRY çiftleri; 1 saatlik 180 gün, 4 saatlik 730 gün) 19 formasyonun yukarı ve aşağı
kırılımları denendi. Hedefe zarar-kesten önce ulaşma oranı da işlem başı ortalama sonuç da aynı coinde rastgele girişten
iyi çıkan 6 yukarı kırılım burada kullanılır (GECMIS). 15 dakikalık, günlük ve haftalık mumlarda, mum formasyonlarında ve
aşağı kırılımlarda üstünlük bulunmadı; onlar kullanılmaz.

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
SECILI = {"1h": ("boga_bayragi", "genisleyen"), "4h": ("genisleyen", "dikdortgen", "yukselen_ucgen", "ikili_dip")}

# Geçmiş test (yukarı kırılım): n, hedefe ulaşma %, aynı coinde rastgele giriş %, işlem başı ortalama % (rastgele),
# hedef/zarar-kes uzaklığı ortancası %. kosul: üstünlüğün görüldüğü piyasa durumu
#   "genislik" = coinlerin en az yarısı kendi MA50'sinin üstünde (zayıf piyasada rastgeleden iyi değildi)
#   "btc" = BTC_TRY kendi MA50'sinin üstünde (altındayken ortalama zarardaydı), None = iki durumda da iyiydi
GECMIS = {
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
                        yak[f["ad"]] = o          # aynı formasyon tipinde en yeni dönüş noktası kalır
    return {"kirilim": list(kir.values()), "yaklasan": list(yak.values())}


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
            f"işlem başı ort. +{_yuzde(g['ort'])} (rastgele +{_yuzde(g['r_ort'])})")


def etiket(aralik: str) -> str:
    return {"1h": "1s", "4h": "4s"}.get(aralik, aralik)
