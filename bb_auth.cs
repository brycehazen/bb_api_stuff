using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using System.Web;
using Microsoft.Win32;

namespace BlackbaudAuth
{
    // Custom exceptions
    public class RequestFailedException : Exception
    {
        public int StatusCode { get; }
        public string ErrorText { get; }
        public JsonElement? ErrorJson { get; }

        public RequestFailedException(int statusCode, string errorText, JsonElement? errorJson = null)
            : base($"Request failed with status {statusCode}. Response: {errorText}")
        {
            StatusCode = statusCode;
            ErrorText = errorText;
            ErrorJson = errorJson;
        }
    }

    public class ResponseStatusCodes : Exception
    {
        public int StatusCode { get; }
        public string Message { get; }
        public int? RetryAfter { get; }

        public ResponseStatusCodes(int statusCode, string message, int? retryAfter = null)
            : base($"Error {statusCode}: {message}")
        {
            StatusCode = statusCode;
            Message = message;
            RetryAfter = retryAfter;
        }
    }

    // Simple credential manager using Windows Registry (replace with more secure solution in production)
    public static class SecureKeyring
    {
        private const string RegistryPath = @"SOFTWARE\BlackbaudAuth\Credentials";

        public static void SetPassword(string key, string value, string description = "")
        {
            using var regKey = Registry.CurrentUser.CreateSubKey(RegistryPath);
            regKey?.SetValue(key, ProtectedData.Protect(value));
        }

        public static string GetPassword(string key)
        {
            using var regKey = Registry.CurrentUser.OpenSubKey(RegistryPath);
            var encryptedValue = regKey?.GetValue(key) as byte[];
            return encryptedValue != null ? ProtectedData.Unprotect(encryptedValue) : null;
        }
    }

    // Simple data protection class (you might want to use Windows DPAPI for better security)
    public static class ProtectedData
    {
        public static byte[] Protect(string data)
        {
            return Encoding.UTF8.GetBytes(Convert.ToBase64String(Encoding.UTF8.GetBytes(data)));
        }

        public static string Unprotect(byte[] data)
        {
            var base64 = Encoding.UTF8.GetString(data);
            return Encoding.UTF8.GetString(Convert.FromBase64String(base64));
        }
    }

    public class BlackbaudAuthClient
    {
        private const string ServiceName = "GlobalSecrets";
        private const string AuthUrl = "https://app.blackbaud.com/oauth/authorize";
        private const string TokenUrl = "https://oauth2.sky.blackbaud.com/token";
        private const int CallbackPort = 13631;

        private readonly string _clientId;
        private readonly string _clientSecret;
        private readonly string _redirectUri;
        private readonly HttpClient _httpClient;

        private string _accessToken;
        private string _refreshToken;

        public BlackbaudAuthClient()
        {
            _clientId = SecureKeyring.GetPassword("sky_app_information.app_id");
            _clientSecret = SecureKeyring.GetPassword("sky_app_information.app_secret");
            _redirectUri = SecureKeyring.GetPassword("other.redirect_url") ?? $"http://localhost:{CallbackPort}/";

            if (string.IsNullOrEmpty(_clientId) || string.IsNullOrEmpty(_clientSecret))
            {
                throw new InvalidOperationException("CLIENT_ID or CLIENT_SECRET not found in keyring. Please store credentials first.");
            }

            _httpClient = new HttpClient();
            _accessToken = SecureKeyring.GetPassword("tokens.access_token");
            _refreshToken = SecureKeyring.GetPassword("tokens.refresh_token");

            if (string.IsNullOrEmpty(_refreshToken))
            {
                Console.WriteLine("No refresh token found. Redirecting to login...");
                _ = AuthenticateUserAsync();
            }
        }

        public async Task AuthenticateUserAsync()
        {
            Console.WriteLine("Opening browser for authentication...");
            var authUrl = $"{AuthUrl}?client_id={_clientId}&response_type=code&redirect_uri={Uri.EscapeDataString(_redirectUri)}";
            
            // Open browser
            Process.Start(new ProcessStartInfo
            {
                FileName = authUrl,
                UseShellExecute = true
            });

            // Start callback server
            await StartCallbackServerAsync();
        }

        private async Task StartCallbackServerAsync()
        {
            var listener = new HttpListener();
            listener.Prefixes.Add($"http://localhost:{CallbackPort}/");
            listener.Start();

            Console.WriteLine($"Listening for OAuth callback on port {CallbackPort}...");

            var context = await listener.GetContextAsync();
            var request = context.Request;
            var response = context.Response;

            var query = HttpUtility.ParseQueryString(request.Url.Query);
            var code = query["code"];

            if (!string.IsNullOrEmpty(code))
            {
                var responseString = "<html><body><h1>Authentication Successful!</h1><p>You can close this tab.</p></body></html>";
                var buffer = Encoding.UTF8.GetBytes(responseString);
                response.ContentLength64 = buffer.Length;
                await response.OutputStream.WriteAsync(buffer, 0, buffer.Length);
                response.OutputStream.Close();

                await ExchangeCodeForTokenAsync(code);
            }
            else
            {
                var responseString = "<html><body><h1>Authentication Failed</h1><p>No authorization code received.</p></body></html>";
                var buffer = Encoding.UTF8.GetBytes(responseString);
                response.StatusCode = 400;
                response.ContentLength64 = buffer.Length;
                await response.OutputStream.WriteAsync(buffer, 0, buffer.Length);
                response.OutputStream.Close();
            }

            listener.Stop();
        }

