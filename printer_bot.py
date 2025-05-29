from flask import Flask, request, Response
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
import os
from dotenv import load_dotenv
import requests
import json
import logging
from twilio.base.exceptions import TwilioRestException

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

app = Flask(__name__)

# Twilio credentials
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_PHONE_NUMBER = os.getenv('TWILIO_PHONE_NUMBER')
HUGGINGFACE_API_KEY = os.getenv('HUGGINGFACE_API_KEY')

# Initialize Twilio client
client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Printer settings
PRINTER_IP = "192.168.1.100"  # Replace with your printer's IP address
PRINTER_SETTINGS = {
    'color_mode': False,
    'copies': 1,
    'paper_size': 'A4',
    'orientation': 'portrait'
}

def analyze_command_with_huggingface(command):
    """Analyze user command using HuggingFace Inference API"""
    try:
        prompt = f"""Convert this printer command into a JSON object with specific fields.\n\nInput command: {command}\n\nRequired JSON format:\n{{\n    \"action\": \"print|scan|copy|status\",\n    \"color_mode\": true|false,\n    \"copies\": number,\n    \"paper_size\": \"A4\",\n    \"orientation\": \"portrait\"\n}}\n\nRules:\n1. For action, use only: \"print\", \"scan\", \"copy\", or \"status\"\n2. For color_mode, use only: true or false\n3. For copies, use a number\n4. For paper_size, use \"A4\"\n5. For orientation, use \"portrait\"\n\nReturn ONLY the JSON object, nothing else."""
        logger.debug("\n=== HuggingFace API Request ===")
        logger.debug(f"Command to analyze: {command}")
        API_URL = "https://api-inference.huggingface.co/models/HuggingFaceH4/zephyr-7b-beta"
        headers = {
            "Authorization": f"Bearer {HUGGINGFACE_API_KEY}",
            "Content-Type": "application/json"
        }
        response = requests.post(API_URL,
            headers=headers,
            json={"inputs": prompt, "parameters": {"max_new_tokens": 150, "temperature": 0.1, "do_sample": False}}
        )
        logger.debug("\n=== HuggingFace API Response ===")
        logger.debug(f"Status Code: {response.status_code}")
        logger.debug(f"Raw Response: {response.text}")
        if response.status_code == 200:
            result = response.json()
            try:
                response_text = result[0]['generated_text'] if isinstance(result, list) else result.get('generated_text', '')
                logger.debug(f"\nGenerated Text: {response_text}")
                response_text = response_text.strip()
                start_idx = response_text.find('{')
                end_idx = response_text.rfind('}') + 1
                if start_idx != -1 and end_idx != -1:
                    json_str = response_text[start_idx:end_idx]
                    logger.debug(f"\nExtracted JSON: {json_str}")
                    parsed_json = json.loads(json_str)
                    required_fields = ['action', 'color_mode', 'copies', 'paper_size', 'orientation']
                    if all(field in parsed_json for field in required_fields):
                        logger.debug(f"\nParsed Settings: {parsed_json}")
                        return parsed_json
                    else:
                        logger.warning("\nMissing required fields in JSON")
                        return None
                else:
                    logger.warning("\nNo JSON object found in response")
                    return None
            except Exception as e:
                logger.error(f"\nError parsing JSON: {e}")
                return None
        else:
            logger.error(f"\nAPI request failed with status {response.status_code}")
            return None
    except Exception as e:
        logger.error(f"\nError analyzing command: {e}")
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

def send_whatsapp_message(to_number, message_body, media_url=None):
    """Send WhatsApp message with proper error handling"""
    try:
        logger.debug("\n=== Sending WhatsApp Message ===")
        logger.debug(f"To: {to_number}")
        logger.debug(f"From: whatsapp:{TWILIO_PHONE_NUMBER}")
        logger.debug(f"Body: {message_body}")
        if media_url:
            logger.debug(f"Media URL: {media_url}")
        
        message_params = {
            'from_': f'whatsapp:{TWILIO_PHONE_NUMBER}',
            'body': message_body,
            'to': to_number
        }
        
        if media_url:
            message_params['media_url'] = [media_url]
        
        message = client.messages.create(**message_params)
        
        logger.debug("\n=== Twilio Response ===")
        logger.debug(f"Message SID: {message.sid}")
        logger.debug(f"Status: {message.status}")
        logger.debug(f"Error Code: {message.error_code}")
        logger.debug(f"Error Message: {message.error_message}")
        
        return message
        
    except TwilioRestException as e:
        logger.error("\n=== Twilio API Error ===")
        logger.error(f"Error Code: {e.code}")
        logger.error(f"Error Message: {e.msg}")
        logger.error(f"More Info: {e.more_info}")
        
        # Handle specific Twilio errors
        if e.code == 21211:  # Invalid phone number
            return send_whatsapp_message(to_number, "Sorry, there was an issue with the phone number. Please try again.")
        elif e.code == 21608:  # Rate limit exceeded
            return send_whatsapp_message(to_number, "Sorry, we're experiencing high traffic. Please try again in a few minutes.")
        elif e.code == 21614:  # Account suspended
            return send_whatsapp_message(to_number, "Sorry, our service is temporarily unavailable. Please try again later.")
        else:
            return send_whatsapp_message(to_number, "Sorry, there was an error sending the message. Please try again.")
            
    except Exception as e:
        logger.error("\n=== Unexpected Error ===")
        logger.error(f"Error Type: {type(e).__name__}")
        logger.error(f"Error Message: {str(e)}")
        return send_whatsapp_message(to_number, "Sorry, an unexpected error occurred. Please try again.")

