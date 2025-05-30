from flask import Flask, request, Response, send_from_directory
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
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

# Twilio credentials
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_PHONE_NUMBER = os.getenv('TWILIO_PHONE_NUMBER')
HUGGINGFACE_API_KEY = os.getenv('HUGGINGFACE_API_KEY')

# Printer settings
PRINTER_IP = "192.168.203.10"
PRINTER_NAME = f"HP_LaserJet_{PRINTER_IP.replace('.', '_')}"

# Initialize Twilio client
client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Create media directory if it doesn't exist
MEDIA_DIR = Path("media")
MEDIA_DIR.mkdir(exist_ok=True)

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

def download_media(media_url, media_type):
    """Download media from Twilio URL and save to local storage"""
    try:
        print(f"\n=== Downloading Media ===")
        print(f"URL: {media_url}")
        print(f"Type: {media_type}")
        
        # Create a temporary file
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=f".{media_type.split('/')[-1]}")
        temp_path = temp_file.name
        
        # Download the file with Twilio authentication
        response = requests.get(
            media_url,
            stream=True,
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        )
        
        if response.status_code == 200:
            with open(temp_path, 'wb') as f:
                response.raw.decode_content = True
                shutil.copyfileobj(response.raw, f)
            
            # Generate a unique filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{timestamp}_{Path(temp_path).name}"
            final_path = MEDIA_DIR / filename
            
            # Move to media directory
            shutil.move(temp_path, final_path)
            print(f"Media saved to: {final_path}")
            return str(final_path)
        else:
            print(f"Failed to download media. Status code: {response.status_code}")
            print(f"Response content: {response.text}")
            return None
    except Exception as e:
        print(f"Error downloading media: {e}")
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
        print(f"\n=== Mock Scanning Document ===")
        print(f"File: {file_path}")
        
        # Simulate scanning delay
        import time
        time.sleep(2)
        
        # Return a mock scanned file path
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        scanned_path = MEDIA_DIR / f"scanned_{timestamp}.pdf"
        return str(scanned_path)
    except Exception as e:
        print(f"Error in mock scanning: {e}")
        return None

def handle_media_message(request_values):
    """Handle incoming media messages"""
    try:
        logger.info("=== Processing Media Message ===")
        
        # Get media information
        media_url = request_values.get('MediaUrl0')
        media_type = request_values.get('MediaContentType0')
        num_media = int(request.values.get('NumMedia', 0))
        
        if not media_url or not media_type or num_media == 0:
            logger.warning("No media found in message")
            return None, "No media found in the message. Please send a document or image."
        
        logger.info(f"Media Type: {media_type}")
        logger.info(f"Media URL: {media_url}")
        
        # Download the media
        file_path = download_media(media_url, media_type)
        if not file_path:
            return None, "Failed to download the media. Please try again."
        
        # Get the last command from the user
        last_command = request_values.get('Body', '').lower()
        settings = analyze_command_with_huggingface(last_command) if last_command else None
        
        if not settings:
            settings = {
                "action": "print",
                "color_mode": False,
                "copies": 1,
                "paper_size": "A4",
                "orientation": "portrait"
            }
        
        # Create response
        resp = MessagingResponse()
        
        # Process based on command
        if settings["action"] == "print":
            success, message = print_document(file_path, settings)
            if success:
                # Add the downloaded media to the response
                media_url = f"https://{request.host}/media/{Path(file_path).name}"
                resp.message(f"✅ {message}").media(media_url)
                return True, resp
            else:
                return False, f"❌ {message}"
        
        elif settings["action"] == "scan":
            scanned_path = mock_scan_document(file_path)
            if scanned_path:
                # Add the scanned media to the response
                media_url = f"https://{request.host}/media/{Path(scanned_path).name}"
                resp.message(f"✅ Document scanned successfully! Saved as: {Path(scanned_path).name}").media(media_url)
                return True, resp
            else:
                return False, "❌ Failed to scan document. Please try again."
        
        else:
            return False, "❌ Invalid command for media. Please specify 'print' or 'scan'."
            
    except Exception as e:
        error_msg = f"Error handling media message: {e}"
        logger.error(error_msg)
        return False, f"❌ An error occurred while processing the media: {str(e)}"

@app.route('/')
def home():
    """Root route to confirm server is running"""
    return "Printer Bot is running! Use /webhook for WhatsApp messages."

@app.route('/media/<filename>')
def serve_media(filename):
    """Serve media files from the media directory"""
    try:
        return send_from_directory(MEDIA_DIR, filename)
    except Exception as e:
        print(f"Error serving media file {filename}: {e}")
        return "File not found", 404

