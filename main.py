import os
import joblib
import pandas as pd
import sqlite3
import secrets
import logging
from datetime import datetime
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
import warnings

warnings.filterwarnings('ignore')

# --- LOGLAMA ---
# Hata ayrıntıları yalnızca sunucu günlüğüne yazılır; istemciye asla gönderilmez.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("klinik_ai")

app = FastAPI(title="Klinik AI Asistan API", description="Yapay Zeka Destekli Kardiyoloji Asistanı")

current_directory = os.path.dirname(os.path.abspath(__file__))

# --- 0. CORS (Cross-Origin Resource Sharing) GÜVENLİK YAPILANDIRMASI ---
#
# Arayüz (HTML/CSS/JS) bu uygulamanın kendisi tarafından sunulduğu için
# istekler "same-origin"dir ve normal kullanımda CORS'a ihtiyaç duyulmaz.
# Bu nedenle güvenli varsayılan BOŞ listedir: dış kaynaklı hiçbir web
# sitesi tarayıcı üzerinden bu API'ye istek atamaz.
#
# İleride arayüz ayrı bir alan adına taşınırsa (örn. Vercel/Netlify),
# kod değiştirmeden ALLOWED_ORIGINS ortam değişkeni tanımlanması yeterlidir:
#     ALLOWED_ORIGINS=https://klinik.ornek.com,https://www.klinik.ornek.com
#
# GÜVENLİK UYARISI: allow_origins=["*"] kullanmayın. Hem hasta verisi
# içeren bu API'yi internetteki her siteye açar, hem de allow_credentials
# ile birlikte kullanıldığında tarayıcılar tarafından zaten reddedilir.
ALLOWED_ORIGINS = [
    kaynak.strip()
    for kaynak in os.getenv("ALLOWED_ORIGINS", "").split(",")
    if kaynak.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,   # varsayılan: [] (dışarıya tamamen kapalı)
    allow_credentials=False,         # çerez/kimlik bilgisi taşınmıyor
    allow_methods=["GET", "POST"],   # uygulamanın gerçekten kullandığı metotlar
    allow_headers=["Content-Type"],  # fetch() isteklerinin gönderdiği tek başlık
    max_age=600,
)

# --- 0.b KİMLİK DOĞRULAMA (HTTP Basic Auth) ---
#
# Hasta verisi döndüren sayfa ve API'ler bu doğrulamanın arkasındadır.
# Kullanıcı adı/şifre ortam değişkeninden okunur; tanımlı değilse
# geliştirme varsayılanına (admin / admin123) düşer.
#
# GÜVENLİK UYARISI: Varsayılan şifre yalnızca yerel geliştirme içindir.
# Buluta çıkmadan önce MUTLAKA güçlü bir değer tanımlayın:
#     ADMIN_KULLANICI=...   ADMIN_SIFRE=...
# Ayrıca HTTP Basic Auth kimlik bilgilerini yalnızca Base64 ile kodlar,
# şifrelemez. Bu nedenle uygulama üretimde MUTLAKA HTTPS üzerinden
# sunulmalıdır; aksi halde şifre ağ üzerinde açık metin gibi okunabilir.
guvenlik = HTTPBasic(realm="Klinik AI - Yetkili Personel")

ADMIN_KULLANICI = os.getenv("ADMIN_KULLANICI", "admin")
ADMIN_SIFRE = os.getenv("ADMIN_SIFRE", "admin123")

if ADMIN_SIFRE == "admin123":
    logger.warning(
        "Varsayilan yonetici sifresi kullaniliyor. Bulut dagitimindan once "
        "ADMIN_KULLANICI ve ADMIN_SIFRE ortam degiskenlerini tanimlayin."
    )


