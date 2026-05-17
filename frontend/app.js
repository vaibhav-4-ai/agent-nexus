const form = document.getElementById('taskForm');
const goalInput = document.getElementById('goalInput');
const submitBtn = document.getElementById('submitBtn');
const statusPanel = document.getElementById('statusPanel');
const taskStatus = document.getElementById('taskStatus');
const stepsCompleted = document.getElementById('stepsCompleted');
const taskTime = document.getElementById('taskTime');
const terminalBody = document.getElementById('terminalBody');
const taskLoader = document.getElementById('taskLoader');

// Inference-settings (BYOK) panel
const inferenceModeBadge = document.getElementById('inferenceModeBadge');
const byokFields = document.getElementById('byokFields');
const byokProvider = document.getElementById('byokProvider');
const byokModel = document.getElementById('byokModel');
const byokApiKey = document.getElementById('byokApiKey');
const byokRemember = document.getElementById('byokRemember');

let currentTaskId = null;
let startTime = 0;
let timerInterval = null;
let ws = null;

// ---------------------------------------------------------------------------
// Inference settings (BYOK)
// ---------------------------------------------------------------------------
const BYOK_STORAGE_KEY = 'agent-nexus/byok-config';

// Provider → suggested LiteLLM-canonical model id
const BYOK_MODEL_DEFAULTS = {
    openai: 'openai/gpt-4o-mini',
    anthropic: 'anthropic/claude-3-5-haiku-latest',
    gemini: 'gemini/gemini-1.5-flash',
    groq: 'groq/llama-3.3-70b-versatile',
};

function getInferenceMode() {
    const checked = document.querySelector('input[name="inferenceMode"]:checked');
    return checked ? checked.value : 'server';
}

function updateInferenceUI() {
    const mode = getInferenceMode();
    if (mode === 'byok') {
        byokFields.hidden = false;
        inferenceModeBadge.textContent = 'Custom credentials';
    } else {
        byokFields.hidden = true;
        inferenceModeBadge.textContent = 'Server credentials';
    }
}

function suggestModelForProvider() {
    const provider = byokProvider.value;
    if (!byokModel.value || Object.values(BYOK_MODEL_DEFAULTS).includes(byokModel.value)) {
        byokModel.value = BYOK_MODEL_DEFAULTS[provider] || '';
    }
    byokModel.placeholder = BYOK_MODEL_DEFAULTS[provider] || '';
}

function loadByokFromStorage() {
    try {
        const raw = localStorage.getItem(BYOK_STORAGE_KEY);
        if (!raw) return;
        const saved = JSON.parse(raw);
        if (saved.provider) byokProvider.value = saved.provider;
        if (saved.model) byokModel.value = saved.model;
        if (saved.apiKey) byokApiKey.value = saved.apiKey;
        if (saved.mode === 'byok') {
            document.querySelector('input[name="inferenceMode"][value="byok"]').checked = true;
        }
        byokRemember.checked = true;
        updateInferenceUI();
    } catch (e) {
        // Stored config is corrupted — clear it silently.
        localStorage.removeItem(BYOK_STORAGE_KEY);
    }
}

function persistByokIfRequested() {
    if (byokRemember.checked && getInferenceMode() === 'byok') {
        const payload = {
            mode: 'byok',
            provider: byokProvider.value,
            model: byokModel.value,
            apiKey: byokApiKey.value,  // localStorage only — never leaves the browser
        };
        localStorage.setItem(BYOK_STORAGE_KEY, JSON.stringify(payload));
    } else {
        localStorage.removeItem(BYOK_STORAGE_KEY);
    }
}

function buildByokPayload() {
    if (getInferenceMode() !== 'byok') return null;
    const provider = byokProvider.value.trim();
    const model = byokModel.value.trim();
    const apiKey = byokApiKey.value.trim();
    if (!provider || !model || !apiKey) {
        throw new Error('Inference settings: provider, model, and API key are all required.');
    }
    return { provider, model, api_key: apiKey };
}

// Wire up panel events
document.querySelectorAll('input[name="inferenceMode"]').forEach(el => {
    el.addEventListener('change', updateInferenceUI);
});
if (byokProvider) byokProvider.addEventListener('change', suggestModelForProvider);
loadByokFromStorage();

function appendToTerminal(text, type = 'system') {
    const line = document.createElement('div');
    line.className = `terminal-line ${type}`;
    line.innerHTML = text;
    terminalBody.appendChild(line);
    terminalBody.scrollTop = terminalBody.scrollHeight;
}

function startTimer() {
    startTime = Date.now();
    clearInterval(timerInterval);
    timerInterval = setInterval(() => {
        const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
        taskTime.textContent = `${elapsed}s`;
    }, 100);
}

function stopTimer() {
    clearInterval(timerInterval);
}

function updateStatus(status) {
    taskStatus.textContent = status;
    taskStatus.className = `status-badge ${status.toLowerCase()}`;
    if (status === 'completed' || status === 'failed') {
        taskLoader.style.display = 'none';
        stopTimer();
        submitBtn.disabled = false;
        submitBtn.innerHTML = '<span>Start New Task</span><i class="fa-solid fa-arrow-right"></i>';
    } else {
        taskLoader.style.display = 'block';
    }
}

