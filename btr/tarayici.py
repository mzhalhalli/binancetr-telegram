"""İndikatör tarayıcısı: seçilen zaman dilimindeki mumlara göre koşulları sağlayan coinleri listeler. Yalnızca okur."""
from __future__ import annotations

import asyncio
import contextlib
import copy
import time

from . import indikator as ind
from . import erken as er
from . import formasyon as fo
from . import uygunluk as uy
from . import yaklasan_kural as yk
from . import benim_sartlar as bs
from .istemci import BtrHata, HizSiniri, Istemci

ARALIKLAR = ["15m", "1h", "4h", "1d", "1w"]
ARALIK_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000, "1w": 604_800_000}
# acik = henüz kapanmamış mum (anlık); kapaliN = sondan geriye N. kapanmış mum
MUMLAR = {"acik": 0, "kapali1": 1, "kapali2": 2, "kapali3": 3}
ARALIK_AD_KISA = {"15m": "15 dakika", "1h": "1 saat", "4h": "4 saat", "1d": "günlük", "1w": "haftalık"}

# Sonuç listesinden çıkarılanlar (16.10.2026, kullanıcı isteği): stabil coinler ve son kapanışı 200 TL'nin üstündekiler
# (200 dahil kalır). Panel taramaları, otomatik al-sat ve Telegram taramaları aynı listeyi kullanır. Piyasa genişliği ve
# BTC_TRY MA50 hesabı eskisi gibi tüm coinlerle yapılır (formasyon uygunluğu değişmesin).
STABIL = {"USDT", "USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "USDE", "USD1", "PYUSD", "EUR", "EURI", "AEUR",
          "EURC", "USDS", "RLUSD", "BFUSD", "USDD", "GUSD", "FRAX", "LUSD", "XUSD", "TRYB", "U"}
ALTIN = {"PAXG", "XAUT"}
# 20.10.2026 kullanıcı isteği: bütün taramalar 309 coinden stabil + altın tokenleri (7 coin) çıkınca kalan 302 coinle yapılır;
# bu coinler hiç taranmaz ("taranan" sayısına da girmez).
TARAMA_DISI = STABIL | ALTIN
# 20.10.2026 kullanıcı isteği (taramayı hızlandırmak için): fiyatı 5 USD'den pahalı ya da 0,0001 USD'den ucuz coinler hiç
# taranmaz. Fiyat son ticker'dan, USD karşılığı USDT_TRY kuruyla bulunur; fiyat ya da kur bilinmiyorsa coin taranır.
# Bu kural 16.10.2026'daki "200 TL üstü listelenmez" kuralının yerini aldı.
FIYAT_UST_USD = 5.0
FIYAT_ALT_USD = 0.0001


def usd_fiyat(s: dict, ticker: dict) -> float | None:
    """Coinin son fiyatı USD (USDT) cinsinden; bilinmiyorsa None."""
    f = (ticker.get(s.get("symbol")) or {}).get("c")
    q = str(s.get("quote", "")).upper()
    if not f:
        return None
    if q in ("USDT", "USDC", "FDUSD"):
        return float(f)
    usdt = (ticker.get("USDT_TRY") or {}).get("c")
    kur = 1.0 if q == "TRY" else (ticker.get(f"{q}_TRY") or {}).get("c")
    return float(f) * float(kur) / float(usdt) if usdt and kur else None


def fiyat_disi(s: dict, ticker: dict) -> bool:
    u = usd_fiyat(s, ticker)
    return u is not None and (u > FIYAT_UST_USD or u < FIYAT_ALT_USD)


# 20.10.2026 kullanıcı isteği (taramayı hızlandırmak için): yalnızca 24 saatlik TL hacmi en yüksek EN_LIKIT (100) coin
# taranır (HACIM_ALT_TL > 0 yapılırsa o tutarın altındakiler de elenir; şu an kapalı). Hacim son ticker'dan (TRY dışı karşı para TL'ye çevrilir).
# Ticker'da hiçbir coinin hacmi yoksa (bot yeni açıldı) bu süzgeç uygulanmaz.
HACIM_ALT_TL = 0.0
EN_LIKIT = 100


def tl_hacim(s: dict, ticker: dict) -> float | None:
    q = (ticker.get(s.get("symbol")) or {}).get("q")
    if q is None:
        return None
    k = str(s.get("quote", "")).upper()
    kur = 1.0 if k == "TRY" else (ticker.get(f"{k}_TRY") or {}).get("c")
    return float(q) * float(kur) if kur else None


def likit_sec(sem: list[dict], ticker: dict) -> list[dict]:
    h = [(tl_hacim(s, ticker), s) for s in sem]
    if not any(v is not None for v, _ in h):
        return sem
    h = [(v, s) for v, s in h if v is not None and v >= HACIM_ALT_TL]
    h.sort(key=lambda x: -x[0])
    return [s for _, s in h[:EN_LIKIT]]


def gizle_nedeni(s: dict, fiyat: float, ticker: dict) -> str | None:
    """'stabil' ya da None (stabiller zaten taranmaz; 200 TL kuralı 20.10.2026'da 5 USD / 0,0001 USD kuralıyla değişti)."""
    if str(s.get("base", "")).upper() in STABIL:
        return "stabil"
    return None

