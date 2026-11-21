import os
from urllib.parse import parse_qs, urlparse

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


def sign_in_by_pasting(flow):
    """Sign in from a codespace, where Google's redirect to localhost can't reach this script.

    After signing in, the browser fails to load a localhost address. That address holds a sign-in
    code, so the user pastes it here.
    """
    flow.redirect_uri = "http://localhost"
    url, _ = flow.authorization_url(prompt="consent")
    print(f"Open this link and sign in:\n\n{url}\n")
    print("Your browser will then show an error page. That's expected.")
    pasted = input("Copy that page's full address (starting with http://localhost) and paste it here: ")
    codes = parse_qs(urlparse(pasted.strip()).query).get("code")
    if not codes:
        raise SystemExit("That address has no sign-in code in it. Run python drive.py again.")
    flow.fetch_token(code=codes[0])
    return flow.credentials


def get_creds():
    """Sign in to Google, reusing the saved login in token.json if there is one."""
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = sign_in_by_pasting(flow) if os.environ.get("CODESPACES") else flow.run_local_server(port=0)
        with open("token.json", "w") as f:
            f.write(creds.to_json())
    return creds


def get_drive():
    return build("drive", "v3", credentials=get_creds())


def get_sheets():
    return build("sheets", "v4", credentials=get_creds())


FOLDER = "application/vnd.google-apps.folder"


def list_all(drive, query, fields):
    """Run a Drive file search and return every result, across as many pages as it takes."""
    files = []
    page_token = None
    while True:
        result = drive.files().list(
            q=query,
            fields=f"nextPageToken, files({fields})",
            pageSize=1000,
            pageToken=page_token,
        ).execute()
        files.extend(result.get("files", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            return files


def list_my_files(drive):
    """List files you own (not ones shared with you), skipping folders and trashed files.

    Each file gets a "folder" entry: its folder path, like "Tax/Tax 2025" ("" for My Drive).
    """
    folders = {f["id"]: f for f in list_all(drive, f"mimeType = '{FOLDER}' and trashed = false", "id, name, parents")}

    def folder_path(folder_id):
        names = []
        while folder_id in folders:
            names.append(folders[folder_id]["name"])
            folder_id = (folders[folder_id].get("parents") or [None])[0]
        return "/".join(reversed(names))

    files = list_all(
        drive,
        f"'me' in owners and trashed = false and mimeType != '{FOLDER}'",
        "id, name, mimeType, modifiedTime, parents",
    )
    for f in files:
        f["folder"] = folder_path((f.get("parents") or [None])[0])
    return files


if __name__ == "__main__":
    for f in list_my_files(get_drive()):
        print(f"{f['mimeType']:50} {f['name']}")