function connectWebSocket(taskId) {
    // Determine WS protocol based on current HTTP protocol
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    // Handle specific port or path if served via HF Spaces or localhost
    const host = window.location.host;
    const wsUrl = `${protocol}//${host}/api/v1/tasks/${taskId}/stream`;
    
    appendToTerminal(`[System] Connecting to WebSocket stream...`, 'info');
    
    ws = new WebSocket(wsUrl);
    
    ws.onmessage = (event) => {
        try {
            const msg = JSON.parse(event.data);
            
            if (msg.type === 'step_update') {
                const step = msg.data;
                const stepNum = step.step_number || '?';
                
                if (step.status === 'pending') {
                    appendToTerminal(`Step ${stepNum}: ${step.description}`, 'step');
                    if (step.tool) {
                        appendToTerminal(`Using tool <span class="highlight">${step.tool}</span>`, 'tool');
                    }
                } else if (step.status === 'completed') {
                    const confidence = step.verification?.confidence || 1.0;
                    appendToTerminal(`✓ Success (confidence: ${confidence.toFixed(2)})`, 'result');
                    if (step.result) {
                        let shortResult = step.result.length > 500 ? step.result.substring(0, 500) + '...' : step.result;
                        
                        // Parse 'File written successfully: filename' and convert to a clickable download link
                        const fileMatch = shortResult.match(/File written successfully:\s+([^\s\(]+)/);
                        if (fileMatch) {
                            const filePath = fileMatch[1];
                            shortResult = shortResult.replace(
                                fileMatch[0], 
                                `File written successfully: <a href="/workspace/${filePath}" target="_blank" style="color: #64ffda; text-decoration: underline; font-weight: bold;">${filePath} <i class="fa-solid fa-up-right-from-square" style="font-size: 0.8em; margin-left: 4px;"></i></a>`
                            );
                        }
                        
                        appendToTerminal(`Output:\n${shortResult}`, 'system');
                    }
                    stepsCompleted.textContent = parseInt(stepsCompleted.textContent) + 1;
                } else if (step.status === 'failed' || step.status === 'escalated') {
                    appendToTerminal(`✗ Failed`, 'error');
                }
            } else if (msg.type === 'task_completed') {
                const data = msg.data;
                updateStatus(data.status);
                if (data.status === 'completed') {
                    appendToTerminal(`Task completed successfully in ${(data.duration_ms/1000).toFixed(2)}s`, 'success');
                } else {
                    appendToTerminal(`Task failed: ${data.error || 'Unknown error'}`, 'error');
                }
            }
        } catch (e) {
            console.error('Error parsing WS message', e);
        }
    };
    
    ws.onerror = (error) => {
        console.error('WebSocket Error:', error);
        appendToTerminal('[System] Connection error. Polling instead...', 'error');
        // Fallback to polling if WS fails
        pollTaskStatus(taskId);
    };
}

async function pollTaskStatus(taskId) {
    // If WS works, we don't need this, but good fallback
    if (ws && ws.readyState === WebSocket.OPEN) return;
    
    try {
        const res = await fetch(`/api/v1/tasks/${taskId}`);
        if (!res.ok) return;
        const data = await res.json();
        
        if (data.status === 'completed' || data.status === 'failed') {
            updateStatus(data.status);
            if (data.status === 'completed') {
                appendToTerminal(`Task completed successfully.`, 'success');
            } else {
                appendToTerminal(`Task failed: ${data.error}`, 'error');
            }
        } else {
            // Check trace for new steps
            const trace = data.execution_trace || [];
            // Simple logic for fallback: just log the last step if it exists
            if (trace.length > 0) {
                const lastStep = trace[trace.length - 1];
                if (lastStep.status === 'completed') {
                    stepsCompleted.textContent = trace.filter(s => s.status === 'completed').length;
                }
            }
            setTimeout(() => pollTaskStatus(taskId), 2000);
        }
    } catch (e) {
        console.error('Polling error', e);
    }
}

form.addEventListener('submit', async (e) => {
    e.preventDefault();
    
    const goal = goalInput.value.trim();
    if (!goal) return;
    
    // Reset UI
    terminalBody.innerHTML = '';
    appendToTerminal(goal, 'user');
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<span>Executing...</span><div class="loader" style="width: 16px; height: 16px;"></div>';
    statusPanel.classList.remove('hidden');
    stepsCompleted.textContent = '0';
    taskTime.textContent = '0.0s';
    
    updateStatus('running');
    startTimer();
    
    try {
        appendToTerminal('[System] Submitting task to Orchestrator Engine...', 'info');

        // Assemble request body, including BYOK override if the user opted in.
        const body = { goal };
        let byok = null;
        try {
            byok = buildByokPayload();
        } catch (e) {
            appendToTerminal(`[BYOK] ${e.message}`, 'error');
            submitBtn.disabled = false;
            submitBtn.innerHTML = '<span>Execute Task</span><i class="fa-solid fa-arrow-right"></i>';
            stopTimer();
            return;
        }
        if (byok) {
            body.byok = byok;
            appendToTerminal(`[BYOK] Routing this task through ${byok.provider} (${byok.model}). Credentials never leave your browser except to make this request.`, 'info');
            persistByokIfRequested();
        }

        // Use relative URL to work on both localhost and Hugging Face
        const response = await fetch('/api/v1/tasks', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        
        if (!response.ok) {
            throw new Error(`API Error: ${response.status}`);
        }
        
        const data = await response.json();
        currentTaskId = data.task_id;
        
        appendToTerminal(`[System] Task created (ID: ${currentTaskId.substring(0,8)}...)`, 'info');
        
        // Connect to WebSocket for live updates
        connectWebSocket(currentTaskId);
        
    } catch (error) {
        appendToTerminal(`Error: ${error.message}`, 'error');
        updateStatus('failed');
    }
});
