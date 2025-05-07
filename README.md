# OpenWRT UCI Configuration Assistant

A smart assistant system for OpenWRT router configuration management based on LLM and RAG.

## Project Overview

This project provides an intelligent interface for managing OpenWRT router configurations through natural language commands. It leverages RAG (Retrieval-Augmented Generation) techniques to understand user queries and generate appropriate UCI commands, enhancing the user experience of router management.

## System Architecture

The system consists of the following components:

- **Web Interface**: A user-friendly web application for interacting with the system
- **Backend API**: FastAPI-based service for processing requests and generating scripts
- **Vector Database**: FAISS-based vector storage for configuration chunks
- **UCI Parser**: Component for splitting and analyzing UCI configuration files
- **Script Executor**: Secure validation and execution of generated scripts on routers

## Key Features

- Natural language understanding of router configuration requests
- Dynamic RAG-based retrieval of relevant configuration context
- Secure script generation and validation
- Real-time feedback and progress tracking
- Knowledge accumulation through continuous learning

## Setup Instructions

### Requirements

- Python 3.8+
- OpenWRT-compatible router with SSH access
- Local or remote LLM service (default: local Ollama with Qwen2.5:7b)

### Installation

1. Clone the repository:
   ```
   git@github.com:RosMarinas/OpenWRT_AutoConfigure.git
   cd OpenWRT_AutoConfigure
   ```

2. Install the required packages:
   ```
   pip install -r scripts/requirements.txt
   ```

3. Setup SSH key-based authentication with your router
   

### Running the Application

Execute the main launcher script:

```
python scripts/run_app.py
```

This will start both the backend API service and the web interface. The application will be accessible at:
- Web Application: http://localhost:8080
- Backend API: http://127.0.0.1:8000

## Usage

1. Open the web interface in your browser
2. Enter your router's IP address in the provided field
3. Type a natural language command describing what you want to configure
4. Review the generated UCI script
5. Execute the script and monitor the results

Examples of commands:
- "Set up a guest WiFi network with limited internet access"
- "Configure bandwidth limiting for specific devices"
- "Set up port forwarding for my home server"

## Project Structure

- `run_app.py`: Main entry point that launches both services
- `web_app.py`: Web interface implementation
- `config.py`: Backend API and core functions
- `uci_splitter_Add_Coment.py`: UCI configuration parser and annotator
- `dataset/`: Contains test datasets and generation scripts
- `templates/`: Web templates
- `uci_configs/`: Storage for UCI configuration chunks
- `uci_annotations/`: Storage for annotations of configuration chunks

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the LICENSE file for details.
