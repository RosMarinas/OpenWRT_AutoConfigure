import sys
import io
import threading
import queue
import time
import logging
from typing import Optional, Callable

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global queue to store print messages
print_queue = queue.Queue()

# Callback function to be set by the web application
print_callback: Optional[Callable[[str], None]] = None

class PrintCapture:
    """
    A class to capture print statements and redirect them to a queue.
    """
    def __init__(self):
        self.original_stdout = sys.stdout
        self.original_stderr = sys.stderr
        self.stdout_buffer = io.StringIO()
        self.stderr_buffer = io.StringIO()
        self.is_capturing = False
        self.capture_thread = None
        self.stop_event = threading.Event()
        self.original_print = print  # Store the original print function
    
    def start_capture(self):
        """Start capturing print statements."""
        if self.is_capturing:
            return
        
        self.is_capturing = True
        self.stop_event.clear()
        
        # Start a thread to monitor the buffers
        self.capture_thread = threading.Thread(target=self._monitor_buffers)
        self.capture_thread.daemon = True
        self.capture_thread.start()
        
        # Redirect stdout and stderr to our buffers
        sys.stdout = self.stdout_buffer
        sys.stderr = self.stderr_buffer
        
        # Replace the built-in print function
        import builtins
        builtins.print = self.custom_print
    
    def stop_capture(self):
        """Stop capturing print statements."""
        if not self.is_capturing:
            return
        
        self.is_capturing = False
        self.stop_event.set()
        
        # Restore original stdout and stderr
        sys.stdout = self.original_stdout
        sys.stderr = self.original_stderr
        
        # Restore the original print function
        import builtins
        builtins.print = self.original_print
        
        # Wait for the capture thread to finish
        if self.capture_thread:
            self.capture_thread.join(timeout=1.0)
    
    def _monitor_buffers(self):
        """Monitor the buffers for new content."""
        while not self.stop_event.is_set():
            # Check stdout buffer
            stdout_content = self.stdout_buffer.getvalue()
            if stdout_content:
                self.stdout_buffer.truncate(0)
                self.stdout_buffer.seek(0)
                for line in stdout_content.splitlines():
                    if line.strip():
                        print_queue.put(line)
            
            # Check stderr buffer
            stderr_content = self.stderr_buffer.getvalue()
            if stderr_content:
                self.stderr_buffer.truncate(0)
                self.stderr_buffer.seek(0)
                for line in stderr_content.splitlines():
                    if line.strip():
                        print_queue.put(f"ERROR: {line}")
            
            # Sleep to avoid high CPU usage
            time.sleep(0.1)
    
    def get_print_messages(self):
        """Get all print messages from the queue."""
        messages = []
        while not print_queue.empty():
            try:
                message = print_queue.get_nowait()
                messages.append(message)
            except queue.Empty:
                break
        return messages
    
    def custom_print(self, *args, **kwargs):
        """Custom print function that captures print statements."""
        # Call the original print function
        self.original_print(*args, **kwargs)
        
        # Add the message to the queue
        message = " ".join(map(str, args))
        print_queue.put(message)
        
        # Call the callback function if set
        if print_callback:
            print_callback(message)

# Global instance
print_capture = PrintCapture()

def start_print_capture():
    """Start capturing print statements."""
    print_capture.start_capture()
    logger.info("Print capture started")

def stop_print_capture():
    """Stop capturing print statements."""
    print_capture.stop_capture()
    logger.info("Print capture stopped")

def get_print_messages():
    """Get all print messages from the queue."""
    return print_capture.get_print_messages()

def set_print_callback(callback: Callable[[str], None]):
    """Set a callback function to be called when a print message is captured."""
    global print_callback
    print_callback = callback 