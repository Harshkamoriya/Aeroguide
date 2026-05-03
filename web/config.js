// AeroGuide — Backend URL Configuration
// Local dev: points to same origin (FastAPI serves both frontend & API)
// Production: update PRODUCTION_BACKEND to your Railway/Render URL after deploying backend

const PRODUCTION_BACKEND = 'https://aeroguide-backend.railway.app'; // ← update this

const BACKEND_URL = (() => {
    const h = window.location.hostname;
    if (h === 'localhost' || h === '127.0.0.1') return window.location.origin;
    return PRODUCTION_BACKEND;
})();

const ADMIN_KEY_DEFAULT = 'aeroguide2024';
