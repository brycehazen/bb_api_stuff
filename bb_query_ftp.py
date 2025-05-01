#!/usr/bin/env python
# bb_query.py
import os
import time
import json
import shutil
import glob
import uuid
import pysftp
import requests
import pandas as pd
from datetime import datetime
from pathlib import Path
import sys
import threading
import warnings
warnings.filterwarnings("ignore", message="Failed to load HostKeys")
sys.path.append(r"C:\Users\parish_report_py_file")
from parish_report import ParishReport # type: ignore


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from bb_auth import BlackbaudAuth, RequestFailedException # type: ignore

EXECUTE_ENDPOINT = "/query/queries/executebyid"
EXECUTE_ADHOC_ENDPOINT = "/query/queries/execute"
JOB_STATUS_ENDPOINT_TEMPLATE = "/query/jobs/{job_id}"
MAX_POLLING_SECONDS = 604800  # 7 days
POLL_INTERVAL = 8  # seconds

# Standard required fields
REQUIRED_FIELDS_STANDARD = ["id", "product", "module"]
OPTIONAL_FIELDS_STANDARD = [
    "ux_mode", "output_format", "formatting_mode", "sql_generation_mode",
    "use_static_query_id_set", "results_file_name", "ask_fields",
    "display_code_table_long_description", "time_zone_offset_in_minutes"
]

# Generated query required field
REQUIRED_FIELD_GENERATED = "query"
OPTIONAL_FIELDS_GENERATED = [
    "ux_mode", "output_format", "formatting_mode", "results_file_name",
    "display_code_table_long_description", "time_zone_offset_in_minutes"
]

BASE_DIR = r"E:\Report Data\API_report_query_request"

REQUEST_FOLDER = os.path.join(BASE_DIR, "query_request")
COMPLETED_FOLDER = os.path.join(BASE_DIR, "query_completed")
FAILED_FOLDER = os.path.join(BASE_DIR, "query_failed")
LOG_FOLDER = os.path.join(BASE_DIR, "api_log")
ARCHIVE_FOLDER = os.path.join(COMPLETED_FOLDER, "archived")

# SFTP details (used for d1_file_import_id.json flows) Example: keyring_cli store --key "" --value "" --description ""

SFTP_HOST = "" # IP address
SFTP_USERNAME = "" # username
SFTP_PASSWORD = "" # password in keyring
SFTP_REMOTE_DIR = "" # remote directory


def ensure_folders_and_log():

    for folder in [REQUEST_FOLDER, COMPLETED_FOLDER, FAILED_FOLDER, ARCHIVE_FOLDER, LOG_FOLDER]:
        if not os.path.exists(folder):
            os.makedirs(folder)
            folder_name = os.path.basename(folder)

    # Ensure api_log.txt exists
    log_file_path = os.path.join(LOG_FOLDER, "api_log.txt")
    if not os.path.exists(log_file_path):
        with open(log_file_path, "w") as f:
            f.write("Log file created\n")


def log_event(message, also_print=True):
    timestamp = datetime.utcnow().strftime('%Y-%m-%d_%H:%M:%S:%f')[:23]
    log_entry = f"{timestamp}\n{message}\n"
    log_file = os.path.join(LOG_FOLDER, "api_log.txt")
    with open(log_file, "a") as f:
        f.write(log_entry + "\n")
    if also_print:
        print(message)


def animate_dots(stop_event, message_prefix):
    dot_patterns = ["   ", ".  ", ".. ", "...", " ..", "  .", "   "]
    dot_index = 0
    
    while not stop_event.is_set():
        sys.stdout.write("\r" + " " * 80)  # Clear line with spaces
        sys.stdout.write(f"\r{message_prefix}{dot_patterns[dot_index]}")
        sys.stdout.flush()
        

        dot_index = (dot_index + 1) % len(dot_patterns)
        
       
        time.sleep(0.3)


def format_job_message(job_id, request_file, status, output_file=None, error_message=None):
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M')
    message = [
        f"\nJob Id: {job_id if job_id else 'N/A'}",
        f"Request file: {request_file}",
        f"Status: {status}"
    ]
    
    if output_file:
        message.append(f"Output file: {output_file}")
    else:
        message.append(f"Output file:")
        
    if error_message:
        message.append(f"Error message: {error_message}")
    
    message.append(f"Time: {current_time}")
        
    return "\n".join(message)


def wait_for_new_json():
    while True:
        json_files = glob.glob(os.path.join(REQUEST_FOLDER, "*.json"))
        if json_files:
            return json_files[0]
        time.sleep(5)


