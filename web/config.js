// AeroGuide — Backend URL Configuration
// For ngrok demo: paste your ngrok URL below (e.g. https://abc123.ngrok-free.app)
// For local dev: leave NGROK_URL as '' — it auto-points to localhost

const NGROK_URL = ''; // ← paste ngrok URL here when demoing (e.g. 'https://abc123.ngrok-free.app')

const BACKEND_URL = (() => {
    if (NGROK_URL) return NGROK_URL;
    const h = window.location.hostname;
    if (h === 'localhost' || h === '127.0.0.1') return window.location.origin;
    return NGROK_URL || window.location.origin;
})();

const ADMIN_KEY_DEFAULT = 'aeroguide2024';
