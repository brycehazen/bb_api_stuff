# `bb_auth.py` - Blackbaud Authentication Module

## **Overview and Prerequisites**
`bb_auth.py` is a Python module that manages authentication for the Blackbaud SKY API. [ It securely stores credentials using `keyring`](https://github.com/brycehazen/keyrin_cli), supports OAuth login, and handles token refreshing automatically. This is wrapped by secure_keyring which tracks usage of keys and api calls
- You must setup an application first by going to your developer.blackbaud.com account and going to My applications. 
### **Only after the application is setup:**
- Get Application ID/OAuth client_id (app_id in json)
- Primary application secret (app_secret in json)
- Set up scopes
- Review and confirm changes in Blackbaud Marketplace as an admin under manage
- Primary access key found in Developer account>My subscriptions (api_subscription_key in json)

---

## **How It Works**
- Uses **keyring** to securely store `client_id`, `client_secret`, and API tokens.
- If no refresh token is found, it **opens a browser for OAuth login**.
- Runs a **temporary local server** at `http://localhost:13631/` to capture the authorization code.
- Exchanges the **auth code for an access token** and refresh token, storing them in `keyring`.
- **Automatically refreshes** tokens when needed.

---

## **Using `bb_auth.py` in Scripts**
### **1. Import and Initialize Authentication**
```python
from bb_auth import BlackbaudAuth

auth = BlackbaudAuth()
```

- If an access token is available, it is used.
- If expired, the script **refreshes the token** automatically .
- If no valid refresh token exists, the user is prompted to log in.

---

### **2. Make an Authenticated API Request**
```python
response = auth.make_request("GET", "/constituent/v1/constituents")
if response:
    print(response)
```

- Uses the stored `access_token` to authenticate API requests.
- If unauthorized (`401`), it attempts to refresh the token before retrying.

---

### **3. Refresh Token Manually**
```python
auth.refresh_access_token()
```

- This retrieves a **new access token** using the refresh token.
- Stores the updated tokens in `keyring`.

---

### **4. Retrieve Stored Credentials**
To get stored credentials from `keyring`:
```python
import keyring

SERVICE_NAME = "GlobalSecrets"
client_id = keyring.get_password(SERVICE_NAME, "sky_app_information.app_id")
refresh_token = keyring.get_password(SERVICE_NAME, "tokens.refresh_token")
```

---

## **Example: Using `bb_auth.py` in Another Script**
### **`bb_query.py` Example**
This example demonstrates how `bb_auth.py` can be integrated into another script to make authenticated API requests.

```python
import json
import requests
from bb_auth import BlackbaudAuth

auth = BlackbaudAuth()

# Define the API endpoint
endpoint = "/gift/v2/gifts"

# Define the request parameters
params = {
    "date_added": "2025-01-01T00:00:00Z"
}

# Make the API request
response = auth.make_request("GET", endpoint, params=params)

# Print the response
if response:
    print(json.dumps(response, indent=4))
else:
    print("Failed to fetch data from Blackbaud API.")
```

---

## **Keyring Storage Keys(depending what you called them when storing them in keyring)**
| **Key Name**                      | **Description** |
|------------------------------------|----------------|
| `sky_app_information.app_id`      | Blackbaud Client ID |
| `sky_app_information.app_secret`  | Blackbaud Client Secret |
| `tokens.access_token`             | OAuth Access Token |
| `tokens.refresh_token`            | OAuth Refresh Token |
| `other.api_subscription_key`      | API Subscription Key |

---

## **Handling OAuth Login**
1. **If no refresh token exists**, `bb_auth.py`:
   - Opens `https://app.blackbaud.com/oauth/authorize`.
   - Starts a local server at `http://localhost:13631/` to receive the auth code.
   - Exchanges the code for an **access token** and **refresh token**.
   - Stores both tokens securely in `keyring`.

2. **On future runs**, the script:
   - Uses the stored `access_token`.
   - If expired, refreshes the token automatically.

---

## **Example Workflow**
1. First run: **Login through browser** (OAuth).
2. Tokens stored in **keyring**.
3. API calls use the **stored access token**.
4. If expired, the **refresh token** is used to get a new one.
5. No JSON files needed—everything is securely stored.

---

## **Notes**
- This module **eliminates the need for JSON/TOML files** for authentication.
- Everything is stored in **keyring** and securely managed.
- Refresh tokens are automatically updated to prevent expired credentials.

---

## **Notes**
- This script **relies on `bb_auth.py` for authentication**.
- It **requires API credentials stored in keyring** (configured via `keyring_cli.py`).
- JSON files should be **properly formatted** before placing them in `query_request/`.


# `bb_query_ftp.py` - Main hub - Auth using bb_auth and triggers workflows

## **Overview**
`bb_query_ftp.py` Monitors folder waiting for specified file types and names. Depnding on the file name and type, triggers specific workflows. It processes Blackbaud query requests, retrieves the results, and can automatically upload them to an SFTP server. This script is ideal for automated data pipelines that need to move data to other systems. Windows Task scheduler is used to move files to tigger recurring workflows.

---

## **How It Works**
1. **Monitors a folder** (`query_request/`) for new JSON query request files.
2. **Processes standard or generated queries** based on the JSON format.
3. Specific json file names, trigger specific classes to process the query output file
4. **Downloads query results** when jobs complete.
5. **Optionally uploads** results to an SFTP server.
6. **Archives processed files** to maintain organization.

## **Key Functions**
### **1. `post_query_request(auth, data)`**
- Submits a query request to the Blackbaud API.

### **2. `poll_job_status(auth, job_id, query_params)`**
- Monitors the status of an ongoing API query job.

### **3. `download_file(url, file_name)`**
- Downloads query results once completed.

### **4. `log_event(message)`**
- Logs API interactions in `api_log/`.

## **Folder Structure**
| **Folder**          | **Description** |
|---------------------|----------------|
| `query_request/`   | New JSON query request files |
| `query_completed/` | Successfully processed requests |
| `failed_requests/` | Failed query requests |
| `api_log/`         | Logs of API interactions |


---

### **Processing Flow**
1. Script detects a new file in `query_request/`.
2. Submits the request to the Blackbaud API.
3. Polls the job status until completion.
4. Downloads the results and moves processed files:
   - Successful requests → `query_completed/`
   - Failed requests → `failed_requests/`

### **JSON Query Request Format**
A query request file must contain:
```json
{
    "id": "12345",
    "product": "RE",
    "module": "Constituent",
    "ux_mode": "Asynchronous",
    "output_format": "CSV"
}
```
- `"id"`: The query ID to execute.
- `"product"`: The Blackbaud product (e.g., `"RE"` for Raiser's Edge).
- `"module"`: The API module being queried.
- Optional fields like `"ux_mode"`, `"output_format"`, `"results_file_name"`.
- Use the name of the json file to trigger different workflows. 


## **Using `bb_query_ftp.py`**
### **1. Run the Script**
```sh
python bb_query_ftp.py
```
- Starts monitoring `query_request/` for new JSON files.
- Processes requests and handles SFTP uploads as needed.

---

## **Example: Running `bb_query.py` in a Project**
```python
from bb_auth import BlackbaudAuth
import json

auth = BlackbaudAuth()

# Define query request payload
query_data = {
    "id": "12345",
    "product": "RE",
    "module": "Constituent",
    "output_format": "CSV"
}

# Make a request
response = auth.make_request("POST", "/query/queries/executebyid", data=query_data)

# Print response
if response:
    print(json.dumps(response, indent=4))
else:
    print("Query execution failed.")
```

### **2. SFTP Configuration**
To enable SFTP uploads, store these credentials in keyring:
```python
keyring.set_password(SERVICE_NAME, "sftp.host", "your_sftp_host")
keyring.set_password(SERVICE_NAME, "sftp.username", "your_username")
keyring.set_password(SERVICE_NAME, "sftp.password", "your_password")
keyring.set_password(SERVICE_NAME, "sftp.remote_dir", "/path/on/remote/server")
```

---

### **3. Extended Features**
- **Improved logging** with detailed status messages
- **Animation during polling** for better user experience
- **Automatic archiving** of older completed files
- **Error handling** with detailed logging

---

## **Example Workflow**
1. Save a query request JSON file in `query_request/`.
2. Run `bb_query_ftp.py`.
3. The script:
   - Submits the query request
   - Polls for completion
   - Downloads results
   - Uploads to SFTP server (if configured)
   - Archives processed files

---

# `notify_email.py` - Email Notifications for Completed Queries

## **Overview**
`notify_email.py` monitors the `query_completed/` folder and sends email notifications when new files appear. This helps users stay informed about completed Blackbaud queries without needing to check manually.

---

## **How It Works**
1. **Monitors the `query_completed/` folder** for new files.
2. **Tracks processed files** to avoid duplicate notifications.
3. **Sends email notifications** with details about new files.
4. **Logs all activity** for troubleshooting.

---

## **Configuration**
Store email credentials in keyring:
```python
keyring.set_password(SERVICE_NAME, "email.from", "your_email@example.com")
keyring.set_password(SERVICE_NAME, "email.password", "your_password")
keyring.set_password(SERVICE_NAME, "email.to", "recipient@example.com")
keyring.set_password(SERVICE_NAME, "email.smtp_server", "smtp.gmail.com")
keyring.set_password(SERVICE_NAME, "email.smtp_port", "587")
```

---

## **Using `notify_email.py`**
### **Run Once**
```sh
python notify_email.py
```

### **Run as Daemon**
```sh
python notify_email.py --daemon
```
- Checks for new files every minute
- Sends notifications for any newly detected files

---

# `notify_pushover.py` - Push Notifications for Completed Queries

## **Overview**
`notify_pushover.py` sends push notifications to your devices via Pushover when new files appear in the `query_completed/` folder. This provides immediate mobile notifications when Blackbaud queries complete.

---

## **How It Works**
1. **Monitors the `query_completed/` folder** for new files.
2. **Tracks processed files** to avoid duplicate notifications.
3. **Sends push notifications** through Pushover's API.
4. **Logs all activity** for troubleshooting.

---

## **Configuration**
Register for a Pushover account and store credentials in keyring:
```python
keyring.set_password(SERVICE_NAME, "pushover.app_token", "your_app_token")
keyring.set_password(SERVICE_NAME, "pushover.user_key", "your_user_key")
```

---

## **Using `notify_pushover.py`**
### **Run Once**
```sh
python notify_pushover.py
```

### **Run as Daemon**
```sh
python notify_pushover.py --daemon
```
- Checks for new files every minute
- Sends push notifications for any newly detected files

---

## **Additional Notification Options**
For SMS notifications, use the included `notify_sms.py` script with Twilio:
```python
keyring.set_password(SERVICE_NAME, "twilio.account_sid", "your_account_sid")
keyring.set_password(SERVICE_NAME, "twilio.auth_token", "your_auth_token")
keyring.set_password(SERVICE_NAME, "twilio.from_number", "+1234567890")
keyring.set_password(SERVICE_NAME, "twilio.to_number", "+1987654321")
```

Run it the same way as the other notification scripts.

