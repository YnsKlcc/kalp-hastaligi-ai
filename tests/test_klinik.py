"""Klinik AI Asistan - regresyon testleri.

Her test, denetimde saptanmış GERÇEK bir hatayı hedefler; test adları
bulgu numaralarına atıfta bulunur. Amaç kapsam yüzdesi değil, düzeltilen
hataların geri gelmesini engellemektir.

Çalıştırma (pytest gerekmez):
    python tests/test_klinik.py

pytest kuruluysa:
    pytest tests/ -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pydantic import ValidationError

from klinik import analiz, rapor
from klinik.ozellikler import (
    TESHIS_RISKLI, TESHIS_SAGLIKLI, TUM_SUTUNLAR, GeriBildirim, HastaVerisi,
)

# Geçerli bir muayene gövdesi; testler bunun üzerinde tek alan değiştirir.
GECERLI = {
    "ad": "Test", "soyad": "Hasta",
    "age": 60, "sex": "Male", "cp": "typical angina",
    "trestbps": 150, "chol": 260, "fbs": "True", "restecg": "normal",
    "thalch": 120, "exang": "True", "oldpeak": 2.5, "slope": "flat",
    "ca": 2, "thal": "reversable defect",
}


# --- Bulgu 1: geçersiz kategorik değerler sessizce kabul ediliyordu --------

def test_gecersiz_kategorik_deger_reddedilir():
    for alan, kotu in [("sex", "YYYY"), ("cp", "ZZZZ"), ("thal", "QQQ"),
                       ("fbs", "HEHE"), ("restecg", "WWW"), ("slope", "XXX"),
                       ("exang", "NOPE")]:
        govde = {**GECERLI, alan: kotu}
        try:
            HastaVerisi(**govde)
        except ValidationError:
            continue
        raise AssertionError(f"{alan}={kotu!r} kabul edildi; reddedilmeliydi.")


def test_gecerli_kategorik_deger_kabul_edilir():
    hasta = HastaVerisi(**GECERLI)
    assert hasta.sex == "Male"
    assert set(hasta.bulgular()) == set(TUM_SUTUNLAR)
    assert "ad" not in hasta.bulgular()


def test_eksik_klinik_alan_reddedilir():
    """Eskiden tüm klinik alanların varsayılanı vardı; {"ad","soyad"} bile
    uydurulmuş bulgulardan teşhis üretiyordu."""
    try:
        HastaVerisi(ad="A", soyad="B")
    except ValidationError:
        return
    raise AssertionError("Eksik gövde kabul edildi; reddedilmeliydi.")


def test_sayisal_sinirlar_uygulanir():
    for alan, kotu in [("age", 500), ("age", 0), ("trestbps", 999),
                       ("chol", -5), ("thalch", 10), ("ca", 9), ("oldpeak", 99)]:
        try:
            HastaVerisi(**{**GECERLI, alan: kotu})
        except ValidationError:
            continue
        raise AssertionError(f"{alan}={kotu} kabul edildi; reddedilmeliydi.")


# --- Bulgu 6: metin alanlarında uzunluk sınırı yoktu -----------------------

def test_asiri_uzun_ad_reddedilir():
    try:
        HastaVerisi(**{**GECERLI, "ad": "A" * 100_000})
    except ValidationError:
        return
    raise AssertionError("100.000 karakterlik ad kabul edildi.")


def test_bos_ad_reddedilir():
    try:
        HastaVerisi(**{**GECERLI, "ad": ""})
    except ValidationError:
        return
    raise AssertionError("Boş ad kabul edildi.")


def test_geribildirim_serbest_metin_kabul_etmez():
    """Kimlik doğrulaması istemeyen uç; yalnızca bilinen iki etiket geçerli."""
    try:
        GeriBildirim(hasta_id=1, orijinal_teshis="rastgele metin",
                     doktor_karari=TESHIS_SAGLIKLI)
    except ValidationError:
        pass
    else:
        raise AssertionError("Serbest metin teşhis kabul edildi.")

    gecerli = GeriBildirim(hasta_id=1, orijinal_teshis=TESHIS_RISKLI,
                           doktor_karari=TESHIS_SAGLIKLI)
    assert gecerli.hasta_id == 1


# --- Bulgu 3: hasta adındaki işaretleme PDF'i çökertiyordu ----------------

def test_pdf_isaretleme_enjeksiyonuna_dayanir():
    tehlikeli = ["Ali <b>Kalin", "Ay<se", "Veli & Ali", "<script>x</script>",
                 "A<i>B", "</para>", "Ali <font color=red>K"]
    analiz_ornegi = _ornek_analiz()

    for ad in tehlikeli:
        pdf = rapor.pdf_uret(ad=ad, soyad="Test", yas=60, cinsiyet="Male",
                             teshis=TESHIS_RISKLI, risk_metni="%87.0",
                             analiz=analiz_ornegi)
        assert pdf.startswith(b"%PDF"), f"{ad!r} icin gecerli PDF uretilemedi"


def test_pdf_kacis_sirasi_dogru():
    """Önce & sonra < kaçışlanmazsa '&lt;' ikinci kez kaçışlanıp bozulur."""
    assert rapor.tr("a & b") == "a &amp; b"
    assert rapor.tr("<b>") == "&lt;b&gt;"
    assert rapor.tr("&<>") == "&amp;&lt;&gt;"
    # Unicode yazı tipi gömüldüğü için Türkçe harfler korunur; kaçış yine çalışır.
    assert rapor.tr("Şişli & Çağlayan") == "Şişli &amp; Çağlayan"


def test_pdf_turkce_harfler_korunur():
    """Resmî raporda hasta adı harf kaybına uğramamalı (Vera gömülür)."""
    assert not rapor.SADELESTIR, "Türkçe kapsamlı yazı tipi yüklenemedi."
    assert rapor.tr("Çağla Şişmanoğlu İpek") == "Çağla Şişmanoğlu İpek"


def test_pdf_font_yedegi_ascii_ye_duser():
    """Yazı tipi yüklenemezse rapor çökmez, ASCII'ye sadeleşir."""
    onceki = rapor.SADELESTIR
    rapor.SADELESTIR = True
    try:
        assert rapor.tr("Şişli & Çağlayan") == "Sisli &amp; Caglayan"
    finally:
        rapor.SADELESTIR = onceki


