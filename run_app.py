#!/usr/bin/env python3
import subprocess
import sys
import os
import time
import signal
import atexit
import logging
import threading
import requests
import json

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Global variables to store processes
backend_process = None
web_process = None

def cleanup():
    """Clean up processes on exit."""
    global backend_process, web_process
    
    logger.info("Cleaning up processes...")
    
    if backend_process:
        logger.info("Terminating backend process...")
        backend_process.terminate()
        try:
            backend_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("Backend process did not terminate, killing...")
            backend_process.kill()
    
    if web_process:
        logger.info("Terminating web process...")
        web_process.terminate()
        try:
            web_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("Web process did not terminate, killing...")
            web_process.kill()
    
    logger.info("Cleanup complete.")

def signal_handler(sig, frame):
    """Handle signals to ensure proper cleanup."""
    logger.info(f"Received signal {sig}, exiting...")
    sys.exit(0)

def read_process_output(process, prefix):
    """Read and log output from a process."""
    while True:
        line = process.stdout.readline()
        if not line and process.poll() is not None:
            break
        if line:
            logger.info(f"{prefix}: {line.strip()}")

def is_backend_ready():
    """Check if the backend service is ready by making a simple request."""
    try:
        response = requests.get("http://127.0.0.1:8000/docs")
        return response.status_code == 200
    except requests.exceptions.ConnectionError:
        return False

def main():
    """Run the backend service and web application."""
    global backend_process, web_process
    
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Register cleanup function
    atexit.register(cleanup)
    
    # Get the directory of this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Create required directories
    os.makedirs("uci_configs", exist_ok=True)
    os.makedirs("uci_annotations", exist_ok=True)
    
    # Start the backend service using uvicorn
    logger.info("Starting backend service...")
    backend_cmd = [
        sys.executable, 
        "-m", 
        "uvicorn", 
        "config:app", 
        "--host", 
        "127.0.0.1", 
        "--port", 
        "8000", 
        "--reload"
    ]
    backend_process = subprocess.Popen(
        backend_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )
    
    # Start a thread to read the backend output
    backend_thread = threading.Thread(
        target=read_process_output, 
        args=(backend_process, "BACKEND"),
        daemon=True
    )
    backend_thread.start()
    
    # Wait for the backend service to start
    logger.info("Waiting for backend service to start...")
    max_retries = 30
    retry_count = 0
    backend_ready = False
    
    while retry_count < max_retries and not backend_ready:
        time.sleep(1)
        retry_count += 1
        backend_ready = is_backend_ready()
        if backend_ready:
            logger.info("Backend service is ready!")
            break
        logger.info(f"Waiting for backend service... ({retry_count}/{max_retries})")
    
    # Check if the backend service is still running
    if backend_process.poll() is not None:
        logger.error("Backend service failed to start!")
        # Try to get the error output
        try:
            output, _ = backend_process.communicate(timeout=1)
            logger.error(f"Backend service error output: {output}")
        except:
            pass
        sys.exit(1)
    
    if not backend_ready:
        logger.error("Backend service did not become ready in time!")
        sys.exit(1)
    
    # Start the web application
    logger.info("Starting web application...")
    web_cmd = [sys.executable, os.path.join(script_dir, "web_app.py")]
    web_process = subprocess.Popen(
        web_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )
    
    # Start a thread to read the web application output
    web_thread = threading.Thread(
        target=read_process_output, 
        args=(web_process, "WEB"),
        daemon=True
    )
    web_thread.start()
    
    # Wait for the web application to start
    logger.info("Waiting for web application to start...")
    time.sleep(3)
    
    # Check if the web application is still running
    if web_process.poll() is not None:
        logger.error("Web application failed to start!")
        # Try to get the error output
        try:
            output, _ = web_process.communicate(timeout=1)
            logger.error(f"Web application error output: {output}")
        except:
            pass
        sys.exit(1)
    
    logger.info("Both services are running!")
    logger.info("Backend service: http://127.0.0.1:8000")
    logger.info("Web application: http://localhost:8080")
    logger.info("Press Ctrl+C to stop both services.")
    
    # Monitor the processes
    try:
        while True:
            # Check if either process has terminated
            if backend_process.poll() is not None:
                logger.error("Backend service terminated unexpectedly!")
                # Try to get the error output
                try:
                    output, _ = backend_process.communicate(timeout=1)
                    logger.error(f"Backend service error output: {output}")
                except:
                    pass
                break
            
            if web_process.poll() is not None:
                logger.error("Web application terminated unexpectedly!")
                # Try to get the error output
                try:
                    output, _ = web_process.communicate(timeout=1)
                    logger.error(f"Web application error output: {output}")
                except:
                    pass
                break
            
            # Sleep to avoid high CPU usage
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, exiting...")
    finally:
        cleanup()

if __name__ == "__main__":
    main() 