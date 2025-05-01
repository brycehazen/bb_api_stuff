#!/usr/bin/env python
import os
import time
import json
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import keyring

# Constants and configuration
SERVICE_NAME = "GlobalSecrets"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COMPLETED_FOLDER = os.path.join(BASE_DIR, "query_completed")
LOG_FOLDER = os.path.join(BASE_DIR, "api_log")
PROCESSED_FILES_RECORD = os.path.join(BASE_DIR, "notified_files.json")

# Email Configuration - retrieve from keyring
EMAIL_FROM = keyring.get_password(SERVICE_NAME, "email.from")
EMAIL_PASSWORD = keyring.get_password(SERVICE_NAME, "email.password")
EMAIL_TO = keyring.get_password(SERVICE_NAME, "email.to") 
SMTP_SERVER = keyring.get_password(SERVICE_NAME, "email.smtp_server") or "smtp.gmail.com"
SMTP_PORT = int(keyring.get_password(SERVICE_NAME, "email.smtp_port") or 587)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_FOLDER, "notification_log.txt")),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def ensure_directories():
    """Ensure required directories exist."""
    if not os.path.exists(LOG_FOLDER):
        os.makedirs(LOG_FOLDER)
        logger.info(f"Created log folder: {LOG_FOLDER}")


def load_processed_files():
    """Load the list of files that have already been processed."""
    if os.path.exists(PROCESSED_FILES_RECORD):
        try:
            with open(PROCESSED_FILES_RECORD, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            logger.error(f"Could not parse {PROCESSED_FILES_RECORD}. Starting with empty list.")
    return []


def save_processed_files(processed_files):
    """Save the list of processed files."""
    with open(PROCESSED_FILES_RECORD, 'w') as f:
        json.dump(processed_files, f)


def send_email(subject, message, files):
    """Send email notification."""
    if not EMAIL_FROM or not EMAIL_PASSWORD or not EMAIL_TO:
        logger.error("Email credentials not found in keyring. Please set them using keyring_cli.py.")
        return False
    
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_FROM
        msg['To'] = EMAIL_TO
        msg['Subject'] = subject
        
        # Add file information to the message
        file_details = "\n".join([f"- {file}" for file in files])
        email_body = f"{message}\n\nProcessed files:\n{file_details}\n\nTimestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        msg.attach(MIMEText(email_body, 'plain'))
        
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(EMAIL_FROM, EMAIL_PASSWORD)
        server.send_message(msg)
        server.quit()
        
        logger.info(f"Email notification sent for {len(files)} files")
        return True
    except Exception as e:
        logger.error(f"Failed to send email: {str(e)}")
        return False


def check_for_new_files():
    """Check for new files in the COMPLETED_FOLDER and send notifications."""
    processed_files = load_processed_files()
    
    new_files = []
    for filename in os.listdir(COMPLETED_FOLDER):
        file_path = os.path.join(COMPLETED_FOLDER, filename)
        
        # Skip directories (like the "archived" folder) and already processed files
        if os.path.isdir(file_path) or filename in processed_files:
            continue
            
        # Only consider files, not directories
        if os.path.isfile(file_path):
            new_files.append(filename)
    
    if new_files:
        logger.info(f"Found {len(new_files)} new files in {COMPLETED_FOLDER}")
        subject = f"Blackbaud Query Completed - {len(new_files)} new files"
        message = f"The Blackbaud query process has completed successfully with {len(new_files)} new file(s)."
        
        if send_email(subject, message, new_files):
            processed_files.extend(new_files)
            save_processed_files(processed_files)
    else:
        logger.info(f"No new files found in {COMPLETED_FOLDER}")


def run_as_daemon():
    """Run as a daemon process, checking periodically for new files."""
    logger.info("Starting notification daemon...")
    
    while True:
        try:
            check_for_new_files()
            time.sleep(60)  # Check every minute
        except KeyboardInterrupt:
            logger.info("Notification daemon stopped by user")
            break
        except Exception as e:
            logger.error(f"Error in notification daemon: {str(e)}")
            time.sleep(60)  # Still wait before retrying


def run_once():
    """Run a single check for new files."""
    logger.info("Checking for new completed files...")
    check_for_new_files()
    logger.info("Check complete")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Send email notifications for completed Blackbaud queries")
    parser.add_argument("--daemon", action="store_true", help="Run as a daemon process")
    args = parser.parse_args()
    
    ensure_directories()
    
    if args.daemon:
        run_as_daemon()
    else:
        run_once() 