/** @type {import('tailwindcss').Config} */
//
// Klinik AI Asistan - Tailwind derleme yapılandırması
//
// Play CDN (cdn.tailwindcss.com) kaldırıldı; CSS artık burada derlenip
// static/tailwind.css dosyasına yazılır. Böylece uygulama internet
// erişimi olmayan klinik/intranet ağlarında da eksiksiz çalışır.
//
// Derlemek için:  npm run css        (tek seferlik, küçültülmüş)
//                 npm run css:watch  (geliştirirken sürekli)
//
// ÖNEMLİ: Tailwind yalnızca "content" listesinde GÖRDÜĞÜ sınıfları üretir.
// Sınıf adları JavaScript içinde de geçtiği için static/*.js taranır.
// Sınıf adlarını asla parça birleştirerek (`bg-${renk}-500`) kurmayın;
// tarayıcı bunları göremez ve stil üretilmez. Bunun yerine tam sınıf
// dizgisini koşulla seçin: kosul ? 'bg-red-500' : 'bg-emerald-500'.
module.exports = {
    content: [
        "./*.html",
        "./static/**/*.js",
    ],
    theme: {
        extend: {
            fontFamily: {
                // style.css'teki --font değişkeniyle aynı aile
                sans: ["Inter", "Segoe UI", "system-ui", "-apple-system", "Roboto", "Arial", "sans-serif"],
            },
        },
    },
    plugins: [],
};