def validate_standard_request_json(data):
    """JSON  contains all required fields for a standard query."""
    missing = [field for field in REQUIRED_FIELDS_STANDARD if field not in data]
    if missing:
        raise ValueError("Missing required fields: " + ", ".join(missing))
    return True


def validate_generated_query_json(data):
    """ JSON data contains the 'query' field for an ad-hoc request."""
    if REQUIRED_FIELD_GENERATED not in data:
        raise ValueError(f"Missing required field: {REQUIRED_FIELD_GENERATED}")
    return True


def generate_uuid():
    return str(uuid.uuid4())


def download_file(url, file_name):
    try:
        r = requests.get(url)
        r.raise_for_status()
        
        # If file_name doesn't end in .csv/.json/.txt, default to .csv
        if not any(file_name.lower().endswith(ext) for ext in [".csv", ".json", ".txt"]):
            file_name += ".csv"
            
        with open(file_name, "wb") as f:
            f.write(r.content)

        return file_name
        
    except Exception as e:
        log_event(format_job_message(
            job_id=None,
            request_file="",
            status="Download failed",
            error_message=f"Error downloading file: {str(e)}"
        ))
        return None


def poll_job_status(auth, job_id, query_params):

    params = query_params.copy()
    params.update({
        "include_read_url": "OnceCompleted",
        "content_disposition": "Attachment"
    })
    job_url = JOB_STATUS_ENDPOINT_TEMPLATE.format(job_id=job_id)
    elapsed = 0
    last_status = None
    
    status_message = format_job_message(
        job_id=job_id,
        request_file="",
        status=f"Polling status every {POLL_INTERVAL} seconds..."
    )
    
    log_event(status_message, also_print=False)
    status_line = f"Status: Polling status every {POLL_INTERVAL} seconds..."
    
    stop_animation = threading.Event()
    animation_thread = threading.Thread(
        target=animate_dots, 
        args=(stop_animation, status_line)
    )
    animation_thread.daemon = True  
    animation_thread.start()
    
    try:
        while elapsed < MAX_POLLING_SECONDS:
            response = auth.make_request(method="GET", endpoint=job_url, params=params, data=None)
            if not response:
                log_event(format_job_message(
                    job_id=job_id,
                    request_file="",
                    status="Failed to get job status",
                    error_message="No response from status endpoint"
                ), also_print=False)  
                return None
                
            status = response.get("status", "")
            
            if status != last_status:
                last_status = status
                if status != "Running" or elapsed == 0:
                    log_event(format_job_message(
                        job_id=job_id,
                        request_file="",
                        status=f"Job status: {status}"
                    ), also_print=False)
            
            if status == "Completed":
                return response
            elif status in ["Failed", "Cancelled", "Throttled"]:
                log_event(format_job_message(
                    job_id=job_id,
                    request_file="",
                    status=f"Job failed with status: {status}",
                    error_message=f"Job status: {status}"
                ), also_print=False)
                return response
                
            time.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL
            
        log_event(format_job_message(
            job_id=job_id,
            request_file="",
            status="Polling timed out",
            error_message=f"Exceeded maximum polling time of {MAX_POLLING_SECONDS} seconds"
        ), also_print=False)
        return None
        
    finally:
        stop_animation.set()
        animation_thread.join(timeout=1.0)
        
        sys.stdout.write("\r" + " " * 80)
        sys.stdout.flush()
        
        if last_status:
            final_message = f"Status: Job {last_status}"
            sys.stdout.write(f"\r{final_message}\n")
            sys.stdout.flush()


def post_standard_query_request(auth, data):

    query = {
        "product": data["product"],
        "module": data["module"]
    }
    body = {
        "id": data["id"]
    }
    for field in OPTIONAL_FIELDS_STANDARD:
        if field in data:
            body[field] = data[field]
    response = auth.make_request(
        method="POST",
        endpoint=EXECUTE_ENDPOINT,
        params=query,
        data=body
    )
    return response, query, body


def post_generated_query_request(auth, data):

    params = {
        "product": "RE",
        "module": "None"
    }
    response = auth.make_request(
        method="POST",
        endpoint=EXECUTE_ADHOC_ENDPOINT,
        params=params,
        data=data
    )
    return response, params


def process_parish_report(downloaded_csv_path):
    try:
        output_excel = os.path.join(os.path.dirname(downloaded_csv_path), 'parish_packages_report.xlsx')
        
        report = ParishReport(downloaded_csv_path)
        
        report.load_data()
        report.generate_report()
        report.save_report(output_excel)
        return output_excel
    except Exception as e:
        error_msg = f"Error processing report: {str(e)}"
        log_event(error_msg)
        print(error_msg)
        return None


