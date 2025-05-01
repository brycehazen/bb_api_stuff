#!/usr/bin/env python
import os
import time
import json
import logging
import requests
from datetime import datetime
import keyring

# Constants and configuration
SERVICE_NAME = "GlobalSecrets"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COMPLETED_FOLDER = os.path.join(BASE_DIR, "query_completed")
LOG_FOLDER = os.path.join(BASE_DIR, "api_log")
PROCESSED_FILES_RECORD = os.path.join(BASE_DIR, "notified_pushover_files.json")

# Pushover Configuration - retrieve from keyring
PUSHOVER_APP_TOKEN = keyring.get_password(SERVICE_NAME, "pushover.app_token")
PUSHOVER_USER_KEY = keyring.get_password(SERVICE_NAME, "pushover.user_key")
PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_FOLDER, "pushover_notification_log.txt")),
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


def send_pushover_notification(title, message, files):
    """Send push notification via Pushover."""
    if not PUSHOVER_APP_TOKEN or not PUSHOVER_USER_KEY:
        logger.error("Pushover credentials not found in keyring. Please set them using keyring_cli.py.")
        return False
    
    try:
        # Add file information to the message
        file_details = "\n".join([f"- {file}" for file in files])
        full_message = f"{message}\n\nProcessed files:\n{file_details}\n\nTimestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        payload = {
            "token": PUSHOVER_APP_TOKEN,
            "user": PUSHOVER_USER_KEY,
            "title": title,
            "message": full_message,
            "priority": 0  # Normal priority
        }
        
        response = requests.post(PUSHOVER_API_URL, data=payload)
        response.raise_for_status()
        
        logger.info(f"Pushover notification sent for {len(files)} files")
        return True
    except Exception as e:
        logger.error(f"Failed to send Pushover notification: {str(e)}")
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
        title = f"Blackbaud Query Completed"
        message = f"The Blackbaud query process has completed successfully with {len(new_files)} new file(s)."
        
        if send_pushover_notification(title, message, new_files):
            processed_files.extend(new_files)
            save_processed_files(processed_files)
    else:
        logger.info(f"No new files found in {COMPLETED_FOLDER}")


def run_as_daemon():
    """Run as a daemon process, checking periodically for new files."""
    logger.info("Starting Pushover notification daemon...")
    
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
    
    parser = argparse.ArgumentParser(description="Send Pushover notifications for completed Blackbaud queries")
    parser.add_argument("--daemon", action="store_true", help="Run as a daemon process")
    args = parser.parse_args()
    
    ensure_directories()
    
    if args.daemon:
        run_as_daemon()
    else:
        run_once() 