@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle incoming WhatsApp messages"""
    try:
        # Log incoming request
        print("\n=== Incoming Webhook Request ===")
        print(f"From: {request.values.get('From', 'Unknown')}")
        print(f"To: {request.values.get('To', 'Unknown')}")
        print(f"Message SID: {request.values.get('MessageSid', 'Unknown')}")
        
        # Check if message contains media
        num_media = int(request.values.get('NumMedia', 0))
        if num_media > 0:
            success, response = handle_media_message(request.values)
            if isinstance(response, str):
                resp = MessagingResponse()
                resp.message(response)
                return str(resp)
            return str(response)
        
        incoming_msg = request.values.get('Body', '').lower()
        print(f"Message Body: {incoming_msg}")
        
        # Handle initial greeting
        if incoming_msg == 'hi':
            welcome_message = "👋 Hi! I'm your Printer Bot. Here are the commands you can use:\n\n" + \
                            "• 'print [in color/black & white]' - Print a document\n" + \
                            "• 'scan' - Scan a document\n" + \
                            "• 'make X copies' - Make X copies of a document\n" + \
                            "• 'status' - Check printer status\n\n" + \
                            "Just type any of these commands and I'll help you!"
            print("\n=== Sending Welcome Message ===")
            print(f"Message: {welcome_message}")
            
            resp = MessagingResponse()
            resp.message(welcome_message)
            return str(resp)
            
        settings = analyze_command_with_huggingface(incoming_msg)
        if settings:
            print("\n=== Command Analysis Results ===")
            print(f"Settings: {json.dumps(settings, indent=2)}")
            
            response_msg = f"✅ I'll help you with that!\n\n" + \
                         f"Action: {settings['action']}\n" + \
                         f"Color Mode: {'Color' if settings['color_mode'] else 'Black & White'}\n" + \
                         f"Copies: {settings['copies']}\n" + \
                         f"Paper Size: {settings['paper_size']}\n" + \
                         f"Orientation: {settings['orientation']}\n\n" + \
                         f"Please scan the QR code to connect to the printer."
            
            print("\n=== Generating QR Code ===")
            qr = qrcode.QRCode(version=1, box_size=10, border=5)
            qr.add_data("https://printer-connect.example.com")
            qr.make(fit=True)
            qr_img = qr.make_image(fill_color="black", back_color="white")
            qr_path = "printer_qr.png"
            qr_img.save(qr_path)
            print(f"QR Code saved to: {qr_path}")
            
            print("\n=== Sending Twilio Message ===")
            try:
                message = client.messages.create(
                    from_=f'whatsapp:{os.getenv("TWILIO_PHONE_NUMBER")}',
                    body=response_msg,
                    to=request.values.get('From', ''),
                    media_url=[f"https://{request.host}/static/{qr_path}"]
                )
                print(f"Message SID: {message.sid}")
                print(f"Message Status: {message.status}")
                
                resp = MessagingResponse()
                resp.message(response_msg)
                return str(resp)
            except Exception as twilio_error:
                print(f"\n=== Twilio Error ===")
                print(f"Error Type: {type(twilio_error).__name__}")
                print(f"Error Message: {str(twilio_error)}")
                error_msg = "Sorry, there was an error sending the message. Please try again later."
                resp = MessagingResponse()
                resp.message(error_msg)
                return str(resp)
        else:
            print("\n=== Command Not Understood ===")
            print(f"Original message: {incoming_msg}")
            resp = MessagingResponse()
            resp.message("I'm not sure I understand. Try saying something like 'make 3 copies' or 'print in color'")
            return str(resp)
    except Exception as e:
        print(f"\n=== Critical Error in Webhook ===")
        print(f"Error Type: {type(e).__name__}")
        print(f"Error Message: {str(e)}")
        print(f"Request Values: {dict(request.values)}")
        resp = MessagingResponse()
        resp.message("Sorry, something went wrong. Please try again.")
        return str(resp)

@app.route('/send-initial', methods=['POST'])
def send_initial_message():
    user_number = request.json.get('phone_number')
    if not user_number:
        return {'error': 'Phone number required'}, 400
    message = "Hi! I'm your Printer Bot. Send 'hi' to see available options."
    success = send_whatsapp_message(user_number, message)
    if success:
        return {'status': 'Message sent successfully'}
    else:
        return {'error': 'Failed to send message'}, 500

if __name__ == '__main__':
    app.run(debug=True, port=5001) 