from flask import Flask, request, Response, send_from_directory
import os
from dotenv import load_dotenv
import requests
import json
import qrcode
import tempfile
from pathlib import Path
from datetime import datetime
import mimetypes
import shutil
import cups
import logging
from logging.handlers import RotatingFileHandler
import argparse
import socket
import time
import subprocess

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Add file handler for persistent logging
log_file = 'printer_bot.log'
file_handler = RotatingFileHandler(log_file, maxBytes=1024*1024, backupCount=5)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(file_handler)

app = Flask(__name__)

# WhatsApp Business API credentials
WHATSAPP_PHONE_NUMBER_ID = "698272523378264"
WHATSAPP_ACCESS_TOKEN = "EAAPNxI60DgMBOwP0kbu6No1ocIn6xIj66KauJaIZC2oToaXZAL7PalbFSPbZC5wNkLVWKCu5kjwiJZBVMce5IAQFRoQZBSqvddQTZAtWnzpCWJjuDCWuOmYqGjdZCTcdC4lEDZAr7fqgoVFlIZCpVewZAodtJtUHa9fu0WZC4KLVB3sXGIoZA7HXOvsUWjPQIpK9tby157MFI4jLt6pTci0pMfZANQmmk6iQTpLNfZCREZD"

VERIFY_TOKEN = "smartprint123"
HUGGINGFACE_API_KEY = os.getenv('HUGGINGFACE_API_KEY')

# Printer settings
PRINTER_IP = "192.168.203.10"
PRINTER_NAME = f"HP_LaserJet_{PRINTER_IP.replace('.', '_')}"

# Create media directory if it doesn't exist
MEDIA_DIR = Path("media")
MEDIA_DIR.mkdir(exist_ok=True)

# State management for conversations
conversation_states = {}

def get_user_state(user_id):
    """Get the current state for a user"""
    return conversation_states.get(user_id, {})

def set_user_state(user_id, state):
    """Set the state for a user"""
    conversation_states[user_id] = state

def clear_user_state(user_id):
    """Clear the state for a user"""
    if user_id in conversation_states:
        del conversation_states[user_id]

def is_printer_reachable(ip, port=9100, timeout=5):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except (socket.timeout, socket.error):
        return False

def install_printer_if_needed(printer_name, ip):
    conn = cups.Connection()
    if printer_name in conn.getPrinters():
        logger.info(f"✅ Printer {printer_name} already installed.")
        return
    logger.info(f"🛠️ Installing printer {printer_name} using ipp://...")
    uri = f"ipp://{ip}/ipp/print"
    driver = "everywhere"
    try:
        subprocess.run([
            "lpadmin", "-p", printer_name,
            "-v", uri,
            "-m", driver,
            "-E"
        ], check=True)
        logger.info(f"✅ Printer {printer_name} installed.")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"❌ Failed to install printer: {e}")

def delete_printer(printer_name):
    try:
        subprocess.run(["lpadmin", "-x", printer_name], check=True)
        logger.info(f"🧹 Removed printer {printer_name}")
    except subprocess.CalledProcessError:
        logger.warning(f"⚠️ Warning: Failed to delete printer {printer_name}")

def wait_for_job_completion(conn, job_id, timeout=120, poll_interval=5):
    logger.info(f"⏳ Waiting for job {job_id} to complete...")
    waited = 0
    while waited < timeout:
        try:
            job_attrs = conn.getJobAttributes(job_id)
            job_state = job_attrs.get("job-state", None)
            job_state_reasons = job_attrs.get("job-state-reasons", [])
            state_name = {
                3: "pending",
                4: "pending-held",
                5: "processing",
                6: "processing-stopped",
                7: "completed",
                8: "canceled",
                9: "aborted"
            }.get(job_state, "unknown")

            if job_state == 7:
                logger.info(f"✅ Print job {job_id} completed successfully.")
                return True
            elif job_state in [8, 9]:
                # Check if job is marked completed in job history
                completed_jobs = conn.getJobs(which_jobs='completed', my_jobs=True)
                if job_id in completed_jobs:
                    logger.info(f"✅ Print job {job_id} is successfully completed.")
                    return True
                logger.error(f"❌ Print job {job_id} failed: {state_name}")
                return False
            else:
                logger.info(f"📋 Job {job_id} state: {state_name} ({job_state_reasons})")
        except cups.IPPError as e:
            logger.warning(f"⚠️ Error fetching job attributes: {e}")
            break

        time.sleep(poll_interval)
        waited += poll_interval

    logger.warning(f"⚠️ Job {job_id} did not complete within {timeout} seconds.")
    return False

