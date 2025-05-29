import qrcode
from PIL import Image
import urllib.parse
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def generate_whatsapp_qr(output_file: str = None):
    # Use the Twilio WhatsApp sandbox number directly
    twilio_number = "14155238886"  # Twilio WhatsApp sandbox number

    # Create WhatsApp URL with pre-filled join code
    join_code = "join angry-met"
    encoded_message = urllib.parse.quote(join_code)
    whatsapp_url = f"https://api.whatsapp.com/send?phone={twilio_number}&text={encoded_message}"
    print(f"WhatsApp URL: {whatsapp_url}")

    # Generate QR code
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(whatsapp_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    # Save or display the QR code
    if output_file:
        img.save(output_file)
        print(f"QR code saved to {output_file}")
        print("\nInstructions:")
        print("1. Scan the QR code with your phone")
        print("2. It will open WhatsApp with a pre-filled join message")
        print("3. Send the message to join the sandbox")
        print("4. After joining, send 'hi' to start using the bot")
    else:
        img.show()

if __name__ == "__main__":
    # Generate QR code that opens WhatsApp chat with Twilio number
    generate_whatsapp_qr("whatsapp_qr.png") 