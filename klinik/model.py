"""Eğitilmiş boru hattının yüklenmesi, tahmin ve başarım metrikleri.

Ön işleme adımları (doldurma, kodlama, ölçekleme) model.pkl içindeki
Pipeline nesnesinin PARÇASIDIR. Bu modül onları elle tekrar uygulamaz;
eskiden aynı zincir üç ayrı yerde kopyalanmıştı ve üçü birbirinden
ayrışmıştı.
"""

import logging
import os
import threading

import joblib
import pandas as pd
from sklearn.metrics import (
    accuracy_score, confusion_matrix, f1_score, precision_score, recall_score,
)
from sklearn.model_selection import train_test_split

from klinik.ozellikler import (
    TESHIS_RISKLI, TESHIS_SAGLIKLI, TUM_SUTUNLAR, veri_seti_oku,
)

logger = logging.getLogger("klinik_ai.model")

PROJE_KOKU = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_YOLU = os.path.join(PROJE_KOKU, "model.pkl")
VERI_YOLU = os.path.join(PROJE_KOKU, "heart_disease_uci.csv")

# model_egit.py ile AYNI olmak ZORUNDA. Farklı olursa metrikler modelin
# eğitimde gördüğü satırlar üzerinden hesaplanır ve şişer.
RASTGELELIK = 42
TEST_ORANI = 0.2

# Özellik adlarının Türkçe karşılıkları (XAI açıklaması için).
TURKCE_AD = {
    "age": "Yaş", "sex": "Cinsiyet", "cp": "Göğüs Ağrısı Tipi",
    "trestbps": "Tansiyon", "chol": "Kolesterol", "fbs": "Açlık Kan Şekeri",
    "restecg": "EKG Sonucu", "thalch": "Maks. Kalp Atış Hızı",
    "exang": "Egzersiz Ağrısı", "oldpeak": "ST Depresyonu",
    "slope": "Eğim", "ca": "Renkli Damar Sayısı (CA)", "thal": "Talasemi",
}


def _model_yukle():
    if not os.path.exists(MODEL_YOLU):
        raise RuntimeError(
            f"Model dosyasi bulunamadi: {MODEL_YOLU}\n"
            "Once 'python model_egit.py' calistirin."
        )
    return joblib.load(MODEL_YOLU)


model = _model_yukle()

# Tahminde belirlenimcilik (determinism).
#
# Model n_jobs=-1 ile eğitildi; bu ayar dosyaya da yazılır. Tahmin sırasında
# ağaçların oyları birden çok iş parçacığında PAYLAŞILAN bir diziye toplanır
# ve toplama sırası çalıştırmadan çalıştırmaya değişir. Sonuç kayan nokta
# düzeyinde oynar: aynı hasta için bir kez 76.31210826210827, bir kez
# ...828 çıkabilir. Klinik bir kayıtta aynı girdinin aynı çıktıyı vermesi
# beklenir, ayrıca tek satırlık tahminde 300 ağacı paralelleştirmenin
# kazancı yoktur; tek iş parçacığı hem daha hızlı hem tekrarlanabilirdir.
model.named_steps["sinif"].n_jobs = 1

# ColumnTransformer sütunları [sayısal..., kategorik...] sırasına dizer.
# Etki skorlarını doğru özellikle eşleştirmek için bu sırayı modelin
# kendisinden okuruz; elle yazılmış bir liste sessizce kayabilirdi.
OZELLIK_SIRASI = [
    ad.split("__", 1)[-1]
    for ad in model.named_steps["on_isleme"].get_feature_names_out()
]


def tahmin_et(bulgular: dict) -> tuple[str, float, str]:
    """Bulgulardan teşhis, risk oranı ve XAI açıklaması üretir.

    Dönen değerler:
        teshis      "Sağlıklı" veya "Riskli (Hasta Olabilir)"
        risk        0-100 arası SAYI (metin değil; biçimlendirme gösterim
                    katmanının işidir - ekranda %87.00, raporda %87 yazması
                    bu ayrımın yapılmamasından kaynaklanıyordu)
        aciklama    en belirleyici iki bulguyu anlatan cümle
    """
    df = pd.DataFrame([{sutun: bulgular[sutun] for sutun in TUM_SUTUNLAR}])

    sinif = int(model.predict(df)[0])
    # Ondalık: oy oranının 15 basamağını saklamak sahte hassasiyettir;
    # gösterimde zaten tek ondalık kullanılıyor.
    risk = round(float(model.predict_proba(df)[0][1]) * 100, 2)
    teshis = TESHIS_RISKLI if sinif == 1 else TESHIS_SAGLIKLI

    # XAI: |ölçeklenmiş değer| x özellik önemi. Ölçekleme adımı boru
    # hattının içinde olduğu için ara çıktıyı oradan alıyoruz.
    olcekli = model[:-1].transform(df)[0]
    onem = model.named_steps["sinif"].feature_importances_
    etkiler = sorted(
        zip(OZELLIK_SIRASI, abs(olcekli) * onem),
        key=lambda ikili: ikili[1],
        reverse=True,
    )
    ilk, ikinci = (TURKCE_AD.get(ad, ad) for ad, _ in etkiler[:2])
    aciklama = (
        f"Bu kararın verilmesinde hastadaki en belirleyici klinik bulgular "
        f"'{ilk}' ve '{ikinci}' değerleri olmuştur."
    )

    return teshis, risk, aciklama


# --- Başarım metrikleri -----------------------------------------------------

_onbellek: dict | None = None
_onbellek_anahtari: tuple | None = None
_kilit = threading.Lock()


def metrikler() -> dict:
    """Test kümesi üzerinde hesaplanmış başarım metriklerini döndürür.

    Önbellek model.pkl'in DEĞİŞİM ZAMANINA bağlıdır. Eskiden önbellek bir
    kez dolduktan sonra süreç ömrü boyunca sabit kalıyordu; model yeniden
    eğitildiğinde /egitim sayfası sunucu yeniden başlatılana kadar eski
    modelin rakamlarını göstermeye devam ediyordu.
    """
    global _onbellek, _onbellek_anahtari

    anahtar = (os.path.getmtime(MODEL_YOLU), os.path.getmtime(VERI_YOLU))
    if _onbellek is not None and _onbellek_anahtari == anahtar:
        return _onbellek

    with _kilit:
        # Kilidi beklerken başka bir iş parçacığı hesaplamış olabilir.
        if _onbellek is not None and _onbellek_anahtari == anahtar:
            return _onbellek

        X, y = veri_seti_oku(VERI_YOLU)
        _, X_test, _, y_test = train_test_split(
            X, y, test_size=TEST_ORANI, random_state=RASTGELELIK, stratify=y
        )
        y_pred = model.predict(X_test)
        tn, fp, fn, tp = (int(v) for v in confusion_matrix(y_test, y_pred).ravel())

        _onbellek = {
            "accuracy": round(float(accuracy_score(y_test, y_pred)) * 100, 2),
            "precision": round(float(precision_score(y_test, y_pred)) * 100, 2),
            "recall": round(float(recall_score(y_test, y_pred)) * 100, 2),
            "f1_score": round(float(f1_score(y_test, y_pred)) * 100, 2),
            "confusion_matrix": {"tn": tn, "fp": fp, "fn": fn, "tp": tp},
        }
        _onbellek_anahtari = anahtar
        logger.info("Model metrikleri hesaplandi: %s", _onbellek)
        return _onbellek