def yetki_dogrula(kimlik: HTTPBasicCredentials = Depends(guvenlik)) -> str:
    """Hasta verisine erişimi HTTP Basic Auth ile sınırlar.

    Karşılaştırmalar secrets.compare_digest ile yapılır; böylece yanıt
    süresinden şifre tahmin edilmesine dayanan zamanlama saldırıları önlenir.
    """
    kullanici_dogru = secrets.compare_digest(
        kimlik.username.encode("utf-8"), ADMIN_KULLANICI.encode("utf-8")
    )
    sifre_dogru = secrets.compare_digest(
        kimlik.password.encode("utf-8"), ADMIN_SIFRE.encode("utf-8")
    )

    if not (kullanici_dogru and sifre_dogru):
        logger.warning("Basarisiz giris denemesi. Kullanici adi: %r", kimlik.username)
        # Hangi alanın hatalı olduğu bilgisi bilinçli olarak verilmez.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Yetkisiz erişim. Geçerli kimlik bilgileri gerekiyor.",
            headers={"WWW-Authenticate": 'Basic realm="Klinik AI - Yetkili Personel"'},
        )

    return kimlik.username

# Ortak CSS/JS dosyaları (static/style.css, static/app.js) tarayıcıya buradan sunulur
app.mount("/static", StaticFiles(directory=os.path.join(current_directory, "static")), name="static")

# Makine Öğrenmesi Modellerini Yükleme
rf_model = joblib.load(os.path.join(current_directory, "rf_model.pkl"))
scaler = joblib.load(os.path.join(current_directory, "scaler.pkl"))
num_imputer = joblib.load(os.path.join(current_directory, "num_imputer.pkl"))
cat_imputer = joblib.load(os.path.join(current_directory, "cat_imputer.pkl"))
label_encoders = joblib.load(os.path.join(current_directory, "label_encoders.pkl"))


# --- 1. VERİTABANI HAZIRLIĞI ---
def veritabani_hazirla():
    conn = sqlite3.connect(os.path.join(current_directory, "klinik_kayitlar.db"))
    cursor = conn.cursor()

    # Hasta Geçmişi Tablosu
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS hasta_gecmisi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tarih TEXT, ad TEXT, soyad TEXT, yas REAL, cinsiyet TEXT,
            cp TEXT, tansiyon REAL, kolesterol REAL, fbs TEXT,
            restecg TEXT, thalch REAL, exang TEXT, oldpeak REAL,
            slope TEXT, ca REAL, thal TEXT,
            yapay_zeka_teshisi TEXT, risk_orani TEXT, aciklama TEXT
        )
    ''')

    # Yeniden Eğitim (Geri Bildirim) Tablosu
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS geribildirimler (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hasta_id INTEGER,
            orijinal_teshis TEXT,
            doktor_karari TEXT,
            tarih TEXT
        )
    ''')
    conn.commit()
    conn.close()


veritabani_hazirla()


# --- 2. VERİ ŞABLONLARI (Pydantic Models) ---
class HastaVerisi(BaseModel):
    ad: str
    soyad: str
    age: float = 55.0
    sex: str = "Male"
    cp: str = "typical angina"
    trestbps: float = 140.0
    chol: float = 240.0
    fbs: str = "False"
    restecg: str = "normal"
    thalch: float = 150.0
    exang: str = "False"
    oldpeak: float = 1.5
    slope: str = "flat"
    ca: float = 0.0
    thal: str = "normal"


class GeriBildirim(BaseModel):
    hasta_id: int
    orijinal_teshis: str
    doktor_karari: str


# --- 3. HTML SAYFA KÖPRÜLERİ ---
def sayfa_gonder(dosya_adi: str):
    """HTML sayfalarını, sunucunun başlatıldığı klasörden bağımsız olarak döndürür."""
    return FileResponse(os.path.join(current_directory, dosya_adi))


@app.get("/")
def ana_sayfa():
    return sayfa_gonder("index.html")


# KORUMALI: hasta kayıtlarını listeleyen sayfa
@app.get("/gecmis")
def gecmis_sayfasi(kullanici: str = Depends(yetki_dogrula)):
    return sayfa_gonder("kayitlar.html")


@app.get("/bilgi")
def bilgi_sayfasi():
    return sayfa_gonder("bilgi.html")


@app.get("/egitim")
def egitim_sayfasi():
    return sayfa_gonder("egitim.html")


