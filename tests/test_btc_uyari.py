import json
import math
import os
import tempfile
import time
import unittest
from unittest import mock

import btc_uyari as bu


def mumlar(n, f, p, son_ms):
    """Kapanmış yapay mumlar (Binance kline biçimi), son mum son_ms'den önce kapanır."""
    bas = (son_ms // p - n) * p
    out = []
    for k in range(n):
        t = bas + k * p
        c = f(k)
        out.append([t, str(c), str(c * 1.002), str(c * 0.998), str(c), "1", t + p - 1])
    return out


class SeviyeTests(unittest.TestCase):
    def test_sinirlar(self):
        beklenen = {0: "ÇOK ZAYIF", 39: "ÇOK ZAYIF", 40: "ZAYIF", 59: "ZAYIF", 60: "GÜÇLÜ", 99: "GÜÇLÜ", 100: "ÇOK GÜÇLÜ"}
        for g, k in beklenen.items():
            self.assertEqual(bu.seviye("alis", g), k + " AL")
            self.assertEqual(bu.seviye("satis", g), k + " SAT")

    def test_guclu(self):
        self.assertTrue(bu.guclu({"yon": "alis", "guc": 60}, "alis"))
        self.assertTrue(bu.guclu({"yon": "alis", "guc": 100}, "alis"))
        self.assertFalse(bu.guclu({"yon": "alis", "guc": 59}, "alis"))
        self.assertFalse(bu.guclu({"yon": "satis", "guc": 100}, "alis"))
        self.assertTrue(bu.guclu({"yon": "satis", "guc": 60}, "satis"))
        self.assertFalse(bu.guclu({"yon": "yok", "guc": None}, "satis"))

    def test_skor_ve_isabet(self):
        agr = {"a": {"agirlik": 0.2}, "b": {"agirlik": -0.1}, "c": {"agirlik": 0.1}}
        self.assertAlmostEqual(bu.skor({"a": 1, "b": -1, "c": 1}, agr), 1.0)
        self.assertIsNone(bu.skor({"a": 1}, {"a": {"agirlik": 0.0}}))
        self.assertEqual(bu.isabet([1, 1, -1, -1, 0, 1], [1, 2, 3, 2, 1, 2], 1), (1.0, 4))
        self.assertEqual(bu.agirlik(0.4, 100), -0.2)

    def test_yukselen_seride_hesap(self):
        son = 1_800_000_000_000
        rows = mumlar(900, lambda k: 100 * 1.001 ** k, bu.MS["4h"], son)
        d = bu.hesapla(rows, "4h")
        self.assertEqual(d["yon"], "alis")
        self.assertEqual(d["seviye"], bu.seviye("alis", d["guc"]))
        self.assertIn("dogrulama", d)


class UyariTests(unittest.TestCase):
    def setUp(self):
        self.eski = os.getcwd()
        self.dir = tempfile.TemporaryDirectory()
        os.chdir(self.dir.name)
        self.env = mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_CHAT_ID": "y"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        os.chdir(self.eski)
        self.dir.cleanup()

    def _calistir(self, durumlar):
        """durumlar: aralık -> hesapla() sonucu; gönderilen mesajların listesini döndürür."""
        giden = []
        rows = [[0, "1", "1", "1", "1", "1", 1]] * (bu.ISINMA + 200)
        with mock.patch.object(bu, "gecmis", return_value=rows), \
             mock.patch.object(bu, "hesapla", side_effect=lambda r, a: durumlar[a]), \
             mock.patch.object(bu.tt, "gonder", side_effect=lambda t, c, m: giden.append(m)):
            bu.main()
        return giden

    @staticmethod
    def _d(yon, guc):
        return {"yon": yon, "guc": guc, "seviye": bu.seviye(yon, guc), "kapanis": 4_000_000.0, "mum_ts": 0,
                "dogrulama": {"isabet": 53.5, "hep_ayni": 50.8}}

    def test_yalnizca_durum_yeni_olusunca_bir_kez(self):
        hepsi = {a: self._d("alis", 72) for a in bu.ARALIKLAR}
        m = self._calistir(hepsi)
        self.assertEqual(len(m), 1)
        self.assertIn("BTC GERÇEK GÜÇLÜ AL DURUMUNDA", m[0])
        self.assertIn("15 dk: GÜÇLÜ AL %72", m[0])
        self.assertIn("%53,5 (%50,8)", m[0])
        self.assertEqual(self._calistir(hepsi), [])                       # sürüyor: tekrar gitmez
        bozuk = dict(hepsi, **{"4h": self._d("alis", 47)})
        self.assertEqual(self._calistir(bozuk), [])                       # bozuldu: mesaj yok
        self.assertIsNone(json.load(open(bu.DURUM))["durum"])
        self.assertEqual(len(self._calistir(hepsi)), 1)                   # yeniden oluştu: yine gider

    def test_biri_eksikse_gitmez(self):
        for a in bu.ARALIKLAR:
            d = {x: self._d("alis", 100) for x in bu.ARALIKLAR}
            d[a] = self._d("satis", 90)
            self.assertEqual(self._calistir(d), [], a)

    def test_cok_guclu_de_sayilir(self):
        d = {a: self._d("alis", 100) for a in bu.ARALIKLAR}
        m = self._calistir(d)
        self.assertEqual(len(m), 1)
        self.assertIn("ÇOK GÜÇLÜ AL %100", m[0])


    def test_guclu_sat(self):
        sat = {a: self._d("satis", 80) for a in bu.ARALIKLAR}
        m = self._calistir(sat)
        self.assertEqual(len(m), 1)
        self.assertIn("BTC GERÇEK GÜÇLÜ SAT DURUMUNDA", m[0])
        self.assertIn("1 saat: GÜÇLÜ SAT %80", m[0])
        self.assertEqual(self._calistir(sat), [])                         # sürüyor: tekrar gitmez
        al = {a: self._d("alis", 100) for a in bu.ARALIKLAR}
        m = self._calistir(al)                                             # doğrudan al'a döndü: al mesajı gider
        self.assertEqual(len(m), 1)
        self.assertIn("GÜÇLÜ AL DURUMUNDA", m[0])

    def test_karisik_gitmez(self):
        d = {"15m": self._d("alis", 90), "1h": self._d("satis", 90), "4h": self._d("satis", 90)}
        self.assertEqual(self._calistir(d), [])


class SureTests(unittest.TestCase):
    def test_hesap_suresi(self):
        """15 dk için ~8.850 mum: GitHub çalıştırması kısa kalmalı."""
        son = 1_800_000_000_000
        rows = mumlar(90 * 96 + bu.ISINMA, lambda k: 100 + 5 * math.sin(k / 9) + 0.002 * k, bu.MS["15m"], son)
        t0 = time.time()
        bu.hesapla(rows, "15m")
        self.assertLess(time.time() - t0, 60)


if __name__ == "__main__":
    unittest.main()
