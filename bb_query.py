#!/usr/bin/env python
#bb_query.py
import os
import time
import json
import shutil
import glob
import requests
from datetime import datetime
from bb_auth import BlackbaudAuth

EXECUTE_ENDPOINT = "/query/queries/executebyid"
JOB_STATUS_ENDPOINT_TEMPLATE = "/query/jobs/{job_id}"
MAX_POLLING_SECONDS = 604800 
POLL_INTERVAL = 8 

REQUIRED_FIELDS = ["id", "product", "module"]
OPTIONAL_FIELDS = [
    "ux_mode", "output_format", "formatting_mode", "sql_generation_mode",
    "use_static_query_id_set", "results_file_name", "ask_fields",
    "display_code_table_long_description", "time_zone_offset_in_minutes"
]

REQUEST_FOLDER = "query_request"
COMPLETED_FOLDER = "query_completed"
FAILED_FOLDER = "failed_quests"
LOG_FOLDER = "api__log"

def ensure_folders():
    for folder in [REQUEST_FOLDER, COMPLETED_FOLDER, FAILED_FOLDER, LOG_FOLDER]:
        if not os.path.exists(folder):
            os.makedirs(folder)
            print(f"Created folder: {folder}")

def wait_for_new_json():

    while True:
        json_files = glob.glob(os.path.join(REQUEST_FOLDER, "*.json"))
        if json_files:
            return json_files[0]
        time.sleep(5)

def validate_request_json(data):
    """Ensure that the JSON data contains all required fields."""
    missing = [field for field in REQUIRED_FIELDS if field not in data]
    if missing:
        raise ValueError("Missing required fields: " + ", ".join(missing))
    return True

def post_query_request(auth, data):
    query = {"product": data["product"], "module": data["module"]}
    body = {"id": data["id"]}
    for field in OPTIONAL_FIELDS:
        if field in data:
            body[field] = data[field]
    response = auth.make_request(method="POST", endpoint=EXECUTE_ENDPOINT, params=query, data=body)
    return response, query, body

def poll_job_status(auth, job_id, query_params):
    params = query_params.copy()
    params.update({
        "include_read_url": "OnceCompleted",
        "content_disposition": "Attachment"
    })
    job_url = JOB_STATUS_ENDPOINT_TEMPLATE.format(job_id=job_id)
    elapsed = 0
    while elapsed < MAX_POLLING_SECONDS:
        response = auth.make_request(method="GET", endpoint=job_url, params=params, data=None)
        if not response:
            print("Failed to get job status")
            return None
        status = response.get("status", "")
        print(f"Job status: {status}")
        if status == "Completed":
            return response
        elif status in ["Failed", "Cancelled", "Throttled"]:
            print(f"Job finished with status: {status}")
            return response
        time.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
    print("Polling timed out")
    return None

def download_file(url, file_name):
    try:
        r = requests.get(url)
        r.raise_for_status()
        if not any(file_name.lower().endswith(ext) for ext in [".csv", ".json", ".txt"]):
            file_name += ".csv"
        with open(file_name, "wb") as f:
            f.write(r.content)
        print(f"File downloaded successfully: {file_name}")
        return file_name
    except Exception as e:
        print(f"Error downloading file: {e}")
        return None

def log_event(message):
    timestamp = datetime.utcnow().strftime('%Y-%m-%d_%H:%M:%S:%f')[:23]
    log_entry = f"{timestamp}\n{message}\n"
    log_file = os.path.join(LOG_FOLDER, "api_log.txt")
    with open(log_file, "a") as f:
        f.write(log_entry + "\n")
    print(log_entry)

def process_request_file(auth, file_path):
    print(f"Processing request file: {file_path}")
    with open(file_path, "r") as f:
        data = json.load(f)
    validate_request_json(data)
    
    print("Posting query request...")
    post_response, query_params, body = post_query_request(auth, data)
    if not post_response:
        log_event(f"file {file_path} > FAILED > Errors  - FAILED")
        raise Exception("No response from query request")
    
    job_id = post_response.get("id")
    if not job_id:
        log_event(f"file {file_path} > FAILED > Errors  - FAILED")
        raise Exception("Job ID not returned in response")
    
    log_event(f"file {file_path} > created job Id: {job_id}")
    
    if data.get("ux_mode", "Asynchronous") == "Synchronous":
        print("Polling job status...")
        job_response = poll_job_status(auth, job_id, query_params)
    else:
        job_response = post_response
    
    if not job_response:
        log_event(f"file {file_path} > created job Id: {job_id} > FAILED > Errors  - FAILED")
        raise Exception("Job did not complete successfully")
    
    sas_uri = job_response.get("sas_uri")
    if not sas_uri:
        log_event(f"file {file_path} > created job Id: {job_id} > FAILED > Errors  - FAILED")
        raise Exception("No SAS URI provided in job response")
    
    file_name = data.get("results_file_name", "query_results")
    downloaded = download_file(sas_uri, file_name)
    if not downloaded:
        log_event(f"file {file_path} > created job Id: {job_id} > FAILED > Errors  - FAILED")
        raise Exception("Download file not generated")
    
    log_event(f"file {file_path} > created job Id: {job_id} > file {downloaded} downloaded > no errors  - COMPLETED")
    return downloaded

def move_processed_files(src_json, downloaded_file, success=True):

    dest_folder = COMPLETED_FOLDER if success else FAILED_FOLDER
    dest_json = os.path.join(dest_folder, os.path.basename(src_json))
    shutil.move(src_json, dest_json)
    print(f"Moved JSON file to {dest_json}")
    if success and downloaded_file and os.path.exists(downloaded_file):
        dest_file = os.path.join(dest_folder, os.path.basename(downloaded_file))
        shutil.move(downloaded_file, dest_file)
        print(f"Moved downloaded file to {dest_file}")


def main():
    auth = BlackbaudAuth() 
    print("Starting query processor. Monitoring 'query_request' folder for new JSON files.")
    
    while True:
        req_file = wait_for_new_json()
        print(f"Found new request file: {req_file}")
        try:
            downloaded = process_request_file(auth, req_file)
            move_processed_files(req_file, downloaded, success=True)
        except Exception as e:
            log_event(f"file {req_file} > FAILED > Errors  - FAILED\nErrors: {str(e)}")
            move_processed_files(req_file, None, success=False)
        time.sleep(2)

if __name__ == "__main__":
    main()
