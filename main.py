import os
import time
import hmac
import base64
import hashlib
import joblib
import pandas as pd
import sqlite3
import secrets
import logging
import warnings
from contextlib import closing, contextmanager
from datetime import datetime

from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from limits import parse as sinir_ayristir
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from sklearn.model_selection import train_test_split
from sklearn.metrics import (
    accuracy_score, confusion_matrix, f1_score, precision_score, recall_score,
)

from klinik import rapor

warnings.filterwarnings('ignore')

# --- ORTAM DEĞİŞKENLERİ (.env) ---
#
# Yönetici kimlik bilgileri, CORS listesi ve port ayarı kod içinde DEĞİL,
# depoya girmeyen .env dosyasında tutulur (.gitignore bu dosyayı dışlar).
# load_dotenv() buradan çağrılmalıdır: aşağıdaki her os.getenv() çağrısı
# bu satırdan SONRA çalıştığı için .env değerleri gerçekten okunur.
#
# Öncelik sırası: gerçek ortam değişkeni > .env dosyası > kod içi varsayılan.
# (override=False olduğu için sunucuda tanımlı bir değişkeni .env ezmez;
#  bulut platformlarının panelden verdiği değerler böylece korunur.)
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"), override=False)

# --- LOGLAMA ---
# Hata ayrıntıları yalnızca sunucu günlüğüne yazılır; istemciye asla gönderilmez.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("klinik_ai")

app = FastAPI(title="Klinik AI Asistan API", description="Yapay Zeka Destekli Kardiyoloji Asistanı")

# --- 0.a HIZ SINIRLAMA (Rate Limiting) ---
#
# Amaç iki yönlüdür:
#   1) Basic Auth şifresinin kaba kuvvetle (brute force) denenmesini önlemek.
#   2) Otomatik betiklerin veritabanını sahte muayeneyle doldurmasını veya
#      hasta kayıtlarını toplu olarak çekmesini (scraping) engellemek.
#
# Sınırlar IP başınadır ve aşağıda TEK YERDE tanımlanır; değiştirmek için
# yalnızca bu sabitleri düzenlemek yeterlidir.
#
# NEDEN HER UÇTA 5/dakika DEĞİL:
# Yazma işlemleri için 5/dakika doğru bir eşiktir; bir hekim dakikada beşten
# fazla muayene kaydetmez. Ancak okuma uçlarında aynı eşik arayüzü kırar:
# /gecmis sayfası açılışta /istatistik + /kayitlar çağırır ve sayfalama
# düğmelerinin her tıklaması bir /kayitlar isteği daha üretir; 5/dakika ile
# doktor dördüncü sayfada 429 alırdı. Bu nedenle okuma uçlarına, normal
# kullanımı engellemeyen ama toplu veri çekmeyi durduran daha geniş bir
# eşik uygulanır.
SINIR_YAZMA = os.getenv("SINIR_YAZMA", "5/minute")     # POST /tahmin, POST /geribildirim
SINIR_OKUMA = os.getenv("SINIR_OKUMA", "60/minute")    # korumalı sayfa ve listeleme uçları
SINIR_PDF = os.getenv("SINIR_PDF", "20/minute")        # PDF üretimi (CPU maliyetli)
SINIR_GIRIS = os.getenv("SINIR_GIRIS", "5/minute")     # BAŞARISIZ giriş denemeleri

