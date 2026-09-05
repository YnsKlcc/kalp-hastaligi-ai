# Klinik AI Asistan

Kardiyolojik risk değerlendirmesi için yapay zeka destekli karar destek sistemi.
Hekim hastanın klinik bulgularını girer; sistem koroner arter hastalığı riskini
tahmin eder, kararın gerekçesini bulgu bulgu açıklar ve PDF rapor üretir.

> **Uyarı** — Bu bir karar destek aracıdır, tanı aracı değildir. Nihai tanı,
> tetkik ve tedavi planı hastayı muayene eden hekime aittir.

---

## Hızlı başlangıç

```bash
pip install -r requirements.txt
cp .env.example .env          # ADMIN_SIFRE ve PDF_JETON_ANAHTARI değerlerini doldurun
python model_egit.py          # model.pkl üretir (depoda varsa atlanabilir)
python main.py                # http://localhost:8000
```

`ADMIN_SIFRE` boş bırakılırsa sunucu her açılışta rastgele geçici bir şifre
üretip terminale yazar — depoda hiçbir zaman gerçek bir şifre bulunmaz.

Arayüz stilleri değiştirilecekse Tailwind'i yeniden derleyin:

```bash
npm install
npm run css        # veya geliştirirken: npm run css:watch
```

---

## Proje yapısı

```
main.py                 HTTP katmanı: uçlar, yetkilendirme, hız sınırlama
model_egit.py           model eğitimi -> model.pkl

klinik/
  ozellikler.py         veri sözleşmesi: sütunlar, izinli değerler, istek şemaları
  veritabani.py         SQLite bağlantısı, şema ve sorgular
  model.py              boru hattının yüklenmesi, tahmin, başarım metrikleri
  analiz.py             kural tabanlı klinik değerlendirme (eşik tablosu)
  rapor.py              PDF rapor üretimi

static/
  app.js                ortak JS: menü, API katmanı, güvenli DOM yardımcıları
  style.css             bileşen katmanı
  tailwind.css          derlenmiş çıktı (npm run css)
  vendor/               Lucide ve Chart.js (CDN bağımlılığı yok)
  fonts/                Inter (yerel; internet erişimi olmayan ağlarda da çalışır)

tests/test_klinik.py    regresyon testleri
```

Katmanlar tek yönlü bağlanır: `main.py` → `klinik/*`. `klinik` paketi içindeki
hiçbir modül `main.py`'yi import etmez, böylece iş mantığı sunucu olmadan da
test edilebilir.

---

## Veri sözleşmesi tek yerdedir

`klinik/ozellikler.py` bu projenin tek gerçek kaynağıdır: sütun listeleri,
izin verilen kategorik değerler ve istek şemaları oradadır. Eğitim betiği,
tahmin boru hattı ve API doğrulaması aynı sabitleri okur.

Yeni bir klinik değişken eklerken değiştirilmesi gereken tek dosya odur —
"form şu değeri gönderiyor ama model başka bir değer bekliyor" türü sessiz
uyuşmazlıklar bu yüzden ortaya çıkamaz.

---

## Model

Ön işleme ve sınıflandırıcı **tek bir sklearn Pipeline** içindedir ve
`model.pkl` olarak kaydedilir:

```
sayısal    -> medyanla doldur
kategorik  -> en sık değerle doldur -> sırasal kodla
tümü       -> ölçekle
             -> Random Forest (300 ağaç, class_weight="balanced")
```

Boru hattı **yalnızca eğitim kümesi üzerinde** fit edilir; bölme her türlü
fit'ten önce yapılır. Veri sızıntısı yapısal olarak imkânsızdır.

Test kümesi başarımı (modelin hiç görmediği 184 kayıt):

| Metrik | Değer |
|---|---|
| Doğruluk | %82,6 |
| Kesinlik | %83,7 |
| Duyarlılık | %85,3 |
| F1 | %84,5 |

Duyarlılık (recall) bilinçli olarak öne çıkarılır: tarama amaçlı bir modelde
atlanan hasta, gereksiz tetkikten daha maliyetlidir. `class_weight="balanced"`
bu yüzden açıktır.