def handle_printer_command(settings, user_number):
    """Handle printer commands with settings"""
    if settings['action'] == 'print':
        return "✅ Print job completed successfully! Your document has been printed."
    elif settings['action'] == 'scan':
        # Simulate scanning and getting a PDF URL
        # In a real implementation, this would be the URL of your scanned document
        scanned_doc_url = "https://example.com/scanned_document.pdf"  # Replace with actual scanned document URL
        
        # Send the scanned document
        message = send_whatsapp_message(
            user_number,
            "✅ Document scanned successfully! Here's your scanned document:",
            media_url=scanned_doc_url
        )
        return "✅ Document scanned and sent to your WhatsApp!"
    elif settings['action'] == 'copy':
        return "✅ Copy job completed successfully! Your copies are ready for pickup."
    elif settings['action'] == 'status':
        return "🖨️ Printer Status:\n• Online\n• Ready to print\n• Paper: Full\n• Toner: 85%"
    else:
        return "❌ Invalid command. Please try again."

@app.route('/')
def home():
    """Root route to confirm server is running"""
    return "Printer Bot is running! Use /webhook for WhatsApp messages."

@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle incoming WhatsApp messages"""
    try:
        # Get message type and content
        message_type = request.values.get('MediaContentType0', '')
        incoming_msg = request.values.get('Body', '').lower()
        media_url = request.values.get('MediaUrl0', '')
        num_media = request.values.get('NumMedia', '0')
        
        logger.info("\n=== Incoming Message Details ===")
        logger.info(f"From: {request.values.get('From', '')}")
        logger.info(f"Message Type: {message_type}")
        logger.info(f"Number of Media Files: {num_media}")
        logger.info(f"Message Body: {incoming_msg}")
        logger.info(f"Media URL: {media_url}")
        
        # Log all request values for debugging
        logger.debug("\n=== All Request Values ===")
        for key, value in request.values.items():
            logger.debug(f"{key}: {value}")
        
        # Handle media messages (documents, images, etc.)
        if message_type:
            logger.info("\n=== Processing Media Message ===")
            if 'application/' in message_type:
                logger.info(f"Document Type: {message_type}")
                if not incoming_msg:  # If no message with the document
                    incoming_msg = "print this document"
                    logger.info("No message provided with document, defaulting to 'print this document'")
                else:
                    incoming_msg = f"{incoming_msg} this document"
                    logger.info(f"Combined message with document: {incoming_msg}")
            elif 'image/' in message_type:
                logger.info(f"Image Type: {message_type}")
                if not incoming_msg:  # If no message with the image
                    incoming_msg = "print this image"
                    logger.info("No message provided with image, defaulting to 'print this image'")
                else:
                    incoming_msg = f"{incoming_msg} this image"
                    logger.info(f"Combined message with image: {incoming_msg}")
            else:
                logger.info(f"Other Media Type: {message_type}")
                if not incoming_msg:  # If no message with the media
                    incoming_msg = "print this file"
                    logger.info("No message provided with media, defaulting to 'print this file'")
                else:
                    incoming_msg = f"{incoming_msg} this file"
                    logger.info(f"Combined message with media: {incoming_msg}")
        
        # Handle basic commands directly
        if incoming_msg == 'hi' or incoming_msg == 'hello':
            welcome_msg = "👋 How can I help you today?\n\n"
            welcome_msg += "Here are some examples of what you can do:\n\n"
            welcome_msg += "• Print a document in color with 2 copies\n"
            welcome_msg += "• Scan this document and send it to WhatsApp\n"
            welcome_msg += "• Make 3 copies of this document\n"
            welcome_msg += "• Check printer status\n\n"
            welcome_msg += "Just tell me what you need in your own words!"
            
            message = send_whatsapp_message(request.values.get('From', ''), welcome_msg)
            resp = MessagingResponse()
            resp.message(welcome_msg)
            return str(resp)
            
        # For other commands, use the LLM
        settings = analyze_command_with_huggingface(incoming_msg)
        if settings:
            response_msg = f"✅ I'll help you with that!\n\n" + \
                         f"Action: {settings['action']}\n" + \
                         f"Color Mode: {'Color' if settings['color_mode'] else 'Black & White'}\n" + \
                         f"Copies: {settings['copies']}\n" + \
                         f"Paper Size: {settings['paper_size']}\n" + \
                         f"Orientation: {settings['orientation']}\n\n" + \
                         f"Processing your request..."
            
            # Send the response message
            message = send_whatsapp_message(request.values.get('From', ''), response_msg)
            
            # Process the printer command
            result = handle_printer_command(settings, request.values.get('From', ''))
            
            # Send the result message
            message = send_whatsapp_message(request.values.get('From', ''), result)
            
            resp = MessagingResponse()
            resp.message(result)
            return str(resp)
        else:
            error_msg = "I'm not sure I understand. Try saying something like 'make 3 copies' or 'print in color'"
            message = send_whatsapp_message(request.values.get('From', ''), error_msg)
            
            resp = MessagingResponse()
            resp.message(error_msg)
            return str(resp)
            
    except Exception as e:
        logger.error("\n=== Webhook Error ===")
        logger.error(f"Error Type: {type(e).__name__}")
        logger.error(f"Error Message: {str(e)}")
        
        error_msg = "Sorry, something went wrong. Please try again."
        try:
            message = send_whatsapp_message(request.values.get('From', ''), error_msg)
        except:
            pass  # If we can't send the error message, at least return a response
        
        resp = MessagingResponse()
        resp.message(error_msg)
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