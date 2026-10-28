# GroomAI — Smart Barber Queue + AI Grooming Studio

GroomAI now includes a barber-shop booking and live queue MVP alongside the original YouCam hairstyle/beard try-on studio.

## What's included

### Customer portal
- Customer account creation with username, password, phone number, and profile emoji.
- Optional Google sign-in when a Google OAuth Web Client ID is configured.
- Browse shop services/packages with prices and estimated durations.
- Select multiple services and join the live queue.
- View queue number, estimated service start, suggested arrival time, and booking status.
- The app polls booking estimates so the customer can see updates after the barber starts or completes a service.

### Barber workspace
- Dedicated barber login (separate from customer accounts).
- View current queue and recent activity.
- Add walk-in customers to the same queue as online bookings.
- Start service, complete service, skip a late customer, mark no-show, and requeue skipped/no-show customers.
- Queue estimates recalculate when the service status changes.

### Admin workspace
- Dedicated admin login separate from customer and barber logins.
- View daily booking counts, waiting count, in-service count, completed revenue estimate, and customer accounts.
- Add services/packages and edit service names, prices, durations, and active status.
- Manage queue status from the admin dashboard.

### Existing AI Grooming Studio
- The original YouCam AI hairstyle and beard try-on experience is still available at `/studio`.
- The YouCam API key remains backend-only in `.env`.

## Local demo credentials

These defaults are created on first launch if the accounts do not already exist:

**Barber portal**
- Username: `barber@groomai.local`
- Password: `Barber@12345`
- Login page: `http://127.0.0.1:8000/barber/login`

**Admin portal**
- Username: `admin@groomai.local`
- Password: `GroomAI@12345`
- Login page: `http://127.0.0.1:8000/admin/login`

Change `BARBER_USERNAME`, `BARBER_PASSWORD`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`, and `GROOMAI_SESSION_SECRET` in `.env` before deployment. The default credentials are for local testing only. Keep `.env` private and do not commit it to GitHub. If you change the staff usernames, do so before the first launch or remove/disable the old staff account in the local database so it cannot continue to sign in.

## Run on Windows 10/11

1. Extract this folder to a convenient location, e.g. `D:\GroomAI`.
2. Double-click `run.bat`.
3. Open `http://127.0.0.1:8000`.

Or run manually in PowerShell:

```powershell
cd D:\GroomAI
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
python -m uvicorn main:app --reload --port 8000
```

If `.env` already exists, do not overwrite it; add the new variables from `.env.example` manually so you keep your private YouCam API key.

## Optional Google sign-in setup

1. Open Google Cloud Console and create an OAuth Client ID with application type **Web application**.
2. Add your local origin as an authorised JavaScript origin, for example `http://127.0.0.1:8000` and/or `http://localhost:8000`.
3. Put the client ID in `.env` as `GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com`.
4. Restart GroomAI. The Google sign-in button appears on the customer login page when configured.

Google sign-in verifies the ID token on the backend against the configured client ID. Google-created customers must provide a phone number when making their first booking. The MVP stores the phone number but does **not** send an SMS OTP; add a trusted SMS provider before relying on phone ownership verification.

## Main pages

- `/` — portal landing page
- `/customer/login` — customer sign-in
- `/customer/signup` — customer registration
- `/customer` — customer booking dashboard
- `/barber/login` — barber sign-in
- `/barber` — barber queue workspace
- `/admin/login` — admin sign-in
- `/admin` — admin dashboard
- `/studio` — original AI hairstyle/beard try-on studio

## Queue behaviour

- Online bookings and walk-ins share one ordered queue.
- Each service has an estimated duration and price. Selecting multiple services adds their duration and price.
- The queue begins with the current time, or the expected finish of the service already in progress, then adds the durations of waiting customers in queue order.
- Suggested arrival is normally estimated as 10 minutes before the expected service start, but never earlier than the current time.
- When a barber starts/completes a service, queue estimates are recalculated. They are estimates, not guaranteed appointment times.
- Staff can mark waiting customers skipped or no-show. They can later requeue them at the end of the line.
- This MVP models one shared service queue. Multi-barber scheduling, shop opening hours, multiple branches, SMS notifications, payments, and travel-time calculations are future enhancements.

## Data and privacy

Customer accounts, staff accounts, services, and bookings are stored in `groomai.db` using SQLite. Passwords are stored as salted PBKDF2 hashes, not plaintext. Login sessions are signed and stored in an HTTP-only cookie. Use HTTPS and set `COOKIE_SECURE=1` in production. Set a long random `GROOMAI_SESSION_SECRET` before deploying.

The MVP does not implement email verification, SMS OTP, password reset, rate limiting, or a full production security review. Do not expose the default demo credentials or deploy this setup publicly without configuring credentials, HTTPS, secrets, backups, and abuse protections.

## YouCam setup (for the optional AI Studio)

In `.env`:

```env
YOUCAM_API_KEY=your_actual_key_here
YOUCAM_API_BASE=https://yce-api-01.makeupar.com
```

Restart the server after editing `.env`. Do not put the key in frontend JavaScript or commit `.env` to GitHub.

## Troubleshooting

- `401 Please sign in`: log in through the portal that matches your role.
- `403 This account does not have permission`: customer, barber, and admin roles are intentionally separate.
- Google button not visible: configure `GOOGLE_CLIENT_ID` and restart.
- Staff login fails after changing `.env`: confirm the username/password variables and whether a previous staff account still exists in `groomai.db`.
- YouCam API errors: check `YOUCAM_API_KEY`, API access, and the YouCam console. The booking features do not require YouCam to be configured.

Customer booking includes a style-reference selector backed by the YouCam template endpoints when `YOUCAM_API_KEY` is configured. The selected style is attached to the booking and shown in the barber queue. Without the key, illustrated fallback ideas are shown instead.

## Customer booking: official YouCam styles and AI grooming preview

The customer booking page (`/customer`) can load official hairstyle and beard templates from the configured YouCam API. It displays the template image URL returned by YouCam; it does not label the bundled illustrated placeholders as YouCam images. A valid `YOUCAM_API_KEY` and access to the template endpoints are required for this gallery.

Customers can select a YouCam hair/beard template, upload a JPG/PNG/WEBP photo (up to 9 MB), and choose **Generate AI preview** before creating the booking. The generated preview URL and requested style are attached to the booking notes. In the barber workspace (`/barber`), the queue card shows the customer's generated preview and the chosen official template reference when available, alongside their haircut instructions. AI output is a visual guide; the barber should confirm the requested cut with the customer before starting.

If the gallery is empty, check `YOUCAM_API_KEY`, `YOUCAM_API_BASE`, and whether the account has access to the hairstyle/beard template endpoints. The application intentionally does not replace missing official template images with locally drawn thumbnails while calling them YouCam images.


## Customer style gallery and walk-in AI preview

- Customer booking and the barber walk-in form include style selection and an inline photo-based YouCam AI preview flow.
- When the configured YouCam account returns official template images, those images are shown directly from the API response.
- If YouCam is unavailable or returns no template images, the UI shows clearly labelled real-person photo inspiration. These inspiration cards can be attached as reference notes but are not represented as official YouCam templates, and they cannot run AI try-on.
- The landing page includes real-person grooming photography from Unsplash. A working internet connection is required to load those external photographs.
- To enable official template images and AI try-on, configure `YOUCAM_API_KEY` in `.env`, restart the server, and ensure the account has access to the hair-transfer and beard-style template endpoints.
