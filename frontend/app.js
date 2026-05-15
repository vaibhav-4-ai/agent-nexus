const form = document.getElementById('taskForm');
const goalInput = document.getElementById('goalInput');
const submitBtn = document.getElementById('submitBtn');
const statusPanel = document.getElementById('statusPanel');
const taskStatus = document.getElementById('taskStatus');
const stepsCompleted = document.getElementById('stepsCompleted');
const taskTime = document.getElementById('taskTime');
const terminalBody = document.getElementById('terminalBody');
const taskLoader = document.getElementById('taskLoader');

let currentTaskId = null;
let startTime = 0;
let timerInterval = null;
let ws = null;

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
        
        // Use relative URL to work on both localhost and Hugging Face
        const response = await fetch('/api/v1/tasks', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ goal: goal })
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
