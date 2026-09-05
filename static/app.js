/* ==========================================================================
   Klinik AI Asistan — Ortak JavaScript Katmanı
   Menü, Türkçe sözlük ve PDF rapor şablonu tüm sayfalarda buradan gelir.
   ========================================================================== */

/* --- 0. İkon Katmanı (Lucide) --------------------------------------------
 * Arayüzdeki tüm simgeler Lucide setinden gelir (CDN ile yüklenir). Emoji
 * kullanılmaz: emoji her işletim sisteminde farklı çizilir, renk alamaz ve
 * metinle aynı optik ağırlığı tutturamaz.
 *
 * İkonlar önce <i data-lucide="ad"> yer tutucusu olarak yazılır; Lucide bunu
 * <svg>'ye çevirirken üzerindeki sınıfları (ör. "w-4 h-4") korur ve rengi
 * currentColor'dan alır. Bu yüzden boyut/renk tamamen Tailwind ile yönetilir.
 * ----------------------------------------------------------------------- */

/** Şablon dizilerinde kullanılacak ikon yer tutucusu üretir. */
function ikonHtml(ad, sinif = '') {
    return `<i data-lucide="${ad}"${sinif ? ` class="${sinif}"` : ''}></i>`;
}

/** DOM kurgusunda (textContent ile güvenli basımda) kullanılacak yer tutucu. */
function ikonYarat(ad, sinif = 'w-4 h-4') {
    const yerTutucu = document.createElement('i');
    yerTutucu.setAttribute('data-lucide', ad);
    yerTutucu.className = sinif;
    return yerTutucu;
}

/** Sayfaya yeni eklenen yer tutucuları gerçek SVG'ye çevirir.
 *  Dinamik içerik basan her yerden (tablo, modal, sonuç kutusu) çağrılır. */
function ikonlariYenile() {
    if (typeof lucide === 'undefined') return;
    lucide.createIcons({ attrs: { 'stroke-width': 1.75 } });
}

/* --- 1. Navigasyon (tek kaynak, 5 sayfada tekrar edilmiyor) --------------
 * Uygulama kabuğu (mobil üst çubuk + sabit sol menü) burada üretilir, böylece
 * beş HTML dosyasında tekrar edilmez. Sınıflar Tailwind yardımcı sınıflarıdır;
 * .nav-toggle / .nav-overlay yalnızca JavaScript'in tutunduğu seçicilerdir.
 * ----------------------------------------------------------------------- */
const SAYFALAR = [
    { href: '/',              ikon: 'clipboard-plus',   ad: 'Yeni Muayene' },
    { href: '/gecmis',        ikon: 'layout-dashboard', ad: 'Hasta Kayıtları' },
    { href: '/bilgi',         ikon: 'book-open',        ad: 'Klinik Bilgi Bankası' },
    { href: '/egitim',        ikon: 'activity',         ad: 'Model Performansı' },
    { href: '/geribildirim',  ikon: 'user-check',       ad: 'Doktor Geri Bildirimleri' }
];

function navKur() {
    const aktifYol = window.location.pathname.replace(/\/$/, '') || '/';
    const aktifSayfa = SAYFALAR.find(s => s.href === aktifYol);

    const linkler = SAYFALAR.map(s => {
        const aktif = s.href === aktifYol;
        return `<a href="${s.href}" class="menu-baglanti${aktif ? ' menu-baglanti--aktif' : ''}"` +
               `${aktif ? ' aria-current="page"' : ''}>` +
               `${ikonHtml(s.ikon)}<span>${s.ad}</span></a>`;
    }).join('');

    const marka = `<span class="marka-tile" aria-hidden="true">${ikonHtml('heart-pulse')}</span>`;

    const kabuk = document.createElement('div');
    kabuk.innerHTML = `
        <header class="ust-cubuk">
            <button type="button" class="nav-toggle" aria-label="Menüyü aç/kapat" aria-expanded="false">
                ${ikonHtml('menu')}
            </button>
            <span class="ust-cubuk__marka">
                ${marka}
                <span class="ust-cubuk__ad topbar__ad">Klinik AI</span>
            </span>
        </header>

        <div class="nav-overlay" hidden></div>

        <nav class="anamenu" aria-label="Ana menü">
            <div class="anamenu__marka">${marka}<span>Klinik AI</span></div>
            ${linkler}
            <p class="anamenu__alt">Yapay zeka destekli karar destek sistemi.<br>Nihai teşhis hekime aittir.</p>
        </nav>`;

    document.body.prepend(...kabuk.childNodes);

    if (aktifSayfa) {
        document.querySelector('.topbar__ad').textContent = aktifSayfa.ad;
    }

    const ac = document.querySelector('.nav-toggle');
    const perde = document.querySelector('.nav-overlay');
    const menu = document.querySelector('.anamenu');

    /* Menü mobilde ekran dışına kaydırılarak gizlenir; 1024px ve üzerinde
       style.css onu zaten kalıcı olarak açık tutar. */
    const menuAyarla = (acik) => {
        menu.classList.toggle('acik', acik);
        ac.setAttribute('aria-expanded', String(acik));
        perde.hidden = !acik;
        document.body.style.overflow = acik ? 'hidden' : '';
    };

    ac.addEventListener('click', () => menuAyarla(ac.getAttribute('aria-expanded') !== 'true'));
    perde.addEventListener('click', () => menuAyarla(false));
    document.addEventListener('keydown', e => { if (e.key === 'Escape') menuAyarla(false); });

    ikonlariYenile();
}

