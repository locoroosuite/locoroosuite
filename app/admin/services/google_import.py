import base64

import requests


GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


def build_google_auth_url(client_id, redirect_uri, state, scopes):
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }
    return GOOGLE_AUTH_URL + "?" + requests.compat.urlencode(params)


def exchange_google_code(client_id, client_secret, code, redirect_uri):
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    response = requests.post(GOOGLE_TOKEN_URL, data=data, timeout=20)
    response.raise_for_status()
    return response.json()


def refresh_google_access_token(client_id, client_secret, refresh_token):
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    response = requests.post(GOOGLE_TOKEN_URL, data=data, timeout=20)
    response.raise_for_status()
    return response.json()


def gmail_profile(access_token):
    response = requests.get(
        f"{GMAIL_API_BASE}/profile",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def gmail_list_messages(access_token, page_token=None, max_results=100):
    params = {
        "maxResults": max(1, min(int(max_results), 500)),
        "q": "-in:spam -in:trash",
        "includeSpamTrash": "false",
        "fields": "messages/id,nextPageToken,resultSizeEstimate",
    }
    if page_token:
        params["pageToken"] = page_token
    response = requests.get(
        f"{GMAIL_API_BASE}/messages",
        headers={"Authorization": f"Bearer {access_token}"},
        params=params,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def gmail_get_raw_message(access_token, message_id):
    response = requests.get(
        f"{GMAIL_API_BASE}/messages/{message_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"format": "raw", "fields": "id,labelIds,internalDate,raw"},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    raw_value = payload.get("raw", "")
    padding = "=" * ((4 - len(raw_value) % 4) % 4)
    raw_bytes = base64.urlsafe_b64decode((raw_value + padding).encode())
    payload["raw_bytes"] = raw_bytes
    return payload