Modeli yeniden eğitmek için `python model_egit.py` yeterlidir; `/egitim`
sayfasındaki metrik önbelleği `model.pkl` dosyasının değişim zamanına bağlı
olduğu için kendini yeniler.

---

## Klinik eşik tablosu

`klinik/analiz.py` içindeki `BULGU_TANIMLARI` tablosu 13 klinik parametrenin
referans aralıklarını ve her aralığa karşılık gelen yorum ile tavsiyeyi tutar.
Eşikler kod değil **veridir**: bir referans aralığını değiştirmek tabloda tek
bir sayıyı düzenlemek demektir, mantığa dokunulmaz.

---

## Güvenlik

| Katman | Uygulama |
|---|---|
| Kimlik doğrulama | HTTP Basic Auth, `secrets.compare_digest` ile sabit süreli karşılaştırma |
| Kaba kuvvet | Yalnızca **başarısız** denemeler kotayı tüketir (IP başına 5/dk); kota dolunca o IP bir dakika boyunca doğru şifreyle de giremez |
| Hız sınırı | Yazma 5/dk, okuma 60/dk, PDF 20/dk — `.env` ile ayarlanır |
| IDOR koruması | PDF için hasta_id'ye bağlı, kısa ömürlü HMAC-SHA256 jetonu |
| Girdi doğrulama | Pydantic `Literal` + uzunluk/aralık kısıtları; geçersiz bulgu 422 |
| XSS | Hasta verisi her yerde `textContent` ile basılır, `innerHTML` ile değil |
| SQL enjeksiyonu | Tüm sorgular parametreli |
| Sırlar | `.env` depo dışında; kod içinde varsayılan şifre yok |

**Yetkilendirme kapsamı.** Koruma uygulamanın tamamına değil, başka
hastaların verisini döndüren uçlara uygulanır:

- **Herkese açık:** `/` `/bilgi` `/egitim` `/model_metrikleri`,
  `POST /tahmin`, `POST /geribildirim`
- **Korumalı:** `/gecmis` `/kayitlar` `/istatistik` `/geribildirim`
  `/geribildirim_listesi` `/pdf-indir/{id}`

`POST /tahmin` analiz yapar ve sonucu **yalnızca isteği yapana** döndürür;
başka hiçbir kaydı okumaz veya listelemez.

> Üretimde **HTTPS zorunludur**. Basic Auth kimlik bilgilerini yalnızca Base64
> ile kodlar, şifrelemez.

Ters vekil (Nginx, Render, Railway) arkasında çalıştırırken uvicorn
`--proxy-headers` ile başlatılmalı ve `FORWARDED_ALLOW_IPS` ayarlanmalıdır;
aksi halde tüm istekler vekilin tek IP'sinden geliyor görünür ve bir kullanıcı
herkesin kotasını tüketir.

---

## Testler

```bash
python tests/test_klinik.py    # pytest gerekmez
pytest tests/ -v               # pytest kuruluysa
```

Her test, denetimde saptanmış gerçek bir hatayı hedefler: geçersiz kategorik
değerin reddedilmesi, PDF'in işaretleme enjeksiyonuna dayanması, eksik yaş
alanıyla rapor üretilebilmesi, risk oranının tek biçimde gösterilmesi, klinik
eşik sınıflarının doğru çalışması ve tahminin tekrarlanabilirliği.

---

## Veritabanı

SQLite (`klinik_kayitlar.db`), WAL kipinde. **Hasta verisi içerir ve depoya
girmez.** Uygulama ilk açılışta şemayı kendisi oluşturur.

Kısıtsız eski bir şema saptanırsa uygulama otomatik göç uygular; göçten önce
zaman damgalı bir yedek alınır (`*.goc-yedegi-*`, bu dosyalar da gitignore
kapsamındadır).

---

## Veri seti

[UCI Heart Disease](https://archive.ics.uci.edu/dataset/45/heart+disease) —
920 kayıt, 13 klinik özellik. Hedef değişken ikili sınıfa indirgenmiştir
(0 = sağlıklı, 1-4 = hastalık var).
