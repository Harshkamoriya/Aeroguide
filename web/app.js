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
const liveCaption    = document.getElementById('live-caption');
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
    addMessage(text, 'agent', null, null, true);
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
                'agent', null, null, true
            );
        } else {
            callBanner.classList.add('fail-call');
            callBannerText.innerHTML = `<i class="fa-solid fa-phone-slash"></i> Call not sent (verify number in Twilio trial)`;
            addMessage(
                `ℹ️ Your flight details are saved. <strong>${flightNumber}</strong> departs from <strong>Gate ${gate}, ${term}</strong>` +
                (time ? ` at <strong>${time}</strong>` : '') + `.`,
                'agent', null, null, true
            );
        }
    } catch {
        callBannerText.textContent = 'Call service unavailable.';
    }
    setTimeout(() => { callBanner.style.opacity = '0'; }, 5000);
    setTimeout(() => { callBanner.style.display = 'none'; callBanner.style.opacity = '1'; callBanner.classList.remove('success-call','fail-call'); }, 5600);
}

// ── Chat helpers ─────────────────────────────────────────────────────────────
function addMessage(text, sender, mapHint = null, directions = null, asHtml = false) {
    const wrap = document.createElement('div');
    wrap.className = `msg ${sender}`;

    const extra = document.createElement('div');
    if (mapHint && Object.values(mapHint).some(v => v)) {
        const parts = [mapHint.terminal, mapHint.floor, mapHint.landmark].filter(Boolean);
        if (parts.length) {
            const hint = document.createElement('div');
            hint.className = 'map-hint';
            hint.innerHTML = `<i class="fa-solid fa-map-location-dot"></i><span>${parts.join(' › ')}</span>`;
            extra.appendChild(hint);
        }
    }
    if (directions?.steps?.length) {
        const steps = directions.steps.map((s, i) => `<li><span class="step-num">${i + 1}</span>${s}</li>`).join('');
        const dist  = directions.distance_m ? `<span class="nav-dist"><i class="fa-solid fa-person-walking"></i> ~${directions.distance_m}m</span>` : '';
        const nav = document.createElement('div');
        nav.className = 'nav-directions';
        nav.innerHTML = `<div class="nav-header"><i class="fa-solid fa-route"></i><strong>Directions to ${directions.destination_name || 'destination'}</strong>${dist}</div><ol class="nav-steps">${steps}</ol>`;
        extra.appendChild(nav);
    }

    const icon = sender === 'user' ? 'fa-user' : 'fa-robot';
    wrap.innerHTML = `<div class="msg-avatar"><i class="fa-solid ${icon}"></i></div>`;
    const body = document.createElement('div');
    body.className = 'msg-body';
    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble';
    if (asHtml) bubble.innerHTML = text;
    else bubble.textContent = text;
    body.appendChild(bubble);
    while (extra.firstChild) body.appendChild(extra.firstChild);
    wrap.appendChild(body);
    chatMessages.appendChild(wrap);
    chatMessages.scrollTop = chatMessages.scrollHeight;
    return wrap;
}