# --- Bulgu 4: yaş NULL ise PDF çöküyordu ----------------------------------

def test_pdf_eksik_yas_ile_uretilir():
    for yas in [None, "", "gecersiz"]:
        pdf = rapor.pdf_uret(ad="Test", soyad="Hasta", yas=yas, cinsiyet="Male",
                             teshis=TESHIS_SAGLIKLI, risk_metni="%20.0",
                             analiz=_ornek_analiz())
        assert pdf.startswith(b"%PDF"), f"yas={yas!r} icin PDF uretilemedi"


# --- Bulgu 11: iki farklı risk biçimi ------------------------------------

def test_risk_orani_tek_bicimde():
    assert analiz.oran_bicimle(87.0) == "%87.0"
    assert analiz.oran_bicimle(87.004) == "%87.0"
    assert analiz.oran_bicimle(0) == "%0.0"
    # Rapor metni ile risk_metni aynı dizgiyi kullanmalı.
    sonuc = analiz.klinik_analiz_uret(_ornek_bulgular(), TESHIS_RISKLI, 87.0)
    assert sonuc["risk_metni"] == "%87.0"
    assert "%87.0" in sonuc["metin"]
    assert "%87.00" not in sonuc["metin"]


# --- Klinik eşik tablosu ---------------------------------------------------

def test_her_bulgu_tanimi_son_care_kurali_icerir():
    """Son çare kuralı (None sınırı) olmayan bir tanım, uç değerlerde
    çalışma anında ValueError fırlatırdı."""
    for tanim in analiz.BULGU_TANIMLARI:
        if isinstance(tanim, analiz.SayisalBulgu):
            assert tanim.esikler[-1][0] is None, f"{tanim.anahtar}: son çare kuralı yok"


def test_tum_bulgular_degerlendirilir():
    bulgular, tavsiyeler = analiz.bulgulari_degerlendir(_ornek_bulgular())
    assert len(bulgular) == len(analiz.BULGU_TANIMLARI) == 13
    assert all(b["seviye"] in analiz.SEVIYE_ETIKETI for b in bulgular)
    # {deger} yer tutucusu her zaman doldurulmuş olmalı.
    assert not any("{deger}" in b["yorum"] for b in bulgular)
    # Yasal uyarı her raporun sonunda yer alır.
    assert tavsiyeler[-1] == analiz.TAVSIYE_YASAL_UYARI


