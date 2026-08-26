/* ==========================================================================
   Klinik AI Asistan — Ortak JavaScript Katmanı
   Menü, Türkçe sözlük ve PDF rapor şablonu tüm sayfalarda buradan gelir.
   ========================================================================== */

/* --- 1. Navigasyon (tek kaynak, 4 sayfada tekrar edilmiyor) -------------- */
const SAYFALAR = [
    { href: '/',              ikon: '📝', ad: 'Yeni Muayene' },
    { href: '/gecmis',        ikon: '📂', ad: 'Hasta Kayıtları' },
    { href: '/bilgi',         ikon: '📖', ad: 'Klinik Bilgi Bankası' },
    { href: '/egitim',        ikon: '🧠', ad: 'Model Performansı' },
    { href: '/geribildirim',  ikon: '👨‍⚕️', ad: 'Doktor Geri Bildirimleri' }
];

function navKur() {
    const aktifYol = window.location.pathname.replace(/\/$/, '') || '/';
    const aktifSayfa = SAYFALAR.find(s => s.href === aktifYol);

    const linkler = SAYFALAR.map(s => {
        const aktif = s.href === aktifYol ? ' aria-current="page"' : '';
        return `<a href="${s.href}"${aktif}><span aria-hidden="true">${s.ikon}</span>${s.ad}</a>`;
    }).join('');

    const kabuk = document.createElement('div');
    kabuk.innerHTML = `
        <header class="topbar">
            <button class="nav-toggle" type="button" aria-label="Menüyü aç/kapat" aria-expanded="false">☰</button>
            <span class="topbar__title">🩺 Klinik AI</span>
        </header>
        <div class="nav-overlay" hidden></div>
        <nav class="sidebar" aria-label="Ana menü">
            <div class="sidebar__brand"><span aria-hidden="true">🩺</span> Klinik AI</div>
            ${linkler}
            <div class="sidebar__foot">Yapay zeka destekli karar destek sistemi.<br>Nihai teşhis hekime aittir.</div>
        </nav>`;

    document.body.prepend(...kabuk.childNodes);

    if (aktifSayfa) {
        document.querySelector('.topbar__title').textContent = aktifSayfa.ad;
    }

    const ac = document.querySelector('.nav-toggle');
    const perde = document.querySelector('.nav-overlay');

    const menuAyarla = (acik) => {
        document.body.classList.toggle('nav-acik', acik);
        ac.setAttribute('aria-expanded', String(acik));
        perde.hidden = !acik;
    };

    ac.addEventListener('click', () => menuAyarla(!document.body.classList.contains('nav-acik')));
    perde.addEventListener('click', () => menuAyarla(false));
    document.addEventListener('keydown', e => { if (e.key === 'Escape') menuAyarla(false); });
}

document.addEventListener('DOMContentLoaded', navKur);

/* --- 2. Türkçe Sözlük ---------------------------------------------------- */
const TR_SOZLUK = {
    'Male': 'Erkek', 'Female': 'Kadın',
    'typical angina': 'Tipik Anjina', 'atypical angina': 'Atipik Anjina',
    'non-anginal pain': 'Anjinal Olmayan', 'asymptomatic': 'Semptomsuz',
    'True': 'Evet', 'False': 'Hayır',
    'normal': 'Normal', 'st-t abnormality': 'ST-T Anormalliği', 'lv hypertrophy': 'LV Hipertrofisi',
    'upsloping': 'Yukarı Eğimli', 'flat': 'Düz', 'downsloping': 'Aşağı Eğimli',
    'fixed defect': 'Sabit Kusur', 'reversable defect': 'Tersine Çevrilebilir'
};

function cevir(deger) {
    return TR_SOZLUK[deger] !== undefined ? TR_SOZLUK[deger] : deger;
}