        public async Task ExchangeCodeForTokenAsync(string authCode)
        {
            var payload = new Dictionary<string, string>
            {
                {"grant_type", "authorization_code"},
                {"code", authCode},
                {"client_id", _clientId},
                {"client_secret", _clientSecret},
                {"redirect_uri", _redirectUri}
            };

            var response = await _httpClient.PostAsync(TokenUrl, new FormUrlEncodedContent(payload));
            response.EnsureSuccessStatusCode();

            var responseContent = await response.Content.ReadAsStringAsync();
            var tokenData = JsonSerializer.Deserialize<JsonElement>(responseContent);

            var accessToken = tokenData.GetProperty("access_token").GetString();
            var refreshToken = tokenData.GetProperty("refresh_token").GetString();

            // Store tokens securely
            SecureKeyring.SetPassword("tokens.access_token", accessToken, "OAuth access token");
            SecureKeyring.SetPassword("tokens.refresh_token", refreshToken, "OAuth refresh token");

            _accessToken = accessToken;
            _refreshToken = refreshToken;

            Console.WriteLine("Authentication successful! Tokens stored securely.");
        }

        public async Task<bool> RefreshAccessTokenAsync()
        {
            if (string.IsNullOrEmpty(_refreshToken))
            {
                Console.WriteLine("No refresh token found. Please re-authenticate.");
                await AuthenticateUserAsync();
                return false;
            }

            var payload = new Dictionary<string, string>
            {
                {"grant_type", "refresh_token"},
                {"refresh_token", _refreshToken},
                {"client_id", _clientId},
                {"client_secret", _clientSecret}
            };

            try
            {
                var response = await _httpClient.PostAsync(TokenUrl, new FormUrlEncodedContent(payload));
                response.EnsureSuccessStatusCode();

                var responseContent = await response.Content.ReadAsStringAsync();
                var tokenData = JsonSerializer.Deserialize<JsonElement>(responseContent);

                var accessToken = tokenData.GetProperty("access_token").GetString();
                var refreshToken = tokenData.GetProperty("refresh_token").GetString();

                // Update stored tokens
                SecureKeyring.SetPassword("tokens.access_token", accessToken, "OAuth access token");
                SecureKeyring.SetPassword("tokens.refresh_token", refreshToken, "OAuth refresh token");

                _accessToken = accessToken;
                _refreshToken = refreshToken;
                return true;
            }
            catch (HttpRequestException e)
            {
                Console.WriteLine($"Error refreshing token: {e.Message}. Please re-authenticate.");
                await AuthenticateUserAsync();
                return false;
            }
        }

        public HttpClient GetSession(bool usePaymentKey = false)
        {
            if (string.IsNullOrEmpty(_accessToken))
            {
                _ = RefreshAccessTokenAsync(); // Ensure valid token
            }

            var client = new HttpClient();
            string subKey;

            if (usePaymentKey)
            {
                subKey = SecureKeyring.GetPassword("other.payment_subscription_key");
                if (string.IsNullOrEmpty(subKey))
                {
                    subKey = SecureKeyring.GetPassword("other.api_subscription_key");
                }
            }
            else
            {
                subKey = SecureKeyring.GetPassword("other.api_subscription_key");
            }

            client.DefaultRequestHeaders.Add("Bb-Api-Subscription-Key", subKey);
            client.DefaultRequestHeaders.Add("Authorization", $"Bearer {_accessToken}");

            return client;
        }

