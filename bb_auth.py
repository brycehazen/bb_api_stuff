import sys
# directory containing secure_keyring.py to python 
sys.path.append(r"C:\path\keyring")
import secure_keyring  # type: ignore
import keyring  
import requests
import webbrowser
import http.server
import socketserver
import json
from urllib.parse import urlparse, parse_qs
from typing import Dict, Any, Optional

#  keyring_cli
SERVICE_NAME = "GlobalSecrets"

# Retrieve stored credentials
CLIENT_ID = secure_keyring.get_password("sky_app_information.app_id")
CLIENT_SECRET = secure_keyring.get_password("sky_app_information.app_secret")
REDIRECT_URI = secure_keyring.get_password("other.redirect_url") or "http://localhost:13631/"

# OAuth
AUTH_URL = "https://app.blackbaud.com/oauth/authorize"
TOKEN_URL = "https://oauth2.sky.blackbaud.com/token"

class RequestFailedException(Exception):
    """
    Custom exception to capture HTTP status code, error text, and JSON details.
    """
    def __init__(self, status_code: int, error_text: str, error_json: Optional[dict] = None):
        self.status_code = status_code
        self.error_text = error_text
        self.error_json = error_json
        message = f"Request failed with status {status_code}. Response: {error_text}"
        super().__init__(message)

class ResponseStatusCodes(Exception):
    def __init__(self, status_code: int, message: str, retry_after: Optional[int] = None):
        self.status_code = status_code
        self.message = message
        self.retry_after = retry_after
        super().__init__(f"Error {status_code}: {message}")

class ResponseStatusCodes(Exception):
    def __init__(self, status_code: int, message: str, retry_after: Optional[int] = None):
        self.status_code = status_code
        self.message = message
        self.retry_after = retry_after
        super().__init__(f"Error {status_code}: {message}")

