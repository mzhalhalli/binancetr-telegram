"""Manuel tarama 5. satırı "Benim şartlarım" (17.10.2026). Şartlar btr/benim_sartlarim.json dosyasından okunur (her taramada),
zaman dilimine göre ayrı liste ("15m", "1h", "4h"); listedeki şartların HEPSİ tutarsa coin listelenir. Yalnızca manuel tarama.

Şart türleri (JSON'da her şart bir sözlük, "tur" alanı zorunlu):
  {"tur": "buyuktur", "sol": A, "sag": B}        A > B. A ve B: kapanis, acilis, ema9, ema21, ema50, ema200, bb_orta, bb_ust, bb_alt
  {"tur": "macd", "kosul": K}                     K: "artida_buyuyor" (hist > 0 ve önceki mumdan büyük), "artida" (hist > 0),
                                                  "sinyal_ustu" (MACD > sinyal), "artiya_dondu" (hist > 0, önceki ≤ 0)
  {"tur": "rsi", "n": 14, "min": x, "max": y}     RSI(n) aralığı (n varsayılan 14; min / max isteğe bağlı)
  {"tur": "rsi_kesisim", "hizli": 6, "yavaslar": [12, 24], "geri": 3}   RSI(hızlı) her yavaş RSI'yı son "geri" mum içinde
                                                  aşağıdan yukarı kesmiş ve şimdi hepsinin üstünde (Binance RSI 6/12/24)
  {"tur": "kdj_kesisim", "n": 9, "geri": 3}       KDJ(n,3,3) J çizgisi K ve D'yi son "geri" mum içinde yukarı kesmiş ve şimdi
                                                  ikisinin de üstünde (Binance KDJ: RSV, K = 2/3 K + 1/3 RSV, D = 2/3 D + 1/3 K, J = 3K − 2D)
  {"tur": "dusuk_bb_alt_ustu"}                    mumun en düşüğü Bollinger(21,2) alt bandının altına inmemiş
  {"tur": "yuzde_b", "min": x, "max": y}          Bollinger(21, 2) %B
  {"tur": "bant_genisligi", "min": x, "max": y, "max_yuzdelik": z}   bant genişliği % ve son 100 mumdaki yüzdeliği
  {"tur": "hacim_kat", "min": x, "max": y}        son mumun TRY hacmi / önceki 20 mum ortalaması
  {"tur": "aralik36", "min": x, "max": y}         son 36 mumun en yüksek / en düşük - 1 (%)
  {"tur": "degisim24", "min": x, "max": y}        son kapanışın 24 saat önceki kapanışa göre değişimi (%)
  {"tur": "yesil"}                                son mum yeşil
  {"tur": "kirilim20", "mum": n}                  kapanış önceki n mumun (varsayılan 20) en yükseğini kırdı
  {"tur": "formasyon_yaklasan", "adlar": [...], "max_atr": x}   kırılıma yaklaşan formasyon (adlar: boga_bayragi, genisleyen,
                                                  dikdortgen, yukselen_ucgen, ikili_dip; boşsa hepsi); çizgiye en çok x ATR
  {"tur": "formasyon_kirilim", "adlar": [...]}    son mumda yukarı kırılım
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from . import indikator as ind

DOSYA = Path(__file__).parent / "benim_sartlarim.json"
ADIM_24S = {"15m": 96, "1h": 24, "4h": 6, "1d": 1, "1w": 1}
DEGERLER = ("kapanis", "acilis", "ema9", "ema21", "ema50", "ema200", "bb_orta", "bb_ust", "bb_alt")
TURLER = ("buyuktur", "macd", "rsi", "rsi_kesisim", "kdj_kesisim", "dusuk_bb_alt_ustu", "yuzde_b", "bant_genisligi", "hacim_kat", "aralik36", "degisim24", "yesil", "kirilim20",
          "formasyon_yaklasan", "formasyon_kirilim")


class SartHata(ValueError):
    pass


def yukle(aralik: str, dosya: Path | None = None) -> list[dict]:
    try:
        d = json.loads(Path(dosya or DOSYA).read_text("utf-8"))
    except (OSError, ValueError) as e:
        raise SartHata(f"benim_sartlarim.json okunamadı: {e}")
    sartlar = d.get(aralik) or []
    for s in sartlar:
        if not isinstance(s, dict) or s.get("tur") not in TURLER:
            raise SartHata(f"Bilinmeyen şart: {s}")
        if s["tur"] == "buyuktur" and (s.get("sol") not in DEGERLER or s.get("sag") not in DEGERLER):
            raise SartHata(f"buyuktur: sol/sag şunlardan biri olmalı: {', '.join(DEGERLER)}")
    return sartlar


def _aralikta(v, s) -> bool:
    return v is not None and (s.get("min") is None or v >= s["min"]) and (s.get("max") is None or v <= s["max"])


def kdj(h, l, c, n=9):
    """Binance KDJ(n,3,3): K ve D 50'den başlar."""
    K_, D_, J_ = [None] * len(c), [None] * len(c), [None] * len(c)
    k = d = 50.0
    for i in range(n - 1, len(c)):
        hh, ll = max(h[i - n + 1:i + 1]), min(l[i - n + 1:i + 1])
        rsv = (c[i] - ll) / (hh - ll) * 100 if hh > ll else 50.0
        k = 2 / 3 * k + rsv / 3
        d = 2 / 3 * d + k / 3
        K_[i], D_[i], J_[i] = k, d, 3 * k - 2 * d
    return K_, D_, J_


