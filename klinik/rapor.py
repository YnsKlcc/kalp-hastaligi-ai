"""PDF klinik rapor üretimi (reportlab).

Rapor YALNIZCA burada üretilir. Hem yeni muayene (index.html) hem de geçmiş
kayıt (kayitlar.html) ekranı aynı /pdf-indir/{hasta_id} adresini çağırır;
böylece hangi ekrandan indirilirse indirilsin içerik birebir aynıdır.

BELGE DÜZENİ
------------
Çıktı, klinikte kullanılan resmî bir hasta raporunun bölümlerini izler:

    kurum başlığı (her sayfada)      -> kurum adı, birim, belge başlığı
    kimlik künyesi                   -> ad soyad, protokol no, yaş, cinsiyet,
                                        muayene ve rapor tarihi
    değerlendirme özeti              -> teşhis, risk yüzdesi, risk kategorisi
    1. klinik gerekçe
    2. bulgular ve ölçümler tablosu  -> parametre / değer / durum / yorum
    3. öneriler ve izlem planı
    4. yasal uyarı
    imza bloğu                       -> hekim onayı olmadan geçersizdir
    sayfa altlığı (her sayfada)      -> belge no, düzenlenme anı, "Sayfa x / y"

Kurum adı ve birim .env üzerinden değiştirilebilir (KURUM_ADI, KURUM_BIRIM);
kod içinde gerçek bir kuruma ait sabit kimlik yoktur.
"""

import io
import os
from datetime import datetime

import reportlab
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.fonts import addMapping
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (
    HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table,
    TableStyle,
)

# --- Yazı tipi -------------------------------------------------------------
#
# PDF'in gömülü standart fontu (Helvetica) Türkçe'ye özgü karakterleri
# çizemez; "Şişli" yerine "Sisli" basılırdı. Resmî bir hasta raporunda hasta
# adının harf kaybına uğraması kabul edilemez, bu yüzden reportlab ile
# birlikte gelen Bitstream Vera Sans (tam Türkçe kapsamlı, ek kurulum
# gerektirmez) TrueType olarak gömülür. Kayıt herhangi bir sebeple başarısız
# olursa Helvetica'ya düşülür ve metin ASCII'ye sadeleştirilir; rapor üretimi
# yazı tipi yüzünden ASLA çökmez.
FONT = "Helvetica"
FONT_KALIN = "Helvetica-Bold"
SADELESTIR = True


def _fontlari_yukle() -> None:
    global FONT, FONT_KALIN, SADELESTIR
    dizin = os.path.join(os.path.dirname(reportlab.__file__), "fonts")
    yuzler = (
        ("KlinikSans", "Vera.ttf"),
        ("KlinikSans-Bold", "VeraBd.ttf"),
        ("KlinikSans-Italic", "VeraIt.ttf"),
        ("KlinikSans-BoldItalic", "VeraBI.ttf"),
    )
    try:
        for ad, dosya in yuzler:
            pdfmetrics.registerFont(TTFont(ad, os.path.join(dizin, dosya)))
        # <b>/<i> etiketlerinin doğru yüze eşlenmesi için aile tanımı.
        for kalin, egik, ad in ((0, 0, "KlinikSans"), (1, 0, "KlinikSans-Bold"),
                                (0, 1, "KlinikSans-Italic"),
                                (1, 1, "KlinikSans-BoldItalic")):
            addMapping("KlinikSans", kalin, egik, ad)
    except Exception:  # font dosyası yok/bozuk -> Helvetica ile devam edilir
        return
    FONT, FONT_KALIN = "KlinikSans", "KlinikSans-Bold"
    SADELESTIR = False


_fontlari_yukle()

# Helvetica'ya düşüldüğünde uygulanan ASCII indirgemesi.
TURKCE_SADELESTIRME = str.maketrans({
    "ı": "i", "ğ": "g", "ü": "u", "ş": "s", "ö": "o", "ç": "c",
    "İ": "I", "Ğ": "G", "Ü": "U", "Ş": "S", "Ö": "O", "Ç": "C",
})