document.addEventListener('DOMContentLoaded', navKur);

/* --- 1.b Grafik Tipografisi ---------------------------------------------
 * Chart.js kendi varsayilan yazi tipini (Helvetica) kullanir. Arayuzun geri
 * kalaniyla ayni gorunmesi icin CSS'teki --font degiskeni grafiklere de
 * aktarilir; boylece sayfadaki tum yazilar tek bir aileden gelir.
 * ----------------------------------------------------------------------- */
(function grafikYaziTipi() {
    if (typeof Chart === 'undefined' || !Chart.defaults) return;

    /* Bu blok app.js'in en üst seviyesinde çalışır; buradaki bir hata
       dosyanın geri kalanını (cevir, veriHucresi, rozetHucresi …) hiç
       tanımlanmadan bırakırdı. Grafik tipografisi kritik değil, bu yüzden
       hata sessizce yutuluyor. */
    try {
        const kokStil = getComputedStyle(document.documentElement);
        const aile = (kokStil.getPropertyValue('--font') || '').trim()
            || "'Inter', 'Segoe UI', Arial, sans-serif";

        Chart.defaults.font.family = aile;
        Chart.defaults.font.size = 12;
        Chart.defaults.font.weight = 500;
        Chart.defaults.color = '#64748b';
        Chart.defaults.plugins.tooltip.titleFont = { family: aile, size: 12.5, weight: '600' };
        Chart.defaults.plugins.tooltip.bodyFont  = { family: aile, size: 12.5, weight: '500' };
        Chart.defaults.plugins.legend.labels.font = { family: aile, size: 12, weight: '500' };
    } catch (hata) {
        console.warn('Grafik yazı tipi ayarlanamadı:', hata);
    }
})();

/* --- 1.c API KATMANI (kimlik doğrulama) ----------------------------------
 * Uçların YALNIZCA BİR KISMI HTTP Basic Auth ile korunur. Analiz akışı
 * (/, POST /tahmin, POST /geribildirim) ürün demosu için herkese açıktır;
 * başka hastaların verisini döndüren uçlar (/kayitlar, /istatistik,
 * /geribildirim_listesi, /pdf-indir) korumalıdır.
 *
 * Korumalı bir sayfaya (ör. /gecmis) girildiğinde tarayıcı kimlik bilgilerini
 * bir kez sorar, origin için önbelleğe alır ve BUNDAN SONRAKİ tüm same-origin
 * isteklere "Authorization" başlığını KENDİSİ ekler. Herkese açık sayfalarda
 * ise hiç sorulmaz.
 *
 * Bu yüzden şifre JavaScript'te tutulmaz, elle başlık kurulmaz ve
 * localStorage'a hiçbir kimlik bilgisi yazılmaz — tarayıcının yerleşik
 * kimlik deposu kullanılır. Tek gereken, isteğin kimlik bilgisi taşımasına
 * izin veren `credentials` ayarıdır; aşağıda açıkça belirtilir.
 *
 * apiIstek() ayrıca yetki (401/403) ve hız sınırı (429) hatalarını anlaşılır
 * tek bir Türkçe mesaja çevirir; böylece her sayfa aynı hatayı ayrı ayrı
 * yorumlamak zorunda kalmaz.
 * ----------------------------------------------------------------------- */

