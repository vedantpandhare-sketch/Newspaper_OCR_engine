import os
import json
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ['https://www.googleapis.com/auth/drive.file', 'https://www.googleapis.com/auth/drive']


def get_drive_service():
    """
    Authenticates via Desktop OAuth flow.
    Supports local token.json or GitHub Actions secret environment variable.
    """
    creds = None
    
    # 1. Check if token JSON is provided via GitHub Secret / Environment Variable
    env_token = os.environ.get("GDRIVE_TOKEN_JSON")
    if env_token:
        print("[Drive Auth] Loading credentials from environment variable...")
        token_data = json.loads(env_token)
        creds = Credentials.from_authorized_user_info(token_data, SCOPES)

    # 2. Check if local token.json file exists
    elif os.path.exists('token.json'):
        print("[Drive Auth] Loading credentials from local token.json...")
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)

    # 3. Refresh token if expired
    if creds and creds.expired and creds.refresh_token:
        print("[Drive Auth] Refreshing expired OAuth token...")
        creds.refresh(Request())
        # Save refreshed token locally if running on local machine
        if os.path.exists('token.json'):
            with open('token.json', 'w') as token:
                token.write(creds.to_json())

    # 4. Fallback for new interactive login (Local runs only)
    if not creds or not creds.valid:
        if not os.path.exists('credentials.json'):
            raise FileNotFoundError(
                "Missing 'credentials.json'! Download Desktop OAuth credentials from Google Cloud Console."
            )
        print("[Drive Auth] Launching browser for one-time Google login...")
        flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
        creds = flow.run_local_server(port=0)

        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    return build('drive', 'v3', credentials=creds)


def upload_file_to_drive(file_path: str, folder_id: str) -> str:
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Local file not found at: {file_path}")

    service = get_drive_service()
    filename = os.path.basename(file_path)

    file_metadata = {
        'name': filename,
        'parents': [folder_id]
    }

    media = MediaFileUpload(file_path, mimetype='application/pdf', resumable=True)

    print(f"[Drive] Uploading '{filename}' to Google Drive...")
    file = service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id'
    ).execute()

    file_id = file.get('id')
    print(f"[Drive] Successfully uploaded! File ID: {file_id}")
    return file_id