def upload_and_archive_csv(local_file):
    base_name = os.path.basename(local_file)
    cnopts = pysftp.CnOpts()
    cnopts.hostkeys = None 
    try:
        with pysftp.Connection(
            host=SFTP_HOST,
            username=SFTP_USERNAME,
            password=SFTP_PASSWORD,
            cnopts=cnopts
        ) as sftp:
            print(f"'{SFTP_HOST}' established.")
            sftp.cwd(SFTP_REMOTE_DIR) 
            folder_name = os.path.basename(SFTP_REMOTE_DIR)
            remote_path = f"{folder_name}/{base_name}"
            sftp.put(local_file, base_name) 

            log_event(f"Uploaded: {base_name} to {folder_name}/{base_name}")
            return True
    except Exception as e:
        msg = f"Error uploading '{base_name}' to SFTP: {e}"
        print(msg)
        log_event(msg)
        return False


def process_email_query_results(downloaded_file):

    try:
        mapping_file = "email_to_importid_mapping.json"
        if not os.path.exists(mapping_file):
            return downloaded_file
        
        # Load the mapping
        with open(mapping_file, 'r') as f:
            email_to_importid_map = json.load(f)
        
        if not email_to_importid_map:
            print("Warning: Email to ImportID mapping is empty.")
            return downloaded_file

        df = pd.read_csv(downloaded_file)
        
        # Add ImportID column
        df['ImportID'] = None
        
        # Map ImportIDs based on email matches in the Phone Number column
        for i, row in df.iterrows():
            phone_number = str(row.get('Phone Number', ''))
            if '@' in phone_number and phone_number in email_to_importid_map:
                df.at[i, 'ImportID'] = email_to_importid_map[phone_number]
        
        processed_file = f"processed_{os.path.basename(downloaded_file)}"
        df.to_csv(processed_file, index=False)
        
        print(f"Results saved: {os.path.basename(processed_file)}")
        log_event(f"Appended ImportID to query results: {os.path.basename(processed_file)}")
        
        return processed_file
    except Exception as e:
        error_msg = f"Error processing email query results: {str(e)}"
        log_event(error_msg)
        print(error_msg)
        return downloaded_file


def process_standard_query_file(auth, file_path):

    file_name = os.path.basename(file_path)
    
    with open(file_path, "r") as f:
        data = json.load(f)

    validate_standard_request_json(data)

    log_event(format_job_message(
        job_id=None,
        request_file=file_name,
        status="Processing"
    ))

    post_response, query_params, _ = post_standard_query_request(auth, data)
    if not post_response:
        raise Exception("No response from query request")

    job_id = post_response.get("id")
    if not job_id:
        raise Exception("Job ID not returned in response")

    log_event(format_job_message(
        job_id=job_id,
        request_file=file_name,
        status="Job created"
    ))

    if data.get("ux_mode", "Asynchronous") == "Synchronous":
        log_event(format_job_message(
            job_id=job_id,
            request_file=file_name,
            status="Polling status every 8 seconds..."
        ))
        
        job_response = poll_job_status(auth, job_id, query_params)
        
        log_event(format_job_message(
            job_id=job_id,
            request_file=file_name,
            status="Polling Completed"
        ))
    else:
        job_response = post_response

    if not job_response:
        raise Exception("Job unsuccessful")

    sas_uri = job_response.get("sas_uri")
    if not sas_uri:
        raise Exception("No SAS URI provided in job response")

    # Generate UUID for the file name
    if os.path.basename(file_path) == "d1_file_import_id.json":
        file_uuid = generate_uuid()
        file_name = f"{file_uuid}.csv"
    else:
        file_name = data.get("results_file_name", "query_results")

    downloaded = download_file(sas_uri, file_name)
    if not downloaded:
        raise Exception("Download file not generated")

    downloaded_basename = os.path.basename(downloaded)

    log_event(format_job_message(
        job_id=job_id,
        request_file=os.path.basename(file_path),
        status="File downloaded",
        output_file=downloaded_basename
    ))
    
    return (file_path, downloaded, job_id)


