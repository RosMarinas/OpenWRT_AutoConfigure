from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import uvicorn
import requests
import json
import os
from pathlib import Path
import asyncio
from typing import Dict, List, Optional
import logging
import threading
import time
from contextlib import asynccontextmanager
import httpx

# Import print capture functionality
from print_capture import start_print_capture, stop_print_capture, get_print_messages, set_print_callback

# Configure logging to capture all logs
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("web_app.log")
    ]
)
logger = logging.getLogger(__name__)

# Store logs for display in the UI
logs = []

# Create a custom handler to capture logs
class LogCaptureHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.logs = []
        self.max_logs = 1000  # Keep the last 1000 logs
    
    def emit(self, record):
        log_entry = self.format(record)
        self.logs.append(log_entry)
        if len(self.logs) > self.max_logs:
            self.logs.pop(0)
    
    def get_logs(self):
        return self.logs
    
    def clear(self):
        self.logs = []

# Add the custom handler to the logger
log_capture = LogCaptureHandler()
log_capture.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(log_capture)

# Also capture uvicorn access logs
uvicorn_logger = logging.getLogger("uvicorn.access")
uvicorn_logger.addHandler(log_capture)

# Function to add logs
def add_log(message):
    logs.append(message)
    # Use logger.info directly instead of print to avoid recursion
    logger.info(message)
    # Keep only the last 100 logs
    if len(logs) > 100:
        logs.pop(0)

# Set up print callback
def print_callback(message):
    # Format backend messages for better display
    if message.startswith("BACKEND:"):
        # Remove the "BACKEND:" prefix for cleaner display
        formatted_message = message.replace("BACKEND:", "").strip()
    else:
        formatted_message = message
    
    # Add special markers for progress tracking
    if "检索相关配置块" in formatted_message:
        add_log("[PROGRESS] Retrieving relevant configurations")
    elif "LLM 响应" in formatted_message:
        add_log("[PROGRESS] LLM Response received")
    elif "脚本验证通过" in formatted_message:
        add_log("[PROGRESS] Script validation passed")
    elif "开始执行脚本" in formatted_message:
        add_log("[PROGRESS] Starting script execution")
    elif "脚本执行完成" in formatted_message:
        add_log("[PROGRESS] Script execution completed")
    elif "配置已更新" in formatted_message or "新增知识单元" in formatted_message:
        add_log("[PROGRESS] Configuration updated")
    
    # Always add the original message to logs
    add_log(formatted_message)

# Define lifespan context manager for FastAPI
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup event
    set_print_callback(print_callback)
    start_print_capture()
    add_log("Print capture started")
    
    # Check if backend service is available
    try:
        response = requests.get("http://127.0.0.1:8000/docs", timeout=5)
        if response.status_code == 200:
            add_log("Backend service is available")
        else:
            add_log(f"Backend service returned status code: {response.status_code}")
    except requests.exceptions.RequestException as e:
        add_log(f"Backend service is not available: {str(e)}")
        add_log("The web application will still start, but API calls may fail")
    
    yield
    
    # Shutdown event
    stop_print_capture()
    add_log("Print capture stopped")

# Create FastAPI app with lifespan
app = FastAPI(title="OpenWRT UCI Configuration Assistant", lifespan=lifespan)

# Create directories for templates and static files
os.makedirs("templates", exist_ok=True)
os.makedirs("static", exist_ok=True)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Templates
templates = Jinja2Templates(directory="templates")

# Define request model
class ScriptRequest(BaseModel):
    command: str
    router_ip: str

# API endpoint to get logs
@app.get("/api/logs")
async def get_logs():
    # Get any new print messages
    new_messages = get_print_messages()
    for message in new_messages:
        add_log(message)
    
    # Return all captured logs
    return {"logs": log_capture.get_logs()}

# API endpoint to clear logs
@app.post("/api/clear_logs")
async def clear_logs():
    log_capture.clear()
    logs.clear()
    return {"status": "success"}

# API endpoint to check backend status
@app.get("/api/backend_status")
async def backend_status():
    try:
        response = requests.get("http://127.0.0.1:8000/docs", timeout=5)
        return {"status": "available", "status_code": response.status_code}
    except requests.exceptions.RequestException as e:
        return {"status": "unavailable", "error": str(e)}

