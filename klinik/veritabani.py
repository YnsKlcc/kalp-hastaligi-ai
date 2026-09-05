"""SQLite erişim katmanı: bağlantı yönetimi, şema ve sorgular.

Tüm SQL bu modülde toplanır. main.py'de tek bir cursor.execute çağrısı
kalmaz; uç noktalar buradaki adlandırılmış fonksiyonları çağırır.
"""

import logging
import os
import shutil
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime

from klinik.ozellikler import TUM_SUTUNLAR

logger = logging.getLogger("klinik_ai.veritabani")

PROJE_KOKU = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_YOLU = os.path.join(PROJE_KOKU, "klinik_kayitlar.db")

# Başka bir istek yazma kilidini tutuyorsa bu kadar saniye beklenir.
# Varsayılan 5 sn yerine 15 sn: eşzamanlı /tahmin istekleri altında
# "database is locked" hatası yerine kısa bir bekleme tercih edilir.
DB_ZAMAN_ASIMI = 15.0

SAYFA_BOYUTU = 10

# --- Sütun eşlemesi ---------------------------------------------------------
#
# Veritabanı sütunları Türkçe, model özellikleri İngilizce adlandırılmıştır.
# Eşleme burada TEK YERDE durur; daha önce hem /kayitlar sorgusunda hem de
# PDF üretiminde ayrı ayrı elle yazılıyordu ve sıra bozulduğunda hasta
# bulguları sessizce birbirine karışabilirdi.
DB_SUTUNU = {
    "age": "yas",
    "sex": "cinsiyet",
    "cp": "cp",
    "trestbps": "tansiyon",
    "chol": "kolesterol",
    "fbs": "fbs",
    "restecg": "restecg",
    "thalch": "thalch",
    "exang": "exang",
    "oldpeak": "oldpeak",
    "slope": "slope",
    "ca": "ca",
    "thal": "thal",
}
BULGU_SUTUNLARI = [DB_SUTUNU[ozellik] for ozellik in TUM_SUTUNLAR]


@contextmanager
def baglanti():
    """Tüm SQLite erişimleri için tek giriş noktası.

    Neden gerekli: sqlite3'te `with sqlite3.connect(...) as conn` bloğu
    YALNIZCA transaction'ı yönetir (başarıda commit, hatada rollback);
    bağlantının kendisini KAPATMAZ. Bağlantı çöp toplayıcı çalışana kadar
    açık kalır, dosya tanıtıcıları ve yazma kilitleri birikir ve çoklu
    istek altında sunucu "database is locked" ile kilitlenir.

    İki katmanlı yapı bunu kesin olarak çözer:
        closing(...)  -> hata olsa da olmasa da bağlantıyı kapatır
        with conn     -> başarıda commit, istisnada rollback

    row_factory = sqlite3.Row: satırlar konum yerine SÜTUN ADIYLA okunur.
    Eskiden API konumsal diziler döndürüyordu ve arayüzde elle yazılmış bir
    indeks tablosu (tarih: 0, ad: 1, ...) vardı; SELECT sırası değiştiğinde
    hasta adı ile teşhis sütunu sessizce yer değiştirebilirdi.
    """
    with closing(sqlite3.connect(DB_YOLU, timeout=DB_ZAMAN_ASIMI)) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            yield conn


# --- Şema -------------------------------------------------------------------

HASTA_TABLOSU = """
    CREATE TABLE IF NOT EXISTS hasta_gecmisi (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        tarih              TEXT    NOT NULL,
        ad                 TEXT    NOT NULL,
        soyad              TEXT    NOT NULL,
        yas                REAL    NOT NULL,
        cinsiyet           TEXT    NOT NULL,
        cp                 TEXT    NOT NULL,
        tansiyon           REAL    NOT NULL,
        kolesterol         REAL    NOT NULL,
        fbs                TEXT    NOT NULL,
        restecg            TEXT    NOT NULL,
        thalch             REAL    NOT NULL,
        exang              TEXT    NOT NULL,
        oldpeak            REAL    NOT NULL,
        slope              TEXT    NOT NULL,
        ca                 REAL    NOT NULL,
        thal               TEXT    NOT NULL,
        yapay_zeka_teshisi TEXT    NOT NULL,
        risk_orani         REAL    NOT NULL,
        aciklama           TEXT    NOT NULL
    )
"""