function setBubbleText(wrap, text) {
    const bubble = wrap?.querySelector('.msg-bubble');
    if (bubble) bubble.textContent = text;
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

// ── One turn at a time, one voice at a time ──────────────────────────────────
const turnQueue = [];
let turnBusy = false;

let audioFinish = null;

function stopAudio() {
    const finish = audioFinish;
    audioFinish = null;
    try { window.speechSynthesis?.cancel(); } catch { /* no speech engine */ }
    if (finish) finish();
}

function playAudio(text) {
    const spoken = String(text || '').replace(/<[^>]+>/g, '').trim();
    if (!spoken || !window.speechSynthesis) return Promise.resolve();

    stopAudio();
    return new Promise((resolve) => {
        let settled = false;
        const finish = () => {
            if (settled) return;
            settled = true;
            if (audioFinish === finish) audioFinish = null;
            resolve();
        };
        audioFinish = finish;
        const utter = new SpeechSynthesisUtterance(spoken);
        utter.onend = finish;
        utter.onerror = finish;
        window.speechSynthesis.speak(utter);
    });
}

function enqueueTurn(job) {
    turnQueue.push(job);
    drainTurns();
}

async function drainTurns() {
    if (turnBusy || recording || micStarting) return;
    const job = turnQueue.shift();
    if (!job) return;
    turnBusy = true;
    try {
        await runTurn(job);
    } finally {
        turnBusy = false;
        drainTurns();
    }
}

async function runTurn(job) {
    const text = (job.text || '').trim();
    if (!text) return;
    if (job.bubble) {
        setBubbleText(job.bubble, text);
        job.bubble.classList.remove('pending');
    }
    showLiveCaption('');
    await askAgent(text);
}

function showLiveCaption(text) {
    if (liveCaption) liveCaption.textContent = text || '';
}

async function askAgent(text) {
    const tid = showTyping();
    try {
        const body = { text, session_id: sessionId };
        if (userLat !== null) { body.user_lat = userLat; body.user_lng = userLng; }
        const res = await fetch(`${API_BASE}/test/full`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error('agent failed');
        const data = await res.json();
        removeTyping(tid);
        let reply = data.response || '';
        if (data.followup) reply += (reply ? ' ' : '') + data.followup;
        addMessage(reply, 'agent', data.map_hint, data.directions);
        if (reply.trim()) await playAudio(reply);
    } catch {
        removeTyping(tid);
        addMessage("Sorry, I couldn't reach the server. Please try again.", 'agent');
    }
}

// ── Text form ─────────────────────────────────────────────────────────────────
textForm.addEventListener('submit', e => {
    e.preventDefault();
    const t = textInput.value.trim();
    if (!t) return;
    textInput.value = '';
    stopAudio();
    if (recording) finishUtterance(latestPartial, true);
    enqueueTurn({ text: t, bubble: addMessage(t, 'user') });
});

// ── Streaming speech (AssemblyAI, same idea as Verviq) ───────────────────────
const MIN_UTTERANCE_CHARS = 2;
const MIN_SPEECH_MS = 400;

let recording = false;
let micStarting = false;
let sttSocket = null;
let sttActx = null;
let sttProc = null;
let sttSrc = null;
let sttStream = null;
let liveBubble = null;
let latestPartial = '';
let speechStartedAt = 0;
let utteranceSent = false;

function floatTo16BitPCM(input) {
    const buffer = new ArrayBuffer(input.length * 2);
    const view = new DataView(buffer);
    for (let i = 0, offset = 0; i < input.length; i++, offset += 2) {
        const s = Math.max(-1, Math.min(1, input[i]));
        view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
    return buffer;
}

function paintPartial(text) {
    if (!text) return;
    latestPartial = text;
    if (!liveBubble) {
        liveBubble = addMessage(text, 'user');
        liveBubble.classList.add('pending');
    } else {
        setBubbleText(liveBubble, text);
        liveBubble.classList.remove('pending');
    }
    showLiveCaption(text);
}

function teardownMic() {
    recording = false;
    try { sttProc?.disconnect(); } catch { /* already stopped */ }
    try { sttSrc?.disconnect(); } catch { /* already stopped */ }
    try { sttStream?.getTracks().forEach(track => track.stop()); } catch { /* already stopped */ }
    try { sttActx?.close(); } catch { /* already stopped */ }
    sttProc = sttSrc = sttStream = sttActx = null;
    if (sttSocket) {
        sttSocket.onmessage = null;
        sttSocket.onclose = null;
        try { sttSocket.close(); } catch { /* already closed */ }
        sttSocket = null;
    }
    micBtn.classList.remove('recording');
}

function finishUtterance(text, quietEmpty = false) {
    if (utteranceSent) {
        teardownMic();
        return;
    }
    utteranceSent = true;
    const bubble = liveBubble;
    liveBubble = null;
    latestPartial = '';
    teardownMic();
    showLiveCaption('');

    const finalText = (text || '').trim();
    if (finalText.length < MIN_UTTERANCE_CHARS) {
        bubble?.remove();
        micStatus.textContent = quietEmpty ? 'Tap to speak' : "Didn't catch that. Tap to speak";
        drainTurns();
        return;
    }
    const target = bubble || addMessage(finalText, 'user');
    setBubbleText(target, finalText);
    target.classList.remove('pending');
    micStatus.textContent = 'Tap to speak';
    enqueueTurn({ text: finalText, bubble: target });
}

function handleSttMessage(raw) {
    let payload;
    try { payload = JSON.parse(raw); } catch { return; }
    if (utteranceSent || !recording) return;

    if (payload.type === 'Error') {
        showLiveCaption(payload.error || 'Speech error');
        return;
    }
    if (payload.type !== 'Turn') return;

    const text = (payload.transcript || '').trim();
    if (text) {
        if (!speechStartedAt) speechStartedAt = Date.now();
        paintPartial(text);
    }
    if (!payload.end_of_turn || !payload.turn_is_formatted) return;

    const finalText = text || latestPartial;
    if (finalText.length < MIN_UTTERANCE_CHARS) return;
    const spokenFor = speechStartedAt ? Date.now() - speechStartedAt : 0;
    if (spokenFor < MIN_SPEECH_MS) return;
    finishUtterance(finalText);
}

async function startStreaming() {
    utteranceSent = false;
    latestPartial = '';
    speechStartedAt = 0;
    stopAudio();
    try { window.speechSynthesis?.resume(); } catch { /* ignore */ }

    const tokenRes = await fetch(`${API_BASE}/stt/token`);
    if (!tokenRes.ok) {
        const err = await tokenRes.json().catch(() => ({}));
        throw new Error(err.detail || 'Speech service is not configured');
    }
    const tokenData = await tokenRes.json();
    if (!tokenData.token) throw new Error('Speech service did not return a token');

    const params = new URLSearchParams({
        sample_rate: '16000',
        encoding: 'pcm_s16le',
        token: tokenData.token,
        format_turns: 'true',
        end_of_turn_confidence_threshold: '0.75',
        min_end_of_turn_silence_when_confident: '1000',
        max_turn_silence: '2500',
    });
    const ws = new WebSocket(`wss://streaming.assemblyai.com/v3/ws?${params}`);
    sttSocket = ws;
    await new Promise((resolve, reject) => {
        const fail = () => reject(new Error('Could not connect to the speech service'));
        ws.addEventListener('open', () => resolve(), { once: true });
        ws.addEventListener('error', fail, { once: true });
    });
    if (utteranceSent) {
        try { ws.close(); } catch { /* user already moved on */ }
        return;
    }

    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    sttStream = stream;
    sttActx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
    if (sttActx.state === 'suspended') await sttActx.resume();
    if (utteranceSent) {
        teardownMic();
        return;
    }
    sttSrc = sttActx.createMediaStreamSource(stream);
    sttProc = sttActx.createScriptProcessor(4096, 1, 1);
    const gain = sttActx.createGain();
    gain.gain.value = 0;
    sttSrc.connect(sttProc);
    sttProc.connect(gain);
    gain.connect(sttActx.destination);

    recording = true;
    liveBubble = addMessage('Listening…', 'user');
    liveBubble.classList.add('pending');
    showLiveCaption('Listening…');
    micBtn.classList.add('recording');
    micStatus.textContent = 'Listening… pause to send';

    sttProc.onaudioprocess = (e) => {
        if (!recording || turnBusy || ws.readyState !== WebSocket.OPEN) return;
        ws.send(floatTo16BitPCM(e.inputBuffer.getChannelData(0)));
    };
    ws.onmessage = (msg) => handleSttMessage(msg.data);
    ws.onclose = () => {
        if (!utteranceSent && recording) {
            teardownMic();
            liveBubble?.remove();
            liveBubble = null;
            showLiveCaption('');
            micStatus.textContent = 'Speech connection closed. Tap to speak';
        }
    };
}

micBtn.addEventListener('click', async () => {
    if (recording) {
        finishUtterance(latestPartial);
        return;
    }
    if (micStarting) return;
    micStarting = true;
    try {
        const prime = new SpeechSynthesisUtterance(' ');
        prime.volume = 0;
        window.speechSynthesis?.speak(prime);
    } catch { /* browser will speak later if it can */ }
    try {
        await startStreaming();
    } catch (err) {
        teardownMic();
        liveBubble?.remove();
        liveBubble = null;
        showLiveCaption('');
        micStatus.textContent = 'Tap to speak';
        alert(err.message || 'Could not start the microphone');
    } finally {
        micStarting = false;
        if (!recording) drainTurns();
    }
});