class BlackbaudAuth:
    def __init__(self):
        """Initialize BlackbaudAuth using keyring for secrets."""
        self.access_token = secure_keyring.get_password("tokens.access_token")
        self.refresh_token = secure_keyring.get_password("tokens.refresh_token")

        if not CLIENT_ID or not CLIENT_SECRET:
            raise ValueError("CLIENT_ID or CLIENT_SECRET not found in keyring. Run `python keyring_cli.py store --key sky_app_information.app_id --value YOUR_CLIENT_ID`")

        # If no refresh token, prompt user to authenticate
        if not self.refresh_token:
            print("No refresh token found. Redirecting to login...")
            self.authenticate_user()

    def authenticate_user(self):
        """Perform OAuth authentication by opening the browser for login."""
        print("Opening browser for authentication...")
        auth_url = f"{AUTH_URL}?client_id={CLIENT_ID}&response_type=code&redirect_uri={REDIRECT_URI}"
        webbrowser.open(auth_url)

        # Start a temporary web server to listen for OAuth callback
        with socketserver.TCPServer(("localhost", 13631), OAuthCallbackHandler) as httpd:
            httpd.handle_request()  # This waits until the browser sends the code

    def exchange_code_for_token(self, auth_code: str):
        """Exchange authorization code for access & refresh tokens."""
        payload = {
            "grant_type": "authorization_code",
            "code": auth_code,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "redirect_uri": REDIRECT_URI,
        }
        response = requests.post(TOKEN_URL, data=payload)
        response.raise_for_status()
        token_data = response.json()

        # Store tokens securely in keyring
        secure_keyring.set_password("tokens.access_token", token_data["access_token"], "OAuth access token")
        secure_keyring.set_password("tokens.refresh_token", token_data["refresh_token"], "OAuth refresh token")

        self.access_token = token_data["access_token"]
        self.refresh_token = token_data["refresh_token"]

        print("Authentication successful! Tokens stored securely in keyring.")

    def refresh_access_token(self) -> bool:
        """Refresh the access token using the refresh token stored in keyring."""
        if not self.refresh_token:
            print("No refresh token found. Please re-authenticate.")
            self.authenticate_user()
            return False

        payload = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET
        }

        try:
            response = requests.post(TOKEN_URL, data=payload)
            response.raise_for_status()
            token_data = response.json()

            #  Update stored tokens
            secure_keyring.set_password("tokens.access_token", token_data["access_token"], "OAuth access token")
            secure_keyring.set_password("tokens.refresh_token", token_data["refresh_token"], "OAuth refresh token")

            self.access_token = token_data["access_token"]
            self.refresh_token = token_data["refresh_token"]
            return True
        except requests.exceptions.RequestException as e:
            print(f"Error refreshing token: {str(e)}. Please re-authenticate.")
            self.authenticate_user()
            return False

    def get_session(self, use_payment_key=False) -> requests.Session:
        """Get a configured requests session with appropriate headers. If use_payment_key is True, use the payment subscription key."""
        if not self.access_token:
            self.refresh_access_token()  # Ensure valid token

        session = requests.Session()
        if use_payment_key:
            sub_key = secure_keyring.get_password("other.payment_subscription_key")
            if not sub_key:
                sub_key = secure_keyring.get_password("other.api_subscription_key")
        else:
            sub_key = secure_keyring.get_password("other.api_subscription_key")
        session.headers = {
            'Bb-Api-Subscription-Key': sub_key,
            'Authorization': f"Bearer {self.access_token}"
        }
        return session

    def make_request(self, method: str, endpoint: str,
                     params: Optional[Dict] = None,
                     data: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Make an authenticated request. If a 401 with invalid subscription key is returned, retry with payment key.
        If both fail, print the error JSON as specified and return None.
        """
        url = f"https://api.sky.blackbaud.com{endpoint}"
        # First try with default key
        session = self.get_session(use_payment_key=False)
        try:
            response = session.request(method, url, params=params, json=data)
            if response.status_code == 401:
                try:
                    error_json = response.json()
                    error_text = json.dumps(error_json, indent=2)
                except Exception:
                    error_json = None
                    error_text = response.text.strip()
                # Check for invalid subscription key message
                if error_json and "invalid subscription key" in error_json.get("message", "").lower():
                    # Try with payment key
                    session = self.get_session(use_payment_key=True)
                    response = session.request(method, url, params=params, json=data)
                    if response.status_code == 401:
                        try:
                            error_json = response.json()
                            error_text = json.dumps(error_json, indent=2)
                        except Exception:
                            error_json = None
                            error_text = response.text.strip()
                        if error_json and "invalid subscription key" in error_json.get("message", "").lower():
                            print(error_text)
                            return None
                        else:
                            raise RequestFailedException(
                                status_code=401,
                                error_text=error_text,
                                error_json=error_json
                            )
                    # If not 401, continue as normal
                else:
                    # Not a subscription key error, try refresh
                    print("Unauthorized (401). Attempting to refresh token...")
                    if self.refresh_access_token():
                        session = self.get_session(use_payment_key=False)
                        response = session.request(method, url, params=params, json=data)
                    else:
                        raise RequestFailedException(
                            status_code=401,
                            error_text="Re-authentication required; 401 Unauthorized",
                        )
            # Check if the response is OK (200-299); if not, capture error details
            if not response.ok:
                status_code = response.status_code
                try:
                    error_json = response.json()
                    error_text = json.dumps(error_json, indent=2)
                except Exception:
                    error_json = None
                    error_text = response.text.strip()
                raise RequestFailedException(
                    status_code=status_code,
                    error_text=error_text,
                    error_json=error_json
                )
            return response.json()
        except requests.exceptions.RequestException as err:
            raise RequestFailedException(
                status_code=-1,
                error_text=f"Request Exception occurred: {err}"
            )


class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    """Handle OAuth callback from Blackbaud login."""
    def do_GET(self):
        parsed_url = urlparse(self.path)
        query_params = parse_qs(parsed_url.query)
        
        if "code" in query_params:
            auth_code = query_params["code"][0]
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body><h1>Authentication Successful!</h1><p>You can close this tab.</p></body></html>")
            
            # Exchange code for tokens
            auth_instance = BlackbaudAuth()
            auth_instance.exchange_code_for_token(auth_code)
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"<html><body><h1>Authentication Failed</h1><p>No authorization code received.</p></body></html>")