# kosul tanımları: arayüz bu listeden form üretir
KOSULLAR = {
    "bb": {"ad": "Bollinger", "param": {"n": 21, "k": 2.0, "son": 3},
           "kosul": {"dip_3_kirmizi": "Alt banttan orta banda değdi, ardından kırmızı mumla alt banda temas",
                     "son_yesil_orta_ustu": "Son N mum ardışık yeşil ve orta bandın üstünde kapattı",
                     "alt_temas": "Alt banda temas", "orta_temas": "Orta banda temas", "ust_temas": "Üst banda temas",
                     "orta_ustu": "Kapanış orta bandın üstünde", "orta_alti": "Kapanış orta bandın altında",
                     "orta_ust_arasi": "Kapanış orta ile üst bant arasında", "alt_orta_arasi": "Kapanış alt ile orta bant arasında",
                     "ust_ustu": "Kapanış üst bandın üstünde", "alt_alti": "Kapanış alt bandın altında",
                     "orta_yukari_kesti": "Kapanış orta bandı yukarı kesti", "orta_asagi_kesti": "Kapanış orta bandı aşağı kesti",
                     "son_orta_ustu": "Son N mumun hepsi orta bandın üstünde kapattı",
                     "son_orta_alti": "Son N mumun hepsi orta bandın altında kapattı"}},
    "mum": {"ad": "Mum rengi", "param": {"son": 3},
            "kosul": {"yesil": "Seçilen mum yeşil (pozitif) kapattı", "kirmizi": "Seçilen mum kırmızı (negatif) kapattı",
                      "son_yesil": "Son N mumun hepsi yeşil kapattı", "son_kirmizi": "Son N mumun hepsi kırmızı kapattı"}},
    "rsi": {"ad": "RSI", "param": {"n": 14, "deger": 30.0, "son": 3},
            "kosul": {"kesisim_6_12_24": "RSI6, RSI12 ve RSI24'ü yukarı kesti (son N mumda)",
                      "alti": "RSI değerin altında", "ustu": "RSI değerin üstünde",
                      "retest_2_yesil": "RSI6, RSI12 ve RSI24'ü yukarı kesti → RSI12'ye retest → orta bant üstünde 2 yeşil mum",
                      "kural1_sonra_kural2": "1. kural (alt bant → orta bant → kırmızı mumla alt bant) ardından 2. kural (RSI retest + 2 yeşil)"}},
    "ema": {"ad": "EMA", "param": {"n": 50, "hizli": 9, "yavas": 21, "tol": 1.0, "son": 1},
            "kosul": {"fiyat_ustu": "Fiyat EMA(n) üstünde", "fiyat_alti": "Fiyat EMA(n) altında",
                      "yakin": "Fiyat EMA(n)'ye yakın (±tol %)", "kesisim_yukari": "Hızlı EMA yavaşı yukarı kesti (son mumlarda)",
                      "kesisim_asagi": "Hızlı EMA yavaşı aşağı kesti (son mumlarda)"}},
    "macd": {"ad": "MACD (12,26,9)", "param": {"son": 1},
             "kosul": {"kesisim_yukari": "AL · MACD sinyali yukarı kesti (son N mumda)",
                       "kesisim_asagi": "SAT · MACD sinyali aşağı kesti (son N mumda)",
                       "sinyal_ustu": "MACD sinyalin üstünde", "sinyal_alti": "MACD sinyalin altında"}},
    "kdj": {"ad": "KDJ (9,3,3)", "param": {"n": 9, "son": 3},
            "kosul": {"kesisim": "J, K ve D'yi yukarı kesti (son N mumda)"}},
    "hacim": {"ad": "Hacim", "param": {"n": 20, "kat": 2.0},
              "kosul": {"artis": "Yüksek hacim girişi var (son mum hacmi, önceki n mum ortalamasının kat katı)"}},
    "benim": {"ad": "Benim şartlarım (benim_sartlarim.json)", "param": {},
              "kosul": {"tam": "Dosyadaki o zaman dilimi şartlarının hepsi (manuel tarama 4. satır)"}},
    "erken": {"ad": "Erken uyarı", "param": {},
              "kosul": {"tam": "8 şartın hepsi tutuyor (ERKEN UYARI)",
                       "yeni": "36 mum aralığı ≥%15 + EMA9>EMA21 + MACD artıda ve büyüyor + yeşil mum"}},
    "formasyon": {"ad": "Formasyon kırılımı (15 dakika, 1 saat, 4 saat)", "param": {},
                  "kosul": {"kirilim": "Son mumda yukarı kırılım (1s/4s geçmişte rastgeleden iyi; 15dk üstünlük yok)",
                            "yaklasan": "Kırılım çizgisine yaklaşıyor (erken uyarı, 1 ATR'den yakın)",
                            "yaklasan9": "Kırılıma yaklaşıyor + dikdörtgen, ≤0,5 ATR, EMA50/EMA200, EMA9>21, MACD, RSI 50-70, yeşil",
                            "boga_retest_y2": "Boğa bayrağı kırılımı → retest → 2 yeşil mum (manuel tarama 3. satır)"}},
}


# 20.10.2026 kullanıcı isteği: paneldeki "İndikatör / Koşul" menüsünde yalnızca bunlar görünür (diğer koşullar hazır
# tarama düğmeleri ve eski ayarlar için tanımlı kalır). MENU_PARAM: o koşulda gösterilen parametreler.
MENU = {"bb": ["alt_temas", "orta_temas", "ust_temas"], "macd": ["kesisim_yukari", "kesisim_asagi"],
        "rsi": ["kesisim_6_12_24"], "kdj": ["kesisim"], "hacim": ["artis"]}
MENU_PARAM = {"bb|alt_temas": ["n", "k"], "bb|orta_temas": ["n", "k"], "bb|ust_temas": ["n", "k"],
              "macd|kesisim_yukari": ["son"], "macd|kesisim_asagi": ["son"], "rsi|kesisim_6_12_24": ["son"],
              "kdj|kesisim": ["n", "son"], "hacim|artis": ["n", "kat"]}

DIP_GERI = 8    # dip_3_kirmizi: orta banda değen mumdan önce alt banda temasın aranacağı en çok mum
ORTA_GERI = 5   # dip_3_kirmizi: orta banda değen mumdan son (kırmızı, alt banda değen) muma en çok mum
ORTA_EN_AZ = 2  # ... en az mum
ORTA_ONCELIK = 3   # tam 3 mumda inenler sonuç listesinde (ve otomatik alımda) önce gelir
ONCELIK_ETIKET = "★ ÖNCELİKLİ (3 mum)"
RETEST_GERI = 6     # retest_2_yesil: RSI12 retestinden ilk yeşil muma en çok mum
KESISIM_GERI = 8    # retest_2_yesil: RSI6 kesişiminden retest mumuna en çok mum
KURAL1_GERI = 12    # kural1_sonra_kural2: 1. kuralın 3. kırmızı mumundan 2. kuralın ilk yeşil mumuna en çok mum


class KosulHata(ValueError):
    pass


def _f(p: dict, k: str, d):
    try:
        v = float(p.get(k, d))
    except (TypeError, ValueError):
        raise KosulHata(f"{k} sayı olmalı")
    if v != v or v < 0:
        raise KosulHata(f"{k} geçersiz")
    return v


def dogrula(kosullar: list) -> list[dict]:
    if not isinstance(kosullar, list) or not kosullar:
        raise KosulHata("En az bir koşul seçin")
    if len(kosullar) > 6:
        raise KosulHata("En çok 6 koşul")
    out = []
    for c in kosullar:
        if isinstance(c, dict) and c.get("ind") == "formasyon_onay":     # otomatik al-sat girişleri (araştırma arayüzünde yok)
            adlar = c.get("adlar")
            if c.get("giris") not in fo.GIRISLER or not isinstance(adlar, list) or not adlar or any(a not in fo.AD for a in adlar):
                raise KosulHata("Formasyon girişi: geçersiz seçim")
            out.append({"ind": "formasyon_onay", "kosul": "onay", "giris": c["giris"], "adlar": list(adlar)})
            continue
        if not isinstance(c, dict) or c.get("ind") not in KOSULLAR:
            raise KosulHata("Bilinmeyen indikatör")
        t = KOSULLAR[c["ind"]]
        if c.get("kosul") not in t["kosul"]:
            raise KosulHata(f"{t['ad']}: bilinmeyen koşul")
        p = {k: _f(c, k, d) for k, d in t["param"].items()}
        for k in ("n", "hizli", "yavas", "son"):
            if k in p:
                p[k] = int(p[k])
                if not 1 <= p[k] <= 300:
                    raise KosulHata(f"{k}: 1-300 arası olmalı")
        if c["ind"] == "ema" and c["kosul"].startswith("kesisim") and p["hizli"] >= p["yavas"]:
            raise KosulHata("Hızlı EMA, yavaştan küçük olmalı")
        out.append({"ind": c["ind"], "kosul": c["kosul"], **p})
    return out