/* "%73.20" -> 73.2 (risk çubuğunu doldurmak için) */
function oranSayisi(metin) {
    const sayi = parseFloat(String(metin).replace('%', '').replace(',', '.'));
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

/** İçinde rozet bulunan bir <td> üretir; rozet metni de düz metindir. */
function rozetHucresi(metin, rozetSinifi, etiket) {
    const td = document.createElement('td');
    const rozet = document.createElement('span');
    rozet.className = rozetSinifi;
    rozet.textContent = metin === null || metin === undefined ? '' : String(metin);
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

/* --- 3. PDF Raporu (index.html ve kayitlar.html ortak kullanır) ---------- */
/*
 * rapor = { ad, soyad, yas, cinsiyet, cp, tansiyon, kolesterol, fbs, restecg,
 *           thalch, exang, oldpeak, slope, ca, thal,
 *           teshis, risk, aciklama, tarih, altBaslik, dosyaAdi }
 */
function pdfRaporIndir(rapor) {
    /* Etiket/değer çiftlerinden tablo üretir; değerler textContent ile basılır. */
    const bulguTablosu = (ciftler) => {
        const tablo = document.createElement('table');
        ciftler.forEach(([etiket, deger]) => {
            const satir = document.createElement('tr');
            const etiketHucresi = document.createElement('td');
            etiketHucresi.textContent = etiket;
            const degerHucresi = document.createElement('td');
            degerHucresi.textContent = deger === null || deger === undefined ? '' : String(deger);
            satir.append(etiketHucresi, degerHucresi);
            tablo.appendChild(satir);
        });
        return tablo;
    };

    const kutu = (sinif, ...cocuklar) => {
        const dugum = document.createElement('div');
        dugum.className = sinif;
        dugum.append(...cocuklar);
        return dugum;
    };

    const metinli = (etiket, sinif, metin) => {
        const dugum = document.createElement(etiket);
        if (sinif) dugum.className = sinif;
        dugum.textContent = metin;
        return dugum;
    };

    let sablon = document.getElementById('pdfSablonu');
    if (!sablon) {
        sablon = document.createElement('div');
        sablon.id = 'pdfSablonu';
        sablon.className = 'pdf-doc';
        document.body.appendChild(sablon);
    }

    // Başlık bloğu
    const baslik = kutu('pdf-doc__baslik',
        metinli('h1', null, 'KLİNİK AI KARDİYOLOJİ MERKEZİ'),
        metinli('p', null, rapor.altBaslik || 'Kardiyolojik Risk Analiz Raporu')
    );

    // İki bulgu sütunu
    const solKolon = kutu('pdf-doc__kolon',
        metinli('h3', null, '👤 Kişisel ve Temel Bulgular'),
        bulguTablosu([
            ['Hasta Adı Soyadı', `${rapor.ad} ${rapor.soyad}`.toUpperCase()],
            ['Yaş / Cinsiyet', `${rapor.yas} / ${cevir(rapor.cinsiyet)}`],
            ['Rapor Tarihi', rapor.tarih],
            ['Tansiyon (Trestbps)', `${rapor.tansiyon} mmHg`],
            ['Kolesterol (Chol)', `${rapor.kolesterol} mg/dl`],
            ['Açlık Şekeri > 120', cevir(rapor.fbs)],
            ['Göğüs Ağrısı Tipi', cevir(rapor.cp)]
        ])
    );

    const sagKolon = kutu('pdf-doc__kolon',
        metinli('h3', null, '🩺 EKG ve Efor Bulguları'),
        bulguTablosu([
            ['Maks. Kalp Atışı', `${rapor.thalch} bpm`],
            ['EKG Sonucu', cevir(rapor.restecg)],
            ['Egzersiz Ağrısı', cevir(rapor.exang)],
            ['ST Depresyonu', rapor.oldpeak],
            ['Eğim (Slope)', cevir(rapor.slope)],
            ['Damar Sayısı (CA)', rapor.ca],
            ['Miyokart Perfüzyonu', cevir(rapor.thal)]
        ])
    );

    // Teşhis bloğu
    const teshisBasligi = metinli('h2', 'pdf-doc__teshis', String(rapor.teshis).toUpperCase());
    teshisBasligi.style.color = riskliMi(rapor.teshis) ? '#dc2626' : '#16a34a';

    const sonucBlogu = kutu('pdf-doc__sonuc',
        metinli('h3', null, 'YAPAY ZEKA TEŞHİSİ'),
        teshisBasligi,
        metinli('div', 'pdf-doc__risk', `Hesaplanan Risk: ${rapor.risk}`),
        metinli('p', 'pdf-doc__aciklama', rapor.aciklama || '')
    );

    // İmza bloğu (sabit metin)
    const imza = document.createElement('div');
    imza.className = 'pdf-doc__imza';
    imza.innerHTML = '<p><strong>İmza / Kaşe</strong></p><div class="bosluk"></div>' +
                     '<p><strong>Dr. Yapay Zeka (AI)</strong></p>' +
                     '<small>Algoritma: Random Forest + XAI</small>';

    sablon.replaceChildren(
        baslik,
        kutu('pdf-doc__kolonlar', solKolon, sagKolon),
        sonucBlogu,
        imza
    );

    sablon.style.display = 'block';

    const dosya = rapor.dosyaAdi || `${rapor.ad}_${rapor.soyad}_Rapor.pdf`;
    html2pdf()
        .set({
            margin: 10,
            filename: dosya,
            image: { type: 'jpeg', quality: 1 },
            html2canvas: { scale: 2, useCORS: true },
            jsPDF: { unit: 'mm', format: 'a4', orientation: 'portrait' }
        })
        .from(sablon)
        .save()
        .then(() => { sablon.style.display = 'none'; });
}
