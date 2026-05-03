const API_BASE = typeof BACKEND_URL !== 'undefined' ? BACKEND_URL : window.location.origin;

// ── Session & state ──────────────────────────────────────────────────────────
const sessionId = 'web_' + Math.random().toString(36).substring(2, 10);
let currentUser = null;

// ── GPS ──────────────────────────────────────────────────────────────────────
let userLat = null, userLng = null;
if (navigator.geolocation) {
    navigator.geolocation.watchPosition(
        p => { userLat = p.coords.latitude; userLng = p.coords.longitude; },
        () => {},
        { enableHighAccuracy: true, maximumAge: 5000 }
    );
}

// ── DOM refs ─────────────────────────────────────────────────────────────────
const pageLanding    = document.getElementById('page-landing');
const loginModal     = document.getElementById('login-modal');
const chatApp        = document.getElementById('chat-app');
const loginForm      = document.getElementById('login-form');
const loginBtnText   = document.getElementById('login-btn-text');
const loginBtnLoad   = document.getElementById('login-btn-loading');
const loginBtn       = document.getElementById('login-btn');
const loginError     = document.getElementById('login-error');
const chatMessages   = document.getElementById('chat-messages');
const textForm       = document.getElementById('text-form');
const textInput      = document.getElementById('text-input');
const micBtn         = document.getElementById('mic-btn');
const micStatus      = document.getElementById('mic-status');
const callBanner     = document.getElementById('call-banner');
const callBannerText = document.getElementById('call-banner-text');
const userBadge      = document.getElementById('user-badge');
const userBadgeName  = document.getElementById('user-badge-name');

// ── Landing → Login ──────────────────────────────────────────────────────────
document.getElementById('btn-get-started').addEventListener('click', () => {
    loginModal.classList.remove('hidden');
});
document.getElementById('btn-close-login').addEventListener('click', () => {
    loginModal.classList.add('hidden');
});
loginModal.addEventListener('click', e => {
    if (e.target === loginModal) loginModal.classList.add('hidden');
});

// ── Login form submit ────────────────────────────────────────────────────────
loginForm.addEventListener('submit', async e => {
    e.preventDefault();
    loginError.textContent = '';
    loginBtnText.style.display = 'none';
    loginBtnLoad.style.display = 'inline';
    loginBtn.disabled = true;

    const name         = document.getElementById('login-name').value.trim();
    const mobileRaw    = document.getElementById('login-mobile').value.trim().replace(/\s/g, '');
    const flightNumber = document.getElementById('login-flight').value.trim().toUpperCase();
    const mobile       = '+91' + mobileRaw;

    try {
        // 1. Register in DB
        const regRes = await fetch(`${API_BASE}/register`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, mobile_number: mobile, flight_number: flightNumber, session_id: sessionId })
        });
        if (!regRes.ok) {
            const err = await regRes.json().catch(() => ({}));
            throw new Error(err.detail || 'Registration failed. Try again.');
        }

        // 2. Fetch flight info
        let flightInfo = null;
        try {
            const flRes = await fetch(`${API_BASE}/flight/${flightNumber}`);
            if (flRes.ok) {
                const fl = await flRes.json();
                flightInfo = fl.schedules?.[0] || null;
            }
        } catch { /* flight lookup optional */ }

        currentUser = { name, mobile, flightNumber, flightInfo };

        // 3. Transition to chat
        loginModal.classList.add('hidden');
        pageLanding.classList.add('fade-out');
        setTimeout(() => { pageLanding.style.display = 'none'; }, 700);
        chatApp.classList.remove('hidden');

        // 4. Show user badge
        userBadge.style.display = 'flex';
        userBadgeName.textContent = name.split(' ')[0];

        // 5. Welcome message
        showWelcome(name, flightNumber, flightInfo);

        // 6. Schedule demo call after 5 seconds
        scheduleReminderCall(name, mobile, flightNumber, flightInfo);

    } catch (err) {
        loginError.textContent = err.message;
    } finally {
        loginBtnText.style.display = 'inline';
        loginBtnLoad.style.display = 'none';
        loginBtn.disabled = false;
    }
});

function showWelcome(name, flightNumber, info) {
    let text = `Welcome, <strong>${name}</strong>! ✈️`;
    if (info) {
        const gate = info.gate_code || '—';
        const term = info.terminal_code || '—';
        const dest = info.dest_city || '—';
        const time = info.scheduled_time ? info.scheduled_time.substring(0, 5) : '—';
        text += ` Your flight <strong>${flightNumber}</strong> to <strong>${dest}</strong> departs at <strong>${time}</strong> from <strong>Gate ${gate}, ${term}</strong>. Ask me anything!`;
    } else {
        text += ` Flight <strong>${flightNumber}</strong> registered. How can I help you?`;
    }
    addMessage(text, 'agent');
}

