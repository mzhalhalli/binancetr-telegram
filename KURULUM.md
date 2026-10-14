# Binance TR 4 saatlik tarama → Telegram (bilgisayar kapalıyken de çalışır)

Bu klasör GitHub'ın ücretsiz sunucularında (GitHub Actions) her 4 saatlik kapanıştan 3 dakika sonra çalışır:
TR saatiyle 03:03, 07:03, 11:03, 15:03, 19:03, 23:03. GitHub bazen 5–15 dakika geç başlatabilir.

Her çalışmada Binance TR'deki tüm TRY çiftlerinin son kapanan 4 saatlik mumuna bakar ve kanala tek mesaj yollar:
1. Orta bandın üstünde ardışık 3 yeşil mum (BOLL 21,2). Süpriz şartlarını da tutan coinin yanında SÜPRİZ yazar.
2. Süpriz listesi (WAL_TRY 14.10 benzeri, 7 şartın en az 6'sı).

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
Actions sekmesi → solda "4s tarama" → Run workflow. 1–2 dakikada kanala mesaj gelmeli.
Gelmezse çalışmaya tıklayın; kırmızı satırdaki hata yazısı sorunu söyler (örnek: "chat not found" = kanal numarası ya da bot yöneticisi değil).
Kanala "veri alınamadı" ve içinde "451" geçen bir mesaj gelirse: GitHub'ın sunucusu ABD'de olduğu için Binance bağlantıyı reddetmiştir. Program önce bölge kısıtlaması olmayan Binance veri adreslerini dener; yine de olursa bu mesajı bana gönderin.

Not: Her çalışma sonucu `son_sonuc.json` dosyasına kaydeder. Bu, GitHub'ın 60 gün hareketsiz depolarda zamanlanmış görevleri durdurmasını önler.
