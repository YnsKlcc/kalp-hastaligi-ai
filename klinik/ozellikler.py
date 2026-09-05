"""Veri sözleşmesi: sütunlar, izin verilen değerler ve istek şemaları.

Bu modül projenin TEK GERÇEK KAYNAĞIDIR. Eğitim betiği (model_egit.py),
tahmin boru hattı (klinik.model) ve API doğrulaması (main.py) aynı sabitleri
buradan okur. Böylece "form şu değeri gönderiyor ama model başka bir değer
bekliyor" türü sessiz uyuşmazlıklar yapısal olarak imkânsız hale gelir.

Yeni bir klinik değişken eklendiğinde değiştirilmesi gereken tek yer burasıdır.
"""

from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field

# --- Model girdileri --------------------------------------------------------
#
# SIRA ÖNEMLİDİR: eğitim sırasında ColumnTransformer sütunları bu sırayla
# işler ve model bu sırayı bekler. Listeleri karıştırmak modeli sessizce
# yanlış tahmin üretir hale getirir.
SAYISAL_SUTUNLAR = ["age", "trestbps", "chol", "thalch", "oldpeak", "ca"]
KATEGORIK_SUTUNLAR = ["sex", "cp", "fbs", "restecg", "exang", "slope", "thal"]
TUM_SUTUNLAR = SAYISAL_SUTUNLAR + KATEGORIK_SUTUNLAR

HEDEF_SUTUN = "num"

# --- İzin verilen kategorik değerler ----------------------------------------
#
# Değerler heart_disease_uci.csv'deki ham etiketlerle BİREBİR aynıdır ve
# index.html'deki <select> seçenekleriyle eşleşir. Aşağıdaki Literal
# tanımları bu listeyi tekrar etmez; Pydantic doğrudan bunları kullanır.
CINSIYET = ("Male", "Female")
GOGUS_AGRISI = ("typical angina", "atypical angina", "non-anginal", "asymptomatic")
EVET_HAYIR = ("True", "False")
EKG = ("normal", "st-t abnormality", "lv hypertrophy")
EGIM = ("upsloping", "flat", "downsloping")
PERFUZYON = ("normal", "fixed defect", "reversable defect")

IZINLI_DEGERLER = {
    "sex": CINSIYET,
    "cp": GOGUS_AGRISI,
    "fbs": EVET_HAYIR,
    "restecg": EKG,
    "exang": EVET_HAYIR,
    "slope": EGIM,
    "thal": PERFUZYON,
}

# --- Teşhis etiketleri ------------------------------------------------------
#
# Bu iki dizgi veritabanına yazılır, PDF raporunda geçer ve geri bildirim
# ucunda doğrulanır. Elle tekrar yazılmamalıdır: riskliMi() kontrolü
# "Riskli" alt dizgisine bakar.
TESHIS_SAGLIKLI = "Sağlıklı"
TESHIS_RISKLI = "Riskli (Hasta Olabilir)"
TESHIS_SECENEKLERI = (TESHIS_SAGLIKLI, TESHIS_RISKLI)


def riskli_mi(teshis: object) -> bool:
    """Teşhis metninin riskli sınıfa ait olup olmadığını söyler."""
    return "Riskli" in str(teshis)


# --- İstek şemaları ---------------------------------------------------------


class HastaVerisi(BaseModel):
    """POST /tahmin gövdesi.

    KLİNİK ALANLARIN VARSAYILANI YOKTUR. Eskiden hepsinin varsayılan değeri
    vardı; bu durumda {"ad": "x", "soyad": "y"} gibi eksik bir gövde bile
    uydurulmuş bulgulardan tam yetkili görünen bir teşhis üretiyordu. Karar
    destek sisteminde eksik bulgu, sessizce doldurulacak bir boşluk değil,
    reddedilmesi gereken bir hatadır.

    Kategorik alanlar Literal ile kısıtlıdır: tanınmayan bir değer artık
    sessizce ilk sınıfa düşürülmez, 422 ile geri çevrilir.
    """

    ad: str = Field(min_length=1, max_length=80)
    soyad: str = Field(min_length=1, max_length=80)

    # Sayısal sınırlar index.html'deki min/max nitelikleriyle aynıdır;
    # arayüz kapatılıp doğrudan API çağrılsa bile aynı aralık geçerlidir.
    age: float = Field(ge=1, le=120)
    trestbps: float = Field(ge=50, le=260)
    chol: float = Field(ge=0, le=700)
    thalch: float = Field(ge=50, le=250)
    oldpeak: float = Field(ge=-3, le=10)
    ca: float = Field(ge=0, le=3)

    sex: Literal[CINSIYET]
    cp: Literal[GOGUS_AGRISI]
    fbs: Literal[EVET_HAYIR]
    restecg: Literal[EKG]
    exang: Literal[EVET_HAYIR]
    slope: Literal[EGIM]
    thal: Literal[PERFUZYON]

    def bulgular(self) -> dict:
        """Yalnızca modele girecek klinik alanları döndürür (ad/soyad hariç)."""
        return {sutun: getattr(self, sutun) for sutun in TUM_SUTUNLAR}


class GeriBildirim(BaseModel):
    """POST /geribildirim gövdesi.

    Bu uç kimlik doğrulaması istemez (analiz ekranındaki Doğru/Yanlış
    düğmeleri herkese açık sayfada yer alır). Bu nedenle serbest metin
    kabul edilmez: iki teşhis alanı da yalnızca bilinen iki etiketten biri
    olabilir. Aksi halde yeniden eğitim havuzu keyfi metinle doldurulabilirdi.
    """

    hasta_id: int = Field(gt=0)
    orijinal_teshis: Literal[TESHIS_SECENEKLERI]
    doktor_karari: Literal[TESHIS_SECENEKLERI]


# --- Veri seti okuma --------------------------------------------------------


def veri_seti_oku(csv_yolu: str) -> tuple[pd.DataFrame, pd.Series]:
    """Ham CSV'yi okuyup (X, y) olarak döndürür.

    Eğitim (model_egit.py) ve metrik hesabı (klinik.model) AYNI bu
    fonksiyonu çağırır. Daha önce iki yerde ayrı ayrı yazılmıştı ve
    zamanla birbirinden ayrışmıştı.

    İki normalizasyon yapılır:
      1) id/dataset sütunları atılır (hasta kimliği ve veri kaynağı,
         klinik bulgu değildir).
      2) fbs/exang sütunları CSV'de Python bool'u olarak durur; API ise
         "True"/"False" dizgileri gönderir. Eğitim ile tahmin aynı dili
         konuşsun diye burada dizgiye çevrilir.
    """
    df = pd.read_csv(csv_yolu)
    df = df.drop(columns=[s for s in ("id", "dataset") if s in df.columns])

    # Hedef: 0 = sağlıklı, 1-4 = hastalık var. İkili sınıflandırmaya indirilir.
    y = (df[HEDEF_SUTUN] > 0).astype(int)

    X = df.drop(columns=[HEDEF_SUTUN])
    for sutun in ("fbs", "exang"):
        # NaN'ler bu aşamada korunur; doldurmayı boru hattındaki imputer yapar.
        X[sutun] = X[sutun].map(lambda d: d if pd.isna(d) else str(d))

    return X[TUM_SUTUNLAR], y
