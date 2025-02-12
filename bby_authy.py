import requests
import keyring
import webbrowser
import http.server
import socketserver
import json
from urllib.parse import urlparse, parse_qs
from typing import Dict, Any, Optional

# Use the correct service name from keyring_cli.py
SERVICE_NAME = "GlobalSecrets"

# Retrieve stored credentials
CLIENT_ID = keyring.get_password(SERVICE_NAME, "sky_app_information.app_id")
CLIENT_SECRET = keyring.get_password(SERVICE_NAME, "sky_app_information.app_secret")
REDIRECT_URI = keyring.get_password(SERVICE_NAME, "other.redirect_url") or "http://localhost:13631/"

# OAuth URLs
AUTH_URL = "https://app.blackbaud.com/oauth/authorize"
TOKEN_URL = "https://oauth2.sky.blackbaud.com/token"

class ResponseStatusCodes(Exception):
    def __init__(self, status_code: int, message: str, retry_after: Optional[int] = None):
        self.status_code = status_code
        self.message = message
        self.retry_after = retry_after
        super().__init__(f"Error {status_code}: {message}")

class BlackbaudAuth:
    def __init__(self):
        """Initialize BlackbaudAuth using keyring for secrets."""
        self.access_token = keyring.get_password(SERVICE_NAME, "tokens.access_token")
        self.refresh_token = keyring.get_password(SERVICE_NAME, "tokens.refresh_token")

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
        keyring.set_password(SERVICE_NAME, "tokens.access_token", token_data["access_token"])
        keyring.set_password(SERVICE_NAME, "tokens.refresh_token", token_data["refresh_token"])

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

            # Update stored tokens
            keyring.set_password(SERVICE_NAME, "tokens.access_token", token_data["access_token"])
            keyring.set_password(SERVICE_NAME, "tokens.refresh_token", token_data["refresh_token"])

            self.access_token = token_data["access_token"]
            self.refresh_token = token_data["refresh_token"]
            return True
        except requests.exceptions.RequestException as e:
            print(f"Error refreshing token: {str(e)}. Please re-authenticate.")
            self.authenticate_user()
            return False

    def get_session(self) -> requests.Session:
        """Get a configured requests session with appropriate headers."""
        if not self.access_token:
            self.refresh_access_token()  # Ensure valid token

        session = requests.Session()
        session.headers = {
            'Bb-Api-Subscription-Key': keyring.get_password(SERVICE_NAME, "other.api_subscription_key"),
            'Authorization': f"Bearer {self.access_token}"
        }
        return session

    def make_request(self, method: str, endpoint: str, params: Optional[Dict] = None, data: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
        """Make an authenticated request."""
        session = self.get_session()
        url = f"https://api.sky.blackbaud.com{endpoint}"

        try:
            response = session.request(method, url, params=params, json=data)

            if response.status_code == 401:
                print("Unauthorized error. Attempting to refresh token...")
                if self.refresh_access_token():
                    session = self.get_session()
                    response = session.request(method, url, params=params, json=data)
                else:
                    print("Re-authentication required.")
                    return None

            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as err:
            print(f"Request failed: {err}")
            return None

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