function scheduleReminderCall(name, mobile, flightNumber, info) {
    const gate = info?.gate_code || 'A5';
    const term = info?.terminal_code || 'T1';
    const dest = info?.dest_city || 'your destination';
    const time = info?.scheduled_time ? info.scheduled_time.substring(0, 5) : '';

    let secs = 5;
    callBanner.style.display = 'flex';
    callBannerText.textContent = `Preparing your flight reminder call in ${secs}s...`;

    const tick = setInterval(() => {
        secs--;
        if (secs > 0) callBannerText.textContent = `Calling your phone in ${secs}s...`;
        else {
            clearInterval(tick);
            fireReminderCall(name, mobile, flightNumber, dest, gate, term, time);
        }
    }, 1000);
}

async function fireReminderCall(name, mobile, flightNumber, dest, gate, term, time) {
    callBannerText.innerHTML = '<i class="fa-solid fa-phone-volume fa-shake"></i> Calling your phone now...';
    try {
        const res = await fetch(`${API_BASE}/test/call`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ to_number: mobile, name, flight_number: flightNumber, destination: dest, gate_code: gate, terminal: term })
        });
        if (res.ok) {
            callBanner.classList.add('success-call');
            callBannerText.innerHTML = `<i class="fa-solid fa-phone-check"></i> Reminder call sent to ${mobile}`;
            addMessage(
                `📞 A departure reminder call has been sent to <strong>${mobile}</strong>. ` +
                `Flight <strong>${flightNumber}</strong> → <strong>${dest}</strong>` +
                (time ? ` at <strong>${time}</strong>` : '') +
                ` · Gate ${gate} · ${term}`,
                'agent'
            );
        } else {
            callBanner.classList.add('fail-call');
            callBannerText.innerHTML = `<i class="fa-solid fa-phone-slash"></i> Call not sent (verify number in Twilio trial)`;
            addMessage(
                `ℹ️ Your flight details are saved. <strong>${flightNumber}</strong> departs from <strong>Gate ${gate}, ${term}</strong>` +
                (time ? ` at <strong>${time}</strong>` : '') + `.`,
                'agent'
            );
        }
    } catch {
        callBannerText.textContent = 'Call service unavailable.';
    }
    setTimeout(() => { callBanner.style.opacity = '0'; }, 5000);
    setTimeout(() => { callBanner.style.display = 'none'; callBanner.style.opacity = '1'; callBanner.classList.remove('success-call','fail-call'); }, 5600);
}