# get_remote_address: istemci IP'sini request.client.host üzerinden okur.
# TERS VEKİL (Nginx, Render, Railway) ARKASINDA ÇALIŞTIRIRKEN: uvicorn
# --proxy-headers ile başlatılmalı ve FORWARDED_ALLOW_IPS ayarlanmalıdır;
# aksi halde tüm istekler vekilin tek IP'sinden geliyormuş gibi görünür ve
# tek bir kullanıcı herkesin kotasını tüketir.
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
def hiz_siniri_asildi(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Sınır aşıldığında net bir 429 yanıtı döndürür.

    Retry-After başlığı standarttır; istemcinin ne kadar bekleyeceğini bilmesini
    sağlar. Gövde, arayüzün diğer hataları çözümlediği biçimle ("hata" anahtarı)
    aynı tutulur, böylece app.js tarafında özel bir çözümleme gerekmez.
    """
    logger.warning("Hiz siniri asildi. IP: %s, yol: %s", get_remote_address(request), request.url.path)
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={
            "hata": "Çok fazla istek gönderildi. Güvenlik nedeniyle bu işlem "
                    "geçici olarak sınırlandırıldı; lütfen bir dakika bekleyip "
                    "tekrar deneyin.",
            "detail": f"Hız sınırı aşıldı ({exc.detail}).",
        },
        headers={"Retry-After": "60"},
    )

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
# KAPSAM: Koruma, uygulamanın tamamına değil, YALNIZCA BAŞKA HASTALARIN
# verisini okuyan uçlara uygulanır. Ürün demosu yapılabilsin diye analiz
# akışı herkese açıktır.
#
#   HERKESE AÇIK (hasta verisi listelemez)
#     GET  /                      muayene formu
#     GET  /bilgi  /egitim        bilgi sayfaları
#     GET  /model_metrikleri      toplu model başarım oranları
#     POST /tahmin                analiz yapar, sonucu yalnızca isteği yapana döner
#     POST /geribildirim          yalnızca yazar, veri döndürmez
#
#   KORUMALI (hasta adı ve klinik bulguları döndürür)
#     GET  /gecmis                hasta kayıtları sayfası
#     GET  /kayitlar              ^ sayfanın veri kaynağı
#     GET  /istatistik            ^ sayfanın grafik verisi
#     GET  /geribildirim          doktor geri bildirimleri sayfası
#     GET  /geribildirim_listesi  ^ sayfanın veri kaynağı (hasta adları içerir)
#     GET  /pdf-indir/{id}        tek bir hastanın tam klinik raporu
#
# /pdf-indir NEDEN KORUMALI: hasta_id sıralı bir tam sayıdır. Bu uç açık
# olsaydı /pdf-indir/1, /2, /3 ... denenerek veritabanındaki TÜM hasta
# raporları (ad, soyad, tüm bulgular) tek tek indirilebilirdi.
#
# Kimlik bilgileri KOD İÇİNDE TUTULMAZ. Kaynak: .env dosyası veya gerçek
# ortam değişkeni (yukarıdaki load_dotenv() ile okunur):
#     ADMIN_KULLANICI=...
#     ADMIN_SIFRE=...
#
# ADMIN_SIFRE tanımlı değilse sabit bir varsayılana DÜŞÜLMEZ; bunun yerine
# her açılışta rastgele tek kullanımlık bir şifre üretilip günlüğe yazılır.
# Böylece depoda hiçbir zaman gerçek bir şifre bulunmaz ve "varsayılan
# şifreyi değiştirmeyi unutma" riski yapısal olarak ortadan kalkar.
#
# GÜVENLİK UYARISI: HTTP Basic Auth kimlik bilgilerini yalnızca Base64 ile
# kodlar, ŞİFRELEMEZ. Uygulama üretimde MUTLAKA HTTPS üzerinden sunulmalıdır;
# aksi halde şifre ağ üzerinde açık metin gibi okunabilir.
guvenlik = HTTPBasic(realm="Klinik AI - Yetkili Personel")

# İsteğe bağlı sürüm: kimlik bilgisi YOKSA 401 fırlatmak yerine None döner.
# /pdf-indir çift kademeli izin kullandığı için gereklidir: önce jeton denenir,
# jeton yoksa/geçersizse Basic Auth'a düşülür.
guvenlik_istege_bagli = HTTPBasic(realm="Klinik AI - Yetkili Personel", auto_error=False)

ADMIN_KULLANICI = os.getenv("ADMIN_KULLANICI", "admin")
ADMIN_SIFRE = os.getenv("ADMIN_SIFRE", "")

if not ADMIN_SIFRE:
    # secrets.token_urlsafe: kriptografik olarak güvenli rastgele şifre.
    ADMIN_SIFRE = secrets.token_urlsafe(12)
    logger.warning(
        "ADMIN_SIFRE tanimli degil. Bu oturum icin gecici sifre uretildi: %s "
        "(kullanici: %s). Sunucu her yeniden baslatildiginda DEGISIR. Kalici "
        "hale getirmek icin .env dosyasina ADMIN_SIFRE=... yazin.",
        ADMIN_SIFRE, ADMIN_KULLANICI,
    )


# --- Kaba kuvvet (brute force) sayacı ---------------------------------------
#
# Şifre denemesi bir uç nokta değil, bir bağımlılık (yetki_dogrula) içinde
# doğrulandığı için slowapi'nin route dekoratörü buraya uygulanamaz. Bunun
# yerine slowapi'nin altında zaten bulunan "limits" kütüphanesi doğrudan
# kullanılır.
#
# Kritik ayrıntı: kotayı YALNIZCA BAŞARISIZ denemeler tüketir. Doğru şifreyle
# çalışan bir hekim sayfalar arasında ne kadar gezinirse gezinsin hiçbir zaman
# engellenmez; buna karşılık şifre deneyen bir saldırgan dakikada 5 denemeyle
# sınırlanır. (Bir dakikada tüm alfabeyi deneyen bir betik, bu sınırla
# pratikte işe yaramaz hale gelir.)
#
# MemoryStorage süreç belleğinde tutar: tek sunucu için yeterlidir, sunucu
# yeniden başlatıldığında sıfırlanır. Birden fazla worker/kopya ile
# çalışılacaksa ortak bir Redis deposuna geçilmelidir.
_giris_deposu = MemoryStorage()
_giris_sayaci = MovingWindowRateLimiter(_giris_deposu)
_GIRIS_SINIRI = sinir_ayristir(SINIR_GIRIS)


def yetki_dogrula(request: Request, kimlik: HTTPBasicCredentials = Depends(guvenlik)) -> str:
    """Hasta verisine erişimi HTTP Basic Auth ile sınırlar.

    İki katmanlı koruma:
      1) secrets.compare_digest -> yanıt süresinden şifre tahmin edilmesine
         dayanan zamanlama (timing) saldırılarını önler.
      2) Başarısız deneme sayacı -> aynı IP'den kaba kuvvetle şifre denenmesini
         dakikada SINIR_GIRIS adediyle sınırlar.
    """
    istemci_ip = get_remote_address(request)

    # Kota dolmuşsa şifre hiç karşılaştırılmadan reddedilir.
    if not _giris_sayaci.test(_GIRIS_SINIRI, "giris", istemci_ip):
        logger.warning("Kaba kuvvet suphesi: %s adresinden cok fazla basarisiz giris.", istemci_ip)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Çok fazla hatalı giriş denemesi yapıldı. Güvenlik nedeniyle "
                   "bu adres geçici olarak engellendi; lütfen bir dakika bekleyin.",
            headers={"Retry-After": "60"},
        )

    kullanici_dogru = secrets.compare_digest(
        kimlik.username.encode("utf-8"), ADMIN_KULLANICI.encode("utf-8")
    )
    sifre_dogru = secrets.compare_digest(
        kimlik.password.encode("utf-8"), ADMIN_SIFRE.encode("utf-8")
    )

    if not (kullanici_dogru and sifre_dogru):
        # Kota yalnızca burada, yani başarısızlıkta tüketilir.
        _giris_sayaci.hit(_GIRIS_SINIRI, "giris", istemci_ip)
        logger.warning(
            "Basarisiz giris denemesi. IP: %s, kullanici adi: %r", istemci_ip, kimlik.username
        )
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
DB_PATH = os.path.join(current_directory, "klinik_kayitlar.db")

# Başka bir istek yazma kilidini elinde tutuyorsa bu kadar saniye beklenir.
# Varsayılan 5 sn yerine 15 sn: eşzamanlı /tahmin istekleri altında
# "database is locked" hatası yerine kısa bir bekleme tercih edilir.
DB_TIMEOUT = 15.0


@contextmanager
def veritabani():
    """Tüm SQLite erişimleri için tek giriş noktası.

    Neden gerekli: sqlite3'te `with sqlite3.connect(...) as conn` bloğu
    YALNIZCA transaction'ı yönetir (başarıda commit, hatada rollback);
    bağlantının kendisini KAPATMAZ. Bağlantı çöp toplayıcı çalışana
    kadar açık kalır, dosya tanıtıcıları ve yazma kilitleri birikir ve
    çoklu istek altında sunucu "database is locked" ile kilitlenir.

    Buradaki iki katmanlı yapı bunu kesin olarak çözer:
      closing(...)  -> hata olsa da olmasa da bağlantıyı kapatır
      with conn     -> başarıda commit, istisnada rollback yapar

    Kullanım:
        with veritabani() as conn:
            cursor = conn.cursor()
            cursor.execute(...)
        # commit ve close çıkışta otomatiktir; elle conn.commit() gerekmez.
    """
    with closing(sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)) as conn:
        with conn:
            yield conn


def veritabani_hazirla():
    with veritabani() as conn:
        cursor = conn.cursor()

        # WAL (Write-Ahead Logging): okuyucular yazıcıyı, yazıcı da
        # okuyucuları bloklamaz. Bu ayar veritabanı dosyasına kalıcı
        # yazılır; yalnızca açılışta bir kez uygulanması yeterlidir.
        cursor.execute("PRAGMA journal_mode=WAL")

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


# HERKESE AÇIK: muayene formu (ürün demosu için kimlik doğrulaması istenmez).
# Sayfa hasta verisi listelemez; yalnızca boş bir form gösterir. Girilen
# bulgular POST /tahmin ile analiz edilir, sonuç yalnızca isteği yapana döner.
@app.get("/")
@limiter.limit(SINIR_OKUMA)
def ana_sayfa(request: Request):
    return sayfa_gonder("index.html")


# KORUMALI: hasta kayıtlarını listeleyen sayfa
@app.get("/gecmis")
@limiter.limit(SINIR_OKUMA)
def gecmis_sayfasi(request: Request, kullanici: str = Depends(yetki_dogrula)):
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
@limiter.limit(SINIR_OKUMA)
def geribildirim_sayfasi(request: Request, kullanici: str = Depends(yetki_dogrula)):
    return sayfa_gonder("geribildirim.html")


# --- 4. VERİ ÇEKME KÖPRÜLERİ (API'ler) ---
# KORUMALI: ad, soyad ve tüm klinik bulguları döndürür
@app.get("/kayitlar")
@limiter.limit(SINIR_OKUMA)
def gecmis_kayitlari_getir(request: Request, sayfa: int = 1, kullanici: str = Depends(yetki_dogrula)):
    limit = 10
    sayfa = max(1, sayfa)
    offset = (sayfa - 1) * limit

    with veritabani() as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM hasta_gecmisi")
        toplam_kayit = cursor.fetchone()[0]
        toplam_sayfa = max(1, (toplam_kayit + limit - 1) // limit)

        cursor.execute(
            "SELECT tarih, ad, soyad, yas, cinsiyet, cp, tansiyon, kolesterol, fbs, restecg, thalch, exang, oldpeak, slope, ca, thal, yapay_zeka_teshisi, risk_orani, aciklama, id FROM hasta_gecmisi ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset))
        kayitlar = cursor.fetchall()

    return {"son_muayeneler": kayitlar, "toplam_sayfa": toplam_sayfa, "aktif_sayfa": sayfa}

# KORUMALI: hasta popülasyonundan türetilmiş istatistikler
# (korumalı /gecmis sayfası tarafından çağrılır; tarayıcı Basic Auth
#  bilgilerini aynı köken için otomatik olarak bu isteğe de ekler)
@app.get("/istatistik")
@limiter.limit(SINIR_OKUMA)
def istatistik_getir(request: Request, kullanici: str = Depends(yetki_dogrula)):
    with veritabani() as conn:
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

    return {"risk_dagilimi": risk_dagilimi, "yas_dagilimi": yas_dagilimi}


# KORUMALI: hasta adları ile teşhis/düzeltme eşleşmelerini döndürür
@app.get("/geribildirim_listesi")
@limiter.limit(SINIR_OKUMA)
def geribildirim_listesi_getir(request: Request, kullanici: str = Depends(yetki_dogrula)):
    with veritabani() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT g.tarih, h.ad, h.soyad, g.orijinal_teshis, g.doktor_karari
            FROM geribildirimler g
            LEFT JOIN hasta_gecmisi h ON g.hasta_id = h.id
            ORDER BY g.id DESC
        ''')
        kayitlar = cursor.fetchall()
    return {"geribildirimler": kayitlar}


_model_metrikleri_onbellek = None


@app.get("/model_metrikleri")
@limiter.limit(SINIR_OKUMA)
def model_metrikleri_getir(request: Request):
    global _model_metrikleri_onbellek
    if _model_metrikleri_onbellek is not None:
        return _model_metrikleri_onbellek

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

        X = X[scaler.feature_names_in_]
        X_scaled = scaler.transform(X)

        X_train, X_test, y_train, y_test = train_test_split(X_scaled, y, test_size=0.2, random_state=42)
        y_pred = rf_model.predict(X_test)

        acc = float(accuracy_score(y_test, y_pred)) * 100
        prec = float(precision_score(y_test, y_pred)) * 100
        rec = float(recall_score(y_test, y_pred)) * 100
        f1 = float(f1_score(y_test, y_pred)) * 100

        cm = confusion_matrix(y_test, y_pred)
        tn, fp, fn, tp = [int(v) for v in cm.ravel()]

        sonuc = {
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
        _model_metrikleri_onbellek = sonuc
        return sonuc
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

# Bulgu ağırlık seviyeleri: PDF raporunda renk kodlaması için kullanılır.
SEVIYE_YUKSEK = "yuksek"     # Belirgin patolojik bulgu
SEVIYE_ORTA = "orta"         # Sınırda / şüpheli bulgu
SEVIYE_NORMAL = "normal"     # Referans aralığında
SEVIYE_VERIYOK = "veriyok"   # Ölçüm girilmemiş

SEVIYE_ETIKETI = {
    SEVIYE_YUKSEK: "PATOLOJİK",
    SEVIYE_ORTA: "SINIRDA",
    SEVIYE_NORMAL: "NORMAL",
    SEVIYE_VERIYOK: "VERİ YOK",
}


def klinik_analiz_uret(hasta_verisi: dict, tahmin: int, olasilik: float) -> dict:
    """
    Kural tabanlı klinik karar destek analizi üretir.

    Dönen sözlük:
      bulgular   : [{baslik, deger, seviye, yorum}] -> her parametrenin tıbbi yorumu
      tavsiyeler : [str]                            -> tekrarsız klinik öneriler
      gerekce    : str                              -> "Neden hasta / neden sağlıklı?" paragrafı
      metin      : str                              -> düz metin klinik rapor (ekranda gösterilen)

    hasta_verisi, /tahmin isteğindeki ham (İngilizce kodlu) değerleri içerir; aynı
    fonksiyon geçmiş kayıtlar için veritabanı satırından da yeniden çağrılabilir.
    """
    bulgular = []
    tavsiyeler = []

    def ekle(baslik, deger, seviye, yorum):
        bulgular.append({"baslik": baslik, "deger": deger, "seviye": seviye, "yorum": yorum})

    def oner(metin):
        if metin not in tavsiyeler:
            tavsiyeler.append(metin)

    def sayi(anahtar, varsayilan=0.0):
        try:
            return float(hasta_verisi.get(anahtar, varsayilan))
        except (TypeError, ValueError):
            return float(varsayilan)

    def metin_al(anahtar, varsayilan=""):
        return str(hasta_verisi.get(anahtar, varsayilan) or varsayilan).strip().lower()

    def evet_mi(anahtar):
        return metin_al(anahtar, "false") in ("true", "1", "1.0", "evet", "var")

    # --- 1) Yaş ------------------------------------------------------------
    age = sayi("age", 0)
    if age >= 65:
        ekle("Yaş", f"{int(age)} yaş", SEVIYE_YUKSEK,
             "İleri yaş (≥65), ateroskleroz için değiştirilemeyen bağımsız bir risk faktörüdür; yaşla "
             "birlikte arteriyel sertlik ve koroner plak yükü belirgin biçimde artar.")
        oner("İleri yaşa bağlı risk nedeniyle kardiyolojik kontroller yılda en az bir kez tekrarlanmalıdır.")
    elif age >= 45:
        ekle("Yaş", f"{int(age)} yaş", SEVIYE_ORTA,
             "Hasta orta yaş grubundadır (≥45). Bu dönem koroner arter hastalığı insidansının belirgin "
             "biçimde yükselmeye başladığı yaş aralığıdır.")
    else:
        ekle("Yaş", f"{int(age)} yaş", SEVIYE_NORMAL,
             "Genç yaş grubu. Yaş, mevcut tabloda kardiyovasküler riski artırıcı bir faktör oluşturmamaktadır.")

    # --- 2) Cinsiyet -------------------------------------------------------
    sex = metin_al("sex", "male")
    if sex in ("male", "erkek", "1"):
        ekle("Cinsiyet", "Erkek", SEVIYE_ORTA,
             "Erkek cinsiyet, koroner arter hastalığı için bağımsız bir risk faktörüdür; aterosklerotik "
             "olaylar erkeklerde ortalama on yıl daha erken ortaya çıkar.")
    else:
        ekle("Cinsiyet", "Kadın", SEVIYE_NORMAL,
             "Premenopozal dönemde östrojenin endotel koruyucu etkisi nedeniyle kadın cinsiyet, koroner "
             "olay riski açısından görece koruyucu kabul edilir.")

    # --- 3) Dinlenik kan basıncı -------------------------------------------
    trestbps = sayi("trestbps", 0)
    if trestbps >= 160:
        ekle("Dinlenik Kan Basıncı", f"{int(trestbps)} mmHg", SEVIYE_YUKSEK,
             f"Sistolik kan basıncı {int(trestbps)} mmHg olup Evre 2 hipertansiyon sınırındadır. Kronik "
             "basınç yükü sol ventrikül hipertrofisine ve endotel disfonksiyonuna yol açar.")
        oner("Antihipertansif tedavinin düzenlenmesi için kardiyoloji/dahiliye değerlendirmesi gereklidir; "
             "günlük tuz alımı 5 gramın altına indirilmelidir.")
    elif trestbps >= 140:
        ekle("Dinlenik Kan Basıncı", f"{int(trestbps)} mmHg", SEVIYE_YUKSEK,
             f"Sistolik kan basıncı {int(trestbps)} mmHg ile Evre 1 hipertansiyon eşiğinin üzerindedir; "
             "aterosklerotik süreci hızlandıran majör bir risk faktörüdür.")
        oner("Kan basıncı evde düzenli ölçülmeli, düşük sodyumlu (DASH tipi) diyet uygulanmalı ve medikal "
             "tedavi için hekime başvurulmalıdır.")
    elif trestbps >= 130:
        ekle("Dinlenik Kan Basıncı", f"{int(trestbps)} mmHg", SEVIYE_ORTA,
             f"Sistolik kan basıncı {int(trestbps)} mmHg ile yüksek normal (prehipertansiyon) kategorisindedir.")
        oner("Kilo kontrolü, düzenli aerobik egzersiz ve tuz kısıtlaması ile kan basıncının normotansif "
             "aralığa çekilmesi hedeflenmelidir.")
    else:
        ekle("Dinlenik Kan Basıncı", f"{int(trestbps)} mmHg", SEVIYE_NORMAL,
             "Sistolik kan basıncı normotansif referans aralığındadır; basınç kaynaklı ek kardiyak yük "
             "saptanmamıştır.")

    # --- 4) Serum kolesterol -----------------------------------------------
    chol = sayi("chol", 0)
    if chol <= 0:
        ekle("Serum Kolesterol", "Ölçüm yok", SEVIYE_VERIYOK,
             "Total kolesterol değeri girilmemiştir; lipid profili olmadan aterosklerotik risk tam olarak "
             "sınıflandırılamaz.")
        oner("Açlık lipid profili (total kolesterol, LDL, HDL, trigliserit) istenerek risk sınıflaması "
             "tamamlanmalıdır.")
    elif chol >= 240:
        ekle("Serum Kolesterol", f"{int(chol)} mg/dl", SEVIYE_YUKSEK,
             f"Total kolesterol {int(chol)} mg/dl ile yüksek risk grubundadır (≥240 mg/dl). Hiperlipidemi, "
             "koroner arterlerde aterom plağı gelişiminin temel tetikleyicisidir.")
        oner("Doymuş yağdan fakir, lifden zengin diyet uygulanmalı; statin başta olmak üzere lipid düşürücü "
             "tedavi endikasyonu hekim tarafından değerlendirilmelidir.")
    elif chol >= 200:
        ekle("Serum Kolesterol", f"{int(chol)} mg/dl", SEVIYE_ORTA,
             f"Total kolesterol {int(chol)} mg/dl ile sınırda yüksek kategorisindedir (200-239 mg/dl).")
        oner("Diyet ve düzenli egzersiz ile lipid değerlerinin ideal aralığa çekilmesi önerilir.")
    else:
        ekle("Serum Kolesterol", f"{int(chol)} mg/dl", SEVIYE_NORMAL,
             "Total kolesterol istenen (<200 mg/dl) aralıktadır; lipid kaynaklı aterosklerotik yük düşüktür.")

    # --- 5) Açlık kan şekeri -----------------------------------------------
    if evet_mi("fbs"):
        ekle("Açlık Kan Şekeri", "> 120 mg/dl", SEVIYE_YUKSEK,
             "Açlık plazma glukozu 120 mg/dl üzerindedir. Diyabet/pre-diyabet, mikro ve makrovasküler "
             "komplikasyonlar yoluyla koroner riski belirgin biçimde artıran majör bir faktördür.")
        oner("Glisemik regülasyon için endokrinoloji veya dahiliye konsültasyonu, HbA1c takibi ve rafine "
             "karbonhidrat kısıtlaması önerilir.")
    else:
        ekle("Açlık Kan Şekeri", "≤ 120 mg/dl", SEVIYE_NORMAL,
             "Açlık plazma glukozu normal sınırlardadır; diyabete bağlı ek vasküler risk saptanmamıştır.")

    # --- 6) Göğüs ağrısı tipi ----------------------------------------------
    cp = metin_al("cp")
    if cp in ("typical angina", "0"):
        ekle("Göğüs Ağrısı Tipi", "Tipik Anjina", SEVIYE_YUKSEK,
             "Efor ile ortaya çıkan, istirahat veya nitrat ile gerileyen retrosternal basıcı ağrı "
             "tanımlanmıştır. Tipik anjina, obstrüktif koroner arter hastalığı için en yüksek pretest "
             "olasılığa sahip semptomdur.")
        oner("Tipik anjina tablosu nedeniyle iskemi araştırması (efor testi, koroner BT anjiyografi veya "
             "invaziv anjiyografi) planlanmalı; ağrının şiddetlenmesi hâlinde acile başvurulmalıdır.")
    elif cp in ("atypical angina", "1"):
        ekle("Göğüs Ağrısı Tipi", "Atipik Anjina", SEVIYE_ORTA,
             "Anjinal özelliklerin yalnızca bir kısmını taşıyan göğüs ağrısı mevcuttur; koroner arter "
             "hastalığı için orta düzeyde pretest olasılık taşır.")
        oner("Atipik anjina nedeniyle noninvaziv iskemi testi ile koroner arter hastalığı dışlanmalıdır.")
    elif cp in ("non-anginal", "non-anginal pain", "2"):
        ekle("Göğüs Ağrısı Tipi", "Anjinal Olmayan", SEVIYE_NORMAL,
             "Ağrı karakteri anjinal değildir; kaynağı büyük olasılıkla kardiyak dışıdır (muskuloskeletal, "
             "gastroözofageal reflü veya anksiyete kökenli).")
        oner("Kardiyak dışı ağrı nedenlerinin (reflü, kostokondrit, kas spazmı) ilgili branşlarca "
             "değerlendirilmesi uygun olur.")
    else:
        ekle("Göğüs Ağrısı Tipi", "Semptomsuz (Asemptomatik)", SEVIYE_NORMAL,
             "Hastanın anjinal yakınması yoktur. Ancak özellikle diyabetiklerde sessiz (silent) iskemi "
             "olasılığı nedeniyle semptomsuzluk tek başına koroner hastalığı dışlamaz.")

    # --- 7) Dinlenik EKG ----------------------------------------------------
    restecg = metin_al("restecg")
    if restecg in ("st-t abnormality", "1"):
        ekle("Dinlenik EKG", "ST-T Anormalliği", SEVIYE_YUKSEK,
             "Dinlenik EKG'de ST segment ve T dalga değişiklikleri izlenmektedir. Bu bulgu subendokardiyal "
             "miyokard iskemisi, geçirilmiş enfarktüs veya elektrolit dengesizliği lehine yorumlanır.")
        oner("ST-T değişikliklerinin etiyolojisi için ekokardiyografi ve 24 saatlik ritim Holter "
             "monitorizasyonu önerilir.")
    elif restecg in ("lv hypertrophy", "2"):
        ekle("Dinlenik EKG", "Sol Ventrikül Hipertrofisi", SEVIYE_ORTA,
             "EKG'de sol ventrikül hipertrofisi voltaj kriterleri mevcuttur. Genellikle uzun süreli basınç "
             "yüküne (hipertansiyon) sekonder gelişir ve diyastolik disfonksiyonun habercisidir.")
        oner("Sol ventrikül kitlesindeki artışın gerilemesi için kan basıncı hedef değerlerde tutulmalı ve "
             "duvar kalınlıkları ekokardiyografi ile değerlendirilmelidir.")
    else:
        ekle("Dinlenik EKG", "Normal", SEVIYE_NORMAL,
             "Dinlenik EKG'de iskemi, hipertrofi veya ileti kusuru lehine patolojik bulgu saptanmamıştır.")

    # --- 8) Maksimum kalp hızı (kronotropik yanıt) -------------------------
    thalch = sayi("thalch", 0)
    beklenen_maks = max(1.0, 220.0 - age)
    yuzde = thalch / beklenen_maks * 100
    if thalch <= 0:
        ekle("Maks. Kalp Atış Hızı", "Ölçüm yok", SEVIYE_VERIYOK,
             "Efor sırasında ulaşılan maksimum kalp hızı kaydedilmemiştir; kronotropik yeterlilik "
             "değerlendirilememiştir.")
    elif yuzde < 70:
        ekle("Maks. Kalp Atış Hızı", f"{int(thalch)} bpm (beklenenin %{yuzde:.0f}'i)", SEVIYE_YUKSEK,
             "Yaşa göre beklenen maksimum kalp hızının %70'ine dahi ulaşılamamıştır. Bu belirgin "
             "kronotropik yetersizlik, yaygın miyokardiyal iskemi veya azalmış kardiyak rezerv göstergesidir.")
        oner("Belirgin kronotropik yetersizlik nedeniyle miyokard perfüzyon sintigrafisi veya stres "
             "ekokardiyografi ile iskemik yükün belirlenmesi gereklidir.")
    elif yuzde < 85:
        ekle("Maks. Kalp Atış Hızı", f"{int(thalch)} bpm (beklenenin %{yuzde:.0f}'i)", SEVIYE_ORTA,
             "Efora kalp hızı yanıtı, yaşa göre beklenen maksimumun %85'inin altında kalmıştır; kronotropik "
             "yetersizlik şüphesi taşır (beta bloker kullanımı da bu tabloyu taklit edebilir).")
        oner("Kardiyak performansın efor testi ile daha ayrıntılı değerlendirilmesi önerilir.")
    else:
        ekle("Maks. Kalp Atış Hızı", f"{int(thalch)} bpm (beklenenin %{yuzde:.0f}'i)", SEVIYE_NORMAL,
             "Kronotropik yanıt yeterlidir; kalp hızı efora yaşa uygun biçimde yükselmektedir. Bu bulgu "
             "korunmuş kardiyak rezervi destekler.")

    # --- 9) Egzersizle indüklenen anjina -----------------------------------
    if evet_mi("exang"):
        ekle("Egzersizle Anjina", "Var", SEVIYE_YUKSEK,
             "Efor sırasında göğüs ağrısı ortaya çıkmaktadır. Egzersizle indüklenen anjina, artan "
             "miyokardiyal oksijen ihtiyacının darlıklı koroner arterlerce karşılanamadığını gösteren güçlü "
             "bir iskemi bulgusudur.")
        oner("Ağır fiziksel efordan kaçınılmalı; efor testi veya miyokard perfüzyon sintigrafisi ile iskemi "
             "lokalizasyonu belirlenmelidir.")
    else:
        ekle("Egzersizle Anjina", "Yok", SEVIYE_NORMAL,
             "Efor sırasında anjinal yakınma tanımlanmamıştır; bu bulgu obstrüktif koroner darlık "
             "olasılığını azaltır.")

    # --- 10) ST depresyonu (oldpeak) ---------------------------------------
    oldpeak = sayi("oldpeak", 0)
    if oldpeak >= 2.0:
        ekle("ST Depresyonu (Oldpeak)", f"{oldpeak:.1f} mm", SEVIYE_YUKSEK,
             f"Efor sonrası ST segment çökmesi {oldpeak:.1f} mm'dir. ≥2 mm horizontal/aşağı eğimli "
             "depresyon, hemodinamik olarak anlamlı miyokardiyal iskemi için yüksek özgüllüğe sahiptir.")
        oner("Belirgin iskemik ST çökmesi nedeniyle koroner anjiyografi planlanması için ivedilikle "
             "kardiyoloji değerlendirmesi gereklidir.")
    elif oldpeak >= 1.0:
        ekle("ST Depresyonu (Oldpeak)", f"{oldpeak:.1f} mm", SEVIYE_ORTA,
             f"Efor sonrası {oldpeak:.1f} mm ST depresyonu saptanmıştır. Hafif-orta düzeydeki bu çökme "
             "miyokardiyal iskemi şüphesi uyandırır.")
        oner("Klinik izlem sürdürülmeli; yakınmalarda artış olması hâlinde ileri iskemi tetkiki yapılmalıdır.")
    else:
        ekle("ST Depresyonu (Oldpeak)", f"{oldpeak:.1f} mm", SEVIYE_NORMAL,
             "Efor sonrası anlamlı ST segment çökmesi izlenmemiştir; iskemiye işaret eden repolarizasyon "
             "kusuru saptanmamıştır.")

    # --- 11) ST segment eğimi (slope) --------------------------------------
    slope = metin_al("slope")
    if slope in ("downsloping", "2"):
        ekle("ST Segment Eğimi", "Aşağı Eğimli (Downsloping)", SEVIYE_YUKSEK,
             "Efor sırasında aşağı eğimli ST depresyonu izlenmiştir. Bu patern, iskemi için en yüksek "
             "tanısal değere sahip olan ve çok damar hastalığı ile ilişkilendirilen bulgudur.")
        oner("Aşağı eğimli ST paterni çok damar hastalığı ile ilişkili olduğundan koroner anatominin "
             "görüntülenmesi önerilir.")
    elif slope in ("flat", "1"):
        ekle("ST Segment Eğimi", "Düz (Horizontal)", SEVIYE_ORTA,
             "Efor sırasında horizontal (düz) ST segment paterni izlenmiştir; bu görünüm iskemi lehine "
             "anlamlı kabul edilir.")
    else:
        ekle("ST Segment Eğimi", "Yukarı Eğimli (Upsloping)", SEVIYE_NORMAL,
             "Yukarı eğimli ST paterni izlenmektedir; bu görünüm genellikle fizyolojik kabul edilir ve "
             "iskemi için düşük tanısal değer taşır.")

    # --- 12) Floroskopide boyanan majör damar sayısı -----------------------
    ca = sayi("ca", 0)
    if ca >= 2:
        ekle("Tutulan Majör Damar Sayısı (CA)", f"{int(ca)} damar", SEVIYE_YUKSEK,
             f"Floroskopide {int(ca)} majör koroner arterde kalsifikasyon/darlık izlenmektedir. Çok damar "
             "hastalığı, sol ventrikül fonksiyonu ve prognoz açısından yüksek riskli bir tablodur.")
        oner("Çok damar tutulumu nedeniyle revaskülarizasyon (perkütan girişim veya koroner by-pass) "
             "seçenekleri kalp ekibi tarafından değerlendirilmelidir.")
    elif ca >= 1:
        ekle("Tutulan Majör Damar Sayısı (CA)", f"{int(ca)} damar", SEVIYE_ORTA,
             "Bir majör koroner arterde kalsifikasyon/darlık saptanmıştır; tek damar hastalığı lehine bulgudur.")
        oner("Saptanan koroner lezyonun hemodinamik anlamlılığı değerlendirilmeli ve agresif medikal tedavi "
             "(antiagregan, statin) planlanmalıdır.")
    else:
        ekle("Tutulan Majör Damar Sayısı (CA)", "0 damar", SEVIYE_NORMAL,
             "Floroskopide majör koroner arterlerde anlamlı darlık veya kalsifikasyon saptanmamıştır; "
             "obstrüktif koroner hastalık olasılığı düşüktür.")

    # --- 13) Miyokard perfüzyonu (thal) ------------------------------------
    thal = metin_al("thal")
    if thal in ("reversable defect", "reversible defect", "3"):
        ekle("Miyokard Perfüzyonu (Thal)", "Geri Dönüşümlü Defekt", SEVIYE_YUKSEK,
             "Perfüzyon sintigrafisinde efor ile ortaya çıkıp istirahatte düzelen defekt izlenmiştir. Bu "
             "bulgu, canlı ancak kanlanması yetersiz (iskemik fakat viabl) miyokard dokusunu gösterir ve "
             "revaskülarizasyondan en çok fayda görecek hasta grubunu tanımlar.")
        oner("Geri dönüşümlü perfüzyon defekti saptandığından, iskemik alanın revaskülarizasyonu için vakit "
             "kaybetmeden kardiyoloji değerlendirmesi gereklidir.")
    elif thal in ("fixed defect", "2"):
        ekle("Miyokard Perfüzyonu (Thal)", "Sabit Defekt", SEVIYE_YUKSEK,
             "Hem efor hem istirahat görüntülerinde devam eden sabit perfüzyon defekti mevcuttur. Bu "
             "görünüm geçirilmiş miyokard enfarktüsüne bağlı kalıcı skar dokusu ile uyumludur.")
        oner("Skar dokusunun sol ventrikül ejeksiyon fraksiyonuna etkisi ekokardiyografi ile ölçülmeli ve "
             "sekonder korunma tedavisi düzenlenmelidir.")
    else:
        ekle("Miyokard Perfüzyonu (Thal)", "Normal", SEVIYE_NORMAL,
             "Miyokardın tüm duvarlarında perfüzyon homojen ve normaldir; iskemi veya skar lehine defekt "
             "izlenmemiştir.")

    # --- Gerekçe paragrafı ("neden hasta / neden sağlıklı?") ---------------
    risk_yuzdesi = int(round(olasilik * 100))
    sonuc_metni = "Riskli (Hasta Olabilir)" if tahmin == 1 else "Sağlıklı"

    yuksekler = [b for b in bulgular if b["seviye"] == SEVIYE_YUKSEK]
    ortalar = [b for b in bulgular if b["seviye"] == SEVIYE_ORTA]
    normaller = [b for b in bulgular if b["seviye"] == SEVIYE_NORMAL]

    def liste(bulgu_listesi):
        return ", ".join(f"{b['baslik']} ({b['deger']})" for b in bulgu_listesi)

    if tahmin == 1:
        gerekce = (
            f"Yapay zeka modeli, hastanın klinik bulgularını %{risk_yuzdesi} olasılıkla koroner arter "
            "hastalığı lehine değerlendirmiş ve hastayı 'Riskli' sınıfına yerleştirmiştir. ")
        if yuksekler:
            gerekce += (
                f"Bu kararı belirleyen başlıca patolojik bulgular şunlardır: {liste(yuksekler)}. Söz konusu "
                "parametreler, miyokardın oksijen ihtiyacı ile koroner kan akımı arasındaki dengenin "
                "bozulduğunu (miyokardiyal iskemi) ve aterosklerotik plak yükünün arttığını "
                "düşündürmektedir. ")
        if ortalar:
            gerekce += (
                f"Ayrıca {liste(ortalar)} bulguları sınırda/şüpheli düzeyde olup toplam kardiyovasküler "
                "risk yükünü artırmaktadır. ")
        if normaller:
            gerekce += (
                f"Buna karşılık {liste(normaller)} referans aralığındadır; bu bulgular prognozu olumlu "
                "yönde etkilese de yukarıdaki patolojik bulguları dengelemeye yetmemiştir. ")
        gerekce += (
            "Özetle hasta, mevcut bulgu kombinasyonu nedeniyle obstrüktif koroner arter hastalığı açısından "
            "yüksek olasılıklı kabul edilmiş; ileri kardiyolojik tetkik ve tedavi planlaması önerilmiştir.")
    else:
        gerekce = (
            "Yapay zeka modeli hastayı 'Sağlıklı' sınıfına yerleştirmiş, koroner arter hastalığı olasılığını "
            f"%{risk_yuzdesi} düzeyinde düşük bulmuştur. ")
        if normaller:
            gerekce += (
                f"Bu kararı destekleyen normal bulgular şunlardır: {liste(normaller)}. Bu parametreler, "
                "miyokard perfüzyonunun korunduğunu, efora kardiyak yanıtın yeterli olduğunu ve iskemi "
                "lehine repolarizasyon kusuru bulunmadığını göstermektedir. ")
        if yuksekler or ortalar:
            gerekce += (
                f"Bununla birlikte {liste(yuksekler + ortalar)} bulguları risk artırıcı yöndedir; bu nedenle "
                "hasta düşük riskli kabul edilse de bu parametrelerin izlemi ve modifiye edilebilir "
                "olanların (kan basıncı, lipid profili, glisemi) kontrol altına alınması sekonder korunma "
                "açısından önemlidir. ")
        else:
            gerekce += "Risk artırıcı anlamlı bir patolojik bulgu saptanmamıştır. "
        gerekce += (
            "Bu değerlendirme mevcut bulgularla sınırlıdır; yeni gelişen göğüs ağrısı, nefes darlığı veya "
            "efor kapasitesinde azalma durumunda hasta yeniden değerlendirilmelidir.")

    if not tavsiyeler:
        oner("Mevcut bulgular normal sınırlardadır. Akdeniz tipi beslenme, haftada en az 150 dakika orta "
             "şiddette aerobik egzersiz ve sigaradan uzak durulması ile kardiyovasküler sağlığın korunması "
             "önerilir.")
    oner("Bu değerlendirme bir karar destek çıktısıdır; nihai tanı, tetkik ve tedavi planı hastayı muayene "
         "eden hekim tarafından belirlenmelidir.")

    # --- Düz metin rapor (ekranda gösterilen sürüm) ------------------------
    satirlar = [
        "Klinik Değerlendirme Raporu",
        f"Hasta: {hasta_verisi.get('ad', '')} {hasta_verisi.get('soyad', '')}".strip(),
        f"Değerlendirme Tarihi: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        "",
        f"Yapay Zeka Modeli Tahmini: {sonuc_metni}",
        f"Hesaplanan Risk Oranı: %{risk_yuzdesi}",
        "",
        "Klinik Gerekçe (Neden bu sonuç?):",
        gerekce,
        "",
        "Bulgu Bazlı Analiz:",
    ]
    for b in bulgular:
        satirlar.append(f"- [{SEVIYE_ETIKETI[b['seviye']]}] {b['baslik']}: {b['deger']} - {b['yorum']}")
    satirlar.append("")
    satirlar.append("Klinik Tavsiyeler:")
    for t in tavsiyeler:
        satirlar.append(f"- {t}")

    return {
        "teshis": sonuc_metni,
        "risk_yuzdesi": risk_yuzdesi,
        "gerekce": gerekce,
        "bulgular": bulgular,
        "tavsiyeler": tavsiyeler,
        "metin": "\n".join(satirlar),
    }


# HERKESE AÇIK: analiz yapar ve sonucu YALNIZCA isteği yapana döndürür.
# Başka hastaların kaydını okumaz veya listelemez.
#
# Kimlik doğrulaması olmadığı için kötüye kullanıma karşı tek savunma hız
# sınırıdır: SINIR_YAZMA (IP başına dakikada 5 istek) otomatik betiklerin
# veritabanını sahte muayeneyle doldurmasını engeller.
@app.post("/tahmin")
@limiter.limit(SINIR_YAZMA)
def tahmin_yap(request: Request, hasta: HastaVerisi):
    try:
        data_dict = hasta.model_dump()
        hasta_adi = str(data_dict.pop('ad', '')).strip() or "İsimsiz"
        hasta_soyadi = str(data_dict.pop('soyad', '')).strip() or "Hasta"

        df_yeni = pd.DataFrame([data_dict])

        numeric_cols = num_imputer.feature_names_in_
        categorical_cols = cat_imputer.feature_names_in_

        df_yeni[numeric_cols] = num_imputer.transform(df_yeni[numeric_cols])
        df_yeni[categorical_cols] = cat_imputer.transform(df_yeni[categorical_cols])

        for col in categorical_cols:
            le = label_encoders[col]
            bilinen_siniflar = list(le.classes_)

            def guvenli_donusum(deger):
                val_str = str(deger).lower().strip()
                if col == "cp" and val_str in ("non-anginal pain", "non-anginal", "non anginal"):
                    return "non-anginal"
                for sinif in bilinen_siniflar:
                    if val_str == str(sinif).lower().strip():
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
        with veritabani() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO hasta_gecmisi (tarih, ad, soyad, yas, cinsiyet, cp, tansiyon, kolesterol, fbs, restecg, thalch, exang, oldpeak, slope, ca, thal, yapay_zeka_teshisi, risk_orani, aciklama)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (simdiki_zaman, hasta_adi, hasta_soyadi, hasta.age, hasta.sex, hasta.cp, hasta.trestbps, hasta.chol,
                  hasta.fbs, hasta.restecg, hasta.thalch, hasta.exang, hasta.oldpeak, hasta.slope, hasta.ca, hasta.thal,
                  sonuc_metni, risk_yuzdesi, aciklama_metni))

            hasta_id = cursor.lastrowid

        analiz = klinik_analiz_uret(hasta.model_dump(), tahmin, float(olasilik[1]))

        return {
            "Teşhis Sonucu": sonuc_metni,
            "Kalp Hastalığı Risk Oranı": risk_yuzdesi,
            "Yapay Zeka Açıklaması": aciklama_metni,
            "Klinik Rapor ve Tavsiye": analiz["metin"],
            # PDF raporunun "neden hasta / neden sağlıklı" bölümünü besleyen
            # yapılandırılmış analiz (gerekçe paragrafı + bulgu bazlı yorumlar).
            "Klinik Analiz": {
                "gerekce": analiz["gerekce"],
                "bulgular": analiz["bulgular"],
                "tavsiyeler": analiz["tavsiyeler"],
            },
            "hasta_id": hasta_id
        }
    except Exception:
        # Hasta verisi ve iç yapı bilgisi istemciye sızmasın diye
        # ayrıntı yalnızca sunucu günlüğüne yazılır.
        logger.exception("Tahmin sirasinda hata olustu")
        return {"hata": "Analiz şu anda tamamlanamadı. Lütfen bulguları kontrol edip tekrar deneyin."}


# HERKESE AÇIK: analiz sonucu ekranındaki "Doğru / Yanlış" düğmeleri herkese
# açık sayfada (/) yer aldığı için bu uç da açıktır. Yalnızca YAZAR; hiçbir
# hasta verisi döndürmez. Toplanan düzeltmeler /geribildirim_listesi
# üzerinden okunur ve O uç korumalıdır.
#
# Yazma yine SINIR_YAZMA (dakikada 5) ile sınırlıdır.
@app.post("/geribildirim")
@limiter.limit(SINIR_YAZMA)
def geri_bildirim_kaydet(request: Request, veri: GeriBildirim):
    simdiki_zaman = datetime.now().strftime("%Y-%m-%d %H:%M")

    with veritabani() as conn:
        cursor = conn.cursor()

        # hasta_id istemciden geldiği için var olduğu doğrulanır; aksi halde
        # hiçbir muayeneye bağlanmayan öksüz geri bildirim satırları oluşur
        # ve /geribildirim_listesi ekranında "Bilinmiyor" olarak görünürdü.
        cursor.execute("SELECT 1 FROM hasta_gecmisi WHERE id = ?", (veri.hasta_id,))
        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="Geri bildirim verilecek hasta kaydı bulunamadı.")

        cursor.execute('''
            INSERT INTO geribildirimler (hasta_id, orijinal_teshis, doktor_karari, tarih)
            VALUES (?, ?, ?, ?)
        ''', (veri.hasta_id, veri.orijinal_teshis, veri.doktor_karari, simdiki_zaman))

    return {
        "mesaj": "Teşekkürler! Uzman görüşünüz (Müdahale) yapay zekanın gelecekteki eğitimi için veritabanına eklendi."}

# --- 6. PDF RAPORU (tek üretim yolu) ---
#
# Rapor YALNIZCA burada, sunucu tarafında üretilir. Hem yeni muayene
# (index.html) hem de geçmiş kayıt (kayitlar.html) ekranı aynı
# /pdf-indir/{hasta_id} adresini çağırır; böylece hangi ekrandan
# indirilirse indirilsin rapor birebir aynı içeriktedir.

# Veritabanı satırındaki sütun sırası (aşağıdaki SELECT ile aynı olmalıdır)
KAYIT_ALANLARI = (
    "yas, cinsiyet, cp, tansiyon, kolesterol, fbs, restecg, thalch, exang, "
    "oldpeak, slope, ca, thal, yapay_zeka_teshisi, risk_orani, ad, soyad, tarih"
)


def kayit_analizi_getir(hasta_id: int) -> dict:
    """Geçmiş bir muayeneyi okuyup klinik analizini yeniden üretir.

    Kayıtta yalnızca ham bulgular ve teşhis metni saklandığı için gerekçe,
    bulgu yorumları ve tavsiyeler klinik_analiz_uret() ile yeniden hesaplanır.
    """
    with veritabani() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT {KAYIT_ALANLARI} FROM hasta_gecmisi WHERE id = ?", (hasta_id,))
        kayit = cursor.fetchone()

    if kayit is None:
        raise HTTPException(status_code=404, detail="Kayıt bulunamadı.")

    (yas, cinsiyet, cp, tansiyon, kolesterol, fbs, restecg, thalch, exang,
     oldpeak, slope, ca, thal, teshis, risk_orani, ad, soyad, tarih) = kayit

    hasta_verisi = {
        "ad": ad, "soyad": soyad, "age": yas, "sex": cinsiyet, "cp": cp,
        "trestbps": tansiyon, "chol": kolesterol, "fbs": fbs, "restecg": restecg,
        "thalch": thalch, "exang": exang, "oldpeak": oldpeak, "slope": slope,
        "ca": ca, "thal": thal,
    }

    tahmin = 1 if "Riskli" in str(teshis) else 0
    try:
        # Kayıtta "%73.20" biçiminde metin olarak durur; orana çevrilir.
        olasilik = float(str(risk_orani).replace("%", "").replace(",", ".").strip()) / 100.0
    except (TypeError, ValueError):
        olasilik = 1.0 if tahmin == 1 else 0.0

    return {
        "ad": ad, "soyad": soyad, "yas": yas, "cinsiyet": cinsiyet,
        "teshis": teshis, "risk_orani": risk_orani, "tarih": tarih,
        "analiz": klinik_analiz_uret(hasta_verisi, tahmin, olasilik),
    }


# KORUMALI: Hasta id'sine göre PDF raporu oluşturur ve indirir
@app.get("/pdf-indir/{hasta_id}")
@limiter.limit(SINIR_PDF)
def pdf_rapor_indir(request: Request, hasta_id: int, kullanici: str = Depends(yetki_dogrula)):
    kayit = kayit_analizi_getir(hasta_id)

    # Belge düzeni (kurum başlığı, künye, bulgu tablosu, imza bloğu, sayfa
    # numarası) klinik/rapor.py içindedir; burada yalnızca veri aktarılır.
    pdf_bytes = rapor.pdf_uret(
        ad=kayit["ad"], soyad=kayit["soyad"],
        yas=kayit["yas"], cinsiyet=kayit["cinsiyet"],
        teshis=kayit["teshis"], risk_metni=kayit["risk_orani"],
        analiz=kayit["analiz"],
        hasta_id=hasta_id, muayene_tarihi=kayit.get("tarih"),
    )

    headers = {"Content-Disposition": f'attachment; filename="klinik_rapor_{hasta_id}.pdf"'}
    return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)


# --- 7. SUNUCU BAŞLATMA ---
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