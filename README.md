# WhatsApp Printer Control Bot

This project implements a WhatsApp bot that can control printers through natural language commands. The bot uses LLM (Language Learning Model) to understand user commands and execute printer operations.

## Features

- WhatsApp bot integration
- Natural language processing for printer commands
- Printer control through CUPS
- Interactive confirmation flow
- QR code-based authentication

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Create a `.env` file with the following variables:
```
OPENAI_API_KEY=your_openai_api_key
```

3. Run the server:
```bash
python server.py
```

4. Scan the QR code with WhatsApp to connect the bot

## Usage

1. Send a message to the bot with your printer command
2. Bot will parse the command and ask for confirmation
3. Confirm the settings
4. Bot will execute the print job

## Requirements

- Python 3.8+
- CUPS (Common Unix Printing System)
- WhatsApp account
- OpenAI API key (for LLM processing) 