import os
import json
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

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


def download_files_from_drive_by_date(
    folder_id: str, date_str: str, dest_dir: str
) -> list[str]:
    """
    Lists files in the given Drive folder whose name contains `date_str`
    (e.g. '2026-08-21'), downloads each one into dest_dir, and returns the
    local file paths. Used by the CI pipeline to pick up PDFs that were
    scraped and uploaded locally (where Cloudflare can actually be passed).
    """
    service = get_drive_service()
    os.makedirs(dest_dir, exist_ok=True)

    query = (
        f"'{folder_id}' in parents "
        f"and name contains '{date_str}' "
        f"and trashed = false"
    )
    response = service.files().list(
        q=query, fields="files(id, name)", spaces="drive"
    ).execute()
    files = response.get("files", [])

    if not files:
        print(f"[Drive] No files found in folder '{folder_id}' matching date '{date_str}'.")
        return []

    local_paths = []
    for f in files:
        file_id = f["id"]
        filename = f["name"]
        local_path = os.path.join(dest_dir, filename)

        print(f"[Drive] Downloading '{filename}' (id={file_id})...")
        request = service.files().get_media(fileId=file_id)
        with open(local_path, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()

        print(f"[Drive] Saved to '{local_path}'")
        local_paths.append(local_path)

    return local_paths