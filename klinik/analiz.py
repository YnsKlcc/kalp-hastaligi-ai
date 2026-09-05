"""Kural tabanlı klinik değerlendirme.

Modelin tahmini "riskli mi?" sorusuna cevap verir ama gerekçe üretmez.
Bu modül her klinik parametreyi referans aralıklarıyla karşılaştırıp
hekimin okuyabileceği bir yorum ve tavsiye listesi çıkarır.

TASARIM: eşikler kod değil, VERİDİR
-----------------------------------
Önceki sürümde bu iş 360 satırlık tek bir fonksiyondu ve gövdesi aynı
kalıbın 13 kez tekrarından ibaretti (eşiği kontrol et -> bulgu ekle ->
tavsiye ekle). Bir referans aralığını değiştirmek için 360 satırın içinde
doğru if bloğunu bulmak gerekiyordu.

Şimdi tüm klinik eşikler aşağıdaki BULGU_TANIMLARI tablosunda, tek ekranda
gözden geçirilebilir halde duruyor. Değerlendirme mantığı ise tabloyu
dolaşan yaklaşık 40 satır. Yeni bir parametre eklemek tabloya bir girdi
eklemek demek; mantığa dokunulmaz.
"""

from dataclasses import dataclass
from datetime import datetime

from klinik.ozellikler import TESHIS_RISKLI, TESHIS_SAGLIKLI, riskli_mi

# --- Bulgu ağırlık seviyeleri ----------------------------------------------
SEVIYE_YUKSEK = "yuksek"     # belirgin patolojik bulgu
SEVIYE_ORTA = "orta"         # sınırda / şüpheli bulgu
SEVIYE_NORMAL = "normal"     # referans aralığında
SEVIYE_VERIYOK = "veriyok"   # ölçüm girilmemiş

SEVIYE_ETIKETI = {
    SEVIYE_YUKSEK: "PATOLOJİK",
    SEVIYE_ORTA: "SINIRDA",
    SEVIYE_NORMAL: "NORMAL",
    SEVIYE_VERIYOK: "VERİ YOK",
}


@dataclass(frozen=True)
class Kural:
    """Bir bulgunun düşebileceği tek bir sonuç.

    yorum içinde {deger} yer tutucusu, ölçülen değerin gösterim biçimiyle
    (ör. "150 mmHg") değiştirilir.
    """
    seviye: str
    yorum: str
    tavsiye: str | None = None


@dataclass(frozen=True)
class SayisalBulgu:
    """Eşik karşılaştırmasıyla değerlendirilen ölçüm.

    esikler: (sınır, Kural) ikilileri, EN AĞIRDAN EN HAFİFE sıralı.
             sınır None ise son çare kuralıdır, her değeri yakalar.
    dusuk_kotu: False -> değer >= sınır ise kural uygulanır (yaş, tansiyon...)
                True  -> değer <  sınır ise kural uygulanır (kalp hızı yanıtı)
    veri_yok:   tanımlıysa, değer <= 0 olduğunda ölçüm girilmemiş sayılır.
    """
    anahtar: str
    baslik: str
    birim: str
    esikler: tuple[tuple[float | None, Kural], ...]
    dusuk_kotu: bool = False
    ondalik: int = 0
    veri_yok: Kural | None = None


@dataclass(frozen=True)
class KategorikBulgu:
    """Değer eşleşmesiyle değerlendirilen parametre.

    secenekler: (eşleşen ham değerler, ekranda görünecek ad, Kural)
    varsayilan: hiçbiri eşleşmezse kullanılacak (ad, Kural)
    """
    anahtar: str
    baslik: str
    secenekler: tuple[tuple[tuple[str, ...], str, Kural], ...]
    varsayilan: tuple[str, Kural]


# ===========================================================================
# KLİNİK EŞİK TABLOSU
#
# Buradaki sıralama rapordaki bulgu sırasını da belirler. Eşik değerleri
# ESC/AHA kardiyovasküler risk kılavuzlarındaki yaygın sınırlara dayanır.
# ===========================================================================