/** Yetki hatalarını diğer hatalardan ayırt etmek için özel hata tipi. */
class YetkiHatasi extends Error {
    constructor(mesaj) {
        super(mesaj);
        this.name = 'YetkiHatasi';
    }
}

/** Hız sınırı (429) hatası. saniye: Retry-After başlığından okunan bekleme süresi. */
class HizSiniriHatasi extends Error {
    constructor(mesaj, saniye) {
        super(mesaj);
        this.name = 'HizSiniriHatasi';
        this.saniye = saniye;
    }
}

/**
 * Korumalı bir uç noktaya istek atar ve JSON yanıtı döndürür.
 *
 * @param {string} yol      Sunucu yolu, ör. '/kayitlar'
 * @param {object} [ayar]   { govde: <POST edilecek nesne> } veya fetch seçenekleri
 * @returns {Promise<object>} Çözümlenmiş JSON gövdesi
 * @throws {YetkiHatasi} 401/403 durumunda
 * @throws {Error} diğer HTTP ve ağ hatalarında
 */
async function apiIstek(yol, ayar = {}) {
    const { govde, ...digerleri } = ayar;

    const secenekler = {
        /* 'same-origin': tarayıcının önbellekteki Basic Auth kimliğini bu
           isteğe ekler. fetch()'in varsayılanı da budur; niyeti görünür
           kılmak ve ileride varsayılan değişse bile kırılmamak için
           açıkça yazılır. */
        credentials: 'same-origin',
        ...digerleri
    };

    if (govde !== undefined) {
        secenekler.method = secenekler.method || 'POST';
        secenekler.headers = { 'Content-Type': 'application/json', ...(secenekler.headers || {}) };
        secenekler.body = JSON.stringify(govde);
    }

    const yanit = await fetch(yol, secenekler);

    if (yanit.status === 401 || yanit.status === 403) {
        throw new YetkiHatasi(
            'Oturumunuz doğrulanamadı. Bu işlem için yetkili personel girişi ' +
            'gerekiyor; sayfayı yenileyip kullanıcı adı ve şifrenizi girin.'
        );
    }

    /* 429: sunucu hız sınırı. Gövdedeki Türkçe mesaj varsa o kullanılır;
       Retry-After başlığı kullanıcıya somut bir bekleme süresi verir. */
    if (yanit.status === 429) {
        const bekleme = parseInt(yanit.headers.get('Retry-After'), 10) || 60;
        let sunucuMesaji = null;
        try {
            sunucuMesaji = (await yanit.json()).detail;
        } catch (hata) { /* gövde JSON değilse varsayılan mesaj kullanılır */ }

        throw new HizSiniriHatasi(
            sunucuMesaji || `Çok fazla istek gönderildi. Lütfen ${bekleme} saniye bekleyip tekrar deneyin.`,
            bekleme
        );
    }

    /* Yanıt gövdesi JSON olmayabilir (ör. sunucu hata sayfası). */
    let veri = null;
    try {
        veri = await yanit.json();
    } catch (hata) {
        if (yanit.ok) throw new Error('Sunucudan beklenen biçimde yanıt alınamadı.');
    }

    if (!yanit.ok) {
        /* FastAPI hataları her zaman {"detail": ...} biçimindedir. Doğrulama
           hatalarında (422) detail bir DİZİdir: her öğe hangi alanın neden
           reddedildiğini söyler. Kullanıcıya "chol: Input should be less than
           or equal to 700" yerine "Kolesterol: 700'den küçük olmalı" demek
           için alan adı Türkçeleştirilir. */
        const ayrinti = veri && veri.detail;
        throw new Error(Array.isArray(ayrinti)
            ? ayrinti.map(dogrulamaHatasi).join(' · ')
            : (ayrinti || `Sunucu hatası: ${yanit.status}`));
    }

    return veri;
}

/** Pydantic doğrulama hatasını okunabilir tek satıra çevirir. */
function dogrulamaHatasi(hata) {
    /* loc = ["body", "chol"] -> son öğe alan adıdır. */
    const alan = Array.isArray(hata.loc) ? hata.loc[hata.loc.length - 1] : null;
    const ad = ALAN_ADLARI[alan] || alan || 'Alan';
    return `${ad}: ${hata.msg || hata.message || 'geçersiz değer'}`;
}