def _kesti(a, b, geri) -> bool:
    """a, b'yi son "geri" mum içinde aşağıdan yukarı kesti ve son mumda a > b."""
    i = len(a) - 1
    if a[i] is None or b[i] is None or not a[i] > b[i]:
        return False
    return any(a[j - 1] is not None and b[j - 1] is not None and a[j - 1] <= b[j - 1] and a[j] > b[j]
               for j in range(max(1, i - geri + 1), i + 1))


def _g(v, d=4):
    return "-" if v is None else f"{v:.{d}g}"


def degerlendir(sartlar: list[dict], acilis, yuksek, dusuk, kapanis, hacim_q, aralik: str, fm: dict | None) -> tuple[bool | None, str]:
    """(hepsi tuttu mu, şart değerleri metni). Veri yetmezse (None, neden)."""
    c, o, h, l = kapanis, acilis, yuksek, dusuk
    n = len(c)
    if n < 60:
        return None, "yetersiz mum"
    ema = {k: ind.ema(c, k)[-1] for k in (9, 21, 50, 200)}
    bb = ind.bollinger(c, 21, 2.0)
    deg = {"kapanis": c[-1], "acilis": o[-1], "ema9": ema[9], "ema21": ema[21], "ema50": ema[50], "ema200": ema[200],
           "bb_orta": bb[0] if bb else None, "bb_ust": bb[1] if bb else None, "bb_alt": bb[2] if bb else None}
    parca, hepsi = [], True
    for s in sartlar:
        t = s["tur"]
        if t == "buyuktur":
            a, b = deg[s["sol"]], deg[s["sag"]]
            ok, m = a is not None and b is not None and a > b, f"{s['sol']} {_g(a)} > {s['sag']} {_g(b)}"
        elif t == "macd":
            mm, ss = ind.macd(c)
            hst = mm[-1] - ss[-1] if ss[-1] is not None else None
            hst_o = mm[-2] - ss[-2] if ss[-2] is not None else None
            k = s.get("kosul", "artida_buyuyor")
            if hst is None or hst_o is None:
                ok = False
            elif k == "artida":
                ok = hst > 0
            elif k == "sinyal_ustu":
                ok = mm[-1] > ss[-1]
            elif k == "artiya_dondu":
                ok = hst > 0 >= hst_o
            else:
                ok = hst > 0 and hst > hst_o
            m = f"MACD hist {_g(hst)} ({k})"
        elif t == "rsi":
            rn = int(s.get("n", 14))
            r = ind.rsi(c, rn)[-1]
            ok, m = _aralikta(r, s), f"RSI{rn} {_g(r, 3)}"
        elif t == "rsi_kesisim":
            hz, ys, geri = int(s.get("hizli", 6)), [int(x) for x in s.get("yavaslar", [12, 24])], int(s.get("geri", 3))
            rh = ind.rsi(c, hz)
            ok = all(_kesti(rh, ind.rsi(c, y), geri) for y in ys)
            m = f"RSI{hz} {_g(rh[-1], 3)} " + " ".join(f"RSI{y} {_g(ind.rsi(c, y)[-1], 3)}" for y in ys) + f" (son {geri} mumda kesişim)"
        elif t == "kdj_kesisim":
            geri = int(s.get("geri", 3))
            K_, D_, J_ = kdj(h, l, c, int(s.get("n", 9)))
            ok = _kesti(J_, K_, geri) and _kesti(J_, D_, geri)
            m = f"KDJ J {_g(J_[-1], 3)} K {_g(K_[-1], 3)} D {_g(D_[-1], 3)} (son {geri} mumda kesişim)"
        elif t == "dusuk_bb_alt_ustu":
            ok = bb is not None and l[-1] >= bb[2]
            m = f"en düşük {_g(l[-1])} ≥ BB alt {_g(bb[2] if bb else None)}"
        elif t == "yuzde_b":
            v = (c[-1] - bb[2]) / (bb[1] - bb[2]) if bb and bb[1] > bb[2] else None
            ok, m = _aralikta(v, s), f"%B {_g(v, 3)}"
        elif t == "bant_genisligi":
            bw = (bb[1] - bb[2]) / bb[0] * 100 if bb else None
            gec = []
            for j in range(max(20, n - 101), n - 1):
                b2 = ind.bollinger(c[:j + 1], 21, 2.0)
                gec.append((b2[1] - b2[2]) / b2[0] * 100)
            yzd = sum(x < bw for x in gec) / len(gec) * 100 if gec and bw is not None else None
            ok = _aralikta(bw, s) and (s.get("max_yuzdelik") is None or (yzd is not None and yzd <= s["max_yuzdelik"]))
            m = f"bant %{_g(bw, 3)} (yüzdelik {_g(yzd, 3)})"
        elif t == "hacim_kat":
            ort = sum(hacim_q[-21:-1]) / 20 if len(hacim_q) >= 21 else 0
            v = hacim_q[-1] / ort if ort else None
            ok, m = _aralikta(v, s), f"hacim {_g(v, 3)}x"
        elif t == "aralik36":
            lo = min(l[-36:])
            v = (max(h[-36:]) / lo - 1) * 100 if lo > 0 else None
            ok, m = _aralikta(v, s), f"36 mum aralığı %{_g(v, 3)}"
        elif t == "degisim24":
            adim = ADIM_24S.get(aralik, 1)
            v = (c[-1] / c[-1 - adim] - 1) * 100 if n > adim else None
            ok, m = _aralikta(v, s), f"24s değişim %{_g(v, 3)}"
        elif t == "yesil":
            ok, m = c[-1] > o[-1], f"mum %{(c[-1] / o[-1] - 1) * 100:+.2f}" if o[-1] else "mum -"
        elif t == "kirilim20":
            k = int(s.get("mum", 20))
            v = max(h[-k - 1:-1])
            ok, m = c[-1] > v, f"{k} mum tepesi {_g(v)}"
        elif t == "formasyon_yaklasan":
            adlar = s.get("adlar") or None
            mx = s.get("max_atr")
            uy = [f for f in (fm or {}).get("yaklasan", []) if (not adlar or f["ad"] in adlar)
                  and (mx is None or (f.get("uzak_atr") is not None and f["uzak_atr"] <= mx))]
            ok = bool(uy)
            m = ("yaklaşan " + ", ".join(f"{f['baslik']} {f.get('uzak_atr', math.nan):.2f} ATR" for f in uy)) if uy else "yaklaşan formasyon yok"
        else:                                          # formasyon_kirilim
            adlar = s.get("adlar") or None
            kr = [f for f in (fm or {}).get("kirilim", []) if not adlar or f["ad"] in adlar]
            ok, m = bool(kr), ("kırılım " + ", ".join(f["baslik"] for f in kr)) if kr else "kırılım yok"
        hepsi = hepsi and ok
        parca.append(("✓ " if ok else "✗ ") + m)
    return hepsi, f"benim şartlarım {sum(p.startswith('✓') for p in parca)}/{len(parca)}: " + " · ".join(parca)
