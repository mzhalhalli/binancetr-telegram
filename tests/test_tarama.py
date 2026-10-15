import json
import unittest
from pathlib import Path
from unittest import mock

import telegram_tarama as tt

WAL = json.loads((Path(__file__).parent / "wal_try_4h_hacim.json").read_text())
BLUR = json.loads((Path(__file__).parent / "blur_try_15m.json").read_text())


def sahte_get(rows_by_symbol):
    def get(url, timeout=20):
        if "common/symbols" in url:
            return {"code": 0, "data": {"list": [{"symbol": s[:-3] + "_TRY", "quoteAsset": "TRY", "type": 1} for s in rows_by_symbol]}}
        sym = url.split("symbol=")[1].split("&")[0]
        return rows_by_symbol[sym]
    return get


class TaramaTests(unittest.TestCase):
    def test_wal_19_00_kapanisi(self):
        # 14.10 19:00 mumu kapandıktan hemen sonra (23:01 TR): 3 yeşil şartı 15:00 kırmızı olduğu için tutmaz
        rows = WAL[:-1]
        now = rows[-1][6] + 60_000
        with mock.patch.object(tt, "_get", sahte_get({"WALTRY": rows})):
            r = tt.tara(now)
        self.assertEqual(r["taranan"], 1)
        x = r["sonuc"][0]
        self.assertEqual((x["s"], x["bb"], x["erken"]), ("WAL_TRY", False, False))
        self.assertNotIn("supriz", x)
        m = tt.mesaj(r)
        self.assertIn("14.10 19:00–23:00", m)
        self.assertNotIn("WAL_TRY", m)                   # yalnızca süpriz listesindeydi; süpriz kaldırıldı
        self.assertNotIn("SÜPRİZ", m)
        self.assertNotIn("Süpriz", m)

    def test_wal_11_00_kapanisi_uc_yesil(self):
        # 14.10 11:00 mumu kapandığında son 3 mum (03:00, 07:00, 11:00) yeşil ve orta bandın üstünde
        j = [r[0] for r in WAL].index(1791964800000)
        rows = WAL[:j + 1]
        with mock.patch.object(tt, "_get", sahte_get({"WALTRY": rows + [WAL[j + 1]]})):
            r = tt.tara(rows[-1][6] + 60_000)          # açık mum (15:00) atılır
        x = r["sonuc"][0]
        self.assertTrue(x["bb"])
        self.assertIn("WAL_TRY", tt.mesaj(r).split("3 yeşil mum")[1].split("🔷 Formasyon")[0])

    def test_4_saatlik_mesajda_erken_uyari_yok(self):
        # 13.10 23:00 mumu 4 saatlikte erken uyarı veriyor; 15.10.2026'dan beri 4 saatlik mesaj yalnızca 3 yeşil + formasyon
        j = [r[0] for r in WAL].index(1791921600000)
        rows = WAL[:j + 1]
        with mock.patch.object(tt, "_get", sahte_get({"WALTRY": rows})):
            r = tt.tara(rows[-1][6] + 60_000)
        self.assertTrue(r["sonuc"][0]["erken"])
        m = tt.mesaj(r)
        self.assertNotIn("Erken uyarı", m)
        self.assertNotIn("ERKEN", m)
        self.assertNotIn("WAL_TRY", m)
        self.assertNotIn("yaklaşan", m)

    def test_15_dakikalik_erken_uyari(self):
        # BLUR_TRY 15.10 11:45 mumu (12:00 TR kapanış) erken uyarı; 12:00 patlama mumu değil
        j = [r[0] for r in BLUR].index(1792053900000)
        with mock.patch.object(tt, "_get", sahte_get({"BLURTRY": BLUR[:j + 1]})):
            r = tt.tara(BLUR[j][6] + 60_000, "15m")
        self.assertTrue(r["sonuc"][0]["erken"])
        m = tt.mesaj_erken(r)
        self.assertIn("🟠 Binance TR · 15 dakikalık erken uyarı · kapanış 15.10 11:45–12:00", m)
        self.assertIn("BLUR_TRY", m)
        with mock.patch.object(tt, "_get", sahte_get({"BLURTRY": BLUR[:j + 2]})):
            r = tt.tara(BLUR[j + 1][6] + 60_000, "15m")
        self.assertIsNone(tt.mesaj_erken(r))             # eşleşme yoksa mesaj yok

    def test_15_dakikalik_main(self):
        import os
        import tempfile
        j = [r[0] for r in BLUR].index(1792053900000)
        rows, kapanis = BLUR[:j + 1], BLUR[j][6] + 1     # 12:00 TR = 09:00 UTC: 4 saatlik kapanış saati değil
        ortam = {"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_CHAT_ID": "y", "GITHUB_EVENT_NAME": "workflow_dispatch", "OTOMATIK": "evet",
                 "ONBES_DK": "evet"}
        eski = os.getcwd()
        with tempfile.TemporaryDirectory() as d:
            os.chdir(d)
            try:
                for olay, gecikme, gitmeli in (("workflow_dispatch", 60_000, True), ("workflow_dispatch", 11 * 60_000, False),
                                               ("schedule", 60_000, False)):
                    giden = []
                    with mock.patch.dict(os.environ, {**ortam, "GITHUB_EVENT_NAME": olay}), \
                            mock.patch.object(tt, "_get", sahte_get({"BLURTRY": rows})), \
                            mock.patch.object(tt, "gonder", lambda t, c, m: giden.append(m)), \
                            mock.patch.object(tt.time, "time", lambda: (kapanis + gecikme) / 1000):
                        tt.main()
                    self.assertEqual(any("BLUR_TRY" in m for m in giden), gitmeli, (olay, gecikme))
                    self.assertFalse(any("4 saatlik" in m for m in giden))
                self.assertFalse(os.path.exists(tt.GONDERILEN))    # 15 dakikalık için dosya yazılmaz (commit atılmasın)
            finally:
                os.chdir(eski)

    def test_hangi_araliklar(self):
        saat = 3_600_000
        b = 1792022400000                                     # 15.10 00:00 UTC (TR 03:00), 4 saatlik kapanış
        self.assertEqual(tt.calistirilacak(b + 3 * 60_000), ["4h"])
        self.assertEqual(tt.calistirilacak(b + 40 * 60_000), ["4h"])        # GitHub geç başlatsa da
        self.assertEqual(tt.calistirilacak(b + saat + 3 * 60_000), [])        # 1 saatlik mesaj artık yok
        self.assertEqual(tt.calistirilacak(b + saat, elle=True), ["4h"])
        self.assertEqual(tt.calistirilacak(b + 60_000, onbes=True), ["4h", "15m"])
        self.assertEqual(tt.calistirilacak(b + saat + 16 * 60_000, onbes=True), ["15m"])

    def test_ayni_mum_iki_kez_gitmez(self):
        import os
        import tempfile
        rows = WAL[:-1]
        now = rows[-1][6] + 60_000                      # 14.10 23:01 TR (UTC 20:01): 1s ve 4s kapanışı
        giden = []
        ortam = {"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_CHAT_ID": "y", "GITHUB_EVENT_NAME": "workflow_dispatch", "OTOMATIK": "evet"}
        eski = os.getcwd()
        with tempfile.TemporaryDirectory() as d:
            os.chdir(d)
            try:
                with mock.patch.dict(os.environ, ortam), mock.patch.object(tt, "_get", sahte_get({"WALTRY": rows})), \
                        mock.patch.object(tt, "gonder", lambda t, c, m: giden.append(m)), \
                        mock.patch.object(tt.time, "time", lambda: now / 1000):
                    tt.main()                            # dış zamanlayıcı: 4s gönderilir (1s verisi bu testte yok)
                    n = len(giden)
                    self.assertTrue(any("4 saatlik kapanış 14.10 19:00–23:00" in m for m in giden))
                    os.environ["GITHUB_EVENT_NAME"] = "schedule"
                    tt.main()                            # GitHub'ın gecikmeli yedeği: aynı 4s mumu tekrar gitmez
                    self.assertFalse(any("4 saatlik" in m for m in giden[n:]))
                    os.environ.update({"GITHUB_EVENT_NAME": "workflow_dispatch", "OTOMATIK": "hayir"})
                    tt.main()                            # elle çalıştırma her zaman gönderir
                    self.assertTrue(any("4 saatlik" in m for m in giden[n:]))
            finally:
                os.chdir(eski)

    def test_formasyon_bolumleri(self):
        # yapay seriler: AAA son 4s mumunda dikdörtgen kırılımı, BBB yükselen üçgen kırılımına yaklaşıyor
        ornek = json.loads((Path(__file__).parent / "formasyon_ornek.json").read_text())
        now = 1792080000000 + 60_000                     # 4s kapanışından 1 dk sonra

        def satirlar(ad, iv):
            x = ornek[ad]["ohlc"]
            son_acilis = now // iv * iv - iv
            return [[son_acilis - (len(x) - 1 - i) * iv, str(o), str(h), str(l), str(c), "1", son_acilis - (len(x) - 1 - i) * iv + iv - 1,
                     str(c * 1000), 1, "0", "0", "0"] for i, (o, h, l, c) in enumerate(x)]
        veri = {"AAATRY": satirlar("4h_kirilim_dikdortgen", 14_400_000), "BBBTRY": satirlar("4h_yaklasan_yukselen_ucgen", 14_400_000)}
        with mock.patch.object(tt, "_get", sahte_get(veri)):
            r = tt.tara(now, "4h")
        m = tt.mesaj(r)
        kir = m.split("🔷 Formasyon kırılımı")[1].split("🟠")[0]
        self.assertIn("AAA_TRY", kir)
        self.assertIn("Dikdörtgen ↑", kir)
        self.assertIn("geçmiş test", kir)
        self.assertIn("Piyasa genişliği hesaplanamadı", kir)      # 50 coinden az
        self.assertTrue(next(x for x in r["sonuc"] if x["s"] == "BBB_TRY")["fm_yaklasan"])   # BBB kırılıma yaklaşıyor ama mesajda yaklaşanlar bölümü yok
        self.assertNotIn("BBB_TRY", m)
        self.assertNotIn("🔹", m)
        self.assertIn("1s BOĞA BAYRAĞI ↑", tt._fm_etiket({"fm_kirilim": [{"baslik": "Boğa bayrağı"}]}, "1h"))
        self.assertIn("4s İKİLİ DİP ↑", tt._fm_etiket({"fm_kirilim": [{"baslik": "İkili dip"}]}, "4h"))

    def test_uzun_liste_kisalir(self):
        self.assertEqual(len(tt._kisalt([f"• {i}" for i in range(40)], 40, 1)), tt.LISTE_EN_COK + 1)

    def test_parcala(self):
        p = tt.parcala("\n".join("x" * 100 for _ in range(100)))
        self.assertTrue(all(len(s) <= 3900 for s in p))
        self.assertEqual(sum(s.count("x") for s in p), 10_000)


if __name__ == "__main__":
    unittest.main()
