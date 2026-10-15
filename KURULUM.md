# Binance TR tarama → Telegram (bilgisayar kapalıyken de çalışır)

Bu klasör GitHub'ın sunucularında (GitHub Actions) çalışır. 15.10.2026'dan beri kanala yalnızca 3 tür mesaj gider:
1. 4 saatlik: orta bandın üstünde ardışık 3 yeşil mum (BOLL 21,2). Formasyon kırılımı olan coinin yanında 🔷 ve formasyonun adı yazar.
2. 4 saatlik: formasyon kırılımı. Geçmiş testte rastgeleden iyi çıkan yukarı kırılımlar: genişleyen formasyon, dikdörtgen,
   yükselen üçgen, ikili dip. Hedef, zarar-kes, geçmiş test sonucu ve piyasa durumu (MA50 üstündeki coin oranı, BTC_TRY) yazar.
   1 ve 2 aynı mesajdadır; TR saatiyle 03, 07, 11, 15, 19, 23'teki 4 saatlik kapanışlardan sonra gelir.
3. 15 dakikalık erken uyarı (BLUR_TRY 15.10 patlama öncesi mumu benzeri, 8 şartın 8'i). Her 15 dakikalık kapanıştan sonra
   bakılır; eşleşen coin yoksa mesaj gelmez. Patlamadan önce yakalar ama yanlış sinyali de çoktur: izlemeye alın.

Zamanlama: cron-job.org her 15 dakikada bir (her saatin 1, 16, 31 ve 46. dakikası) bu iş akışını başlatır.
GitHub'ın kendi zamanlayıcısı yalnızca 4 saatlik mesaj için yedektir (30–40 dakika geç kalabilir; aynı mum iki kez gitmez).
Depo herkese açık (Public) olmalıdır: gizli depoda GitHub ayda 2.000 dakika verir, 15 dakikada bir çalışmak ayda yaklaşık
2.900 dakika eder. Herkese açık depoda dakika sınırı yoktur. Telegram anahtarı "Secrets" bölümünde olduğu için açık depoda da gizli kalır.

Yalnızca herkese açık piyasa verisini okur. Borsa API anahtarı gerekmez, alım satım yapmaz.
Telegram anahtarını kimseyle paylaşmayın, sohbete yazmayın; sadece GitHub'daki "Secrets" bölümüne girin.

## 1. Telegram botu ve kanal
1. Telegram'da @BotFather'a yazın, `/newbot` deyin, bota bir ad verin. Size bir anahtar (token) verir. Bunu saklayın.
2. Kanalınızı açın: Yöneticiler → Yönetici ekle → botunuzu bulun, "Mesaj gönderme" izniyle ekleyin.
3. Kanal numarası:
   - Kanal herkese açıksa kanal adını kullanın, örnek `@kanaladim`.
   - Kanal gizliyse: kanala bir mesaj yazın, sonra tarayıcıda `https://api.telegram.org/bot<ANAHTAR>/getUpdates` adresini açın
     (<ANAHTAR> yerine kendi anahtarınız). Çıkan yazıda `"chat":{"id":-100...` ile başlayan sayı kanal numaranızdır.

## 2. GitHub
1. github.com'da ücretsiz hesap açın. Sağ üstte + → New repository → ad: `binancetr-telegram`, **Public** seçin, Create.
2. Dosyaları yükleyin: "uploading an existing file" bağlantısına tıklayın, bu klasörün İÇİNDEKİ her şeyi
   (`.github` klasörü dahil) sürükleyip bırakın, Commit changes.
3. Settings → Secrets and variables → Actions → New repository secret:
   - Ad `TELEGRAM_BOT_TOKEN`, değer: BotFather'ın anahtarı
   - Ad `TELEGRAM_CHAT_ID`, değer: `@kanaladim` ya da `-100...` numarası

## 3. Deneme
Actions sekmesi → solda "4s tarama" → Run workflow. 1–2 dakikada kanala son 4 saatlik kapanışın mesajı gelmeli
(elle çalıştırmada 15 dakikalık erken uyarıya bakılmaz).
Gelmezse çalışmaya tıklayın; kırmızı satırdaki hata yazısı sorunu söyler (örnek: "chat not found" = kanal numarası ya da bot yöneticisi değil).
Kanala "veri alınamadı" ve içinde "451" geçen bir mesaj gelirse: GitHub'ın sunucusu ABD'de olduğu için Binance bağlantıyı reddetmiştir. Program önce bölge kısıtlaması olmayan Binance veri adreslerini dener; yine de olursa bu mesajı bana gönderin.

Not: Her çalışma sonucu `son_sonuc.json` dosyasına kaydeder. Bu, GitHub'ın 60 gün hareketsiz depolarda zamanlanmış görevleri durdurmasını önler.

## 4. cron-job.org (15 dakikada bir başlatma)
GitHub'ın kendi zamanlayıcısı geç kaldığı için iş akışını cron-job.org başlatır. İşin ayarı:
- Adres (POST): `https://api.github.com/repos/<kullanıcı>/binancetr-telegram/actions/workflows/tarama.yml/dispatches`
- Başlıklar: `Authorization: Bearer <yalnızca bu depoda Actions okuma-yazma izni olan anahtar>`, `Accept: application/vnd.github+json`
- Gövde: `{"ref":"main","inputs":{"otomatik":"evet"}}`
- Zamanlama: her 15 dakikada bir, dakika 1, 16, 31, 46. Başarılı çağrının cevabı 204'tür.
Önce depoyu herkese açık yapın, sonra zamanlamayı 15 dakikaya alın (gizli depoda dakika ay ortasında biter).