def process_generated_query_file(auth, file_path):

    file_name = os.path.basename(file_path)
    
    with open(file_path, "r") as f:
        data = json.load(f)

    validate_generated_query_json(data)

    log_event(format_job_message(
        job_id=None,
        request_file=file_name,
        status="Processing"
    ))

    post_response, query_params = post_generated_query_request(auth, data)
    if not post_response:
        raise Exception("No response from request")

    job_id = post_response.get("id")
    if not job_id:
        raise Exception("Job ID not returned in generated query response")

    log_event(format_job_message(
        job_id=job_id,
        request_file=file_name,
        status="Job created"
    ))

    if data.get("ux_mode", "Asynchronous") == "Synchronous":

        log_event(format_job_message(
            job_id=job_id,
            request_file=file_name,
            status="Polling status every 8 seconds..."
        ))
        
        job_response = poll_job_status(auth, job_id, query_params)
        
        log_event(format_job_message(
            job_id=job_id,
            request_file=file_name,
            status="Polling Completed"
        ))
    else:
        job_response = post_response

    if not job_response:
        raise Exception("Job did not complete successfully")

    sas_uri = job_response.get("sas_uri")
    if not sas_uri:
        raise Exception("No SAS URI provided in job response")

    # If it's "d1_file_import_id.json", override file name with a UUID
    if os.path.basename(file_path) == "d1_file_import_id.json":
        file_name = generate_uuid()
    else:
        file_name = data.get("results_file_name", "query_results")

    downloaded = download_file(sas_uri, file_name)
    if not downloaded:
        raise Exception("Download file not generated")

    downloaded_basename = os.path.basename(downloaded)
    
    log_event(format_job_message(
        job_id=job_id,
        request_file=os.path.basename(file_path),
        status="File downloaded",
        output_file=downloaded_basename
    ))
    
    # If this is an email query, process the results to append ImportID
    if os.path.basename(file_path) == "generated_query.json":
        processed_file = process_email_query_results(downloaded)
        if processed_file != downloaded:
            processed_basename = os.path.basename(processed_file)
            
            log_event(format_job_message(
                job_id=job_id,
                request_file=os.path.basename(file_path),
                status="File processed with ImportID",
                output_file=processed_basename
            ))
            
            return processed_file, job_id
    
    return downloaded, job_id


def archive_old_files():

    now = time.time()
    threshold = 6 * 24 * 60 * 60
    Path(ARCHIVE_FOLDER).mkdir(exist_ok=True, parents=True)
    files_archived = 0
    
    try:
        for filename in os.listdir(COMPLETED_FOLDER):
            file_path = os.path.join(COMPLETED_FOLDER, filename)
            if filename.lower().endswith('.json') or os.path.isdir(file_path):
                continue
                
            try:
                mod_time = os.path.getmtime(file_path)
                file_age = now - mod_time
                age_days = file_age / (24 * 60 * 60)
                                
                if file_age > threshold:
                    date_prefix = datetime.fromtimestamp(mod_time).strftime('%Y-%m-%d_')
                    new_filename = date_prefix + filename
                    archive_path = os.path.join(ARCHIVE_FOLDER, new_filename)
                    
                    if os.path.exists(archive_path):
                        unique_id = str(uuid.uuid4())[:8]
                        new_filename = f"{date_prefix}{unique_id}_{filename}"
                        archive_path = os.path.join(ARCHIVE_FOLDER, new_filename)
                    
                    shutil.move(file_path, archive_path)
                    files_archived += 1
            except Exception as e:
                print(f"  ERROR with file {filename}: {str(e)}")
    except Exception as e:
        print(f"Error listing directory: {str(e)}")

    return files_archived


def move_processed_files(src_json, downloaded_file, success=True, job_id=None):

    dest_folder = COMPLETED_FOLDER if success else FAILED_FOLDER
    status = "Complete" if success else "FAILED"
    src_json_name = os.path.basename(src_json) if src_json and os.path.exists(src_json) else "N/A"
    downloaded_name = os.path.basename(downloaded_file) if downloaded_file and os.path.exists(downloaded_file) else None
    destination_file = None
    
    if src_json and os.path.exists(src_json):
        dest_json = os.path.join(dest_folder, src_json_name)
        shutil.move(src_json, dest_json)
    
    if success and downloaded_file and os.path.exists(downloaded_file):
        dest_file = os.path.join(dest_folder, downloaded_name)
        shutil.move(downloaded_file, dest_file)
        destination_file = dest_file
    
    log_event(format_job_message(
        job_id=job_id,
        request_file=src_json_name,
        status=status,
        output_file=downloaded_name
    ))
    
    return destination_file