# NOT: Aynı "/geribildirim" yolunda aşağıda bir @app.post da bulunuyor.
# FastAPI yönlendirmeyi yol + HTTP metodu ikilisine göre yaptığı için ikisi çakışmaz:
# GET  -> bu sayfayı açar, POST -> yeni geri bildirim kaydeder.
# KORUMALI: hasta adlarını içeren geri bildirim havuzu sayfası
@app.get("/geribildirim")
def geribildirim_sayfasi(kullanici: str = Depends(yetki_dogrula)):
    return sayfa_gonder("geribildirim.html")


# --- 4. VERİ ÇEKME KÖPRÜLERİ (API'ler) ---
# KORUMALI: ad, soyad ve tüm klinik bulguları döndürür
@app.get("/kayitlar")
def gecmis_kayitlari_getir(sayfa: int = 1, kullanici: str = Depends(yetki_dogrula)):
    limit = 10
    offset = (sayfa - 1) * limit
    conn = sqlite3.connect(os.path.join(current_directory, "klinik_kayitlar.db"))
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM hasta_gecmisi")
    toplam_sayfa = max(1, (cursor.fetchone()[0] + limit - 1) // limit)

    cursor.execute(
        "SELECT tarih, ad, soyad, yas, cinsiyet, cp, tansiyon, kolesterol, fbs, restecg, thalch, exang, oldpeak, slope, ca, thal, yapay_zeka_teshisi, risk_orani, aciklama, id FROM hasta_gecmisi ORDER BY id DESC LIMIT ? OFFSET ?",
        (limit, offset))
    kayitlar = cursor.fetchall()
    conn.close()

    return {"son_muayeneler": kayitlar, "toplam_sayfa": toplam_sayfa, "aktif_sayfa": sayfa}


# KORUMALI: hasta popülasyonundan türetilmiş istatistikler
# (korumalı /gecmis sayfası tarafından çağrılır; tarayıcı Basic Auth
#  bilgilerini aynı köken için otomatik olarak bu isteğe de ekler)
@app.get("/istatistik")
def istatistik_getir(kullanici: str = Depends(yetki_dogrula)):
    conn = sqlite3.connect(os.path.join(current_directory, "klinik_kayitlar.db"))
    cursor = conn.cursor()

    cursor.execute("SELECT yapay_zeka_teshisi, COUNT(*) FROM hasta_gecmisi GROUP BY yapay_zeka_teshisi")
    risk_dagilimi = cursor.fetchall()

    cursor.execute('''
        SELECT 
            CASE 
                WHEN yas < 40 THEN '40 Yaş Altı' 
                WHEN yas >= 40 AND yas <= 55 THEN '40-55 Yaş Arası' 
                ELSE '55 Yaş Üstü' 
            END as yas_grubu,
            SUM(CASE WHEN yapay_zeka_teshisi LIKE '%Riskli%' THEN 1 ELSE 0 END) as riskli_sayisi,
            SUM(CASE WHEN yapay_zeka_teshisi LIKE '%Sağlıklı%' THEN 1 ELSE 0 END) as saglikli_sayisi
        FROM hasta_gecmisi 
        GROUP BY yas_grubu 
        ORDER BY yas_grubu
    ''')
    yas_dagilimi = cursor.fetchall()
    conn.close()

    return {"risk_dagilimi": risk_dagilimi, "yas_dagilimi": yas_dagilimi}


# KORUMALI: hasta adları ile teşhis/düzeltme eşleşmelerini döndürür
@app.get("/geribildirim_listesi")
def geribildirim_listesi_getir(kullanici: str = Depends(yetki_dogrula)):
    conn = sqlite3.connect(os.path.join(current_directory, "klinik_kayitlar.db"))
    cursor = conn.cursor()
    cursor.execute('''
        SELECT g.tarih, h.ad, h.soyad, g.orijinal_teshis, g.doktor_karari
        FROM geribildirimler g
        LEFT JOIN hasta_gecmisi h ON g.hasta_id = h.id
        ORDER BY g.id DESC
    ''')
    kayitlar = cursor.fetchall()
    conn.close()
    return {"geribildirimler": kayitlar}


@app.get("/model_metrikleri")
def model_metrikleri_getir():
    try:
        csv_path = os.path.join(current_directory, "heart_disease_uci.csv")
        df = pd.read_csv(csv_path)
        if 'id' in df.columns and 'dataset' in df.columns:
            df = df.drop(columns=['id', 'dataset'])
        df['num'] = df['num'].apply(lambda x: 1 if x > 0 else 0)

        X = df.drop(columns=['num'])
        y = df['num']

        numeric_cols = num_imputer.feature_names_in_
        categorical_cols = cat_imputer.feature_names_in_

        X[numeric_cols] = num_imputer.transform(X[numeric_cols])
        X[categorical_cols] = cat_imputer.transform(X[categorical_cols])

        for col in categorical_cols:
            le = label_encoders[col]
            X[col] = le.transform(X[col])

        X_scaled = scaler.transform(X)

        from sklearn.model_selection import train_test_split
        from sklearn.metrics import confusion_matrix, accuracy_score, precision_score, recall_score, f1_score

        X_train, X_test, y_train, y_test = train_test_split(X_scaled, y, test_size=0.2, random_state=42)
        y_pred = rf_model.predict(X_test)

        acc = float(accuracy_score(y_test, y_pred)) * 100
        prec = float(precision_score(y_test, y_pred)) * 100
        rec = float(recall_score(y_test, y_pred)) * 100
        f1 = float(f1_score(y_test, y_pred)) * 100

        cm = confusion_matrix(y_test, y_pred)
        tn, fp, fn, tp = [int(v) for v in cm.ravel()]

        return {
            "accuracy": round(acc, 2),
            "precision": round(prec, 2),
            "recall": round(rec, 2),
            "f1_score": round(f1, 2),
            "confusion_matrix": {
                "tn": tn,
                "fp": fp,
                "fn": fn,
                "tp": tp
            }
        }
    except Exception:
        # Ayrıntılı hata (yığın izi dahil) yalnızca sunucu günlüğüne yazılır.
        logger.exception("Model metrikleri hesaplanirken hata olustu")
        return {"hata": "Model metrikleri şu anda hesaplanamıyor. Lütfen daha sonra tekrar deneyin."}


# --- 5. İŞLEM (ACTION) KÖPRÜLERİ ---

turkce_sozluk = {
    "age": "Yaş", "sex": "Cinsiyet", "cp": "Göğüs Ağrısı Tipi",
    "trestbps": "Tansiyon", "chol": "Kolesterol", "fbs": "Açlık Kan Şekeri",
    "restecg": "EKG Sonucu", "thalch": "Maks. Kalp Atış Hızı", "exang": "Egzersiz Ağrısı",
    "oldpeak": "ST Depresyonu", "slope": "Eğim", "ca": "Renkli Damar Sayısı (CA)", "thal": "Talasemi"
}


@app.post("/tahmin")
def tahmin_yap(hasta: HastaVerisi):
    try:
        df_yeni = pd.DataFrame([hasta.model_dump()])
        hasta_adi = hasta.ad
        hasta_soyadi = hasta.soyad
        df_yeni = df_yeni.drop(columns=['ad', 'soyad'])

        numeric_cols = num_imputer.feature_names_in_
        categorical_cols = cat_imputer.feature_names_in_

        df_yeni[numeric_cols] = num_imputer.transform(df_yeni[numeric_cols])
        df_yeni[categorical_cols] = cat_imputer.transform(df_yeni[categorical_cols])

        for col in categorical_cols:
            le = label_encoders[col]
            bilinen_siniflar = list(le.classes_)

            def guvenli_donusum(deger):
                for sinif in bilinen_siniflar:
                    if str(deger).lower().strip() == str(sinif).lower().strip():
                        return sinif
                return bilinen_siniflar[0]

            df_yeni[col] = df_yeni[col].apply(guvenli_donusum)
            df_yeni[col] = le.transform(df_yeni[col])

        df_yeni = df_yeni[scaler.feature_names_in_]
        X_scaled = scaler.transform(df_yeni)

        tahmin = rf_model.predict(X_scaled)[0]
        olasilik = rf_model.predict_proba(X_scaled)[0]
        sonuc_metni = "Riskli (Hasta Olabilir)" if tahmin == 1 else "Sağlıklı"
        risk_yuzdesi = f"%{olasilik[1] * 100:.2f}"

        onem_dereceleri = rf_model.feature_importances_
        hasta_degerleri = X_scaled[0]
        etki_skorlari = abs(hasta_degerleri) * onem_dereceleri
        etkiler = list(zip(scaler.feature_names_in_, etki_skorlari))
        etkiler.sort(key=lambda x: x[1], reverse=True)

        sebep_1 = turkce_sozluk.get(etkiler[0][0], etkiler[0][0])
        sebep_2 = turkce_sozluk.get(etkiler[1][0], etkiler[1][0])
        aciklama_metni = f"Bu kararın verilmesinde hastadaki en belirleyici klinik bulgular '{sebep_1}' ve '{sebep_2}' değerleri olmuştur."

        simdiki_zaman = datetime.now().strftime("%Y-%m-%d %H:%M")
        conn = sqlite3.connect(os.path.join(current_directory, "klinik_kayitlar.db"))
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO hasta_gecmisi (tarih, ad, soyad, yas, cinsiyet, cp, tansiyon, kolesterol, fbs, restecg, thalch, exang, oldpeak, slope, ca, thal, yapay_zeka_teshisi, risk_orani, aciklama)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (simdiki_zaman, hasta_adi, hasta_soyadi, hasta.age, hasta.sex, hasta.cp, hasta.trestbps, hasta.chol,
              hasta.fbs, hasta.restecg, hasta.thalch, hasta.exang, hasta.oldpeak, hasta.slope, hasta.ca, hasta.thal,
              sonuc_metni, risk_yuzdesi, aciklama_metni))

        hasta_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return {
            "Teşhis Sonucu": sonuc_metni,
            "Kalp Hastalığı Risk Oranı": risk_yuzdesi,
            "Yapay Zeka Açıklaması": aciklama_metni,
            "hasta_id": hasta_id
        }
    except Exception:
        # Hasta verisi ve iç yapı bilgisi istemciye sızmasın diye
        # ayrıntı yalnızca sunucu günlüğüne yazılır.
        logger.exception("Tahmin sirasinda hata olustu")
        return {"hata": "Analiz şu anda tamamlanamadı. Lütfen bulguları kontrol edip tekrar deneyin."}