def gerekli_mum(kosullar: list[dict], aralik: str | None = None) -> int:
    m = fo.GEREKLI_MUM if aralik in fo.SECILI else 30     # 15 dk, 1 ve 4 saatte formasyon etiketi her taramada hesaplanır
    for c in kosullar:
        if c["ind"] == "erken":
            m = max(m, er.GEREKLI_MUM)
        elif c["ind"] == "ema":
            m = max(m, 3 * max(c["n"], c["yavas"]) + c["son"] + 2)
        elif c["ind"] == "macd":
            m = max(m, 26 * 3 + 9 + c["son"] + 2)
        elif c["ind"] == "rsi" and c["kosul"] in ("retest_2_yesil", "kural1_sonra_kural2", "kesisim_6_12_24"):
            m = max(m, 10 * 24 + RETEST_GERI + KESISIM_GERI + 4)
        elif c["ind"] == "rsi":
            m = max(m, 10 * c["n"] + 2)
        elif c["ind"] == "bb" and c["kosul"] == "dip_3_kirmizi":
            m = max(m, c["n"] + ORTA_GERI + DIP_GERI + 2)
        else:
            m = max(m, c.get("n", 20) + c.get("son", 1) + 2)
    return min(1000, max(m, er.GEREKLI_MUM))  # erken uyarı etiketi her taramada hesaplanır


def formasyon_metni(fm: dict, kosul: str, aralik: str) -> str:
    """Formasyon koşulunun 'İndikatör değerleri' sütunundaki açıklaması."""
    parca = []
    for f in fm.get(kosul) or []:
        ne = "yukarı kırıldı" if kosul == "kirilim" else f"kırılıma %{f['uzaklik_pct']:.1f} kaldı"
        m = (f"{fo.etiket(aralik)} {f['baslik']} {ne}: seviye {f['seviye']:.6g}, hedef {f['hedef']:.6g} (+{f['hedef_pct']:.1f}%), "
             f"zarar-kes {f['stop']:.6g} (-{f['stop_pct']:.1f}%) · {fo.gecmis_metni(aralik, f['ad'])}")
        if f.get("uygun_not"):
            m += f" · {'✓' if f['uygun'] else '⚠' if f['uygun'] is False else '?'} {f['uygun_not']}"
        parca.append(m)
    return " | ".join(parca)


def _try_hacim(r) -> float:
    try:
        return float(r[7])
    except (IndexError, TypeError, ValueError):
        return float(r[5]) * float(r[4])


def _kesisim(a: list, b: list, son: int, yukari: bool) -> bool:
    n = len(a)
    for i in range(max(1, n - son), n):
        if None in (a[i], b[i], a[i - 1], b[i - 1]):
            continue
        if yukari and a[i - 1] <= b[i - 1] and a[i] > b[i]:
            return True
        if not yukari and a[i - 1] >= b[i - 1] and a[i] < b[i]:
            return True
    return False


# 20.10.2026 (tarama hızı): aynı coinin aynı mum serisi için RSI ve Bollinger bir kez hesaplanır, modeller tekrar kullanır.
# Anahtar seri kimliği + uzunluk + uç değerler; her tarama başında temizlenir (_tara).
_HESAP: dict = {}


