# Google login for the PuspaBot desktop app — setup (2 minutes)

Why this page exists: the first Windows test showed
`{"error":"login Google belum dikonfigurasi (PUSPA_GOOGLE_CLIENT_ID kosong)"}`.
The cause was not code: **no Google OAuth client had ever been
created for Puspa**. The app needs one, and only you can create it,
because it lives on your Google account. This takes about two
minutes, and you do it once.

## What you need

- Your Google account (the one you want to sign in to PuspaBot with).
- Nothing to install, nothing to pay.

## Step 1 — Create a project

1. Open [Google Cloud Console](https://console.cloud.google.com/) and sign in.
2. Top-left, next to "Google Cloud", click the project picker, then **New Project**.
3. Project name: `PuspaBot`. Click **Create**. Wait for it to finish, then make sure it is the selected project.

## Step 2 — Set up the consent screen (the page Google shows when you log in)

1. Left menu: **APIs & Services** > **OAuth consent screen**.
2. User type: **External** (it is the only option for a personal account). Click **Create**.
3. App name: `PuspaBot`. User support email: your email. Developer contact information: your email. Click **Save and Continue** through the Scopes and Test users pages for now (scopes are already right: the app only asks for your basic profile).
4. **Test users (important):** on the OAuth consent screen, open **Test users** and click **Add users**. Add your own Google account email, then **Save**. While the app is in testing mode, Google only lets listed test users sign in — without this step your own login is refused.

## Step 3 — Create the client (the ID + secret the app needs)

1. Left menu: **APIs & Services** > **Credentials**.
2. Click **Create Credentials** > **OAuth client ID**.
3. Application type: **Desktop app**. Name: `PuspaBot Desktop`. Click **Create**.
4. Google shows a **Client ID** and a **Client secret**. Keep this window open.

## Step 4 — Give them to the app (the secure handoff)

1. In PuspaBot, open **Pengaturan Lainnya** (on the login screen, under the Google button).
2. Paste the **Client ID** into the Client ID field and the **Client secret** into the Client Secret field. Click **Simpan**.
3. The app stores them in a file on your own computer (`%APPDATA%\Puspa\google-oauth.json` on Windows) and the backend reads them from there. No restart is needed, and the secret never leaves your computer.

## Step 5 — Log in

Click **Masuk dengan Google**. Your system browser (Chrome/Edge, the normal one) opens Google's sign-in page. Sign in, approve, and the browser hands you back to PuspaBot, logged in.

## If Google warns "Google hasn't verified this app"

That warning appears because the app is in testing mode and only you use it. It is expected: click **Advanced** > **Go to PuspaBot (unsafe)**. It goes away entirely if the app is ever published and verified, which is a later decision, not a blocker.

## Notes for developers

- The backend reads the client ID/secret from the environment first (`PUSPA_GOOGLE_CLIENT_ID` / `PUSPA_GOOGLE_CLIENT_SECRET`), then from the config file above. Environment wins; the file is the default for desktop users.
- Desktop-app clients use a loopback redirect (`http://127.0.0.1:<port>`): no redirect URI has to be typed anywhere. The backend already handles the loopback callback, the code exchange, and the session save (`puspa/auth/google_oauth.py`, `puspa/pwa/oauth_pages.py`).
- When credentials are missing, the app shows a human message in-app (and the backend returns a human HTML page). Raw JSON errors are never shown to the user.
- Never paste the client secret into chat, an issue, or a screenshot. If it ever leaks, delete the client in Credentials and create a new one.
