from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import platform
import os

CHROMEDRIVER_PATH = os.path.expanduser("~/.wdm/drivers/chromedriver/mac64/136.0.7103.113/chromedriver-mac-arm64/chromedriver")

class WhatsAppBot:
    def __init__(self):
        print("Initializing WhatsApp Bot...")
        self.setup_driver()
        
    def setup_driver(self):
        try:
            # Set up Chrome options
            chrome_options = Options()
            chrome_options.add_argument("--start-maximized")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            
            # Get system architecture
            system = platform.system()
            machine = platform.machine()
            print(f"System: {system}, Architecture: {machine}")
            
            # Manually specify the chromedriver binary path
            service = Service(CHROMEDRIVER_PATH)
            self.driver = webdriver.Chrome(service=service, options=chrome_options)
            print("Chrome driver initialized successfully!")
            
        except Exception as e:
            print(f"Error setting up Chrome driver: {e}")
            raise
        
    def start(self):
        try:
            # Open WhatsApp Web
            print("Opening WhatsApp Web...")
            self.driver.get("https://web.whatsapp.com")
            
            # Wait for QR code to be scanned
            print("\nPlease scan the QR code with your phone...")
            print("Waiting for QR code scan (timeout: 60 seconds)...")
            
            # Wait for the main chat list to appear (indicating successful login)
            WebDriverWait(self.driver, 60).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div[data-testid='chat-list']"))
            )
            print("Successfully logged in to WhatsApp Web!")
            
            # Keep the browser open
            while True:
                time.sleep(1)
                
        except Exception as e:
            print(f"An error occurred: {e}")
        finally:
            self.driver.quit()

def main():
    print("Starting WhatsApp Bot...")
    print("Note: This will open WhatsApp Web in your browser.")
    print("Please make sure you have Chrome installed.")
    
    try:
        bot = WhatsAppBot()
        bot.start()
    except Exception as e:
        print(f"Failed to start WhatsApp Bot: {e}")

if __name__ == "__main__":
    main() 