BULGU_TANIMLARI = (

    # --- 1) Yaş ------------------------------------------------------------
    SayisalBulgu(
        anahtar="age", baslik="Yaş", birim="yaş",
        esikler=(
            (65, Kural(
                SEVIYE_YUKSEK,
                "İleri yaş (≥65), ateroskleroz için değiştirilemeyen bağımsız bir risk "
                "faktörüdür; yaşla birlikte arteriyel sertlik ve koroner plak yükü "
                "belirgin biçimde artar.",
                "İleri yaşa bağlı risk nedeniyle kardiyolojik kontroller yılda en az bir "
                "kez tekrarlanmalıdır.")),
            (45, Kural(
                SEVIYE_ORTA,
                "Hasta orta yaş grubundadır (≥45). Bu dönem koroner arter hastalığı "
                "insidansının belirgin biçimde yükselmeye başladığı yaş aralığıdır.")),
            (None, Kural(
                SEVIYE_NORMAL,
                "Genç yaş grubu. Yaş, mevcut tabloda kardiyovasküler riski artırıcı bir "
                "faktör oluşturmamaktadır.")),
        ),
    ),

    # --- 2) Cinsiyet -------------------------------------------------------
    KategorikBulgu(
        anahtar="sex", baslik="Cinsiyet",
        secenekler=(
            (("male", "erkek", "1"), "Erkek", Kural(
                SEVIYE_ORTA,
                "Erkek cinsiyet, koroner arter hastalığı için bağımsız bir risk "
                "faktörüdür; aterosklerotik olaylar erkeklerde ortalama on yıl daha "
                "erken ortaya çıkar.")),
        ),
        varsayilan=("Kadın", Kural(
            SEVIYE_NORMAL,
            "Premenopozal dönemde östrojenin endotel koruyucu etkisi nedeniyle kadın "
            "cinsiyet, koroner olay riski açısından görece koruyucu kabul edilir.")),
    ),

    # --- 3) Dinlenik kan basıncı -------------------------------------------
    SayisalBulgu(
        anahtar="trestbps", baslik="Dinlenik Kan Basıncı", birim="mmHg",
        esikler=(
            (160, Kural(
                SEVIYE_YUKSEK,
                "Sistolik kan basıncı {deger} olup Evre 2 hipertansiyon sınırındadır. "
                "Kronik basınç yükü sol ventrikül hipertrofisine ve endotel "
                "disfonksiyonuna yol açar.",
                "Antihipertansif tedavinin düzenlenmesi için kardiyoloji/dahiliye "
                "değerlendirmesi gereklidir; günlük tuz alımı 5 gramın altına "
                "indirilmelidir.")),
            (140, Kural(
                SEVIYE_YUKSEK,
                "Sistolik kan basıncı {deger} ile Evre 1 hipertansiyon eşiğinin "
                "üzerindedir; aterosklerotik süreci hızlandıran majör bir risk "
                "faktörüdür.",
                "Kan basıncı evde düzenli ölçülmeli, düşük sodyumlu (DASH tipi) diyet "
                "uygulanmalı ve medikal tedavi için hekime başvurulmalıdır.")),
            (130, Kural(
                SEVIYE_ORTA,
                "Sistolik kan basıncı {deger} ile yüksek normal (prehipertansiyon) "
                "kategorisindedir.",
                "Kilo kontrolü, düzenli aerobik egzersiz ve tuz kısıtlaması ile kan "
                "basıncının normotansif aralığa çekilmesi hedeflenmelidir.")),
            (None, Kural(
                SEVIYE_NORMAL,
                "Sistolik kan basıncı normotansif referans aralığındadır; basınç "
                "kaynaklı ek kardiyak yük saptanmamıştır.")),
        ),
    ),

    # --- 4) Serum kolesterol -----------------------------------------------
    SayisalBulgu(
        anahtar="chol", baslik="Serum Kolesterol", birim="mg/dl",
        veri_yok=Kural(
            SEVIYE_VERIYOK,
            "Total kolesterol değeri girilmemiştir; lipid profili olmadan "
            "aterosklerotik risk tam olarak sınıflandırılamaz.",
            "Açlık lipid profili (total kolesterol, LDL, HDL, trigliserit) istenerek "
            "risk sınıflaması tamamlanmalıdır."),
        esikler=(
            (240, Kural(
                SEVIYE_YUKSEK,
                "Total kolesterol {deger} ile yüksek risk grubundadır (≥240 mg/dl). "
                "Hiperlipidemi, koroner arterlerde aterom plağı gelişiminin temel "
                "tetikleyicisidir.",
                "Doymuş yağdan fakir, lifden zengin diyet uygulanmalı; statin başta "
                "olmak üzere lipid düşürücü tedavi endikasyonu hekim tarafından "
                "değerlendirilmelidir.")),
            (200, Kural(
                SEVIYE_ORTA,
                "Total kolesterol {deger} ile sınırda yüksek kategorisindedir "
                "(200-239 mg/dl).",
                "Diyet ve düzenli egzersiz ile lipid değerlerinin ideal aralığa "
                "çekilmesi önerilir.")),
            (None, Kural(
                SEVIYE_NORMAL,
                "Total kolesterol istenen (<200 mg/dl) aralıktadır; lipid kaynaklı "
                "aterosklerotik yük düşüktür.")),
        ),
    ),

    # --- 5) Açlık kan şekeri -----------------------------------------------
    KategorikBulgu(
        anahtar="fbs", baslik="Açlık Kan Şekeri",
        secenekler=(
            (("true", "1", "1.0", "evet", "var"), "> 120 mg/dl", Kural(
                SEVIYE_YUKSEK,
                "Açlık plazma glukozu 120 mg/dl üzerindedir. Diyabet/pre-diyabet, mikro "
                "ve makrovasküler komplikasyonlar yoluyla koroner riski belirgin biçimde "
                "artıran majör bir faktördür.",
                "Glisemik regülasyon için endokrinoloji veya dahiliye konsültasyonu, "
                "HbA1c takibi ve rafine karbonhidrat kısıtlaması önerilir.")),
        ),
        varsayilan=("≤ 120 mg/dl", Kural(
            SEVIYE_NORMAL,
            "Açlık plazma glukozu normal sınırlardadır; diyabete bağlı ek vasküler risk "
            "saptanmamıştır.")),
    ),

    # --- 6) Göğüs ağrısı tipi ----------------------------------------------
    KategorikBulgu(
        anahtar="cp", baslik="Göğüs Ağrısı Tipi",
        secenekler=(
            (("typical angina", "0"), "Tipik Anjina", Kural(
                SEVIYE_YUKSEK,
                "Efor ile ortaya çıkan, istirahat veya nitrat ile gerileyen retrosternal "
                "basıcı ağrı tanımlanmıştır. Tipik anjina, obstrüktif koroner arter "
                "hastalığı için en yüksek pretest olasılığa sahip semptomdur.",
                "Tipik anjina tablosu nedeniyle iskemi araştırması (efor testi, koroner "
                "BT anjiyografi veya invaziv anjiyografi) planlanmalı; ağrının "
                "şiddetlenmesi hâlinde acile başvurulmalıdır.")),
            (("atypical angina", "1"), "Atipik Anjina", Kural(
                SEVIYE_ORTA,
                "Anjinal özelliklerin yalnızca bir kısmını taşıyan göğüs ağrısı mevcuttur; "
                "koroner arter hastalığı için orta düzeyde pretest olasılık taşır.",
                "Atipik anjina nedeniyle noninvaziv iskemi testi ile koroner arter "
                "hastalığı dışlanmalıdır.")),
            (("non-anginal", "non-anginal pain", "2"), "Anjinal Olmayan", Kural(
                SEVIYE_NORMAL,
                "Ağrı karakteri anjinal değildir; kaynağı büyük olasılıkla kardiyak "
                "dışıdır (muskuloskeletal, gastroözofageal reflü veya anksiyete kökenli).",
                "Kardiyak dışı ağrı nedenlerinin (reflü, kostokondrit, kas spazmı) ilgili "
                "branşlarca değerlendirilmesi uygun olur.")),
        ),
        varsayilan=("Semptomsuz (Asemptomatik)", Kural(
            SEVIYE_NORMAL,
            "Hastanın anjinal yakınması yoktur. Ancak özellikle diyabetiklerde sessiz "
            "(silent) iskemi olasılığı nedeniyle semptomsuzluk tek başına koroner "
            "hastalığı dışlamaz.")),
    ),

    # --- 7) Dinlenik EKG ----------------------------------------------------
    KategorikBulgu(
        anahtar="restecg", baslik="Dinlenik EKG",
        secenekler=(
            (("st-t abnormality", "1"), "ST-T Anormalliği", Kural(
                SEVIYE_YUKSEK,
                "Dinlenik EKG'de ST segment ve T dalga değişiklikleri izlenmektedir. Bu "
                "bulgu subendokardiyal miyokard iskemisi, geçirilmiş enfarktüs veya "
                "elektrolit dengesizliği lehine yorumlanır.",
                "ST-T değişikliklerinin etiyolojisi için ekokardiyografi ve 24 saatlik "
                "ritim Holter monitorizasyonu önerilir.")),
            (("lv hypertrophy", "2"), "Sol Ventrikül Hipertrofisi", Kural(
                SEVIYE_ORTA,
                "EKG'de sol ventrikül hipertrofisi voltaj kriterleri mevcuttur. Genellikle "
                "uzun süreli basınç yüküne (hipertansiyon) sekonder gelişir ve diyastolik "
                "disfonksiyonun habercisidir.",
                "Sol ventrikül kitlesindeki artışın gerilemesi için kan basıncı hedef "
                "değerlerde tutulmalı ve duvar kalınlıkları ekokardiyografi ile "
                "değerlendirilmelidir.")),
        ),
        varsayilan=("Normal", Kural(
            SEVIYE_NORMAL,
            "Dinlenik EKG'de iskemi, hipertrofi veya ileti kusuru lehine patolojik bulgu "
            "saptanmamıştır.")),
    ),

    # --- 8) Maksimum kalp hızı (kronotropik yanıt) -------------------------
    #
    # Tek türetilmiş bulgu: eşikler ham bpm değeriyle değil, yaşa göre
    # beklenen maksimumun (220 - yaş) YÜZDESİYLE karşılaştırılır. Bu yüzden
    # dusuk_kotu=True: düşük yüzde kötü habercidir.
    SayisalBulgu(
        anahtar="thalch", baslik="Maks. Kalp Atış Hızı", birim="bpm",
        dusuk_kotu=True,
        veri_yok=Kural(
            SEVIYE_VERIYOK,
            "Efor sırasında ulaşılan maksimum kalp hızı kaydedilmemiştir; kronotropik "
            "yeterlilik değerlendirilememiştir."),
        esikler=(
            (70, Kural(
                SEVIYE_YUKSEK,
                "Yaşa göre beklenen maksimum kalp hızının %70'ine dahi ulaşılamamıştır. "
                "Bu belirgin kronotropik yetersizlik, yaygın miyokardiyal iskemi veya "
                "azalmış kardiyak rezerv göstergesidir.",
                "Belirgin kronotropik yetersizlik nedeniyle miyokard perfüzyon "
                "sintigrafisi veya stres ekokardiyografi ile iskemik yükün belirlenmesi "
                "gereklidir.")),
            (85, Kural(
                SEVIYE_ORTA,
                "Efora kalp hızı yanıtı, yaşa göre beklenen maksimumun %85'inin altında "
                "kalmıştır; kronotropik yetersizlik şüphesi taşır (beta bloker kullanımı "
                "da bu tabloyu taklit edebilir).",
                "Kardiyak performansın efor testi ile daha ayrıntılı değerlendirilmesi "
                "önerilir.")),
            (None, Kural(
                SEVIYE_NORMAL,
                "Kronotropik yanıt yeterlidir; kalp hızı efora yaşa uygun biçimde "
                "yükselmektedir. Bu bulgu korunmuş kardiyak rezervi destekler.")),
        ),
    ),

    # --- 9) Egzersizle indüklenen anjina -----------------------------------
    KategorikBulgu(
        anahtar="exang", baslik="Egzersizle Anjina",
        secenekler=(
            (("true", "1", "1.0", "evet", "var"), "Var", Kural(
                SEVIYE_YUKSEK,
                "Efor sırasında göğüs ağrısı ortaya çıkmaktadır. Egzersizle indüklenen "
                "anjina, artan miyokardiyal oksijen ihtiyacının darlıklı koroner "
                "arterlerce karşılanamadığını gösteren güçlü bir iskemi bulgusudur.",
                "Ağır fiziksel efordan kaçınılmalı; efor testi veya miyokard perfüzyon "
                "sintigrafisi ile iskemi lokalizasyonu belirlenmelidir.")),
        ),
        varsayilan=("Yok", Kural(
            SEVIYE_NORMAL,
            "Efor sırasında anjinal yakınma tanımlanmamıştır; bu bulgu obstrüktif koroner "
            "darlık olasılığını azaltır.")),
    ),

    # --- 10) ST depresyonu (oldpeak) ---------------------------------------
    SayisalBulgu(
        anahtar="oldpeak", baslik="ST Depresyonu (Oldpeak)", birim="mm", ondalik=1,
        esikler=(
            (2.0, Kural(
                SEVIYE_YUKSEK,
                "Efor sonrası ST segment çökmesi {deger} ölçülmüştür. ≥2 mm "
                "horizontal/aşağı eğimli depresyon, hemodinamik olarak anlamlı "
                "miyokardiyal iskemi için yüksek özgüllüğe sahiptir.",
                "Belirgin iskemik ST çökmesi nedeniyle koroner anjiyografi planlanması "
                "için ivedilikle kardiyoloji değerlendirmesi gereklidir.")),
            (1.0, Kural(
                SEVIYE_ORTA,
                "Efor sonrası {deger} ST depresyonu saptanmıştır. Hafif-orta düzeydeki "
                "bu çökme miyokardiyal iskemi şüphesi uyandırır.",
                "Klinik izlem sürdürülmeli; yakınmalarda artış olması hâlinde ileri "
                "iskemi tetkiki yapılmalıdır.")),
            (None, Kural(
                SEVIYE_NORMAL,
                "Efor sonrası anlamlı ST segment çökmesi izlenmemiştir; iskemiye işaret "
                "eden repolarizasyon kusuru saptanmamıştır.")),
        ),
    ),

    # --- 11) ST segment eğimi (slope) --------------------------------------
    KategorikBulgu(
        anahtar="slope", baslik="ST Segment Eğimi",
        secenekler=(
            (("downsloping", "2"), "Aşağı Eğimli (Downsloping)", Kural(
                SEVIYE_YUKSEK,
                "Efor sırasında aşağı eğimli ST depresyonu izlenmiştir. Bu patern, iskemi "
                "için en yüksek tanısal değere sahip olan ve çok damar hastalığı ile "
                "ilişkilendirilen bulgudur.",
                "Aşağı eğimli ST paterni çok damar hastalığı ile ilişkili olduğundan "
                "koroner anatominin görüntülenmesi önerilir.")),
            (("flat", "1"), "Düz (Horizontal)", Kural(
                SEVIYE_ORTA,
                "Efor sırasında horizontal (düz) ST segment paterni izlenmiştir; bu "
                "görünüm iskemi lehine anlamlı kabul edilir.")),
        ),
        varsayilan=("Yukarı Eğimli (Upsloping)", Kural(
            SEVIYE_NORMAL,
            "Yukarı eğimli ST paterni izlenmektedir; bu görünüm genellikle fizyolojik "
            "kabul edilir ve iskemi için düşük tanısal değer taşır.")),
    ),

    # --- 12) Floroskopide boyanan majör damar sayısı -----------------------
    SayisalBulgu(
        anahtar="ca", baslik="Tutulan Majör Damar Sayısı (CA)", birim="damar",
        esikler=(
            (2, Kural(
                SEVIYE_YUKSEK,
                "Floroskopide {deger} düzeyinde kalsifikasyon/darlık izlenmektedir. Çok "
                "damar hastalığı, sol ventrikül fonksiyonu ve prognoz açısından yüksek "
                "riskli bir tablodur.",
                "Çok damar tutulumu nedeniyle revaskülarizasyon (perkütan girişim veya "
                "koroner by-pass) seçenekleri kalp ekibi tarafından değerlendirilmelidir.")),
            (1, Kural(
                SEVIYE_ORTA,
                "Bir majör koroner arterde kalsifikasyon/darlık saptanmıştır; tek damar "
                "hastalığı lehine bulgudur.",
                "Saptanan koroner lezyonun hemodinamik anlamlılığı değerlendirilmeli ve "
                "agresif medikal tedavi (antiagregan, statin) planlanmalıdır.")),
            (None, Kural(
                SEVIYE_NORMAL,
                "Floroskopide majör koroner arterlerde anlamlı darlık veya kalsifikasyon "
                "saptanmamıştır; obstrüktif koroner hastalık olasılığı düşüktür.")),
        ),
    ),

    # --- 13) Miyokard perfüzyonu (thal) ------------------------------------
    KategorikBulgu(
        anahtar="thal", baslik="Miyokard Perfüzyonu (Thal)",
        secenekler=(
            (("reversable defect", "reversible defect", "3"), "Geri Dönüşümlü Defekt", Kural(
                SEVIYE_YUKSEK,
                "Perfüzyon sintigrafisinde efor ile ortaya çıkıp istirahatte düzelen "
                "defekt izlenmiştir. Bu bulgu, canlı ancak kanlanması yetersiz (iskemik "
                "fakat viabl) miyokard dokusunu gösterir ve revaskülarizasyondan en çok "
                "fayda görecek hasta grubunu tanımlar.",
                "Geri dönüşümlü perfüzyon defekti saptandığından, iskemik alanın "
                "revaskülarizasyonu için vakit kaybetmeden kardiyoloji değerlendirmesi "
                "gereklidir.")),
            (("fixed defect", "2"), "Sabit Defekt", Kural(
                SEVIYE_YUKSEK,
                "Hem efor hem istirahat görüntülerinde devam eden sabit perfüzyon defekti "
                "mevcuttur. Bu görünüm geçirilmiş miyokard enfarktüsüne bağlı kalıcı skar "
                "dokusu ile uyumludur.",
                "Skar dokusunun sol ventrikül ejeksiyon fraksiyonuna etkisi "
                "ekokardiyografi ile ölçülmeli ve sekonder korunma tedavisi "
                "düzenlenmelidir.")),
        ),
        varsayilan=("Normal", Kural(
            SEVIYE_NORMAL,
            "Miyokardın tüm duvarlarında perfüzyon homojen ve normaldir; iskemi veya skar "
            "lehine defekt izlenmemiştir.")),
    ),
)