def test_esik_siniflari_dogru_calisir():
    def seviye(bulgular, baslik):
        liste, _ = analiz.bulgulari_degerlendir(bulgular)
        return next(b["seviye"] for b in liste if b["baslik"] == baslik)

    yasli = {**_ornek_bulgular(), "age": 70}
    assert seviye(yasli, "Yaş") == analiz.SEVIYE_YUKSEK
    assert seviye({**_ornek_bulgular(), "age": 50}, "Yaş") == analiz.SEVIYE_ORTA
    assert seviye({**_ornek_bulgular(), "age": 30}, "Yaş") == analiz.SEVIYE_NORMAL

    assert seviye({**_ornek_bulgular(), "trestbps": 165}, "Dinlenik Kan Basıncı") == analiz.SEVIYE_YUKSEK
    assert seviye({**_ornek_bulgular(), "trestbps": 120}, "Dinlenik Kan Basıncı") == analiz.SEVIYE_NORMAL

    # chol = 0 -> ölçüm girilmemiş
    assert seviye({**_ornek_bulgular(), "chol": 0}, "Serum Kolesterol") == analiz.SEVIYE_VERIYOK


def test_kalp_hizi_yasa_gore_degerlendirilir():
    """thalch tek türetilmiş bulgudur: eşik ham bpm değil, beklenenin yüzdesi."""
    def seviye(age, thalch):
        liste, _ = analiz.bulgulari_degerlendir(
            {**_ornek_bulgular(), "age": age, "thalch": thalch})
        return next(b["seviye"] for b in liste if b["baslik"] == "Maks. Kalp Atış Hızı")

    # 30 yaşında beklenen 190; 120 bpm -> %63 -> yetersiz
    assert seviye(30, 120) == analiz.SEVIYE_YUKSEK
    # 70 yaşında beklenen 150; 140 bpm -> %93 -> normal
    assert seviye(70, 140) == analiz.SEVIYE_NORMAL


# --- Bulgu 10: gerekçe ile modelin girdisi ayrışıyordu --------------------

def test_gerekce_teshise_uygun():
    riskli = analiz.klinik_analiz_uret(_ornek_bulgular(), TESHIS_RISKLI, 87.0)
    assert "Riskli" in riskli["gerekce"]
    assert riskli["teshis"] == TESHIS_RISKLI

    saglikli = analiz.klinik_analiz_uret(_ornek_bulgular(), TESHIS_SAGLIKLI, 12.0)
    assert "Sağlıklı" in saglikli["gerekce"]


# --- Model boru hattı ------------------------------------------------------

def test_model_tahmini_tutarli():
    """Aynı girdi aynı sonucu vermeli; risk sayı olarak dönmeli."""
    from klinik import model

    bulgular = HastaVerisi(**GECERLI).bulgular()
    teshis, risk, aciklama = model.tahmin_et(bulgular)

    assert teshis in (TESHIS_SAGLIKLI, TESHIS_RISKLI)
    assert isinstance(risk, float) and 0.0 <= risk <= 100.0
    assert aciklama and "None" not in aciklama
    # Riskli teşhis ile risk oranı aynı yönü göstermeli.
    assert (risk >= 50) == (teshis == TESHIS_RISKLI)
    # Tekrarlanabilirlik
    assert model.tahmin_et(bulgular) == (teshis, risk, aciklama)


def test_model_ozellik_sirasi_bozulmamis():
    from klinik import model
    assert sorted(model.OZELLIK_SIRASI) == sorted(TUM_SUTUNLAR)


# --- Yardımcılar -----------------------------------------------------------

def _ornek_bulgular() -> dict:
    return {k: v for k, v in GECERLI.items() if k in TUM_SUTUNLAR}


def _ornek_analiz() -> dict:
    return analiz.klinik_analiz_uret(_ornek_bulgular(), TESHIS_RISKLI, 87.0)


# --- pytest yoksa da çalışsın ---------------------------------------------

if __name__ == "__main__":
    testler = [(ad, nesne) for ad, nesne in sorted(globals().items())
               if ad.startswith("test_") and callable(nesne)]
    basarisiz = 0
    for ad, calistir in testler:
        try:
            calistir()
            print(f"  GECTI  {ad}")
        except Exception as hata:
            basarisiz += 1
            print(f"  KALDI  {ad}\n         {type(hata).__name__}: {hata}")
    print(f"\n{len(testler) - basarisiz}/{len(testler)} test gecti.")
    sys.exit(1 if basarisiz else 0)