/** Korumalı bir dosyayı (PDF) yeni sekmede açar. Kimlik bilgisini tarayıcı ekler. */
function korumaliAc(yol) {
    window.open(yol, '_blank');
}

/* --- 1.d ORTAK EYLEMLER --------------------------------------------------
 * Geri bildirim gönderme ve PDF indirme, index.html ile kayitlar.html'de
 * neredeyse birebir aynı iki fonksiyon olarak kopyalanmıştı (yalnızca hasta
 * kimliğini nereden okudukları farklıydı). İkisi de buraya taşındı.
 * ----------------------------------------------------------------------- */

/** AI teşhisinin karşıtını döndürür (doktor "Yanlış" dediğinde kaydedilir). */
function karsitTeshis(teshis) {
    return riskliMi(teshis) ? TESHIS_SAGLIKLI : TESHIS_RISKLI;
}

/**
 * Uzman görüşünü sunucuya gönderir ve sonucu verilen elemana yazar.
 *
 * @param {number} hastaId  geri bildirim verilecek muayene kaydı
 * @param {string} teshis   yapay zekanın orijinal teşhisi
 * @param {string} durum    'Doğru' | 'Yanlış'
 * @param {HTMLElement} mesajElemani  sonucun gösterileceği eleman
 */
async function geribildirimGonder(hastaId, teshis, durum, mesajElemani) {
    try {
        const sonuc = await apiIstek('/geribildirim', {
            govde: {
                hasta_id: hastaId,
                orijinal_teshis: teshis,
                doktor_karari: durum === 'Doğru' ? teshis : karsitTeshis(teshis)
            }
        });
        mesajElemani.textContent = sonuc.mesaj || 'Geri bildiriminiz kaydedildi.';
    } catch (err) {
        mesajElemani.textContent = `Geri bildirim gönderilemedi: ${err.message}`;
    }
    mesajElemani.hidden = false;
}

/**
 * Sunucuda üretilen PDF raporunu yeni sekmede açar.
 *
 * @param {number} hastaId
 * @param {string} [jeton]  /tahmin ile gelen kısa ömürlü imzalı jeton.
 *   Verilirse ziyaretçiye şifre penceresi açılmaz. Verilmezse sunucu Basic
 *   Auth'a düşer ve yetkili personel kendi şifresiyle indirir.
 */
function raporIndir(hastaId, jeton) {
    if (!hastaId) {
        alert('Önce bir hasta analizi yapın veya listeden bir kayıt seçin.');
        return;
    }
    korumaliAc(`/pdf-indir/${hastaId}` + (jeton ? `?token=${encodeURIComponent(jeton)}` : ''));
}

/* --- 2. Türkçe Sözlük ---------------------------------------------------- */

/* Teşhis etiketleri. Sunucudaki klinik/ozellikler.py ile AYNI dizgiler
   olmak zorundadır: /geribildirim ucu bu iki değerden başkasını kabul etmez. */
const TESHIS_SAGLIKLI = 'Sağlıklı';
const TESHIS_RISKLI = 'Riskli (Hasta Olabilir)';

/* Doğrulama hatalarında alan adlarının Türkçe karşılığı. */
const ALAN_ADLARI = {
    ad: 'Ad', soyad: 'Soyad', age: 'Yaş', sex: 'Cinsiyet',
    cp: 'Göğüs ağrısı tipi', trestbps: 'Tansiyon', chol: 'Kolesterol',
    fbs: 'Açlık kan şekeri', restecg: 'EKG sonucu', thalch: 'Maks. kalp atış hızı',
    exang: 'Egzersiz ağrısı', oldpeak: 'ST depresyonu', slope: 'Eğim',
    ca: 'Damar sayısı', thal: 'Miyokard perfüzyonu'
};