def _seri_anahtar(x: list) -> tuple:
    return (id(x), len(x), x[0] if x else None, x[-1] if x else None, x[len(x) // 2] if x else None)


def _rsi(kapanis: list[float], n: int) -> list:
    k = ("rsi", n) + _seri_anahtar(kapanis)
    if k not in _HESAP:
        _HESAP[k] = ind.rsi(kapanis, n)
    return _HESAP[k]


def _bb(kapanis: list[float], j: int, n: int, kat: float):
    """kapanis[:j+1] üzerinde Bollinger (orta, üst, alt)."""
    k = ("bb", j, n, kat) + _seri_anahtar(kapanis)
    if k not in _HESAP:
        _HESAP[k] = ind.bollinger(kapanis[:j + 1], n, kat)
    return _HESAP[k]


# 20.10.2026 (tarama hızı): formasyon taraması (fo.bul) turun en pahalı hesabıydı ve her coinde her modelde yeniden
# yapılıyordu. Artık yalnızca gerektiğinde (formasyon şartı, formasyon içeren benim şartlarım ya da eşleşen coinin
# sonucu) ve aynı mum serisi için bir kez hesaplanır; aynı zaman dilimindeki öteki modeller kopyasını kullanır.
# Anahtar: sembol + zaman dilimi + seri uzunluğu + ilk mumun açılışı + son mumun açılış/yüksek/düşük/kapanışı.
_FORMASYON: dict = {}
_FORMASYON_EN_COK = 3000


def _formasyon(sym: str, aralik: str, rows: list, acl, yuk, dus, kap):
    if aralik not in fo.SECILI:
        return None
    k = (sym, aralik, len(rows), rows[0][0], rows[-1][0], rows[-1][2], rows[-1][3], rows[-1][4])
    if k not in _FORMASYON:
        if len(_FORMASYON) >= _FORMASYON_EN_COK:
            _FORMASYON.clear()
        _FORMASYON[k] = fo.bul(acl, yuk, dus, kap, aralik)
    return copy.deepcopy(_FORMASYON[k])       # sonuçta formasyonlara alan eklenir; saklanan kopya değişmesin


def degerlendir(c: dict, kapanis: list[float], hacim: list[float], yuksek: list[float] | None = None,
                dusuk: list[float] | None = None, acilis: list[float] | None = None,
                hacim_q: list[float] | None = None) -> tuple[bool | None, str]:
    """(sonuç, açıklama). Veri yetmezse (None, neden). Bantlar seçilen mumun kapanışı dahil hesaplanır."""
    f = kapanis[-1]
    i = c["ind"]
    k = c["kosul"]
    if i == "erken":
        if acilis is None:
            return None, "açılış verisi yok"
        if k == "yeni":                       # yalnızca manuel tarama 3. satırı (17.10.2026)
            if yuksek is None or dusuk is None:
                return None, "yüksek/düşük verisi yok"
            r = er.hesapla_yeni(kapanis, yuksek, dusuk, acilis)
        else:
            r = er.hesapla(kapanis, hacim_q or hacim, acilis)
        if r is None:
            return None, "yetersiz mum"
        return r["ok"], f"erken uyarı {r['puan']}/{r['toplam']}: {r['detay']}"
    if i == "mum":
        if acilis is None:
            return None, "açılış verisi yok"
        n = 1 if k in ("yesil", "kirmizi") else c["son"]
        if len(kapanis) < n:
            return None, "yetersiz mum"
        degisim = [(kapanis[j] / acilis[j] - 1) * 100 if acilis[j] else 0 for j in range(-n, 0)]
        ok = all(d > 0 for d in degisim) if k in ("yesil", "son_yesil") else all(d < 0 for d in degisim)
        return ok, "mum değişimi (eskiden yeniye): " + " / ".join(f"{d:+.2f}%" for d in degisim)
    if i == "bb" and k == "son_yesil_orta_ustu":
        if acilis is None:
            return None, "açılış verisi yok"
        n = c["son"]
        if len(kapanis) < c["n"] + n - 1:
            return None, "yetersiz mum"
        parca = []
        ok = True
        for j in range(n - 1, -1, -1):
            seri = kapanis[:len(kapanis) - j]
            o, kp = acilis[len(kapanis) - 1 - j], seri[-1]
            m = ind.bollinger(seri, c["n"], c["k"])[0]
            ok = ok and kp > o and kp > m
            parca.append(f"{'yeşil' if kp > o else 'kırmızı'} {(kp / o - 1) * 100:+.2f}%, ortaya göre {(kp / m - 1) * 100:+.1f}%")
        return ok, f"son {n} mum (eskiden yeniye): " + " | ".join(parca)
    if i == "bb" and k == "dip_3_kirmizi":
        # 20.10.2026 manuel tarama 1. satır: seçilen mum kırmızı ve alt banda değdi. Ondan önce (en çok ORTA_GERI mum içinde)
        # orta banda değen bir mum, onun da öncesinde (en çok DIP_GERI mum içinde) alt banda temas. Kırmızı mum sayısı
        # aranmaz (kullanıcı isteği: "3 mum olmasına gerek yok"); koşul adı eski ayarlar bozulmasın diye aynı kaldı.
        if acilis is None or yuksek is None or dusuk is None:
            return None, "açılış/yüksek/düşük verisi yok"
        L = len(kapanis)
        if L < c["n"] + ORTA_GERI + DIP_GERI + 1:
            return None, "yetersiz mum"
        bant = lambda j: _bb(kapanis, j, c["n"], c["k"])                   # (orta, üst, alt) j. mumda
        s = L - 1
        b = bant(s)
        if not kapanis[s] < acilis[s]:
            return False, "son mum kırmızı değil"
        if dusuk[s] > b[2]:
            return False, f"son mum alt banda değmedi (düşük {dusuk[s]:.6g}, alt bant {b[2]:.6g})"
        # kullanıcı: önemli olan alt bant -> orta bant -> alt bant; orta banttan alt banda 2-5 mumda olabilir, 3 mum öncelikli
        # orta banda değen mum: alt banda da değen tek büyük mum (ör. orta banttan alt banda inen) sayılmaz, sıra bozulmasın
        M = next((j for j in range(s - 1, s - 1 - ORTA_GERI, -1) if yuksek[j] >= bant(j)[0] and dusuk[j] > bant(j)[2]), None)
        if M is None:
            return False, f"son {ORTA_GERI} mumda orta banda değen mum yok"
        if s - M < ORTA_EN_AZ:
            return False, f"orta banda değen mumdan alt banda {s - M} mumda inildi (en az {ORTA_EN_AZ})"
        A = next((j for j in range(M - 1, M - 1 - DIP_GERI, -1) if dusuk[j] <= bant(j)[2]), None)
        if A is None:
            return False, f"orta banda değmeden önceki {DIP_GERI} mumda alt banda temas yok"
        return True, (f"{ONCELIK_ETIKET + ' · ' if s - M == ORTA_ONCELIK else ''}"
                      f"orta banttan alt banda {s - M} mumda · alt banda temas {s - A} mum önce · son mum kırmızı "
                      f"{(kapanis[s] / acilis[s] - 1) * 100:+.2f}% ve alt banda değdi · BB alt {b[2]:.6g} · orta {b[0]:.6g}")
    if i == "bb" and k in ("son_orta_ustu", "son_orta_alti"):
        n = c["son"]
        if len(kapanis) < c["n"] + n - 1:
            return None, "yetersiz mum"
        konum = []
        for j in range(n - 1, -1, -1):
            seri = kapanis[:len(kapanis) - j]
            konum.append((seri[-1] / ind.bollinger(seri, c["n"], c["k"])[0] - 1) * 100)
        ok = all(x > 0 for x in konum) if k == "son_orta_ustu" else all(x < 0 for x in konum)
        return ok, f"son {n} mum kapanışı ortaya göre (eskiden yeniye): " + " / ".join(f"{x:+.1f}%" for x in konum)
    if i == "bb":
        b = ind.bollinger(kapanis, c["n"], c["k"])
        if not b:
            return None, "yetersiz mum"
        m, u, a = b
        h = yuksek[-1] if yuksek else f
        l = dusuk[-1] if dusuk else f
        acik = (f"BB alt {a:.6g} · orta {m:.6g} · üst {u:.6g} | mum düşük {l:.6g} · yüksek {h:.6g} · kapanış {f:.6g} "
                f"(ortaya göre {(f / m - 1) * 100:+.1f}%)")
        # temas: mumun fitili ya da gövdesi bandı gördü
        if k in ("orta_yukari_kesti", "orta_asagi_kesti"):
            onceki = ind.bollinger(kapanis[:-1], c["n"], c["k"])
            if not onceki:
                return None, "yetersiz mum"
            p, pm = kapanis[-2], onceki[0]
            ok = (p <= pm and f > m) if k == "orta_yukari_kesti" else (p >= pm and f < m)
            return ok, acik
        ok = {"alt_temas": l <= a, "orta_temas": l <= m <= h, "ust_temas": h >= u,
              "orta_ustu": f > m, "orta_alti": f < m, "orta_ust_arasi": m < f <= u, "alt_orta_arasi": a <= f < m,
              "ust_ustu": f > u, "alt_alti": f < a}[k]
        return ok, acik
    if i == "rsi" and k == "kesisim_6_12_24":
        r6, r12, r24 = _rsi(kapanis, 6), _rsi(kapanis, 12), _rsi(kapanis, 24)
        if r24[-1] is None:
            return None, "yetersiz mum"
        ok = bs._kesti(r6, r12, c["son"]) and bs._kesti(r6, r24, c["son"])
        return ok, f"RSI6 {r6[-1]:.1f} · RSI12 {r12[-1]:.1f} · RSI24 {r24[-1]:.1f} (son {c['son']} mumda kesişim)"
    if i == "kdj":
        if yuksek is None or dusuk is None:
            return None, "yüksek/düşük verisi yok"
        K_, D_, J_ = bs.kdj(yuksek, dusuk, kapanis, c["n"])
        if J_[-1] is None:
            return None, "yetersiz mum"
        ok = bs._kesti(J_, K_, c["son"]) and bs._kesti(J_, D_, c["son"])
        return ok, f"KDJ J {J_[-1]:.1f} · K {K_[-1]:.1f} · D {D_[-1]:.1f} (son {c['son']} mumda kesişim)"
    if i == "rsi" and k == "kural1_sonra_kural2":
        # 20.10.2026 manuel tarama 3. satır: son kapanan mumda 2. kural gerçekleşti ve ondan önce (ilk yeşil mumdan en çok
        # KURAL1_GERI mum önce biten) 1. kural gerçekleşmişti.
        if acilis is None or yuksek is None or dusuk is None:
            return None, "açılış/yüksek/düşük verisi yok"
        ok2, acik2 = degerlendir({"ind": "rsi", "kosul": "retest_2_yesil"}, kapanis, hacim, yuksek, dusuk, acilis)
        if not ok2:
            return ok2, f"2. kural yok: {acik2}"
        g1 = len(kapanis) - 2
        k1 = {"ind": "bb", "kosul": "dip_3_kirmizi", "n": 21, "k": 2}
        for j in range(g1 - 1, max(g1 - 1 - KURAL1_GERI, 0), -1):
            ok1, acik1 = degerlendir(k1, kapanis[:j + 1], hacim[:j + 1], yuksek[:j + 1], dusuk[:j + 1], acilis[:j + 1])
            if ok1:
                return True, f"1. kural {len(kapanis) - 1 - j} mum önce ({acik1}) · 2. kural şimdi ({acik2})"
        return False, f"2. kural var ama son {KURAL1_GERI} mumda 1. kural yok"
    if i == "rsi" and k == "retest_2_yesil":
        # 20.10.2026 manuel tarama 2. satır: seçilen mum 2. yeşil. Sırayla: RSI6, RSI12 ve RSI24'ü yukarı keser (C) ->
        # RSI6 geri dönüp RSI12'ye değer ya da altına iner (retest, T) -> RSI6 yeniden RSI12 üstünde -> orta bandın (21,2)
        # üstünde kapanan ardışık 2 yeşil mum; bu, retestten sonraki ilk böyle 2 mumdur (eski sinyal tekrar verilmez).
        if acilis is None:
            return None, "açılış verisi yok"
        L = len(kapanis)
        r6, r12, r24 = _rsi(kapanis, 6), _rsi(kapanis, 12), _rsi(kapanis, 24)
        g1, g2 = L - 2, L - 1
        if L < 21 + RETEST_GERI + KESISIM_GERI + 3 or None in (r24[g1 - RETEST_GERI - KESISIM_GERI - 1],):
            return None, "yetersiz mum"
        orta = lambda j: _bb(kapanis, j, 21, 2)[0]
        uygun = lambda j: kapanis[j] > acilis[j] and kapanis[j] > orta(j)
        if not (uygun(g1) and uygun(g2)):
            return False, "son 2 mum orta bant üstünde yeşil değil"
        if not r6[g1] > r12[g1]:
            return False, f"ilk yeşilde RSI6 {r6[g1]:.1f} RSI12 {r12[g1]:.1f} altında"
        T = next((t for t in range(g1 - 1, g1 - 1 - RETEST_GERI, -1) if r6[t] <= r12[t]), None)
        if T is None:
            return False, "son mumlarda RSI12 retesti yok"
        if any(uygun(j) and uygun(j + 1) for j in range(T + 1, g1)):
            return False, "retestten sonraki 2 yeşil mum daha önce geldi"
        C = next((x for x in range(T - 1, T - 1 - KESISIM_GERI, -1)
                  if None not in (r24[x - 1], r12[x - 1]) and r6[x - 1] <= max(r12[x - 1], r24[x - 1]) and r6[x] > max(r12[x], r24[x])), None)
        if C is None:
            return False, "retestten önce RSI6'nın RSI12 ve RSI24'ü yukarı kesişi yok"
        return True, (f"RSI6 kesişim {L - 1 - C} mum önce · retest {L - 1 - T} mum önce (RSI6 {r6[T]:.1f} / RSI12 {r12[T]:.1f}) · "
                      f"şimdi RSI6 {r6[g2]:.1f} RSI12 {r12[g2]:.1f} RSI24 {r24[g2]:.1f} · 2 yeşil: "
                      + " / ".join(f"{(kapanis[j] / acilis[j] - 1) * 100:+.2f}%, ortaya göre {(kapanis[j] / orta(j) - 1) * 100:+.1f}%" for j in (g1, g2)))
    if i == "rsi":
        r = ind.rsi(kapanis, c["n"])[-1]
        if r is None:
            return None, "yetersiz mum"
        return (r < c["deger"] if k == "alti" else r > c["deger"]), f"RSI({c['n']}) {r:.1f}"
    if i == "ema":
        if k.startswith("kesisim"):
            h, y = ind.ema(kapanis, c["hizli"]), ind.ema(kapanis, c["yavas"])
            if y[-1] is None:
                return None, "yetersiz mum"
            return _kesisim(h, y, c["son"], k == "kesisim_yukari"), f"EMA{c['hizli']} {h[-1]:.6g} / EMA{c['yavas']} {y[-1]:.6g}"
        e = ind.ema(kapanis, c["n"])[-1]
        if e is None:
            return None, "yetersiz mum"
        uz = (f / e - 1) * 100
        ok = {"fiyat_ustu": f > e, "fiyat_alti": f < e, "yakin": abs(uz) <= c["tol"]}[k]
        return ok, f"EMA{c['n']} {e:.6g} ({uz:+.2f}%)"
    if i == "macd":
        m, s = ind.macd(kapanis)
        if s[-1] is None:
            return None, "yetersiz mum"
        acik = f"MACD {m[-1]:.4g} / sinyal {s[-1]:.4g}"
        if k.startswith("kesisim"):
            return _kesisim(m, s, c["son"], k == "kesisim_yukari"), acik
        return (m[-1] > s[-1] if k == "sinyal_ustu" else m[-1] < s[-1]), acik
    if i == "hacim":
        if len(hacim) < c["n"] + 1:
            return None, "yetersiz mum"
        ort = sum(hacim[-c["n"] - 1:-1]) / c["n"]
        kat = hacim[-1] / ort if ort > 0 else 0
        return kat >= c["kat"], f"hacim {kat:.1f}x ortalama"
    return None, "?"


class MumBellegi:
    """20.10.2026 (tarama hızı): coin + zaman dilimi başına mum geçmişi. İlk istekte bir kez indirilir; sonra yalnızca son
    mumdan sonrası istenir (ya da mum_akisi.MumAkisi canlı günceller). Tarayıcılar arasında paylaşılır."""
    EN_COK = 1500

    def __init__(self):
        self.d: dict[tuple, dict] = {}

    def get(self, key):
        return self.d.get(key)

    def koy(self, key, rows, ts, bas=None):
        """bas: indirmenin istendiği başlangıç (ms). İlk mum bundan sonra açılmış olabilir (coin yeni listelendi)."""
        self.d[key] = {"rows": rows[-self.EN_COK:], "ts": ts, "bas": bas if bas is not None else (int(float(rows[0][0])) if rows else 0)}

    def birlestir(self, key, yeni, ts) -> bool:
        """Yeni mumları sona ekler / aynı açılış zamanlıyı yeniler. Arada boşluk kalırsa False (tam indirme gerekir)."""
        b = self.d[key]
        rows = b["rows"]
        for r in yeni:
            if not isinstance(r, (list, tuple)) or len(r) < 7:
                continue
            t0 = int(float(r[0]))
            son = int(float(rows[-1][0]))
            if t0 == son:
                rows[-1] = r
            elif t0 > son:
                rows.append(r)
        if len(rows) > self.EN_COK:
            b["bas"] = int(float(rows[-self.EN_COK][0]))
        b["rows"], b["ts"] = rows[-self.EN_COK:], ts
        return True

    def akistan(self, key, satir, iv) -> bool:
        """Canlı akıştan gelen mum: geçmiş varsa ve ardışıksa uygular."""
        b = self.d.get(key)
        if not b or not b["rows"]:
            return False
        son = int(float(b["rows"][-1][0]))
        if satir[0] == son:
            b["rows"][-1] = satir
        elif satir[0] == son + iv:
            b["rows"].append(satir)
            if len(b["rows"]) > self.EN_COK:
                del b["rows"][:-self.EN_COK]
                b["bas"] = int(float(b["rows"][0][0]))
        else:
            return False
        return True


class Tarayici:
    def __init__(self, client: Istemci, semboller, ticker, clock=time.time, paralel=8, ana_ag=None, yon_uyari=None, arz=None, derinlik=None,
                 bellek: MumBellegi | None = None, akis=None):
        """semboller(): {symbol: {...}}; ticker(): {symbol: {q: 24s hacim, ...}}; ana_ag: AnaAg (ana_ag.py) ya da None
        (None: ana ağ şartı uygulanmaz, testler için)"""
        self.client, self.semboller, self.ticker, self.clock = client, semboller, ticker, clock
        self.ana_ag = ana_ag
        # 20.10.2026: yon_uyari(s, aralik) -> metin ya da None: ana ağın yönü (trend kutusu) ya da güncel haber olumlu
        # değilse sonuç satırına uyarı yazılır. Coin elenmez, alım engellenmez.
        self.yon_uyari = yon_uyari
        self.derinlik = derinlik   # derinlik.Derinlik: sonuçlara emir defteri ve vadeli fonlama bilgisi (20.10.2026)
        self.arz = arz        # arz.Arz: dolaşımdaki arzı en çok arzın yarısından az coinler taranmaz (20.10.2026)
        self._benim = None
        self.paralel = paralel
        self.anlik_onbellek_sn = 20     # anlık (açık mum) modunda mumların yeniden indirilmeden kullanılacağı süre
        self.bellek = bellek or MumBellegi()      # mum geçmişi (paylaşılabilir)
        self.akis = akis                          # mum_akisi.MumAkisi: canlı güncelleme (None = yalnızca REST)
        self.calisiyor = False
        self.ilerleme = {"bitti": 0, "toplam": 0}
        self._lock = asyncio.Lock()

    async def _mumlar(self, s: dict, aralik: str, limit: int, kapali: bool) -> list:
        """Son `limit` mumu (başlangıç: şimdi - (limit+1) mum) eskiden yeniye döndürür — her zaman aynı pencere, böylece
        sonuçlar indirme yönteminden bağımsızdır. 20.10.2026: geçmiş bellekte tutulur; canlı akış tazeyse hiç istek
        atılmaz, değilse yalnızca son mumdan sonrası istenir; geçmiş yetmezse ya da boşluk varsa bir kez tam indirilir."""
        key = (s["symbol"], aralik)
        now = self.clock()
        iv = ARALIK_MS[aralik]
        start = int(now * 1000) - (limit + 1) * iv
        b = self.bellek.get(key)
        if b and b["rows"] and min(b.get("bas", 0), int(float(b["rows"][0][0]))) <= start:
            rows = b["rows"]
            taze = ((self.akis is not None and self.akis.taze(key, b)) or now - b["ts"] < self.anlik_onbellek_sn
                    or (kapali and now * 1000 < float(rows[-1][6])))
            if not taze:
                yeni = await self.client.mumlar(s, aralik, start_ms=int(float(rows[-1][0])), limit=1000)
                if len(yeni) >= 1000:                 # çok uzun ara: tam indir
                    b = None
                else:
                    self.bellek.birlestir(key, yeni, now)
        else:
            b = None
        if b is None:
            rows = await self.client.mumlar(s, aralik, start_ms=start, limit=limit + 2)
            tekil = {}
            for r in rows:
                if isinstance(r, (list, tuple)) and len(r) >= 7:
                    tekil[int(float(r[0]))] = r
            self.bellek.koy(key, [tekil[k] for k in sorted(tekil)], now, start)
        return [r for r in self.bellek.get(key)["rows"] if int(float(r[0])) >= start]

    async def tara(self, aralik: str, kosullar: list, mum="kapali1", quotes=None, min_hacim=0.0, ana_ag_sart=True) -> dict:
        if aralik not in ARALIKLAR:
            raise KosulHata("Geçersiz zaman dilimi")
        mum = "kapali1" if mum == "kapali" else mum
        if mum not in MUMLAR:
            raise KosulHata("Geçersiz mum seçimi")
        kosullar = dogrula(kosullar)
        # 20.10.2026: 1. satır koşulunda (manuel ve otomatik) ana ağ şartı uygulanmaz
        if any(c["ind"] == "bb" and c["kosul"] == "dip_3_kirmizi" for c in kosullar):
            ana_ag_sart = False
        if any(c["ind"] in ("formasyon", "formasyon_onay") for c in kosullar) and aralik not in fo.SECILI:
            raise KosulHata("Formasyon kırılımı yalnızca 15 dakika, 1 saat ve 4 saat için (günlük ve haftalıkta geçmiş testte üstünlük yok)")
        benim = None
        if any(c["ind"] == "benim" for c in kosullar):      # 5. satır: şartlar dosyadan, her taramada okunur
            try:
                benim = bs.yukle(aralik)
            except bs.SartHata as e:
                raise KosulHata(str(e))
            if not benim:
                raise KosulHata(f"Şartlar henüz girilmedi ({ARALIK_AD_KISA.get(aralik, aralik)} için benim_sartlarim.json boş)")
        if self._lock.locked():
            raise KosulHata("Bir tarama zaten sürüyor, bitmesini bekleyin")
        async with self._lock:
            self._benim = benim
            return await self._tara(aralik, kosullar, MUMLAR[mum], quotes, float(min_hacim or 0), ana_ag_sart)

    def evren(self, tick: dict | None = None, quotes=None, min_hacim: float = 0.0) -> list[dict]:
        """Taranacak coinler (her taramada): stabil/altın hariç, 5 USD üstü ve 0,0001 USD altı hariç, arzı yarıdan az olan
        hariç, kalanlardan 24 saatlik TL hacmi en yüksek EN_LIKIT coin. Altcoin piyasa yönü (piyasa_rejim) de bunu kullanır."""
        tick = self.ticker() if tick is None else tick
        sem = [s for s in self.semboller().values()
               if str(s.get("base", "")).upper() not in TARAMA_DISI and not fiyat_disi(s, tick)
               and not (self.arz and self.arz.disi(s.get("base", "")))
               and (not quotes or s["quote"].upper() in {q.upper() for q in quotes})
               and (min_hacim <= 0 or tick.get(s["symbol"], {}).get("q", 0) >= min_hacim)]
        return likit_sec(sem, tick)

    async def _tara(self, aralik, kosullar, geri, quotes, min_hacim, ana_ag_sart=True):
        _HESAP.clear()
        kapali = geri > 0
        iv = ARALIK_MS[aralik]
        t0 = time.monotonic()
        tick = self.ticker()
        sem = self.evren(tick, quotes, min_hacim)
        if self.akis is not None:
            self.akis.iste(sem)                   # canlı akış bu coinlere abone olur
        limit = gerekli_mum(kosullar, aralik) + geri + 1
        self.ilerleme = {"bitti": 0, "toplam": len(sem)}
        sonuc, yetersiz, hatalar = [], 0, []
        eski = 0
        gate = asyncio.Semaphore(self.paralel)
        now_ms = self.clock() * 1000
        benim_fm = any(str(x.get("tur", "")).startswith("formasyon") for x in self._benim or [])

        rl: list[str] = []
        ma_ustu: list[bool] = []                       # piyasa genişliği: kapanışı kendi MA50'sinin üstündeki coinler
        btc: list[bool] = []
        gizlenen = {"stabil": 0, "200tl": 0, "ana_ag": 0}

        async def bir(s):
            nonlocal yetersiz, eski
            async with gate:
                if rl:
                    self.ilerleme["bitti"] += 1
                    return
                try:
                    rows = await self._mumlar(s, aralik, limit, kapali)
                except HizSiniri as e:
                    rl.append(str(e))
                    return
                except BtrHata as e:
                    hatalar.append(f"{s['symbol']}: {e}")
                    return
                finally:
                    self.ilerleme["bitti"] += 1
            if kapali:
                rows = [r for r in rows if float(r[6]) < now_ms]
                rows = rows[:len(rows) - (geri - 1)] if geri > 1 else rows
            if rows and float(rows[-1][0]) < now_ms - (geri + 2) * iv:
                eski += 1                     # coin işlemden kalkmış ya da veri güncel değil
                return
            if len(rows) < 3:
                yetersiz += 1
                return
            kap = [float(r[4]) for r in rows]
            hac = [float(r[5]) for r in rows]
            yuk = [float(r[2]) for r in rows]
            acl = [float(r[1]) for r in rows]
            dus = [float(r[3]) for r in rows]
            # TRY (karşı para) hacmi; yoksa adet × kapanış
            hq = [_try_hacim(r) for r in rows]
            m50 = fo.ma_ustu(kap)
            if m50 is not None:
                ma_ustu.append(m50)
                if s["symbol"] == "BTC_TRY":
                    btc.append(m50)
            fm_: list = []

            def fm_al():                      # formasyonlar yalnızca gerekince (bkz. _formasyon)
                if not fm_:
                    fm_.append(_formasyon(s["symbol"], aralik, rows, acl, yuk, dus, kap))
                return fm_[0]
            aciklama, onaylar, yak10 = [], None, None
            for c in kosullar:
                if c["ind"] == "formasyon_onay" or (c["ind"] == "formasyon" and c["kosul"] == "boga_retest_y2"):
                    giris, adlar = (c["giris"], c["adlar"]) if c["ind"] == "formasyon_onay" else ("retest_y2", ["boga_bayragi"])
                    onaylar = fo.onay(acl, yuk, dus, kap, aralik, giris, adlar)
                    if onaylar is None:
                        yetersiz += 1
                        return
                    if not onaylar:
                        return
                    aciklama.append(" | ".join(
                        f"{fo.etiket(aralik)} {f['baslik']} · {f['giris_ad']}: seviye {f['seviye']:.6g}, hedef {f['hedef']:.6g} "
                        f"(+{f['hedef_pct']:.1f}%), zarar-kes {f['stop']:.6g} (-{f['stop_pct']:.1f}%)" for f in onaylar))
                    continue
                if c["ind"] == "benim":
                    ok, acik = bs.degerlendir(self._benim or [], acl, yuk, dus, kap, hq, aralik, fm_al() if benim_fm else None)
                    if ok is None:
                        yetersiz += 1
                        return
                    if not ok:
                        return
                    aciklama.append(acik)
                    continue
                if c["ind"] == "formasyon":
                    fm = fm_al()
                    if fm is None:
                        yetersiz += 1
                        return
                    if c["kosul"] == "yaklasan9":       # 17.10.2026: yaklaşan + 9 şartın hepsi (yaklasan_kural.py)
                        yak10 = yk.sec(fm["yaklasan"], yk.ozellikler(acl, yuk, dus, kap))
                        if not yak10:
                            return
                    elif not fm[c["kosul"]]:
                        return
                    aciklama.append("")               # metin, piyasa durumu belli olunca yazılır
                    continue
                ok, acik = degerlendir(c, kap, hac, yuk, dus, acl, hq)
                if ok is None:
                    yetersiz += 1
                    return
                if not ok:
                    return
                aciklama.append(acik)
            neden = gizle_nedeni(s, kap[-1], tick)
            if neden:                         # koşullar tutsa da listelenmez (genişliğe yukarıda sayıldı)
                gizlenen[neden] += 1
                return
            if self.ana_ag and ana_ag_sart:   # ana ağın coini bu zaman diliminde en az ZAYIF AL olmalı
                gecti, ana_not = self.ana_ag.kontrol(s, aralik)
                if not gecti:
                    gizlenen["ana_ag"] += 1
                    return
                aciklama.append(ana_not)
            t = tick.get(s["symbol"], {})
            fm = fm_al()
            erk = er.hesapla_yeni(kap, yuk, dus, acl)     # ERKEN UYARI etiketi (17.10.2026: yeni 4 şart)
            yu = None
            if self.yon_uyari:
                with contextlib.suppress(Exception):
                    yu = self.yon_uyari(s, aralik)
            atr_pct = None                       # 20.10.2026: ATR(14) / kapanış (%) — otomatik alımda zarar kes mesafesi için
            with contextlib.suppress(Exception):
                a_ = fo.atr(yuk, dus, kap)[-1]
                if a_ == a_ and kap[-1]:
                    atr_pct = round(a_ / kap[-1] * 100, 3)
            sonuc.append({"atr_pct": atr_pct, "yon_uyari": yu, "erken": erk["puan"] if erk else None, "erken_ok": bool(erk and erk["ok"]), "erken_detay": erk["detay"] if erk else None, "s": s["symbol"], "fiyat": kap[-1], "mum_ts": int(float(rows[-1][0])), "mum_bitis": int(float(rows[-1][6])), "aciklama": aciklama,
                          "formasyon": fm if fm and (fm["kirilim"] or fm["yaklasan"]) else None, "onay": onaylar,
                          "P": t.get("P"), "q": t.get("q"),
                          "uygunluk": uy.coin(acl, kap, hq), "birlesim": yak10})

        await asyncio.gather(*(bir(s) for s in sem))
        # geçmiş testteki gibi: en az 50 coin varsa genişlik; formasyonların o piyasa durumundaki geçmiş sonucu
        g = round(sum(ma_ustu) / len(ma_ustu) * 100, 1) if len(ma_ustu) >= 50 else None
        piyasa = {"genislik_pct": g, "coin": len(ma_ustu), "btc_ust": btc[0] if btc else None}
        for x in sonuc:
            if aralik in fo.SECILI:
                for f in (x["formasyon"] or {}).get("kirilim", []) + (x["formasyon"] or {}).get("yaklasan", []):
                    f["uygun"], f["uygun_not"] = fo.uygunluk(aralik, f["ad"], g, piyasa["btc_ust"])
                    f["gecmis"] = fo.gecmis_metni(aralik, f["ad"])
                for i, c in enumerate(kosullar):
                    if c["ind"] == "formasyon" and c["kosul"] != "boga_retest_y2":
                        if c["kosul"] == "yaklasan9":
                            x["aciklama"][i] = f"{x['birlesim']} | " + formasyon_metni(x["formasyon"], "yaklasan", aralik)
                        else:
                            x["aciklama"][i] = formasyon_metni(x["formasyon"], c["kosul"], aralik)
            k = x["uygunluk"] + uy.piyasa(g, piyasa["btc_ust"])
            if aralik in fo.KANITLI:                  # 15 dakikada formasyonun geçmişte üstünlüğü yok: yüzdeye katılmaz
                k.append(uy.formasyon(x["formasyon"]))
            x["uygunluk"] = uy.ozet(k)
        for x in sonuc:     # 1. kural: orta banttan alt banda tam 3 mumda inenler öncelikli (20.10.2026)
            x["oncelik"] = any(ONCELIK_ETIKET in str(a) for a in x["aciklama"])
        sonuc.sort(key=lambda r: (not r["oncelik"], -(r.get("q") or 0)))
        if self.derinlik and sonuc:
            with contextlib.suppress(Exception):
                await self.derinlik.zenginlestir(sonuc, self.semboller())
        mum_ad = next(k for k, v in MUMLAR.items() if v == geri)
        return {"aralik": aralik, "mum": mum_ad, "taranan": len(sem), "eslesen": len(sonuc),
                "yetersiz": yetersiz, "eski": eski, "hata": len(hatalar), "hata_ornek": hatalar[:3], "hiz_siniri": rl[0] if rl else None,
                "sure": round(time.monotonic() - t0, 1), "ts": self.clock(), "piyasa": piyasa, "gizlenen": gizlenen,
                "sonuc": sonuc}


async def kontrol(client: Istemci, s: dict, aralik: str, n=21, k=2.0, adet=12) -> dict:
    """Tek sembol için iki kaynaktan (Binance API ve Binance TR) ham mumları ve her mumdaki Bollinger değerlerini döndürür."""
    if aralik not in ARALIKLAR:
        raise KosulHata("Geçersiz zaman dilimi")
    limit = n + adet + 2
    now_ms = int(time.time() * 1000)
    start = now_ms - (limit + 1) * ARALIK_MS[aralik]
    kaynaklar = {}
    for ad, kind, path, sym in (("binance_api", "api", "/api/v3/klines", s["bn"]),
                                ("binance_tr", "tr", "/open/v1/market/klines", s["symbol"])):
        q = {"symbol": sym, "interval": aralik, "startTime": start, "limit": limit + 2}
        try:
            d = await client.get(kind, path, q)
        except Exception as e:  # her iki kaynağı da göstermek için hatayı yaz, devam et
            kaynaklar[ad] = {"hata": f"{type(e).__name__}: {e}", "sunucu": client.secili.get(kind)}
            continue
        ham = d.get("data") if isinstance(d, dict) else d
        if isinstance(ham, dict):
            ham = ham.get("list", ham)
        tekil = {}
        for r in ham if isinstance(ham, list) else []:
            if isinstance(r, (list, tuple)) and len(r) >= 7:
                tekil[int(float(r[0]))] = r
        rows = [tekil[t] for t in sorted(tekil)]
        kap = [float(r[4]) for r in rows]
        out = []
        for i, r in enumerate(rows):
            b = ind.bollinger(kap[:i + 1], n, k)
            out.append({"acilis_ts": int(float(r[0])), "kapanis_ts": int(float(r[6])), "acik": float(r[6]) >= now_ms,
                        "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4]),
                        "bb_alt": b[2] if b else None, "bb_orta": b[0] if b else None, "bb_ust": b[1] if b else None})
        kaynaklar[ad] = {"sunucu": client.secili.get(kind), "mum_sayisi": len(rows), "mumlar": out[-adet:],
                         "ham_ornek": str(ham)[:300] if not rows else None}
    return {"sembol": s["symbol"], "aralik": aralik, "n": n, "k": k, "ts": time.time(), "kaynaklar": kaynaklar}