# API endpoint to call the backend service
@app.post("/api/generate_script")
async def generate_script(request: ScriptRequest):
    add_log(f"Generating script for command: {request.command}")
    add_log(f"Router IP: {request.router_ip}")
    
    url = "http://127.0.0.1:8000/generate_script"
    data = {
        "command": request.command,
        "router_ip": request.router_ip
    }
    headers = {
        "Content-Type": "application/json"
    }
    
    try:
        add_log("Sending request to backend service...")
        response = requests.post(url, json=data, headers=headers, timeout=200)
        response.raise_for_status()
        result = response.json()
        
        # Process backend messages
        if "messages" in result:
            for message in result["messages"]:
                print_callback(message)
        
        add_log(f"Script generated successfully: {result.get('script', 'No script returned')}")
        return result
    except requests.exceptions.HTTPError as http_err:
        error_msg = f"HTTP error occurred: {http_err}"
        add_log(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)
    except requests.exceptions.RequestException as req_err:
        error_msg = f"Request error occurred: {req_err}"
        add_log(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)

# Home page
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "logs": log_capture.get_logs()})

# Create HTML template
def create_html_template():
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>OpenWRT UCI Configuration Assistant</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/css/bootstrap.min.css" rel="stylesheet">
        <style>
            body {
                padding-top: 20px;
                padding-bottom: 20px;
            }
            .log-container {
                height: 800px;
                overflow-y: auto;
                background-color: #FAFAD2;
                color: #d4d4d4;
                border: 1px solid #333;
                border-radius: 0.25rem;
                padding: 10px;
                font-family: 'Courier New', monospace;
                font-size: 0.9rem;
                white-space: pre-wrap;
                word-wrap: break-word;
            }
            .log-entry {
                margin-bottom: 2px;
                border-bottom: 1px solid #333;
                padding-bottom: 2px;
            }
            .log-entry:hover {
                background-color: #2a2a2a;
            }
            .log-info {
                color: #569cd6;
            }
            .log-error {
                color: #f14c4c;
            }
            .log-warning {
                color: #dcdcaa;
            }
            .log-success {
                color: #4ec9b0;
            }
            .log-http {
                color: #9cdcfe;
            }
            .log-step {
                color: #ce9178;
                font-weight: bold;
            }
            .log-backend {
                color: #9cdcfe;
                font-style: italic;
            }
            .result-container {
                margin-top: 20px;
            }
            .loading {
                display: none;
                text-align: center;
                margin: 20px 0;
            }
            .backend-status {
                margin-bottom: 10px;
                padding: 5px;
                border-radius: 4px;
            }
            .backend-status.available {
                background-color: #d4edda;
                color: #155724;
            }
            .backend-status.unavailable {
                background-color: #f8d7da;
                color: #721c24;
            }
            .log-controls {
                margin-bottom: 10px;
            }
            .log-controls button {
                margin-right: 5px;
            }
            .progress-indicator {
                margin-top: 10px;
                margin-bottom: 10px;
                padding: 10px;
                background-color: #87CEFA;
                border-radius: 5px;
                display: none;
            }
            .progress-step {
                margin-bottom: 5px;
                padding: 5px;
                border-radius: 3px;
            }
            .progress-step.active {
                background-color: #FFD700;
                border-left: 3px solid #569cd6;
            }
            .progress-step.completed {
                background-color: #FFFFE0;
                border-left: 3px solid #4ec9b0;
            }
            .progress-step.pending {
                background-color: #FAFAD2;
                border-left: 3px solid #666;
                color: #666;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1 class="text-center mb-4">OpenWRT UCI Configuration Assistant</h1>
            
            <div class="row">
                <div class="col-md-6">
                    <div class="card">
                        <div class="card-header">
                            <h5 class="card-title">Generate Configuration Script</h5>
                        </div>
                        <div class="card-body">
                            <div id="backendStatus" class="backend-status">
                                Checking backend status...
                            </div>
                            
                            <form id="scriptForm">
                                <div class="mb-3">
                                    <label for="command" class="form-label">Command</label>
                                    <textarea class="form-control" id="command" rows="3" placeholder="Enter your command here..."></textarea>
                                </div>
                                <div class="mb-3">
                                    <label for="routerIp" class="form-label">Router IP</label>
                                    <input type="text" class="form-control" id="routerIp" value="192.168.6.1">
                                </div>
                                <button type="submit" class="btn btn-primary">Generate Script</button>
                            </form>
                            
                            <div class="loading" id="loading">
                                <div class="spinner-border text-primary" role="status">
                                    <span class="visually-hidden">Loading...</span>
                                </div>
                                <p>Generating script, please wait...</p>
                            </div>
                            
                            <div class="progress-indicator" id="progressIndicator">
                                <h6>Progress:</h6>
                                <div class="progress-step pending" id="step1">1. Analyzing command</div>
                                <div class="progress-step pending" id="step2">2. Retrieving relevant configurations</div>
                                <div class="progress-step pending" id="step3">3. Generating script</div>
                                <div class="progress-step pending" id="step4">4. Validating script</div>
                                <div class="progress-step pending" id="step5">5. Executing on router</div>
                                <div class="progress-step pending" id="step6">6. Updating knowledge base</div>
                            </div>
                        </div>
                    </div>
                    
                    <div class="card mt-4 result-container" id="resultCard" style="display: none;">
                        <div class="card-header">
                            <h5 class="card-title">Generated Script</h5>
                        </div>
                        <div class="card-body">
                            <pre id="scriptResult" class="bg-light p-3 rounded"></pre>
                        </div>
                    </div>
                </div>
                
                <div class="col-md-6">
                    <div class="card">
                        <div class="card-header d-flex justify-content-between align-items-center">
                            <h5 class="card-title mb-0">Backend Logs</h5>
                            <div class="log-controls">
                                <button class="btn btn-sm btn-outline-secondary" id="clearLogs">Clear Logs</button>
                                <button class="btn btn-sm btn-outline-secondary" id="toggleAutoScroll">Auto-scroll: ON</button>
                            </div>
                        </div>
                        <div class="card-body">
                            <div class="log-container" id="logContainer">
                                {% for log in logs %}
                                <div class="log-entry {% if 'ERROR' in log %}log-error{% elif 'WARNING' in log %}log-warning{% elif 'INFO' in log %}log-info{% elif 'HTTP' in log %}log-http{% elif 'success' in log.lower() %}log-success{% elif 'step' in log.lower() %}log-step{% elif 'backend' in log.lower() %}log-backend{% endif %}">{{ log }}</div>
                                {% endfor %}
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        
        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/js/bootstrap.bundle.min.js"></script>
        <script>
            document.addEventListener('DOMContentLoaded', function() {
                const scriptForm = document.getElementById('scriptForm');
                const loading = document.getElementById('loading');
                const resultCard = document.getElementById('resultCard');
                const scriptResult = document.getElementById('scriptResult');
                const logContainer = document.getElementById('logContainer');
                const clearLogsBtn = document.getElementById('clearLogs');
                const toggleAutoScrollBtn = document.getElementById('toggleAutoScroll');
                const backendStatus = document.getElementById('backendStatus');
                const progressIndicator = document.getElementById('progressIndicator');
                
                // Progress steps
                const steps = [
                    document.getElementById('step1'),
                    document.getElementById('step2'),
                    document.getElementById('step3'),
                    document.getElementById('step4'),
                    document.getElementById('step5'),
                    document.getElementById('step6')
                ];
                
                let autoScroll = true;
                let currentStep = 0;
                
                // Function to check backend status
                function checkBackendStatus() {
                    fetch('/api/backend_status')
                        .then(response => response.json())
                        .then(data => {
                            if (data.status === 'available') {
                                backendStatus.textContent = 'Backend service is available';
                                backendStatus.className = 'backend-status available';
                            } else {
                                backendStatus.textContent = `Backend service is unavailable: ${data.error}`;
                                backendStatus.className = 'backend-status unavailable';
                            }
                        })
                        .catch(error => {
                            backendStatus.textContent = `Error checking backend status: ${error}`;
                            backendStatus.className = 'backend-status unavailable';
                        });
                }
                
                // Check backend status on page load
                checkBackendStatus();
                
                // Toggle auto-scroll
                toggleAutoScrollBtn.addEventListener('click', function() {
                    autoScroll = !autoScroll;
                    toggleAutoScrollBtn.textContent = `Auto-scroll: ${autoScroll ? 'ON' : 'OFF'}`;
                });
                
                // Function to update progress
                function updateProgress(step) {
                    if (step < 0 || step >= steps.length) return;
                    
                    // Mark previous steps as completed
                    for (let i = 0; i < step; i++) {
                        steps[i].classList.remove('active', 'pending');
                        steps[i].classList.add('completed');
                    }
                    
                    // Mark current step as active
                    steps[step].classList.remove('pending', 'completed');
                    steps[step].classList.add('active');
                    
                    // Mark future steps as pending
                    for (let i = step + 1; i < steps.length; i++) {
                        steps[i].classList.remove('active', 'completed');
                        steps[i].classList.add('pending');
                    }
                    
                    currentStep = step;
                }
                
                // Function to update logs
                function updateLogs() {
                    fetch('/api/logs')
                        .then(response => response.json())
                        .then(data => {
                            logContainer.innerHTML = '';
                            data.logs.forEach(log => {
                                const logEntry = document.createElement('div');
                                logEntry.className = 'log-entry';
                                
                                // Add appropriate class based on log content
                                if (log.includes('ERROR')) {
                                    logEntry.classList.add('log-error');
                                } else if (log.includes('WARNING')) {
                                    logEntry.classList.add('log-warning');
                                } else if (log.includes('INFO')) {
                                    logEntry.classList.add('log-info');
                                } else if (log.includes('HTTP')) {
                                    logEntry.classList.add('log-http');
                                } else if (log.toLowerCase().includes('success')) {
                                    logEntry.classList.add('log-success');
                                } else if (log.toLowerCase().includes('[progress]')) {
                                    logEntry.classList.add('log-step');
                                } else if (log.toLowerCase().includes('backend')) {
                                    logEntry.classList.add('log-backend');
                                }
                                
                                // Check for progress indicators in the log
                                if (log.includes('[PROGRESS]')) {
                                    logEntry.classList.add('log-step');
                                    const logLower = log.toLowerCase();
                                    if (logLower.includes('retrieving relevant configurations')) {
                                        updateProgress(1);
                                    } else if (logLower.includes('llm response received')) {
                                        updateProgress(2);
                                    } else if (logLower.includes('script validation passed')) {
                                        updateProgress(3);
                                    } else if (logLower.includes('starting script execution')) {
                                        updateProgress(4);
                                    } else if (logLower.includes('script execution completed')) {
                                        updateProgress(5);
                                    } else if (logLower.includes('configuration updated')) {
                                        updateProgress(6);
                                    }
                                }
                                
                                logEntry.textContent = log;
                                logContainer.appendChild(logEntry);
                            });
                            
                            if (autoScroll) {
                                logContainer.scrollTop = logContainer.scrollHeight;
                            }
                        });
                }
                
                // Update logs more frequently (every 100ms)
                setInterval(updateLogs, 100);
                
                // Clear logs
                clearLogsBtn.addEventListener('click', function() {
                    fetch('/api/clear_logs', { method: 'POST' })
                        .then(response => response.json())
                        .then(data => {
                            if (data.status === 'success') {
                                logContainer.innerHTML = '';
                                // Reset progress
                                updateProgress(0);
                            }
                        });
                });
                
                // Handle form submission
                scriptForm.addEventListener('submit', function(e) {
                    e.preventDefault();
                    
                    const command = document.getElementById('command').value;
                    const routerIp = document.getElementById('routerIp').value;
                    
                    if (!command) {
                        alert('Please enter a command');
                        return;
                    }
                    
                    loading.style.display = 'block';
                    resultCard.style.display = 'none';
                    progressIndicator.style.display = 'block';
                    updateProgress(0);
                    
                    fetch('/api/generate_script', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({
                            command: command,
                            router_ip: routerIp
                        })
                    })
                    .then(response => {
                        if (!response.ok) {
                            return response.json().then(data => {
                                throw new Error(data.detail || 'An error occurred');
                            });
                        }
                        return response.json();
                    })
                    .then(data => {
                        scriptResult.textContent = data.script || 'No script was generated';
                        resultCard.style.display = 'block';
                        loading.style.display = 'none';
                    })
                    .catch(error => {
                        alert(`Error: ${error.message}`);
                        loading.style.display = 'none';
                    });
                });
            });
        </script>
    </body>
    </html>
    """
    
    # Create the templates directory if it doesn't exist
    os.makedirs("templates", exist_ok=True)
    
    # Write the HTML template to a file
    with open("templates/index.html", "w") as f:
        f.write(html_content)
    
    logger.info("HTML template created")

# Create the HTML template on startup
create_html_template()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080) 