const TR_SOZLUK = {
    'Male': 'Erkek', 'Female': 'Kadın',
    'male': 'Erkek', 'female': 'Kadın',
    'typical angina': 'Tipik Anjina', 'atypical angina': 'Atipik Anjina',
    'non-anginal': 'Anjinal Olmayan', 'non-anginal pain': 'Anjinal Olmayan', 'asymptomatic': 'Semptomsuz',
    'True': 'Evet', 'False': 'Hayır',
    'true': 'Evet', 'false': 'Hayır',
    '1': 'Evet', '0': 'Hayır',
    1: 'Evet', 0: 'Hayır',
    'normal': 'Normal', 'st-t abnormality': 'ST-T Anormalliği', 'lv hypertrophy': 'LV Hipertrofisi',
    'upsloping': 'Yukarı Eğimli', 'flat': 'Düz', 'downsloping': 'Aşağı Eğimli',
    'fixed defect': 'Sabit Kusur', 'reversable defect': 'Tersine Çevrilebilir', 'reversible defect': 'Tersine Çevrilebilir'
};

function cevir(deger) {
    if (deger === null || deger === undefined) return '';
    if (TR_SOZLUK[deger] !== undefined) return TR_SOZLUK[deger];
    const strVal = String(deger).trim();
    return TR_SOZLUK[strVal] !== undefined ? TR_SOZLUK[strVal] : deger;
}

/* Risk oranı sunucudan SAYI olarak gelir; biçimlendirme tek yerde yapılır.
   Eskiden sunucu "%73.20" metnini döndürüyor, rapor metni ise aynı değeri
   "%73" olarak yazıyordu; aynı ekranda iki farklı oran görünüyordu. */
function oranMetni(sayi) {
    const deger = Number(sayi);
    return isNaN(deger) ? '—' : `%${deger.toFixed(1)}`;
}

/* Risk çubuğunun genişliği için 0-100 aralığına kırpar. */
function oranSayisi(deger) {
    const sayi = typeof deger === 'number'
        ? deger
        : parseFloat(String(deger).replace('%', '').replace(',', '.'));
    return isNaN(sayi) ? 0 : Math.max(0, Math.min(100, sayi));
}

function riskliMi(teshis) {
    return String(teshis).includes('Riskli');
}

/* --- 2.b GÜVENLİ DOM YARDIMCILARI (XSS koruması) -------------------------
 * Hasta adı gibi dışarıdan gelen veriler ASLA innerHTML ile basılmaz.
 * textContent, içeriği her zaman düz metin olarak işler; içinde <script>
 * veya <img onerror=...> geçse bile tarayıcı bunu kod olarak çalıştırmaz.
 * ------------------------------------------------------------------------ */

/** İçeriği düz metin olan bir <td> üretir. */
function veriHucresi(metin, etiket, sinif) {
    const td = document.createElement('td');
    td.textContent = metin === null || metin === undefined ? '' : String(metin);
    if (sinif) td.className = sinif;
    if (etiket) td.dataset.label = etiket;   // mobil kart görünümündeki başlık
    return td;
}

/** İçinde rozet bulunan bir <td> üretir; rozet metni de düz metindir.
 *  ikonAdi verilirse metnin soluna aynı aileden bir çizgi ikon eklenir. */
function rozetHucresi(metin, rozetSinifi, etiket, ikonAdi) {
    const td = document.createElement('td');
    const rozet = document.createElement('span');
    rozet.className = rozetSinifi;

    const yazi = document.createElement('span');
    yazi.textContent = metin === null || metin === undefined ? '' : String(metin);

    if (ikonAdi) rozet.appendChild(ikonYarat(ikonAdi));
    rozet.appendChild(yazi);

    td.appendChild(rozet);
    if (etiket) td.dataset.label = etiket;
    return td;
}

/** Tablo boşken gösterilecek tek hücreli satırı üretir. */
function bosSatir(mesaj, kolonSayisi) {
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.colSpan = kolonSayisi;
    td.className = 't-bos';
    td.textContent = mesaj;
    tr.appendChild(td);
    return tr;
}

/* --- 3. PDF Raporu ------------------------------------------------------
 * PDF ARTIK İSTEMCİDE ÜRETİLMEZ. Rapor tek bir yerde, sunucuda
 * (main.py -> /pdf-indir/{hasta_id}, reportlab ile) hazırlanır.
 *
 * Önceden burada html2pdf tabanlı ikinci bir üretici daha vardı; aynı
 * hastanın raporu hangi ekrandan indirildiğine göre farklı içerikte
 * çıkıyordu (istemci sürümünde klinik gerekçe ve bulgu yorumları yoktu).
 * Tek yola indirildi.
 *
 * Kullanımı:  window.open(`/pdf-indir/${hastaId}`, '_blank');
 * ----------------------------------------------------------------------- */