# reportlab'ın Paragraph'ı kendi mini işaretleme dilini ayrıştırır: <b>, <i>,
# <br/> gibi etiketler biçimlendirme olarak yorumlanır. Hasta adı gibi dış
# kaynaklı metinler bu ayrıştırıcıya HAM verilirse:
#   - "Ali <b>Kalin" gibi kapatılmamış bir etiket ayrıştırıcıyı çökertir
#     (ValueError -> uç noktada 500),
#   - kapatılmış bir etiket rapora biçimlendirme enjekte eder.
# Bu yüzden metin, gövdeye konmadan ÖNCE her zaman kaçışlanır.
XML_KACISI = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"))

# --- Kurum künyesi ---------------------------------------------------------
#
# Ortam değişkenleri MODÜL YÜKLENİRKEN DEĞİL, rapor üretilirken okunur:
# main.py bu modülü load_dotenv() çağrısından önce içe aktarabilir; import
# anında okunsaydı .env'deki kurum adı hiçbir zaman etkili olmazdı.
KURUM_ADI_VARSAYILAN = "SAĞLIK KURULUŞU"
KURUM_BIRIM_VARSAYILAN = "Kardiyoloji Birimi · Klinik Karar Destek Sistemi"
BELGE_BASLIGI = "KARDİYOVASKÜLER RİSK DEĞERLENDİRME RAPORU"

# --- Renk paleti -----------------------------------------------------------
LACIVERT = colors.HexColor("#1e3a8a")
KOYU = colors.HexColor("#111827")
GRI = colors.HexColor("#4b5563")
ACIK_GRI = colors.HexColor("#d1d5db")
ZEMIN = colors.HexColor("#f3f4f6")

# Bulgu ağırlık seviyeleri (klinik.analiz ile aynı anahtarlar).
SEVIYE_GORUNUM = {
    "yuksek":  ("PATOLOJİK", colors.HexColor("#b91c1c"), colors.HexColor("#fee2e2")),
    "orta":    ("SINIRDA",   colors.HexColor("#b45309"), colors.HexColor("#fef3c7")),
    "normal":  ("NORMAL",    colors.HexColor("#15803d"), colors.HexColor("#dcfce7")),
    "veriyok": ("VERİ YOK",  GRI,                        ZEMIN),
}

# Risk yüzdesine karşılık gelen klinik kategori (en yüksekten aşağıya).
RISK_KATEGORILERI = (
    (75.0, "ÇOK YÜKSEK RİSK", colors.HexColor("#991b1b")),
    (50.0, "YÜKSEK RİSK",     colors.HexColor("#b91c1c")),
    (25.0, "ORTA RİSK",       colors.HexColor("#b45309")),
    (0.0,  "DÜŞÜK RİSK",      colors.HexColor("#15803d")),
)

CINSIYET_ADI = {"male": "Erkek", "female": "Kadın", "m": "Erkek", "f": "Kadın"}


def tr(metin: object) -> str:
    """Metni PDF gövdesine konmaya hazır hale getirir.

    Sıra önemlidir: önce & kaçışlanır, sonra < ve >. Ters sırada yapılırsa
    "&lt;" dizgisindeki & ikinci kez kaçışlanıp "&amp;lt;" olurdu.

    Türkçe sadeleştirmesi yalnızca Unicode yazı tipi yüklenemediğinde
    uygulanır; normal koşulda hasta adı Türkçe harfleriyle basılır.
    """
    if metin is None:
        return ""
    sonuc = str(metin)
    if SADELESTIR:
        sonuc = sonuc.translate(TURKCE_SADELESTIRME)
    for arama, yerine in XML_KACISI:
        sonuc = sonuc.replace(arama, yerine)
    return sonuc


def _duz(metin: object) -> str:
    """Kaçışsız düz metin: canvas.drawString işaretleme ayrıştırmaz."""
    if metin is None:
        return ""
    sonuc = str(metin)
    return sonuc.translate(TURKCE_SADELESTIRME) if SADELESTIR else sonuc


