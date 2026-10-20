# Binance TR Telegram taraması (GitHub) — botun son hali

Bu klasör, GitHub'da bilgisayar kapalıyken de çalışan Telegram taramasıdır. 20.10.2026'da bilgisayardaki botun
(binancetr_arastirma) **son kurallarına** getirildi; eski taramaların (4 saatlik 3 yeşil mum, 4 saatlik formasyon kırılımı,
15 dakikalık erken uyarı, süpriz) yerini alır.

## Ne gönderir
Her 15 dakikada bir tarar ve **botun alacağı** sinyalleri gönderir. 20.10.2026 kullanıcı isteği: Telegram'a yalnızca
1. ve 2. model, yalnızca 15 dakika ve 1 saat:
1. Alt-orta-alt bant + kırmızı mumla alt banda temas (15 dk, açık mum)
2. ALTIN FORMASYON: orta banttan alt banda → RSI6 RSI12 ve RSI24'ü yukarı keser → retest (RSI6 RSI12'nin altında
   kapanmaz) → orta bant üstünde 2 yeşil (15 dk, 1 saat; 2. yeşil mum kapanmış olmalı)
(Botun 3. modeli — boğa bayrağı retest — Telegram'a gönderilmez.)

Süzgeçler botla aynı: stabil/altın yok, 5 USD üstü ve 0,0001 USD altı yok, arzı en çok arzın yarısından az olan yok,
24 saatlik TL hacmi en yüksek 100 coin; uygunluk %0-40 ya da %50-69; aynı coin 24 saatte bir. Altcoin piyasa yönü
filtresi kaldırıldı. Yeni sinyal yoksa mesaj gelmez.

Haber uyarıları, büyük/orta alım alarmları, sahte cüzdan alış-satışları ve 12:00 özeti **bilgisayardaki bottan** gelir.

## GitHub'a yükleme
1. GitHub'daki tarama deponuzu açın. Eski dosyaları silin: `telegram_tarama.py`, `btr/` klasörü, `tests/`, `KURULUM.md`,
   `.github/workflows/tarama.yml` (ve varsa `son_sonuc.json`).
2. Bu klasördeki her şeyi yükleyin (**Add file > Upload files**; `.github/workflows/tarama.yml` dahil). Klasör yapısı aynı kalmalı.
3. **Settings > Secrets and variables > Actions** içinde `TELEGRAM_BOT_TOKEN` ve `TELEGRAM_CHAT_ID` zaten varsa bir şey yapmayın.
4. **Actions** sekmesinde iş akışını açın, **Run workflow** ile bir kez elle çalıştırıp günlüğe bakın.

## Dikkat
- **Süre:** 15 dakikada bir çalışır (her çalışma ~1-2 dk). Depo **herkese açık** (public) ise ücretsizdir. **Özel** (private)
  depoda GitHub'ın aylık ücretsiz süresi yetmeyebilir; o zaman `.github/workflows/tarama.yml` içindeki
  `cron: "2,17,32,47 * * * *"` satırını `cron: "7 * * * *"` yapın (saatte bir).
- **Gizlilik:** depo herkese açıksa `btr/benim_sartlarim.json` (kendi şartlarınız) ve `durum.json` (gönderilen sinyaller)
  herkes tarafından görülebilir. Bunu istemiyorsanız depoyu özel yapın.
- Bilgisayardaki botta kural değişirse bu klasör kendiliğinden değişmez; yeniden güncellenmesi gerekir.
- Yalnızca herkese açık borsa verisini okur; borsa API anahtarı, hesap ve emir yoktur. Yatırım tavsiyesi değildir.
