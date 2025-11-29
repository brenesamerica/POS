/**
 * Translation system for POS application (TPL-004)
 * Supports Hungarian (hu) and English (en) languages
 */

// Current language state
let currentLang = localStorage.getItem('pos_language') || 'hu';

// Translation dictionary
const translations = {
    hu: {
        // Environment
        testEnv: 'TESZT kornyezet',
        prodEnv: 'ELES RENDSZER',

        // Auth
        loggedInAs: 'Bejelentkezve mint',
        manageUsers: 'Felhasznalok kezelese',
        logout: 'Kijelentkezes',
        login: 'Bejelentkezes',
        username: 'Felhasznalonev',
        password: 'Jelszo',

        // Main menu
        posSystem: 'Ertekesitesi rendszer',
        marketOperations: 'Piaci muveletek',
        prepareMarket: 'Piac elokeszites',
        prepareMarketDesc: 'Termekek es LOT szamok beallitasa',
        marketMode: 'Piac mod',
        marketModeDesc: 'Ertekesites inditasa',
        marketHistory: 'Piaci elozmenyek',
        marketHistoryDesc: 'Korabbi munkaamenetek es statisztikak',
        management: 'Kezeles',
        categories: 'Kategoriak',
        categoriesDesc: 'Termekkategoriak kezelese',
        products: 'Termekek',
        productsDesc: 'Termekek megtekintese es szerkesztese',
        createReceipt: 'Szamla letrehozasa',
        createReceiptDesc: 'Kezi szamla letrehozas',
        roastTracker: 'Porkoles koveto',
        roastTrackerDesc: 'Porkoles, termeles es keszlet',
        system: 'Rendszer',

        // Market mode
        cart: 'Kosar',
        total: 'Osszesen',
        discount: 'Kedvezmeny',
        clearCart: 'Kosar torles',
        checkout: 'Fizetes',
        cash: 'Keszpenz',
        card: 'Kartya',
        email: 'Email',
        sendReceipt: 'Nyugta kuldese',
        noReceipt: 'Nincs nyugta',
        quantity: 'Mennyiseg',
        lotNumber: 'LOT szam',
        remaining: 'Maradvany',

        // Common actions
        save: 'Mentes',
        cancel: 'Megse',
        delete: 'Torles',
        edit: 'Szerkesztes',
        add: 'Hozzaadas',
        close: 'Bezaras',
        back: 'Vissza',
        search: 'Kereses',
        filter: 'Szures',

        // Messages
        success: 'Sikeres',
        error: 'Hiba',
        confirmDelete: 'Biztosan torolni szeretne?',
        loading: 'Betoltes...',
        noItems: 'Nincsenek elemek',

        // Market session
        sessionName: 'Munkamenet neve',
        initialCash: 'Kezdo keszpenz',
        createSession: 'Munkamenet letrehozasa',
        closeSession: 'Munkamenet lezarasa',
        activeSession: 'Aktiv munkamenet',
        noActiveSession: 'Nincs aktiv munkamenet'
    },
    en: {
        // Environment
        testEnv: 'TEST Environment',
        prodEnv: 'LIVE SYSTEM',

        // Auth
        loggedInAs: 'Logged in as',
        manageUsers: 'Manage Users',
        logout: 'Logout',
        login: 'Login',
        username: 'Username',
        password: 'Password',

        // Main menu
        posSystem: 'Point of Sale System',
        marketOperations: 'Market Operations',
        prepareMarket: 'Prepare Market',
        prepareMarketDesc: 'Set up products & LOT numbers',
        marketMode: 'Market Mode',
        marketModeDesc: 'Start selling',
        marketHistory: 'Market History',
        marketHistoryDesc: 'View past sessions & analytics',
        management: 'Management',
        categories: 'Categories',
        categoriesDesc: 'Manage product categories',
        products: 'Products',
        productsDesc: 'View & edit products',
        createReceipt: 'Create Receipt',
        createReceiptDesc: 'Manual receipt creation',
        roastTracker: 'Roast Tracker',
        roastTrackerDesc: 'Roasts, production & inventory',
        system: 'System',

        // Market mode
        cart: 'Cart',
        total: 'Total',
        discount: 'Discount',
        clearCart: 'Clear Cart',
        checkout: 'Checkout',
        cash: 'Cash',
        card: 'Card',
        email: 'Email',
        sendReceipt: 'Send Receipt',
        noReceipt: 'No Receipt',
        quantity: 'Quantity',
        lotNumber: 'LOT Number',
        remaining: 'Remaining',

        // Common actions
        save: 'Save',
        cancel: 'Cancel',
        delete: 'Delete',
        edit: 'Edit',
        add: 'Add',
        close: 'Close',
        back: 'Back',
        search: 'Search',
        filter: 'Filter',

        // Messages
        success: 'Success',
        error: 'Error',
        confirmDelete: 'Are you sure you want to delete?',
        loading: 'Loading...',
        noItems: 'No items',

        // Market session
        sessionName: 'Session Name',
        initialCash: 'Initial Cash',
        createSession: 'Create Session',
        closeSession: 'Close Session',
        activeSession: 'Active Session',
        noActiveSession: 'No active session'
    }
};

/**
 * Get translation for a key
 * @param {string} key - Translation key
 * @param {string} [lang] - Language code (defaults to currentLang)
 * @returns {string} Translated text or key if not found
 */
function t(key, lang = currentLang) {
    return translations[lang]?.[key] || translations['en']?.[key] || key;
}

/**
 * Set the current language and update all translated elements
 * @param {string} lang - Language code ('en' or 'hu')
 */
function setLanguage(lang) {
    currentLang = lang;
    localStorage.setItem('pos_language', lang);

    // Update toggle buttons
    document.querySelectorAll('.lang-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.lang === lang);
    });

    // Update all elements with data-i18n attribute
    document.querySelectorAll('[data-i18n]').forEach(el => {
        const key = el.dataset.i18n;
        if (translations[lang]?.[key]) {
            el.textContent = translations[lang][key];
        }
    });

    // Update placeholders
    document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
        const key = el.dataset.i18nPlaceholder;
        if (translations[lang]?.[key]) {
            el.placeholder = translations[lang][key];
        }
    });

    // Dispatch event for custom handling
    document.dispatchEvent(new CustomEvent('languageChanged', { detail: { lang } }));
}

/**
 * Initialize language on page load
 */
function initLanguage() {
    const savedLang = localStorage.getItem('pos_language') || 'hu';
    setLanguage(savedLang);
}

// Auto-initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initLanguage);
} else {
    initLanguage();
}