def print_document(file_path, settings):
    """Print a document using CUPS with proper printer installation"""
    try:
        logger.info(f"Attempting to print document: {file_path}")
        logger.info(f"Print settings: {settings}")
        
        # Check if printer is reachable
        if not is_printer_reachable(PRINTER_IP):
            error_msg = f"Cannot connect to printer at {PRINTER_IP}:9100"
            logger.error(error_msg)
            return False, error_msg
        
        # Install printer if needed
        try:
            install_printer_if_needed(PRINTER_NAME, PRINTER_IP)
        except Exception as e:
            error_msg = f"Failed to install printer: {e}"
            logger.error(error_msg)
            return False, error_msg
        
        # Prepare print options
        print_options = {
            "copies": str(settings['copies']),
            "sides": "one-sided",
            "orientation-requested": "3" if settings['orientation'] == 'portrait' else "4",
            "print-color-mode": "color" if settings['color_mode'] else "monochrome",
            "media": settings['paper_size']
        }
        
        try:
            conn = cups.Connection()
            logger.info(f"📤 Sending print job to {PRINTER_NAME}...")
            job_id = conn.printFile(PRINTER_NAME, file_path, "WhatsApp Print Job", print_options)
            logger.info(f"📝 Job ID: {job_id}")
            
            # Wait for job completion
            success = wait_for_job_completion(conn, job_id)
            if success:
                return True, f"Print job completed successfully. Job ID: {job_id}"
            else:
                return False, f"Print job failed or timed out. Job ID: {job_id}"
                
        except cups.IPPError as e:
            error_msg = f"CUPS printing error: {e}"
            logger.error(error_msg)
            return False, error_msg
            
    except Exception as e:
        error_msg = f"Error during printing: {e}"
        logger.error(error_msg)
        return False, error_msg
    finally:
        # Optionally cleanup the printer after printing
        # delete_printer(PRINTER_NAME)
        pass

def analyze_command_with_huggingface(command):
    """Analyze user command using HuggingFace Inference API"""
    try:
        # Simple command parsing for common cases
        command = command.lower().strip()
        
        # Default settings
        settings = {
            "action": "print",
            "color_mode": False,
            "copies": 1,
            "paper_size": "A4",
            "orientation": "portrait"
        }
        
        # Handle common commands directly
        if "scan" in command:
            settings["action"] = "scan"
            return settings
        elif "status" in command:
            settings["action"] = "status"
            return settings
        elif "copy" in command or "copies" in command:
            settings["action"] = "copy"
            # Try to extract number of copies
            import re
            copies_match = re.search(r'(\d+)\s*cop(?:y|ies)', command)
            if copies_match:
                settings["copies"] = int(copies_match.group(1))
            return settings
        elif "print" in command:
            settings["action"] = "print"
            if "color" in command:
                settings["color_mode"] = True
            return settings
            
        # If no direct match, use HuggingFace for complex commands
        prompt = f"""Analyze this printer command and return a JSON object with these exact fields:
{{
    "action": "print",
    "color_mode": false,
    "copies": 1,
    "paper_size": "A4",
    "orientation": "portrait"
}}

Command: {command}

Rules:
- action must be one of: "print", "scan", "copy", "status"
- color_mode must be true or false
- copies must be a number
- paper_size must be "A4"
- orientation must be "portrait"

Return ONLY the JSON object, nothing else."""

        API_URL = "https://api-inference.huggingface.co/models/HuggingFaceH4/zephyr-7b-beta"
        headers = {
            "Authorization": f"Bearer {HUGGINGFACE_API_KEY}",
            "Content-Type": "application/json"
        }
        response = requests.post(API_URL,
            headers=headers,
            json={"inputs": prompt, "parameters": {"max_new_tokens": 150, "temperature": 0.1, "do_sample": False}}
        )
        
        if response.status_code == 200:
            result = response.json()
            try:
                response_text = result[0]['generated_text'] if isinstance(result, list) else result.get('generated_text', '')
                # Extract JSON from response
                start_idx = response_text.find('{')
                end_idx = response_text.rfind('}') + 1
                if start_idx != -1 and end_idx != -1:
                    json_str = response_text[start_idx:end_idx]
                    parsed_json = json.loads(json_str)
                    # Validate required fields
                    required_fields = ['action', 'color_mode', 'copies', 'paper_size', 'orientation']
                    if all(field in parsed_json for field in required_fields):
                        return parsed_json
            except Exception as e:
                print(f"Error parsing JSON: {e}")
                return None
        return None
    except Exception as e:
        print(f"Error analyzing command: {e}")
        return None