def main():
    ensure_folders_and_log()

    auth = BlackbaudAuth()
    log_event("Starting query processor... \nMonitoring folder 'query_request'")

    while True:
        try:
            req_file = wait_for_new_json()
            req_file_name = os.path.basename(req_file)
            log_event(f"New request file: {req_file_name}\n")
            
            downloaded_file = None
            job_id = None
            
            try:
                filename_only = os.path.basename(req_file)
                
                if filename_only == "generated_query.json":
                    # Process generated query
                    downloaded_file, job_id = process_generated_query_file(auth, req_file)
                    move_processed_files(req_file, downloaded_file, success=True, job_id=job_id)
                    
                    # Also move the mapping file if it exists
                    mapping_file = "email_to_importid_mapping.json"
                    if os.path.exists(mapping_file):
                        dest_mapping = os.path.join(COMPLETED_FOLDER, mapping_file)
                        shutil.move(mapping_file, dest_mapping)
                        log_event(format_job_message(
                            job_id=job_id,
                            request_file=mapping_file,
                            status="Moved mapping file"
                        ))
                    
                elif filename_only == "parish_trans_report.json":
                    # Process parish report JSON
                    json_file, downloaded_csv, job_id = process_standard_query_file(auth, req_file)
                    
                    # Generate the parish report Excel file
                    log_event(format_job_message(
                        job_id=job_id, 
                        request_file=filename_only,
                        status="Generating parish report"
                    ))
                    
                    excel_report = process_parish_report(downloaded_csv)
                    
                    if excel_report and os.path.exists(excel_report):
                        dest_json = os.path.join(COMPLETED_FOLDER, os.path.basename(req_file))
                        shutil.move(req_file, dest_json)
                        
                        excel_name = os.path.basename(excel_report)
                        dest_excel = os.path.join(COMPLETED_FOLDER, excel_name)
                        shutil.move(excel_report, dest_excel)
                        
                        csv_name = os.path.basename(downloaded_csv)
                        dest_csv = os.path.join(ARCHIVE_FOLDER, csv_name)
                        shutil.move(downloaded_csv, dest_csv)
                        
                        log_event(format_job_message(
                            job_id=job_id,
                            request_file=os.path.basename(req_file),
                            status="Complete",
                            output_file=f"{excel_name}\n"
                        ))
                    else:
                        move_processed_files(req_file, downloaded_csv, success=False, job_id=job_id)
                
                elif filename_only == "d1_file_import_id.json":
                    # Existing d1_file_import_id.json logic
                    json_file, downloaded_file, job_id = process_standard_query_file(auth, req_file)
                    
                    log_event(format_job_message(
                        job_id=job_id,
                        request_file=filename_only,
                        status="Uploading to SFTP",
                        output_file=os.path.basename(downloaded_file)
                    ))
                    
                    upload_success = upload_and_archive_csv(downloaded_file)
                    
                    if upload_success:
                        # Only move files if upload was successful
                        move_processed_files(req_file, downloaded_file, success=True, job_id=job_id)
                    else:
                        if os.path.exists(downloaded_file):
                            move_processed_files(req_file, downloaded_file, success=False, job_id=job_id)
                        else:
                            move_processed_files(req_file, None, success=False, job_id=job_id)
                else:
                    # Normal flow for other files
                    json_file, downloaded_file, job_id = process_standard_query_file(auth, req_file)
                    move_processed_files(req_file, downloaded_file, success=True, job_id=job_id)

                # After all processing is complete, run the archive function
                try:
                    archive_count = archive_old_files()
                    if archive_count > 0:
                        log_event(f"Archived {archive_count} old files")
                except Exception as e:
                    log_event(f"Archive process error: {str(e)}", also_print=True)

            except RequestFailedException as ex:
                error_msg = f"HTTP Error: {ex.status_code}\nResponse Error: {ex.error_text}"
                log_event(format_job_message(
                    job_id=job_id,
                    request_file=req_file_name,
                    status="FAILED",
                    error_message=error_msg
                ))
                
                if os.path.exists(req_file):
                    move_processed_files(req_file, None, success=False, job_id=job_id)

            except Exception as e:
                import traceback
                error_msg = str(e)
                trace_msg = traceback.format_exc()
                
                log_event(format_job_message(
                    job_id=job_id,
                    request_file=req_file_name,
                    status="FAILED",
                    error_message=error_msg
                ))
                
                # traceback
                log_event(f"Traceback for {req_file_name}:\n{trace_msg}")
                
                if os.path.exists(req_file):
                    move_processed_files(req_file, None, success=False, job_id=job_id)
            
            log_event("Monitoring 'query_request' folder\n")
            
        except Exception as e:
            # Catch-all for errors that could occur outside the request processing
            import traceback
            error_msg = f"Critical error in main loop: {str(e)}"
            trace_msg = traceback.format_exc()
            log_event(error_msg)
            log_event(f"Traceback:\n{trace_msg}")
            
            time.sleep(5)
            # Continue the loop rather than crashing
            log_event("Recovering from error. \nMonitoring for new files in 'query_request' folder\n")
            continue
            
        time.sleep(2)


if __name__ == "__main__":
    main()