def _tam_sayi(deger: object, yedek: str = "—") -> str:
    """Sayıya çevrilemeyen (ör. NULL) değerlerde çökmek yerine yedeğe düşer."""
    try:
        return str(int(float(deger)))
    except (TypeError, ValueError):
        return yedek


def _yuzde(risk_metni: object):
    """'%87.0' -> 87.0. Çözülemezse None; rapor yine de üretilir."""
    try:
        return float(str(risk_metni).replace("%", "").replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


def _risk_kategorisi(yuzde):
    if yuzde is None:
        return "—", GRI
    for esik, ad, renk in RISK_KATEGORILERI:
        if yuzde >= esik:
            return ad, renk
    return "—", GRI


def _tarih_bicimle(deger: object) -> str:
    """Veritabanındaki tarihi gg.aa.yyyy SS:DD biçimine getirir."""
    if not deger:
        return "—"
    metin = str(deger).strip()
    for kalip in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
                  "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(metin, kalip).strftime("%d.%m.%Y %H:%M")
        except ValueError:
            continue
    return metin


class _SayfaliCanvas(pdfcanvas.Canvas):
    """"Sayfa 1 / 3" yazabilmek için iki geçişli canvas.

    Toplam sayfa sayısı ancak belge bittiğinde bilinir; bu yüzden sayfalar
    önce bellekte biriktirilir, toplam belli olunca altlık her sayfaya
    yeniden çizilip sayfa gerçekten yazılır.
    """

    def __init__(self, *args, altlik=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._sayfa_durumlari = []
        self._altlik = altlik

    def showPage(self):
        self._sayfa_durumlari.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        toplam = len(self._sayfa_durumlari)
        for sira, durum in enumerate(self._sayfa_durumlari, start=1):
            self.__dict__.update(durum)
            if self._altlik is not None:
                self._altlik(self, sira, toplam)
            super().showPage()
        super().save()


def _kunye_tablosu(satirlar, sutun_genislikleri, etiket_stili, deger_stili):
    """Etiket/değer künyesi: etiket sütunları zeminli ve kalın.

    Hücreler ham dizgi değil Paragraph'tır; "Hasta Adı Soyadı" gibi uzun bir
    etiket ya da uzun bir hasta adı sütuna sığmadığında kırpılmak yerine alt
    satıra kayar.
    """
    govde = [[Paragraph(hucre, etiket_stili if sutun % 2 == 0 else deger_stili)
              for sutun, hucre in enumerate(satir)] for satir in satirlar]
    tablo = Table(govde, colWidths=sutun_genislikleri)
    tablo.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), ZEMIN),
        ("BACKGROUND", (2, 0), (2, -1), ZEMIN),
        ("GRID", (0, 0), (-1, -1), 0.4, ACIK_GRI),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return tablo


