from flask import Flask, render_template, request, jsonify
import qrcode
from PIL import Image
import os
from datetime import datetime
import json

app = Flask(__name__)

# Store printer information
PRINTERS = {
    "printer1": {
        "name": "Main Office Printer",
        "location": "Floor 1",
        "message": "Your document is ready for pickup at the Main Office Printer on Floor 1."
    },
    "printer2": {
        "name": "Conference Room Printer",
        "location": "Floor 2",
        "message": "Your document is ready for pickup at the Conference Room Printer on Floor 2."
    }
}

@app.route('/')
def home():
    return render_template('index.html', printers=PRINTERS)

@app.route('/generate-qr/<printer_id>')
def generate_qr(printer_id):
    if printer_id not in PRINTERS:
        return "Printer not found", 404
    
    # Create a unique URL for this printer
    printer_url = f"{request.host_url}scan/{printer_id}"
    
    # Generate QR code
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(printer_url)
    qr.make(fit=True)
    
    # Create QR code image
    img = qr.make_image(fill_color="black", back_color="white")
    
    # Save QR code
    qr_path = f"static/qr_codes/{printer_id}.png"
    os.makedirs("static/qr_codes", exist_ok=True)
    img.save(qr_path)
    
    return render_template('qr.html', 
                         printer=PRINTERS[printer_id],
                         qr_path=qr_path)

@app.route('/scan/<printer_id>')
def scan(printer_id):
    if printer_id not in PRINTERS:
        return "Printer not found", 404
    
    # Get the user's phone number from the request
    # Note: This is a placeholder. In a real implementation, you'd need to get the user's phone number
    # through WhatsApp Business API or another secure method
    phone_number = request.args.get('phone')
    
    if not phone_number:
        return render_template('phone_input.html', printer_id=printer_id)
    
    # Send message via WhatsApp
    # Note: This is where you'd integrate with WhatsApp Business API
    message = PRINTERS[printer_id]['message']
    # TODO: Implement actual WhatsApp message sending
    
    return render_template('success.html', 
                         message="Message sent successfully!",
                         printer=PRINTERS[printer_id])

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001) 