GERIBILDIRIM_TABLOSU = """
    CREATE TABLE IF NOT EXISTS geribildirimler (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        hasta_id        INTEGER NOT NULL REFERENCES hasta_gecmisi(id) ON DELETE CASCADE,
        orijinal_teshis TEXT    NOT NULL,
        doktor_karari   TEXT    NOT NULL,
        tarih           TEXT    NOT NULL
    )
"""


def hazirla() -> None:
    """Şemayı oluşturur ve gerekiyorsa eski şemadan göç ettirir."""
    with baglanti() as conn:
        imlec = conn.cursor()

        # WAL (Write-Ahead Logging): okuyucular yazıcıyı, yazıcı da okuyucuları
        # bloklamaz. Ayar veritabanı dosyasına kalıcı yazılır.
        imlec.execute("PRAGMA journal_mode=WAL")
        imlec.execute("PRAGMA foreign_keys=ON")
        imlec.execute(HASTA_TABLOSU)
        imlec.execute(GERIBILDIRIM_TABLOSU)

    _eski_semadan_goc_et()


def _eski_semadan_goc_et() -> None:
    """Kısıtsız eski tabloyu NOT NULL'lu yeni şemaya taşır.

    CREATE TABLE IF NOT EXISTS var olan bir tabloyu DEĞİŞTİRMEZ; bu yüzden
    daha önce oluşturulmuş veritabanları kısıt olmadan kalırdı. Örneğin
    yas sütunu NULL olan bir satır PDF üretimini int(None) ile 500'e
    düşürüyordu.

    Göç yıkıcıdır, bu yüzden önce zaman damgalı bir yedek alınır. NULL
    içeren satırlar taşınmaz; sayıları günlüğe yazılır (bu satırlar zaten
    eksik bulgu içerdiği için klinik olarak da değerlendirilemez).
    """
    with baglanti() as conn:
        sutunlar = conn.execute("PRAGMA table_info(hasta_gecmisi)").fetchall()
        if not sutunlar:
            return
        # notnull sütunu 0/1 döner. Hepsi 0 ise tablo eski şemadadır.
        kisitli = any(s["notnull"] for s in sutunlar if s["name"] != "id")
        if kisitli:
            return
        toplam = conn.execute("SELECT COUNT(*) AS n FROM hasta_gecmisi").fetchone()["n"]

    yedek = f"{DB_YOLU}.goc-yedegi-{datetime.now():%Y%m%d-%H%M%S}"
    shutil.copy2(DB_YOLU, yedek)
    logger.warning("Eski sema saptandi. Yedek alindi: %s", yedek)

    zorunlu = ["tarih", "ad", "soyad", *BULGU_SUTUNLARI,
               "yapay_zeka_teshisi", "risk_orani", "aciklama"]
    kosul = " AND ".join(f"{s} IS NOT NULL" for s in zorunlu)
    alanlar = ", ".join(["id", *zorunlu])

    # risk_orani eskiden "%73.20" biçiminde METİN olarak tutuluyordu; ekranda
    # "%73.20", rapor metninde "%73" görünmesinin sebebi buydu. Artık sayı
    # olarak saklanır ve biçimlendirme yalnızca gösterim anında yapılır.
    secim = ", ".join(
        "CAST(REPLACE(REPLACE(risk_orani, '%', ''), ',', '.') AS REAL)"
        if s == "risk_orani" else s
        for s in ["id", *zorunlu]
    )

    with baglanti() as conn:
        imlec = conn.cursor()
        imlec.execute("PRAGMA foreign_keys=OFF")
        imlec.execute(HASTA_TABLOSU.replace("hasta_gecmisi", "hasta_gecmisi_yeni"))
        imlec.execute(
            f"INSERT INTO hasta_gecmisi_yeni ({alanlar}) "
            f"SELECT {secim} FROM hasta_gecmisi WHERE {kosul}"
        )
        tasinan = imlec.rowcount
        imlec.execute("DROP TABLE hasta_gecmisi")
        imlec.execute("ALTER TABLE hasta_gecmisi_yeni RENAME TO hasta_gecmisi")
        # Öksüz geri bildirimler: kaynak muayenesi taşınmayan satırlar.
        imlec.execute(
            "DELETE FROM geribildirimler WHERE hasta_id NOT IN (SELECT id FROM hasta_gecmisi)"
        )
        oksuz = imlec.rowcount

    logger.warning(
        "Goc tamamlandi. %s kayittan %s tasindi, %s eksik veri nedeniyle atlandi, "
        "%s oksuz geri bildirim silindi.",
        toplam, tasinan, toplam - tasinan, oksuz,
    )


# --- Yazma ------------------------------------------------------------------