def pdf_uret(ad: str, soyad: str, yas: object, cinsiyet: str,
             teshis: str, risk_metni: str, analiz: dict,
             hasta_id: object = None, muayene_tarihi: object = None) -> bytes:
    """Resmî düzende klinik raporu PDF olarak üretip bayt dizisi döndürür.

    hasta_id ve muayene_tarihi isteğe bağlıdır; verilmezse künyede ilgili
    alanlar "—" basılır, böylece eski çağrılar bozulmaz.
    """
    tampon = io.BytesIO()
    kurum_adi = os.getenv("KURUM_ADI", KURUM_ADI_VARSAYILAN)
    kurum_birim = os.getenv("KURUM_BIRIM", KURUM_BIRIM_VARSAYILAN)
    simdi = datetime.now()
    belge_no = (f"KRD-{simdi:%Y}-{int(hasta_id):06d}"
                if str(hasta_id).strip().isdigit() else "—")
    duzenlenme = simdi.strftime("%d.%m.%Y %H:%M")
    hasta_adi = f"{ad or ''} {soyad or ''}".strip() or "—"

    sol = sag = 15 * mm
    ust_bosluk = 34 * mm      # kurum başlığı bandının kapladığı yer
    alt_bosluk = 20 * mm      # sayfa altlığının kapladığı yer

    def ust_bilgi(kanvas, _belge):
        kanvas.saveState()
        genislik, yukseklik = A4
        kanvas.setFillColor(LACIVERT)
        kanvas.setFont(FONT_KALIN, 12.5)
        kanvas.drawCentredString(genislik / 2, yukseklik - 16 * mm, _duz(kurum_adi))
        kanvas.setFillColor(GRI)
        kanvas.setFont(FONT, 8)
        kanvas.drawCentredString(genislik / 2, yukseklik - 20.5 * mm, _duz(kurum_birim))
        kanvas.setStrokeColor(LACIVERT)
        kanvas.setLineWidth(1.2)
        kanvas.line(sol, yukseklik - 24 * mm, genislik - sag, yukseklik - 24 * mm)
        kanvas.setFillColor(KOYU)
        kanvas.setFont(FONT_KALIN, 10.5)
        kanvas.drawCentredString(genislik / 2, yukseklik - 30 * mm, _duz(BELGE_BASLIGI))
        kanvas.restoreState()

    def alt_bilgi(kanvas, sayfa, toplam):
        kanvas.saveState()
        genislik = A4[0]
        taban = 12 * mm
        kanvas.setStrokeColor(ACIK_GRI)
        kanvas.setLineWidth(0.6)
        kanvas.line(sol, taban + 4 * mm, genislik - sag, taban + 4 * mm)
        kanvas.setFillColor(GRI)
        kanvas.setFont(FONT, 7.5)
        kanvas.drawString(sol, taban + 1.5 * mm,
                          _duz(f"Belge No: {belge_no}   ·   Düzenlenme: {duzenlenme}"))
        kanvas.drawRightString(genislik - sag, taban + 1.5 * mm,
                               _duz(f"Sayfa {sayfa} / {toplam}"))
        # Not, künye satırıyla çakışmasın diye ALTINDA ayrı bir satırdadır.
        kanvas.setFont(FONT, 7)
        kanvas.drawCentredString(
            genislik / 2, taban - 2.5 * mm,
            _duz("Elektronik ortamda üretilmiştir; hekim imzası olmadan geçerli değildir."))
        kanvas.restoreState()

    belge = SimpleDocTemplate(
        tampon, pagesize=A4,
        leftMargin=sol, rightMargin=sag,
        topMargin=ust_bosluk, bottomMargin=alt_bosluk,
        title=f"{BELGE_BASLIGI} - {hasta_adi}",
        author=kurum_adi, subject=BELGE_BASLIGI,
    )
    icerik_genisligi = A4[0] - sol - sag

    stiller = getSampleStyleSheet()
    bolum = ParagraphStyle(
        "Bolum", parent=stiller["Heading2"], fontName=FONT_KALIN, fontSize=10,
        textColor=LACIVERT, spaceBefore=12, spaceAfter=5, leading=13)
    normal = ParagraphStyle(
        "GovdeMetni", parent=stiller["Normal"], fontName=FONT, fontSize=9.5,
        leading=14, alignment=TA_JUSTIFY, textColor=KOYU, spaceAfter=4)
    # Dar sütunlarda iki yana yaslama kelimeler arasında büyük boşluklar
    # açtığı için hücreler sola hizalıdır; yalnızca geniş yorum sütunu yaslıdır.
    hucre = ParagraphStyle("Hucre", parent=normal, fontSize=8.5, leading=11,
                           alignment=TA_LEFT, spaceAfter=0)
    hucre_kalin = ParagraphStyle("HucreKalin", parent=hucre, fontName=FONT_KALIN)
    hucre_yorum = ParagraphStyle("HucreYorum", parent=hucre, alignment=TA_JUSTIFY)
    kucuk = ParagraphStyle("Kucuk", parent=normal, fontSize=7.5, leading=10.5,
                           textColor=GRI)
    imza_stili = ParagraphStyle("Imza", parent=normal, fontSize=8.5, leading=13,
                                alignment=TA_CENTER, textColor=KOYU)

    akis = []

    # --- Kimlik künyesi ----------------------------------------------------
    yuzde = _yuzde(risk_metni)
    kategori_adi, kategori_rengi = _risk_kategorisi(yuzde)
    cinsiyet_gosterim = CINSIYET_ADI.get(
        str(cinsiyet).strip().lower(), tr(cinsiyet) or "—")

    kunye_etiket = ParagraphStyle("KunyeEtiket", parent=normal, fontSize=9,
                                  fontName=FONT_KALIN, alignment=TA_LEFT,
                                  leading=12, spaceAfter=0)
    kunye_deger = ParagraphStyle("KunyeDeger", parent=kunye_etiket,
                                 fontName=FONT)
    akis.append(_kunye_tablosu(
        [
            ["Hasta Adı Soyadı", tr(hasta_adi),
             "Protokol No", tr(hasta_id) if hasta_id else "—"],
            ["Yaş", _tam_sayi(yas), "Cinsiyet", cinsiyet_gosterim],
            ["Muayene Tarihi", tr(_tarih_bicimle(muayene_tarihi)),
             "Rapor Tarihi", duzenlenme],
        ],
        [37 * mm, 58 * mm, 32 * mm, icerik_genisligi - 127 * mm],
        kunye_etiket, kunye_deger,
    ))
    akis.append(Spacer(1, 10))

    # --- Değerlendirme özeti ----------------------------------------------
    ozet_baslik = ParagraphStyle("OzetBaslik", parent=hucre_kalin, fontSize=9,
                                 textColor=colors.white)
    ozet = Table(
        [[Paragraph("DEĞERLENDİRME ÖZETİ", ozet_baslik)],
         [Paragraph(
             f"<b>Ön Değerlendirme:</b> {tr(teshis)}<br/>"
             f"<b>Hesaplanan Risk Skoru:</b> {tr(risk_metni)}<br/>"
             f"<b>Risk Kategorisi:</b> "
             f"<font color='#{kategori_rengi.hexval()[2:]}'><b>{tr(kategori_adi)}</b></font><br/>"
             f"<b>Değerlendirme Yöntemi:</b> Makine öğrenmesi tabanlı sınıflandırma "
             f"ve kural tabanlı klinik eşik kontrolü",
             hucre)]],
        colWidths=[icerik_genisligi],
    )
    ozet.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), LACIVERT),
        ("BACKGROUND", (0, 1), (0, 1), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.6, ACIK_GRI),
        ("LINEBEFORE", (0, 1), (0, 1), 3, kategori_rengi),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    akis.append(ozet)

    # --- 1. Klinik gerekçe -------------------------------------------------
    akis.append(Paragraph("1. KLİNİK GEREKÇE", bolum))
    akis.append(Paragraph(tr(analiz.get("gerekce")), normal))

    # --- 2. Bulgular ve ölçümler ------------------------------------------
    if analiz.get("bulgular"):
        akis.append(Paragraph("2. KLİNİK BULGULAR VE ÖLÇÜMLER", bolum))
        tablo_baslik = ParagraphStyle("TabloBaslik", parent=hucre_kalin,
                                      fontSize=8, textColor=colors.white)
        sira_stili = ParagraphStyle("Sira", parent=hucre, alignment=TA_CENTER)
        satirlar = [[Paragraph(b, tablo_baslik) for b in
                     ("#", "Parametre", "Ölçüm / Değer", "Durum", "Klinik Yorum")]]
        durum_zeminleri = []

        for sira, bulgu in enumerate(analiz["bulgular"], start=1):
            etiket, yazi_rengi, zemin_rengi = SEVIYE_GORUNUM.get(
                str(bulgu.get("seviye", "")).lower(), ("—", GRI, colors.white))
            durum_zeminleri.append((sira, zemin_rengi))
            durum_stili = ParagraphStyle(
                f"Durum{sira}", parent=hucre, alignment=TA_CENTER, fontSize=7.5,
                fontName=FONT_KALIN, textColor=yazi_rengi)
            satirlar.append([
                Paragraph(str(sira), sira_stili),
                Paragraph(tr(bulgu.get("baslik")), hucre_kalin),
                Paragraph(tr(bulgu.get("deger")), hucre),
                Paragraph(tr(etiket), durum_stili),
                Paragraph(tr(bulgu.get("yorum")), hucre_yorum),
            ])

        bulgu_tablosu = Table(
            satirlar, repeatRows=1,
            colWidths=[9 * mm, 30 * mm, 30 * mm, 21 * mm,
                       icerik_genisligi - 90 * mm])
        bulgu_stili = [
            ("BACKGROUND", (0, 0), (-1, 0), LACIVERT),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (0, 0), (0, -1), "CENTER"),
            ("GRID", (0, 0), (-1, -1), 0.4, ACIK_GRI),
            ("BOX", (0, 0), (-1, -1), 0.7, GRI),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
        for sira, zemin in durum_zeminleri:
            bulgu_stili.append(("BACKGROUND", (3, sira), (3, sira), zemin))
        bulgu_tablosu.setStyle(TableStyle(bulgu_stili))
        akis.append(bulgu_tablosu)

    # --- 3. Öneriler -------------------------------------------------------
    if analiz.get("tavsiyeler"):
        akis.append(Paragraph("3. ÖNERİLER VE İZLEM PLANI", bolum))
        oneri_tablosu = Table(
            [[Paragraph(f"{sira}.", hucre_kalin), Paragraph(tr(tavsiye), hucre)]
             for sira, tavsiye in enumerate(analiz["tavsiyeler"], start=1)],
            colWidths=[10 * mm, icerik_genisligi - 10 * mm])
        oneri_tablosu.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, -2), 0.3, ACIK_GRI),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        akis.append(oneri_tablosu)

    # --- 4. Yasal uyarı ----------------------------------------------------
    uyari = Table([[Paragraph(
        "<b>YASAL UYARI:</b> Bu belge, makine öğrenmesi tabanlı bir klinik karar "
        "destek sistemi tarafından otomatik olarak üretilmiş bir ön değerlendirme "
        "çıktısıdır. Tıbbi tanı, tedavi veya ilaç düzenlemesi amacıyla tek başına "
        "kullanılamaz. Nihai klinik karar, hastayı muayene eden uzman hekime aittir. "
        "Rapor, aşağıdaki alan hekim tarafından imzalanmadıkça onaylı sayılmaz.",
        kucuk)]], colWidths=[icerik_genisligi])
    uyari.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), ZEMIN),
        ("BOX", (0, 0), (-1, -1), 0.5, ACIK_GRI),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))

    # --- İmza bloğu --------------------------------------------------------
    bos_satirlar = "<br/>" * 4
    imza = Table([[
        Paragraph(f"<b>Değerlendiren Hekim</b>{bos_satirlar}"
                  "____________________________<br/>"
                  "<font size='7'>Ad Soyad / Uzmanlık · Kaşe ve İmza</font>",
                  imza_stili),
        Paragraph(f"<b>Onaylayan Sorumlu Hekim</b>{bos_satirlar}"
                  "____________________________<br/>"
                  "<font size='7'>Ad Soyad / Uzmanlık · Kaşe ve İmza</font>",
                  imza_stili),
    ]], colWidths=[icerik_genisligi / 2] * 2)
    imza.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))

    # Uyarı ve imza bloğu bölünmeden aynı sayfada kalır.
    akis.append(Spacer(1, 14))
    akis.append(KeepTogether([
        HRFlowable(width="100%", thickness=0.6, color=ACIK_GRI, spaceAfter=8),
        uyari, imza,
    ]))

    belge.build(
        akis, onFirstPage=ust_bilgi, onLaterPages=ust_bilgi,
        canvasmaker=lambda *a, **k: _SayfaliCanvas(*a, altlik=alt_bilgi, **k),
    )
    return tampon.getvalue()