# Genel tavsiyeler: her raporun sonuna eklenir.
TAVSIYE_SAGLIKLI_YASAM = (
    "Mevcut bulgular normal sınırlardadır. Akdeniz tipi beslenme, haftada en az 150 "
    "dakika orta şiddette aerobik egzersiz ve sigaradan uzak durulması ile "
    "kardiyovasküler sağlığın korunması önerilir."
)
TAVSIYE_YASAL_UYARI = (
    "Bu değerlendirme bir karar destek çıktısıdır; nihai tanı, tetkik ve tedavi planı "
    "hastayı muayene eden hekim tarafından belirlenmelidir."
)


# --- Yardımcılar ------------------------------------------------------------


def oran_bicimle(risk: float) -> str:
    """Risk oranını TEK bir biçimde metne çevirir.

    Ekranda "%87.00", rapor metninde "%87" görünmesinin sebebi iki ayrı
    biçimlendirmeydi. Artık gösterim yapan her yer bu fonksiyonu çağırır.
    """
    return f"%{float(risk):.1f}"


def _sayi(veri: dict, anahtar: str) -> float:
    try:
        return float(veri.get(anahtar, 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _metin(veri: dict, anahtar: str) -> str:
    return str(veri.get(anahtar, "") or "").strip().lower()


def _sayisal_degerlendir(tanim: SayisalBulgu, veri: dict) -> tuple[str, Kural]:
    ham = _sayi(veri, tanim.anahtar)

    if tanim.veri_yok is not None and ham <= 0:
        return "Ölçüm yok", tanim.veri_yok

    if tanim.dusuk_kotu:
        # Kalp hızı: eşikler yaşa göre beklenen maksimumun yüzdesidir.
        beklenen = max(1.0, 220.0 - _sayi(veri, "age"))
        karsilastirilan = ham / beklenen * 100
        gosterim = f"{ham:.0f} {tanim.birim} (beklenenin %{karsilastirilan:.0f}'i)"
    else:
        karsilastirilan = ham
        gosterim = f"{ham:.{tanim.ondalik}f} {tanim.birim}"

    for sinir, kural in tanim.esikler:
        if sinir is None:
            return gosterim, kural
        if (karsilastirilan < sinir) if tanim.dusuk_kotu else (karsilastirilan >= sinir):
            return gosterim, kural

    # Tabloda son çare kuralı yoksa buraya düşülür; tanım hatasıdır.
    raise ValueError(f"{tanim.anahtar}: eşik tablosunda son çare kuralı (None) eksik.")


def _kategorik_degerlendir(tanim: KategorikBulgu, veri: dict) -> tuple[str, Kural]:
    deger = _metin(veri, tanim.anahtar)
    for eslesenler, gosterim, kural in tanim.secenekler:
        if deger in eslesenler:
            return gosterim, kural
    return tanim.varsayilan


def bulgulari_degerlendir(veri: dict) -> tuple[list[dict], list[str]]:
    """Tabloyu dolaşıp bulgu listesi ve tekrarsız tavsiye listesi üretir."""
    bulgular: list[dict] = []
    tavsiyeler: list[str] = []

    for tanim in BULGU_TANIMLARI:
        if isinstance(tanim, SayisalBulgu):
            gosterim, kural = _sayisal_degerlendir(tanim, veri)
        else:
            gosterim, kural = _kategorik_degerlendir(tanim, veri)

        bulgular.append({
            "baslik": tanim.baslik,
            "deger": gosterim,
            "seviye": kural.seviye,
            "yorum": kural.yorum.format(deger=gosterim),
        })

        if kural.tavsiye and kural.tavsiye not in tavsiyeler:
            tavsiyeler.append(kural.tavsiye)

    if not tavsiyeler:
        tavsiyeler.append(TAVSIYE_SAGLIKLI_YASAM)
    tavsiyeler.append(TAVSIYE_YASAL_UYARI)

    return bulgular, tavsiyeler


def _gerekce_yaz(bulgular: list[dict], teshis: str, risk: float) -> str:
    """"Neden hasta / neden sağlıklı?" paragrafını kurar."""
    oran = oran_bicimle(risk)

    def liste(secilenler):
        return ", ".join(f"{b['baslik']} ({b['deger']})" for b in secilenler)

    yuksekler = [b for b in bulgular if b["seviye"] == SEVIYE_YUKSEK]
    ortalar = [b for b in bulgular if b["seviye"] == SEVIYE_ORTA]
    normaller = [b for b in bulgular if b["seviye"] == SEVIYE_NORMAL]

    parcalar = []

    if riskli_mi(teshis):
        parcalar.append(
            f"Yapay zeka modeli, hastanın klinik bulgularını {oran} olasılıkla koroner "
            "arter hastalığı lehine değerlendirmiş ve hastayı 'Riskli' sınıfına "
            "yerleştirmiştir.")
        if yuksekler:
            parcalar.append(
                f"Bu kararı belirleyen başlıca patolojik bulgular şunlardır: "
                f"{liste(yuksekler)}. Söz konusu parametreler, miyokardın oksijen "
                "ihtiyacı ile koroner kan akımı arasındaki dengenin bozulduğunu "
                "(miyokardiyal iskemi) ve aterosklerotik plak yükünün arttığını "
                "düşündürmektedir.")
        if ortalar:
            parcalar.append(
                f"Ayrıca {liste(ortalar)} bulguları sınırda/şüpheli düzeyde olup toplam "
                "kardiyovasküler risk yükünü artırmaktadır.")
        if normaller:
            parcalar.append(
                f"Buna karşılık {liste(normaller)} referans aralığındadır; bu bulgular "
                "prognozu olumlu yönde etkilese de yukarıdaki patolojik bulguları "
                "dengelemeye yetmemiştir.")
        parcalar.append(
            "Özetle hasta, mevcut bulgu kombinasyonu nedeniyle obstrüktif koroner arter "
            "hastalığı açısından yüksek olasılıklı kabul edilmiş; ileri kardiyolojik "
            "tetkik ve tedavi planlaması önerilmiştir.")
    else:
        parcalar.append(
            "Yapay zeka modeli hastayı 'Sağlıklı' sınıfına yerleştirmiş, koroner arter "
            f"hastalığı olasılığını {oran} düzeyinde düşük bulmuştur.")
        if normaller:
            parcalar.append(
                f"Bu kararı destekleyen normal bulgular şunlardır: {liste(normaller)}. "
                "Bu parametreler, miyokard perfüzyonunun korunduğunu, efora kardiyak "
                "yanıtın yeterli olduğunu ve iskemi lehine repolarizasyon kusuru "
                "bulunmadığını göstermektedir.")
        if yuksekler or ortalar:
            parcalar.append(
                f"Bununla birlikte {liste(yuksekler + ortalar)} bulguları risk artırıcı "
                "yöndedir; bu nedenle hasta düşük riskli kabul edilse de bu "
                "parametrelerin izlemi ve modifiye edilebilir olanların (kan basıncı, "
                "lipid profili, glisemi) kontrol altına alınması sekonder korunma "
                "açısından önemlidir.")
        else:
            parcalar.append("Risk artırıcı anlamlı bir patolojik bulgu saptanmamıştır.")
        parcalar.append(
            "Bu değerlendirme mevcut bulgularla sınırlıdır; yeni gelişen göğüs ağrısı, "
            "nefes darlığı veya efor kapasitesinde azalma durumunda hasta yeniden "
            "değerlendirilmelidir.")

    return " ".join(parcalar)


def klinik_analiz_uret(veri: dict, teshis: str, risk: float,
                       ad: str = "", soyad: str = "") -> dict:
    """Tam klinik değerlendirmeyi üretir.

    veri : ön işlemeden geçmiş klinik bulgular (MODELİN GÖRDÜĞÜ değerler).
           Daha önce buraya ham istek gövdesi veriliyordu; model normalize
           edilmiş veriyle tahmin yaparken gerekçe metni ham veriyi
           anlatıyordu ve ikisi ayrışabiliyordu.
    teshis: "Sağlıklı" | "Riskli (Hasta Olabilir)"
    risk  : 0-100 arası sayı

    Döndürdüğü sözlük:
        teshis      model kararı
        risk        sayı olarak risk oranı
        risk_metni  gösterime hazır biçim ("%87.0")
        gerekce     "neden bu sonuç?" paragrafı
        bulgular    [{baslik, deger, seviye, yorum}]
        tavsiyeler  [str]
        metin       ekranda gösterilen düz metin rapor
    """
    bulgular, tavsiyeler = bulgulari_degerlendir(veri)
    gerekce = _gerekce_yaz(bulgular, teshis, risk)
    risk_metni = oran_bicimle(risk)

    satirlar = [
        "Klinik Değerlendirme Raporu",
        f"Hasta: {ad} {soyad}".strip(),
        f"Değerlendirme Tarihi: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        "",
        f"Yapay Zeka Modeli Tahmini: {teshis}",
        f"Hesaplanan Risk Oranı: {risk_metni}",
        "",
        "Klinik Gerekçe (Neden bu sonuç?):",
        gerekce,
        "",
        "Bulgu Bazlı Analiz:",
        *[f"- [{SEVIYE_ETIKETI[b['seviye']]}] {b['baslik']}: {b['deger']} - {b['yorum']}"
          for b in bulgular],
        "",
        "Klinik Tavsiyeler:",
        *[f"- {t}" for t in tavsiyeler],
    ]

    return {
        "teshis": teshis,
        "risk": risk,
        "risk_metni": risk_metni,
        "gerekce": gerekce,
        "bulgular": bulgular,
        "tavsiyeler": tavsiyeler,
        "metin": "\n".join(satirlar),
    }


__all__ = [
    "SEVIYE_YUKSEK", "SEVIYE_ORTA", "SEVIYE_NORMAL", "SEVIYE_VERIYOK",
    "SEVIYE_ETIKETI", "BULGU_TANIMLARI", "TESHIS_RISKLI", "TESHIS_SAGLIKLI",
    "bulgulari_degerlendir", "klinik_analiz_uret", "oran_bicimle",
]
