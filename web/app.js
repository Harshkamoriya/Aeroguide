const chatContainer = document.getElementById('chat-container');
const textForm = document.getElementById('text-form');
const textInput = document.getElementById('text-input');
const micBtn = document.getElementById('mic-btn');
const micStatus = document.getElementById('mic-status');

// Generate a random session ID for the browser user
const sessionId = 'web_' + Math.random().toString(36).substring(2, 10);
const API_BASE = window.location.origin;

let mediaRecorder;
let audioChunks = [];
let isRecording = false;

// ── UI Helpers ─────────────────────────────────────────────────────────────

function addMessage(text, sender, mapHint = null) {
    const msgDiv = document.createElement('div');
    msgDiv.className = `message ${sender}-message`;

    let avatarIcon = sender === 'user' ? 'fa-user' : 'fa-robot';
    
    let mapHtml = '';
    if (mapHint && Object.values(mapHint).some(v => v)) {
        const parts = [mapHint.terminal, mapHint.floor, mapHint.landmark].filter(Boolean);
        if (parts.length > 0) {
            mapHtml = `
                <div class="map-hint">
                    <i class="fa-solid fa-map-location-dot"></i>
                    <span>${parts.join(' › ')}</span>
                </div>
            `;
        }
    }

    msgDiv.innerHTML = `
        <div class="avatar"><i class="fa-solid ${avatarIcon}"></i></div>
        <div>
            <div class="bubble">${text}</div>
            ${mapHtml}
        </div>
    `;

    chatContainer.appendChild(msgDiv);
    chatContainer.scrollTop = chatContainer.scrollHeight;
}

function showTypingIndicator() {
    const id = 'typing-' + Date.now();
    const msgDiv = document.createElement('div');
    msgDiv.className = `message agent-message`;
    msgDiv.id = id;
    msgDiv.innerHTML = `
        <div class="avatar"><i class="fa-solid fa-robot"></i></div>
        <div class="bubble"><i class="fa-solid fa-ellipsis fa-fade"></i></div>
    `;
    chatContainer.appendChild(msgDiv);
    chatContainer.scrollTop = chatContainer.scrollHeight;
    return id;
}

function removeTypingIndicator(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
}

// ── API Calls ──────────────────────────────────────────────────────────────

async function processText(text) {
    addMessage(text, 'user');
    const typingId = showTypingIndicator();

    try {
        const response = await fetch(`${API_BASE}/test/full`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: text, session_id: sessionId })
        });
        
        if (!response.ok) throw new Error("Server error");
        const data = await response.json();
        
        removeTypingIndicator(typingId);
        
        let reply = data.response;
        if (data.followup) reply += " " + data.followup;
        
        addMessage(reply, 'agent', data.map_hint);
        
        // Play TTS
        playAudioForText(reply);

    } catch (err) {
        console.error(err);
        removeTypingIndicator(typingId);
        addMessage("Sorry, I'm having trouble connecting to the server.", 'agent');
    }
}

async function transcribeAudio(audioBlob) {
    const typingId = showTypingIndicator();
    micStatus.innerText = "Processing audio...";
    
    try {
        const formData = new FormData();
        formData.append('audio', audioBlob, 'audio.wav');

        const response = await fetch(`${API_BASE}/test/transcribe`, {
            method: 'POST',
            body: formData
        });

        if (!response.ok) throw new Error("Transcription failed");
        
        const data = await response.json();
        removeTypingIndicator(typingId);
        
        if (data.text) {
            processText(data.text);
        } else {
            addMessage("I couldn't hear that clearly. Could you try again?", 'agent');
        }

    } catch (err) {
        console.error(err);
        removeTypingIndicator(typingId);
        addMessage("Sorry, there was an error processing your voice.", 'agent');
    } finally {
        micStatus.innerText = "Tap to speak";
    }
}

async function playAudioForText(text) {
    try {
        const response = await fetch(`${API_BASE}/test/synthesize`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: text })
        });
        
        if (!response.ok) throw new Error("Synthesis failed");
        
        const blob = await response.blob();
        const audioUrl = URL.createObjectURL(blob);
        const audio = new Audio(audioUrl);
        audio.play();
    } catch (err) {
        console.error("Audio playback error:", err);
    }
}

// ── Event Listeners ────────────────────────────────────────────────────────

textForm.addEventListener('submit', (e) => {
    e.preventDefault();
    const text = textInput.value.trim();
    if (text) {
        textInput.value = '';
        processText(text);
    }
});

micBtn.addEventListener('click', async () => {
    if (isRecording) {
        // Stop recording
        mediaRecorder.stop();
        micBtn.classList.remove('recording');
        micStatus.innerText = "Processing...";
        isRecording = false;
        return;
    }

    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        mediaRecorder = new MediaRecorder(stream);
        audioChunks = [];

        mediaRecorder.ondataavailable = e => {
            if (e.data.size > 0) audioChunks.push(e.data);
        };

        mediaRecorder.onstop = () => {
            const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
            transcribeAudio(audioBlob);
            stream.getTracks().forEach(track => track.stop());
        };

        mediaRecorder.start();
        isRecording = true;
        micBtn.classList.add('recording');
        micStatus.innerText = "Listening... tap to stop";
    } catch (err) {
        console.error("Microphone access denied:", err);
        alert("Please allow microphone access to use voice features.");
    }
});

