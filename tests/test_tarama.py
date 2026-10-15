import json
import unittest
from pathlib import Path
from unittest import mock

import telegram_tarama as tt

WAL = json.loads((Path(__file__).parent / "wal_try_4h_hacim.json").read_text())


def sahte_get(rows_by_symbol):
    def get(url, timeout=20):
        if "common/symbols" in url:
            return {"code": 0, "data": {"list": [{"symbol": s[:-3] + "_TRY", "quoteAsset": "TRY", "type": 1} for s in rows_by_symbol]}}
        sym = url.split("symbol=")[1].split("&")[0]
        return rows_by_symbol[sym]
    return get


class TaramaTests(unittest.TestCase):
    def test_wal_19_00_kapanisi(self):
        # 14.10 19:00 mumu kapandıktan hemen sonra (23:01 TR): WAL SÜPRİZ olmalı, 3 yeşil şartı 15:00 kırmızı olduğu için tutmaz
        rows = WAL[:-1]
        now = rows[-1][6] + 60_000
        with mock.patch.object(tt, "_get", sahte_get({"WALTRY": rows})):
            r = tt.tara(now)
        self.assertEqual(r["taranan"], 1)
        x = r["sonuc"][0]
        self.assertEqual((x["s"], x["supriz"], x["bb"], x["erken"]), ("WAL_TRY", 7, False, False))
        m = tt.mesaj(r)
        self.assertIn("14.10 19:00–23:00", m)
        self.assertIn("WAL_TRY", m)
        self.assertIn("SÜPRİZ", m)

    def test_wal_11_00_kapanisi_uc_yesil(self):
        # 14.10 11:00 mumu kapandığında son 3 mum (03:00, 07:00, 11:00) yeşil ve orta bandın üstünde
        j = [r[0] for r in WAL].index(1791964800000)
        rows = WAL[:j + 1]
        with mock.patch.object(tt, "_get", sahte_get({"WALTRY": rows + [WAL[j + 1]]})):
            r = tt.tara(rows[-1][6] + 60_000)          # açık mum (15:00) atılır
        x = r["sonuc"][0]
        self.assertTrue(x["bb"])
        self.assertEqual(x["supriz"], 7)

    def test_wal_13_10_23_00_erken_uyari(self):
        # 13.10 23:00 mumu (14.10 03:00'te kapandı): kırılımdan önceki sessiz hazırlık mumu, ERKEN UYARI listesinde
        j = [r[0] for r in WAL].index(1791921600000)
        rows = WAL[:j + 1]
        with mock.patch.object(tt, "_get", sahte_get({"WALTRY": rows})):
            r = tt.tara(rows[-1][6] + 60_000)
        x = r["sonuc"][0]
        self.assertTrue(x["erken"])
        m = tt.mesaj(r)
        self.assertIn("🟠 Erken uyarı", m)
        self.assertIn("WAL_TRY", m.split("🟠 Erken uyarı")[1])

    def test_hangi_araliklar(self):
        saat = 3_600_000
        b = 1792022400000                                     # 15.10 00:00 UTC (TR 03:00), 4 saatlik kapanış
        self.assertEqual(tt.calistirilacak(b + 3 * 60_000), ["1h", "4h"])
        self.assertEqual(tt.calistirilacak(b + 14 * 60_000), ["1h", "4h"])   # GitHub geç başlatsa da
        self.assertEqual(tt.calistirilacak(b + saat + 3 * 60_000), ["1h"])
        self.assertEqual(tt.calistirilacak(b + saat, elle=True), ["1h", "4h"])

    def test_1_saatlik_mesaj(self):
        # WAL mumlarını 1 saat aralıklı yeniden zamanla (yalnızca başlık ve aralık mantığını sınar)
        bas = WAL[-2][0] - (len(WAL) - 2) * 3_600_000
        rows = [[bas + i * 3_600_000] + r[1:6] + [bas + (i + 1) * 3_600_000 - 1] + r[7:] for i, r in enumerate(WAL[:-1])]
        with mock.patch.object(tt, "_get", sahte_get({"WALTRY": rows})):
            r = tt.tara(rows[-1][6] + 60_000, "1h")
        self.assertEqual(r["aralik"], "1h")
        m = tt.mesaj(r)
        self.assertIn("1 saatlik kapanış", m)
        self.assertNotIn("4 saatlik", m)

    def test_parcala(self):
        p = tt.parcala("\n".join("x" * 100 for _ in range(100)))
        self.assertTrue(all(len(s) <= 3900 for s in p))
        self.assertEqual(sum(s.count("x") for s in p), 10_000)


if __name__ == "__main__":
    unittest.main()