// ── Chat helpers ─────────────────────────────────────────────────────────────
function addMessage(text, sender, mapHint = null, directions = null) {
    const wrap = document.createElement('div');
    wrap.className = `msg ${sender}`;

    let extra = '';
    if (mapHint && Object.values(mapHint).some(v => v)) {
        const parts = [mapHint.terminal, mapHint.floor, mapHint.landmark].filter(Boolean);
        if (parts.length) extra += `<div class="map-hint"><i class="fa-solid fa-map-location-dot"></i><span>${parts.join(' › ')}</span></div>`;
    }
    if (directions?.steps?.length) {
        const steps = directions.steps.map((s, i) => `<li><span class="step-num">${i+1}</span>${s}</li>`).join('');
        const dist  = directions.distance_m ? `<span class="nav-dist"><i class="fa-solid fa-person-walking"></i> ~${directions.distance_m}m</span>` : '';
        extra += `<div class="nav-directions"><div class="nav-header"><i class="fa-solid fa-route"></i><strong>Directions to ${directions.destination_name || 'destination'}</strong>${dist}</div><ol class="nav-steps">${steps}</ol></div>`;
    }

    const icon = sender === 'user' ? 'fa-user' : 'fa-robot';
    wrap.innerHTML = `
        <div class="msg-avatar"><i class="fa-solid ${icon}"></i></div>
        <div class="msg-body"><div class="msg-bubble">${text}</div>${extra}</div>`;
    chatMessages.appendChild(wrap);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

function showTyping() {
    const id  = 'ty-' + Date.now();
    const div = document.createElement('div');
    div.className = 'msg agent'; div.id = id;
    div.innerHTML = `<div class="msg-avatar"><i class="fa-solid fa-robot"></i></div><div class="msg-body"><div class="msg-bubble"><i class="fa-solid fa-ellipsis fa-fade"></i></div></div>`;
    chatMessages.appendChild(div);
    chatMessages.scrollTop = chatMessages.scrollHeight;
    return id;
}
const removeTyping = id => document.getElementById(id)?.remove();

// ── API ───────────────────────────────────────────────────────────────────────
async function processText(text) {
    addMessage(text, 'user');
    const tid = showTyping();
    try {
        const body = { text, session_id: sessionId };
        if (userLat !== null) { body.user_lat = userLat; body.user_lng = userLng; }
        const res  = await fetch(`${API_BASE}/test/full`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        if (!res.ok) throw new Error();
        const data = await res.json();
        removeTyping(tid);
        let reply = data.response;
        if (data.followup) reply += ' ' + data.followup;
        addMessage(reply, 'agent', data.map_hint, data.directions);
        playAudio(reply);
    } catch {
        removeTyping(tid);
        addMessage("Sorry, I couldn't reach the server. Please try again.", 'agent');
    }
}

async function transcribeAudio(blob) {
    const tid = showTyping();
    micStatus.textContent = 'Processing...';
    try {
        const fd = new FormData(); fd.append('audio', blob, 'audio.wav');
        const res  = await fetch(`${API_BASE}/test/transcribe`, { method: 'POST', body: fd });
        if (!res.ok) throw new Error();
        const data = await res.json();
        removeTyping(tid);
        if (data.text) processText(data.text);
        else addMessage("Couldn't hear that clearly — please try again.", 'agent');
    } catch {
        removeTyping(tid);
        addMessage('Voice processing error. Please try typing.', 'agent');
    } finally { micStatus.textContent = 'Tap to speak'; }
}

async function playAudio(text) {
    try {
        const res = await fetch(`${API_BASE}/test/synthesize`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) });
        if (res.ok) new Audio(URL.createObjectURL(await res.blob())).play();
    } catch { /* TTS optional */ }
}

// ── Text form ─────────────────────────────────────────────────────────────────
textForm.addEventListener('submit', e => {
    e.preventDefault();
    const t = textInput.value.trim();
    if (t) { textInput.value = ''; processText(t); }
});

// ── Voice recording ───────────────────────────────────────────────────────────
let isRec = false, actx, proc, src, chunks = [], stream;

micBtn.addEventListener('click', async () => {
    if (isRec) {
        isRec = false; micBtn.classList.remove('recording'); micStatus.textContent = 'Processing...';
        proc.disconnect(); src.disconnect(); stream?.getTracks().forEach(t => t.stop());
        let len = 0; for (const c of chunks) len += c.length;
        const samples = new Float32Array(len); let off = 0;
        for (const c of chunks) { samples.set(c, off); off += c.length; }
        transcribeAudio(encodeWAV(samples, actx.sampleRate));
        return;
    }
    try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        actx   = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
        chunks = [];
        src    = actx.createMediaStreamSource(stream);
        proc   = actx.createScriptProcessor(4096, 1, 1);
        const gain = actx.createGain(); gain.gain.value = 0;
        src.connect(proc); proc.connect(gain); gain.connect(actx.destination);
        proc.onaudioprocess = e => { if (isRec) chunks.push(new Float32Array(e.inputBuffer.getChannelData(0))); };
        isRec = true; micBtn.classList.add('recording'); micStatus.textContent = 'Listening... tap to stop';
    } catch { alert('Please allow microphone access.'); }
});

function encodeWAV(samples, sr) {
    const buf  = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(buf);
    const ws   = (o, s) => { for (let i = 0; i < s.length; i++) view.setUint8(o + i, s.charCodeAt(i)); };
    ws(0,'RIFF'); view.setUint32(4, 36 + samples.length * 2, true);
    ws(8,'WAVE'); ws(12,'fmt '); view.setUint32(16, 16, true);
    view.setUint16(20,1,true); view.setUint16(22,1,true);
    view.setUint32(24,sr,true); view.setUint32(28,sr*2,true);
    view.setUint16(32,2,true); view.setUint16(34,16,true);
    ws(36,'data'); view.setUint32(40, samples.length * 2, true);
    let o = 44;
    for (let i = 0; i < samples.length; i++, o += 2) {
        const s = Math.max(-1, Math.min(1, samples[i]));
        view.setInt16(o, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
    }
    return new Blob([view], { type: 'audio/wav' });
}
