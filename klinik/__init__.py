"""Klinik AI Asistan - uygulama katmanları.

    ozellikler   veri sözleşmesi: sütunlar, izinli değerler, istek şemaları
    veritabani   SQLite bağlantısı, şema ve sorgular
    model        eğitilmiş boru hattının yüklenmesi, tahmin ve metrikler
    analiz       kural tabanlı klinik değerlendirme
    rapor        PDF rapor üretimi

main.py bu katmanların üzerinde yalnızca HTTP uçlarını, yetkilendirmeyi ve
hız sınırlamasını barındırır.
"""