        public async Task<JsonElement?> MakeRequestAsync(string method, string endpoint, 
            Dictionary<string, string> parameters = null, object data = null)
        {
            var url = $"https://api.sky.blackbaud.com{endpoint}";
            
            if (parameters?.Count > 0)
            {
                var query = HttpUtility.ParseQueryString(string.Empty);
                foreach (var param in parameters)
                {
                    query[param.Key] = param.Value;
                }
                url += "?" + query.ToString();
            }

            // First try with default key
            using var session = GetSession(usePaymentKey: false);
            
            try
            {
                HttpResponseMessage response = method.ToUpper() switch
                {
                    "GET" => await session.GetAsync(url),
                    "POST" => await session.PostAsync(url, data != null ? 
                        new StringContent(JsonSerializer.Serialize(data), Encoding.UTF8, "application/json") : null),
                    "PUT" => await session.PutAsync(url, data != null ? 
                        new StringContent(JsonSerializer.Serialize(data), Encoding.UTF8, "application/json") : null),
                    "DELETE" => await session.DeleteAsync(url),
                    _ => throw new ArgumentException($"Unsupported HTTP method: {method}")
                };

                if (response.StatusCode == HttpStatusCode.Unauthorized)
                {
                    var errorContent = await response.Content.ReadAsStringAsync();
                    JsonElement? errorJson = null;
                    string errorText = errorContent;

                    try
                    {
                        errorJson = JsonSerializer.Deserialize<JsonElement>(errorContent);
                        errorText = JsonSerializer.Serialize(errorJson, new JsonSerializerOptions { WriteIndented = true });
                    }
                    catch { }

                    // Check for invalid subscription key message
                    if (errorJson?.TryGetProperty("message", out var messageProperty) == true &&
                        messageProperty.GetString()?.ToLower().Contains("invalid subscription key") == true)
                    {
                        // Try with payment key
                        using var paymentSession = GetSession(usePaymentKey: true);
                        response = method.ToUpper() switch
                        {
                            "GET" => await paymentSession.GetAsync(url),
                            "POST" => await paymentSession.PostAsync(url, data != null ? 
                                new StringContent(JsonSerializer.Serialize(data), Encoding.UTF8, "application/json") : null),
                            "PUT" => await paymentSession.PutAsync(url, data != null ? 
                                new StringContent(JsonSerializer.Serialize(data), Encoding.UTF8, "application/json") : null),
                            "DELETE" => await paymentSession.DeleteAsync(url),
                            _ => throw new ArgumentException($"Unsupported HTTP method: {method}")
                        };

                        if (response.StatusCode == HttpStatusCode.Unauthorized)
                        {
                            errorContent = await response.Content.ReadAsStringAsync();
                            try
                            {
                                errorJson = JsonSerializer.Deserialize<JsonElement>(errorContent);
                                errorText = JsonSerializer.Serialize(errorJson, new JsonSerializerOptions { WriteIndented = true });
                            }
                            catch { }

                            if (errorJson?.TryGetProperty("message", out messageProperty) == true &&
                                messageProperty.GetString()?.ToLower().Contains("invalid subscription key") == true)
                            {
                                Console.WriteLine(errorText);
                                return null;
                            }
                            else
                            {
                                throw new RequestFailedException(401, errorText, errorJson);
                            }
                        }
                    }
                    else
                    {
                        // Not a subscription key error, try refresh
                        Console.WriteLine("Unauthorized (401). Attempting to refresh token...");
                        if (await RefreshAccessTokenAsync())
                        {
                            using var refreshedSession = GetSession(usePaymentKey: false);
                            response = method.ToUpper() switch
                            {
                                "GET" => await refreshedSession.GetAsync(url),
                                "POST" => await refreshedSession.PostAsync(url, data != null ? 
                                    new StringContent(JsonSerializer.Serialize(data), Encoding.UTF8, "application/json") : null),
                                "PUT" => await refreshedSession.PutAsync(url, data != null ? 
                                    new StringContent(JsonSerializer.Serialize(data), Encoding.UTF8, "application/json") : null),
                                "DELETE" => await refreshedSession.DeleteAsync(url),
                                _ => throw new ArgumentException($"Unsupported HTTP method: {method}")
                            };
                        }
                        else
                        {
                            throw new RequestFailedException(401, "Re-authentication required; 401 Unauthorized");
                        }
                    }
                }

                // Check if the response is OK (200-299)
                if (!response.IsSuccessStatusCode)
                {
                    var errorContent = await response.Content.ReadAsStringAsync();
                    JsonElement? errorJson = null;
                    string errorText = errorContent;

                    try
                    {
                        errorJson = JsonSerializer.Deserialize<JsonElement>(errorContent);
                        errorText = JsonSerializer.Serialize(errorJson, new JsonSerializerOptions { WriteIndented = true });
                    }
                    catch { }

                    throw new RequestFailedException((int)response.StatusCode, errorText, errorJson);
                }

                var responseContent = await response.Content.ReadAsStringAsync();
                return JsonSerializer.Deserialize<JsonElement>(responseContent);
            }
            catch (HttpRequestException err)
            {
                throw new RequestFailedException(-1, $"Request Exception occurred: {err.Message}");
            }
        }

        public void Dispose()
        {
            _httpClient?.Dispose();
        }
    }

    // Example usage
    public class Program
    {
        public static async Task Main(string[] args)
        {
            try
            {
                var authClient = new BlackbaudAuthClient();
                
                // Example API call
                var result = await authClient.MakeRequestAsync("GET", "/constituent/v1/constituents");
                
                if (result.HasValue)
                {
                    Console.WriteLine("API Response:");
                    Console.WriteLine(JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true }));
                }
            }
            catch (Exception ex)
            {
                Console.WriteLine($"Error: {ex.Message}");
            }
        }
    }
}