@app.post("/geribildirim")
def geri_bildirim_kaydet(veri: GeriBildirim):
    conn = sqlite3.connect(os.path.join(current_directory, "klinik_kayitlar.db"))
    cursor = conn.cursor()
    simdiki_zaman = datetime.now().strftime("%Y-%m-%d %H:%M")
    cursor.execute('''
        INSERT INTO geribildirimler (hasta_id, orijinal_teshis, doktor_karari, tarih)
        VALUES (?, ?, ?, ?)
    ''', (veri.hasta_id, veri.orijinal_teshis, veri.doktor_karari, simdiki_zaman))
    conn.commit()
    conn.close()
    return {
        "mesaj": "Teşekkürler! Uzman görüşünüz (Müdahale) yapay zekanın gelecekteki eğitimi için veritabanına eklendi."}


# --- 6. SUNUCU BAŞLATMA ---
if __name__ == "__main__":
    import uvicorn

    # host="0.0.0.0": konteyner/bulut ortamında dışarıdan erişilebilmesi için
    # tüm ağ arayüzlerini dinler. ("127.0.0.1" yalnızca makinenin kendisinden
    # erişime izin verdiği için bulutta uygulamaya hiç ulaşılamaz.)
    #
    # PORT: Render, Railway, Heroku gibi platformlar dinlenecek portu bu ortam
    # değişkeniyle bildirir; yerel geliştirmede 8000'e düşer.
    sunucu_adresi = os.getenv("HOST", "0.0.0.0")
    sunucu_portu = int(os.getenv("PORT", 8000))

    logger.info("Klinik AI Asistan sunucusu baslatiliyor: http://%s:%s", sunucu_adresi, sunucu_portu)
    uvicorn.run(app, host=sunucu_adresi, port=sunucu_portu)
