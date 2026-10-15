# Binance TR 1 saatlik ve 4 saatlik tarama → Telegram (bilgisayar kapalıyken de çalışır)

Bu klasör GitHub'ın ücretsiz sunucularında (GitHub Actions) her saat başından 3 dakika sonra çalışır.
Her saat son kapanan 1 saatlik mumu tarar; TR saatiyle 03, 07, 11, 15, 19, 23'te 4 saatlik mumu da tarar.
GitHub bazen 5–15 dakika geç başlatabilir. Özel depoda aylık ücretsiz süre (2000 dk) her saat çalışmaya yeter.

Her taramada Binance TR'deki tüm TRY çiftlerine bakar ve her zaman dilimi için kanala ayrı mesaj yollar:
1. Orta bandın üstünde ardışık 3 yeşil mum (BOLL 21,2). Erken uyarı şartlarını da tutan coinin yanında ERKEN UYARI,
   formasyon kırılımı olan coinin yanında 🔷 ve formasyonun adı yazar.
2. Formasyon kırılımı: geçmiş testte rastgeleden iyi çıkan yukarı kırılımlar (1 saatlik: boğa bayrağı, genişleyen formasyon;
   4 saatlik: genişleyen formasyon, dikdörtgen, yükselen üçgen, ikili dip). Hedef, zarar-kes, geçmiş test sonucu ve
   piyasa durumu (coinlerin yüzde kaçı 50 mumluk ortalamasının üstünde, BTC_TRY) birlikte yazar.
3. Erken uyarı listesi (BLUR_TRY 15.10 patlama öncesi mumu benzeri, 8 şartın 8'i). Patlamadan önce yakalar ama yanlış sinyali de çoktur: izlemeye alın.
4. Erken uyarı · formasyon kırılımına yaklaşanlar: aynı formasyonlar hazır, henüz kırılmadı, kapanış kırılım çizgisine 1 ATR'den yakın (izleme listesi).

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
1. github.com'da ücretsiz hesap açın. Sağ üstte + → New repository → ad: `binancetr-telegram`, **Private** seçin, Create.
2. Dosyaları yükleyin: "uploading an existing file" bağlantısına tıklayın, bu klasörün İÇİNDEKİ her şeyi
   (`.github` klasörü dahil) sürükleyip bırakın, Commit changes.
3. Settings → Secrets and variables → Actions → New repository secret:
   - Ad `TELEGRAM_BOT_TOKEN`, değer: BotFather'ın anahtarı
   - Ad `TELEGRAM_CHAT_ID`, değer: `@kanaladim` ya da `-100...` numarası

## 3. Deneme
Actions sekmesi → solda "4s tarama" → Run workflow. 1–2 dakikada kanala iki mesaj (1 saatlik ve 4 saatlik) gelmeli.
Gelmezse çalışmaya tıklayın; kırmızı satırdaki hata yazısı sorunu söyler (örnek: "chat not found" = kanal numarası ya da bot yöneticisi değil).
Kanala "veri alınamadı" ve içinde "451" geçen bir mesaj gelirse: GitHub'ın sunucusu ABD'de olduğu için Binance bağlantıyı reddetmiştir. Program önce bölge kısıtlaması olmayan Binance veri adreslerini dener; yine de olursa bu mesajı bana gönderin.

Not: Her çalışma sonucu `son_sonuc.json` dosyasına kaydeder. Bu, GitHub'ın 60 gün hareketsiz depolarda zamanlanmış görevleri durdurmasını önler.