def muayene_kaydet(ad: str, soyad: str, bulgular: dict,
                   teshis: str, risk_orani: float, aciklama: str) -> int:
    """Yeni muayeneyi yazar ve kayıt id'sini döndürür."""
    sutunlar = ["tarih", "ad", "soyad", *BULGU_SUTUNLARI,
                "yapay_zeka_teshisi", "risk_orani", "aciklama"]
    degerler = [
        datetime.now().strftime("%Y-%m-%d %H:%M"), ad, soyad,
        *[bulgular[ozellik] for ozellik in TUM_SUTUNLAR],
        teshis, risk_orani, aciklama,
    ]
    yer_tutucu = ", ".join("?" * len(sutunlar))

    with baglanti() as conn:
        imlec = conn.execute(
            f"INSERT INTO hasta_gecmisi ({', '.join(sutunlar)}) VALUES ({yer_tutucu})",
            degerler,
        )
        return imlec.lastrowid


def muayene_var_mi(hasta_id: int) -> bool:
    with baglanti() as conn:
        return conn.execute(
            "SELECT 1 FROM hasta_gecmisi WHERE id = ?", (hasta_id,)
        ).fetchone() is not None


def geribildirim_kaydet(hasta_id: int, orijinal_teshis: str, doktor_karari: str) -> None:
    with baglanti() as conn:
        conn.execute(
            "INSERT INTO geribildirimler (hasta_id, orijinal_teshis, doktor_karari, tarih) "
            "VALUES (?, ?, ?, ?)",
            (hasta_id, orijinal_teshis, doktor_karari,
             datetime.now().strftime("%Y-%m-%d %H:%M")),
        )


# --- Okuma ------------------------------------------------------------------


def muayene_getir(hasta_id: int) -> dict | None:
    """Tek bir muayeneyi sözlük olarak döndürür; yoksa None."""
    with baglanti() as conn:
        satir = conn.execute(
            "SELECT * FROM hasta_gecmisi WHERE id = ?", (hasta_id,)
        ).fetchone()
    return dict(satir) if satir else None


def muayene_sayfasi(sayfa: int) -> dict:
    """Kayıtları sayfalı olarak döndürür (en yeni önce)."""
    sayfa = max(1, sayfa)
    with baglanti() as conn:
        toplam = conn.execute("SELECT COUNT(*) AS n FROM hasta_gecmisi").fetchone()["n"]
        satirlar = conn.execute(
            "SELECT * FROM hasta_gecmisi ORDER BY id DESC LIMIT ? OFFSET ?",
            (SAYFA_BOYUTU, (sayfa - 1) * SAYFA_BOYUTU),
        ).fetchall()

    return {
        "son_muayeneler": [dict(s) for s in satirlar],
        "toplam_sayfa": max(1, (toplam + SAYFA_BOYUTU - 1) // SAYFA_BOYUTU),
        "aktif_sayfa": sayfa,
    }


def istatistikler() -> dict:
    """Gösterge panelinin grafiklerini besleyen toplu sayımlar."""
    with baglanti() as conn:
        risk = conn.execute(
            "SELECT yapay_zeka_teshisi AS teshis, COUNT(*) AS adet "
            "FROM hasta_gecmisi GROUP BY yapay_zeka_teshisi"
        ).fetchall()

        yas = conn.execute("""
            SELECT
                CASE
                    WHEN yas < 40 THEN '40 Yaş Altı'
                    WHEN yas <= 55 THEN '40-55 Yaş Arası'
                    ELSE '55 Yaş Üstü'
                END AS grup,
                SUM(CASE WHEN yapay_zeka_teshisi LIKE '%Riskli%'  THEN 1 ELSE 0 END) AS riskli,
                SUM(CASE WHEN yapay_zeka_teshisi LIKE '%Sağlıklı%' THEN 1 ELSE 0 END) AS saglikli
            FROM hasta_gecmisi
            GROUP BY grup
        """).fetchall()

    return {
        "risk_dagilimi": [dict(s) for s in risk],
        "yas_dagilimi": [dict(s) for s in yas],
    }


def geribildirim_listesi() -> list[dict]:
    with baglanti() as conn:
        satirlar = conn.execute("""
            SELECT g.tarih, h.ad, h.soyad,
                   g.orijinal_teshis, g.doktor_karari
            FROM geribildirimler g
            JOIN hasta_gecmisi h ON g.hasta_id = h.id
            ORDER BY g.id DESC
        """).fetchall()
    return [dict(s) for s in satirlar]