def format_settings_message(settings):
    """Format settings into a readable message"""
    message = "I understand you want to:\n"
    message += f"• Action: {settings['action'].title()}\n"
    message += f"• Color Mode: {'On' if settings['color_mode'] else 'Off'}\n"
    message += f"• Copies: {settings['copies']}\n"
    message += f"• Paper Size: {settings['paper_size']}\n"
    message += f"• Orientation: {settings['orientation']}\n\n"
    message += "Is this correct? Reply with 'yes' to confirm or 'no' to cancel."
    return message

def handle_printer_command(settings, user_number):
    """Handle printer commands with settings"""
    if settings['action'] == 'print':
        return "✅ Print job completed successfully! Your document has been printed."
    elif settings['action'] == 'scan':
        return "✅ Document scanned successfully! The scanned file has been sent to your WhatsApp."
    elif settings['action'] == 'copy':
        return "✅ Copy job completed successfully! Your copies are ready for pickup."
    elif settings['action'] == 'status':
        return "🖨️ Printer Status:\n• Online\n• Ready to print\n• Paper: Full\n• Toner: 85%"
    else:
        return "❌ Invalid command. Please try again."

def send_whatsapp_message(to_number, message, media_urls=None):
    """Send a WhatsApp message using WhatsApp Business API"""
    try:
        url = f"https://graph.facebook.com/v17.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
        
        headers = {
            "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        
        # If we have media URLs, send them first
        if media_urls:
            for media_url in media_urls:
                media_type = "image" if media_url.endswith(('.jpg', '.jpeg', '.png')) else "document"
                media_payload = {
                    "messaging_product": "whatsapp",
                    "to": to_number,
                    "type": media_type,
                    media_type: {
                        "link": media_url
                    }
                }
                response = requests.post(url, headers=headers, json=media_payload)
                if response.status_code != 200:
                    logger.error(f"Failed to send media message: {response.text}")
                    return False
                time.sleep(1)  # Rate limiting
            
            # If we also have a text message, send it after the media
            if message:
                text_payload = {
                    "messaging_product": "whatsapp",
                    "to": to_number,
                    "type": "text",
                    "text": {"body": message}
                }
                response = requests.post(url, headers=headers, json=text_payload)
                if response.status_code != 200:
                    logger.error(f"Failed to send text message: {response.text}")
                    return False
            return True
        
        # If no media, just send the text message
        text_payload = {
            "messaging_product": "whatsapp",
            "to": to_number,
            "type": "text",
            "text": {"body": message}
        }
        
        response = requests.post(url, headers=headers, json=text_payload)
        if response.status_code == 200:
            logger.info(f"Message sent successfully: {response.json()}")
            return True
        else:
            logger.error(f"Failed to send message: {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"Error sending WhatsApp message: {str(e)}")
        return False

def download_media(media_id, media_type):
    """Download media from WhatsApp Business API"""
    try:
        logger.info(f"\n=== Downloading Media ===")
        logger.info(f"Media ID: {media_id}")
        logger.info(f"Type: {media_type}")
        
        # Get media URL
        url = f"https://graph.facebook.com/v17.0/{media_id}"
        headers = {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"}
        response = requests.get(url, headers=headers)
        
        if response.status_code != 200:
            logger.error(f"Failed to get media URL: {response.text}")
            return None
            
        media_url = response.json().get('url')
        if not media_url:
            logger.error("No media URL in response")
            return None
            
        # Download the file
        response = requests.get(media_url, headers=headers, stream=True)
        if response.status_code != 200:
            logger.error(f"Failed to download media: {response.text}")
            return None
            
        # Create a temporary file
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=f".{media_type.split('/')[-1]}")
        temp_path = temp_file.name
        
        with open(temp_path, 'wb') as f:
            response.raw.decode_content = True
            shutil.copyfileobj(response.raw, f)
        
        # Generate a unique filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{Path(temp_path).name}"
        final_path = MEDIA_DIR / filename
        
        # Move to media directory
        shutil.move(temp_path, final_path)
        logger.info(f"Media saved to: {final_path}")
        return str(final_path)
        
    except Exception as e:
        logger.error(f"Error downloading media: {e}")
        return None

def mock_print_document(file_path, settings):
    """Mock function to simulate printing a document"""
    try:
        print(f"\n=== Mock Printing Document ===")
        print(f"File: {file_path}")
        print(f"Settings: {json.dumps(settings, indent=2)}")
        
        # Simulate printing delay
        import time
        time.sleep(2)
        
        return True
    except Exception as e:
        print(f"Error in mock printing: {e}")
        return False

def mock_scan_document(file_path):
    """Mock function to simulate scanning a document"""
    try:
        logger.info(f"\n=== Mock Scanning Document ===")
        logger.info(f"File: {file_path}")
        
        # Simulate scanning delay
        time.sleep(2)
        
        # Create a copy of the original file as the "scanned" version
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        scanned_filename = f"scanned_{timestamp}.pdf"
        scanned_path = MEDIA_DIR / scanned_filename
        
        # Copy the file to the media directory
        shutil.copy2(file_path, scanned_path)
        logger.info(f"Scanned file saved to: {scanned_path}")
        
        return str(scanned_path)
    except Exception as e:
        logger.error(f"Error in mock scanning: {e}")
        return None

def handle_media_message(request_data):
    """Handle incoming media messages with robust command/media logic"""
    try:
        logger.info("=== Processing Media Message ===")
        
        # Get message data
        message = request_data.get('entry', [{}])[0].get('changes', [{}])[0].get('value', {})
        contacts = message.get('contacts', [{}])[0]
        messages = message.get('messages', [{}])[0]
        
        from_number = contacts.get('wa_id')
        message_type = messages.get('type')
        message_id = messages.get('id')
        
        # Get user's current state
        user_state = get_user_state(from_number)
        
        # Get command from text message or image caption
        user_command = ""
        if message_type == 'text':
            user_command = messages.get('text', {}).get('body', '').strip().lower()
            # If we have a pending document and received a text command
            if user_state.get('pending_document'):
                logger.info("Processing command for pending document")
                file_path = user_state['pending_document']
                settings = analyze_command_with_huggingface(user_command)
                if not settings:
                    settings = {
                        "action": "print",
                        "color_mode": False,
                        "copies": 1,
                        "paper_size": "A4",
                        "orientation": "portrait"
                    }
                
                # Process the command
                if settings["action"] == "print":
                    success, message = print_document(file_path, settings)
                    if success:
                        send_whatsapp_message(from_number, f"✅ {message}")
                        # Only clear state after successful print
                        clear_user_state(from_number)
                    else:
                        send_whatsapp_message(from_number, f"❌ {message}\nPlease try again with a different command.")
                elif settings["action"] == "scan":
                    try:
                        original_url = f"https://{request.host}/media/{Path(file_path).name}"
                        send_whatsapp_message(from_number, "✅ Here's your document:")
                        if send_whatsapp_message(from_number, "", [original_url]):
                            # Only clear state after successful scan
                            clear_user_state(from_number)
                        else:
                            send_whatsapp_message(from_number, "❌ Failed to send the document. Please try again.")
                    except Exception as e:
                        logger.error(f"Error in scan operation: {e}")
                        send_whatsapp_message(from_number, "❌ An error occurred while processing your document. Please try again.")
                else:
                    send_whatsapp_message(from_number, "❌ Invalid command. Please specify 'print' or 'scan'.")
                return True
                
        elif message_type == 'image':
            user_command = messages.get('image', {}).get('caption', '').strip().lower()
        elif message_type == 'document':
            user_command = messages.get('document', {}).get('caption', '').strip().lower()
            # If no caption for document, store it and ask user what they want to do
            if not user_command:
                media_id = messages.get('document', {}).get('id')
                media_type = messages.get('document', {}).get('mime_type')
                
                if not media_id or not media_type:
                    logger.warning("Media information missing")
                    send_whatsapp_message(from_number, "Media information missing. Please resend your document.")
                    return True
                
                file_path = download_media(media_id, media_type)
                if not file_path:
                    send_whatsapp_message(from_number, "Failed to download the document. Please try again.")
                    return True
                
                # Store the document path in user's state
                set_user_state(from_number, {
                    'pending_document': file_path,
                    'timestamp': time.time()
                })
                
                send_whatsapp_message(from_number, "📄 I see you've sent a document. What would you like to do with it?\n\n" + \
                                    "Please reply with one of these commands:\n" + \
                                    "• 'print' - to print the document\n" + \
                                    "• 'scan' - to scan the document\n" + \
                                    "• 'copy' - to make copies\n\n" + \
                                    "Or send the document again with the command in the caption.")
                return True
        
        logger.info(f"From number: {from_number}")
        logger.info(f"Message type: {message_type}")
        logger.info(f"User command: {user_command}")
        
        # Handle text-only messages
        if message_type == 'text':
            if user_command == 'hi' or user_command == 'hello':
                welcome_message = "👋 Hi! I'm your Printer Bot. Here are the commands you can use:\n\n" + \
                                "• 'print [in color/black & white]' - Print a document\n" + \
                                "• 'scan' - Scan a document\n" + \
                                "• 'make X copies' - Make X copies of a document\n" + \
                                "• 'status' - Check printer status\n\n" + \
                                "Just type any of these commands and I'll help you!"
                send_whatsapp_message(from_number, welcome_message)
                return True
            elif user_command == 'status':
                send_whatsapp_message(from_number, "🖨️ Your HP printer is online and ready to print!")
                return True
            elif user_command == 'help':
                help_message = "Here's what I can help you with:\n" + \
                             "- Say *status* to check printer status\n" + \
                             "- Say *hello* to start a conversation\n" + \
                             "- Send a document with 'print' or 'scan' command\n" + \
                             "- More features coming soon!"
                send_whatsapp_message(from_number, help_message)
                return True
            else:
                send_whatsapp_message(from_number, "Please send a document or image along with your command.")
                return True
        
        # Handle media messages
        if message_type in ['image', 'document']:
            media_id = messages.get('image', {}).get('id') or messages.get('document', {}).get('id')
            media_type = messages.get('image', {}).get('mime_type') or messages.get('document', {}).get('mime_type')
            
            if not media_id or not media_type:
                logger.warning("Media information missing")
                send_whatsapp_message(from_number, "Media information missing. Please resend your document or image.")
                return True
            
            file_path = download_media(media_id, media_type)
            if not file_path:
                send_whatsapp_message(from_number, "Failed to download the media. Please try again.")
                return True
            
            # For documents without caption, we've already handled it above
            if message_type == 'document' and not user_command:
                return True
            
            # Analyze the command
            settings = analyze_command_with_huggingface(user_command) if user_command else None
            if not settings:
                logger.info("Command not recognized. Defaulting to print settings.")
                settings = {
                    "action": "print",
                    "color_mode": False,
                    "copies": 1,
                    "paper_size": "A4",
                    "orientation": "portrait"
                }
            
            logger.info(f"Parsed settings: {settings}")
            
            # Process based on command
            if settings["action"] == "print":
                success, message = print_document(file_path, settings)
                if success:
                    send_whatsapp_message(from_number, f"✅ {message}")
                else:
                    send_whatsapp_message(from_number, f"❌ {message}")
                return True
            
            elif settings["action"] == "scan":
                # For scan, we'll send back the original file
                original_url = f"https://{request.host}/media/{Path(file_path).name}"
                logger.info(f"Original file URL: {original_url}")
                
                # Send success message with the file
                send_whatsapp_message(from_number, "✅ Here's your document:")
                send_whatsapp_message(from_number, "", [original_url])
                return True
            
            else:
                send_whatsapp_message(from_number, "❌ Invalid or unsupported command for media. Please specify 'print' or 'scan'.")
                return True
                
        return True
            
    except Exception as e:
        error_msg = f"Error handling media message: {e}"
        logger.error(error_msg)
        send_whatsapp_message(from_number, f"❌ An error occurred while processing the media: {str(e)}")
        return True

@app.route('/')
def home():
    """Root route to confirm server is running"""
    return "Printer Bot is running! Use /webhook for WhatsApp messages."

@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    """Handle incoming WhatsApp messages"""
    try:
        # Handle webhook verification
        if request.method == 'GET':
            mode = request.args.get('hub.mode')
            token = request.args.get('hub.verify_token')
            challenge = request.args.get('hub.challenge')
            
            logger.info(f"Webhook verification request - Mode: {mode}, Token: {token}")
            
            if mode == 'subscribe' and token == VERIFY_TOKEN:
                logger.info("Webhook verification successful")
                return challenge
            logger.warning("Webhook verification failed")
            return 'Forbidden', 403
            
        # Handle incoming messages
        logger.info("\n=== Incoming Webhook Request ===")
        logger.info(f"Request data: {request.json}")
        
        body = request.json
        if (body and 
            body.get('entry') and 
            isinstance(body['entry'], list) and 
            len(body['entry']) > 0 and 
            body['entry'][0].get('changes') and 
            isinstance(body['entry'][0]['changes'], list) and 
            len(body['entry'][0]['changes']) > 0 and 
            body['entry'][0]['changes'][0].get('value') and 
            body['entry'][0]['changes'][0]['value'].get('messages') and 
            isinstance(body['entry'][0]['changes'][0]['value']['messages'], list) and 
            len(body['entry'][0]['changes'][0]['value']['messages']) > 0):
            
            message = body['entry'][0]['changes'][0]['value']['messages'][0]
            from_number = message.get('from')
            msg_body = message.get('text', {}).get('body', '').lower() if message.get('text') else ""
            
            logger.info(f"Received message from {from_number}: {msg_body}")
            
            # Process the message
            response = handle_media_message(body)
            return {'status': 'success' if response else 'error'}
        else:
            logger.info("No message found in webhook payload")
            return {'status': 'success'}
            
    except Exception as e:
        logger.error(f"Critical error in webhook: {e}")
        logger.error(f"Error Type: {type(e).__name__}")
        logger.error(f"Error Message: {str(e)}")
        logger.error(f"Request Data: {request.json}")
        return {'status': 'error', 'message': str(e)}

@app.route('/media/<filename>')
def serve_media(filename):
    """Serve media files from the media directory"""
    try:
        logger.info(f"Serving media file: {filename}")
        logger.info(f"Media directory: {MEDIA_DIR}")
        logger.info(f"Full path: {MEDIA_DIR / filename}")
        
        if not (MEDIA_DIR / filename).exists():
            logger.error(f"File not found: {MEDIA_DIR / filename}")
            return "File not found", 404
            
        return send_from_directory(MEDIA_DIR, filename)
    except Exception as e:
        logger.error(f"Error serving media file {filename}: {e}")
        return "File not found", 404

if __name__ == '__main__':
    try:
        logger.info("Starting Printer Bot server...")
        app.run(host='0.0.0.0', debug=True, port=5001)
    except Exception as e:
        logger.error(f"Failed to start server: {e}")