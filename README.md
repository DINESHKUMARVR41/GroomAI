# GroomAI — Virtual Hair & Beard Studio

A local-first MVP for trying YouCam AI hairstyles and beard styles, saving looks, generating a barber reference card, and collecting after-cut feedback.

## Features
- Upload a photo or capture one from your device camera.
- Browse hairstyle and beard templates from YouCam API.
- Submit a try-on task and poll until the generated image is ready.
- Compare the original image with the generated result.
- Save favourites locally in SQLite.
- Generate a simple barber reference card.
- Save post-haircut satisfaction and preview-accuracy feedback.
- Keep the YouCam API key on the backend in `.env`.

## Requirements
- Windows 10/11
- Python 3.10+ (Python 3.11 recommended)
- YouCam API key and available API units
- Internet access for YouCam requests

## Run on Windows
1. Extract this folder somewhere easy, e.g. `D:\GroomAI`.
2. Double-click `run.bat`, or use the commands below.
3. Open `http://127.0.0.1:8000` in Chrome or Edge.
4. Edit `.env` and paste your API key after the first run, then restart the server.

### Manual commands (PowerShell)
```powershell
cd D:\GroomAI
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
python -m uvicorn main:app --reload
```

If PowerShell blocks activation, run without activating the venv:
```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn main:app --reload
```

## Add your YouCam API key
In `.env`:
```env
YOUCAM_API_KEY=your_actual_key_here
YOUCAM_API_BASE=https://yce-api-01.makeupar.com
```
Restart the server after editing `.env`. Do not paste your API key into frontend code or commit `.env` to GitHub.

## YouCam APIs used
- Hairstyle template list: `GET /s2s/v2.1/task/template/hair-transfer`
- Hairstyle task: `POST /s2s/v2.1/task/hair-transfer`
- Hairstyle result: `GET /s2s/v2.1/task/hair-transfer/{task_id}`
- Beard template list: `GET /s2s/v2.0/task/template/beard-style`
- Beard task: `POST /s2s/v2.0/task/beard-style`
- Beard result: `GET /s2s/v2.0/task/beard-style/{task_id}`
- Image file upload: `POST /s2s/v2.0/file`, then PUT the image bytes to the returned signed upload URL.

API workflow follows the official guides:
- https://docs.perfectcorp.com/reference/ai_hairstyle/section/overview/integration-guide
- https://docs.perfectcorp.com/reference/ai_beard_style
- https://docs.perfectcorp.com/develop/quick_start_guide

The app intentionally uses actual YouCam task results; it does not fabricate a generated result when the API key is missing or an API task fails.

## Image tips
- Use a clear, well-lit, front-facing photo with only one person.
- Avoid sunglasses, heavy shadows, face coverings and strong head turns.
- Keep the file under 9 MB; the backend accepts JPG/JPEG/PNG, although individual YouCam APIs may have narrower format requirements. If an API rejects PNG, use a JPG.
- Hair API requirements and supported formats can differ from beard API requirements. Follow any error message from YouCam.

## Data and privacy
Saved looks and feedback are stored in `groomai.db` on your machine. Uploaded images are sent to YouCam when you start a try-on task. The MVP does not include account login or cloud sync. Delete `groomai.db` to clear local saved looks and feedback.

## Troubleshooting
- `YouCam API key is not configured`: edit `.env` and restart the app.
- `401 / InvalidAccessToken`: check that the key is correct and active.
- No templates returned: verify API access/units and inspect the API error; the app needs an accessible template endpoint.
- Upload/task failure: confirm image format, size and face visibility.
- Camera unavailable: camera access requires localhost or HTTPS and browser permission.
- `ModuleNotFoundError`: activate the virtual environment and run `pip install -r requirements.txt`.

### Personalisation score
The MVP displays a transparent **preference-match estimate** based on the user's selected goal, preferred length and maintenance preference, matched against the style's name/group. It is a lightweight ranking heuristic—not a facial attractiveness score or a claim that a style objectively suits a person's face.


## Optional Gemini compatibility recommendations

Add a Gemini API key from Google AI Studio (https://aistudio.google.com/app/apikey) to your local `.env` file:

```env
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-3.5-flash-lite
```

Restart GroomAI, add a photo, load the real YouCam style catalogue, then use **Analyze my current hair & recommend compatible styles**. Gemini vision compares the visible current hair/beard with only the styles returned by YouCam. It is a cautious suggestion, not a guarantee of what a barber can achieve. API quotas and free-tier availability depend on Google's current account and model limits.

## Camera note

The live camera includes instant colour/appearance filters and can remain open while browsing styles. The configured YouCam hair-transfer and beard-style endpoints process still images rather than a continuous live video stream, so an AI hairstyle transformation still needs a captured frame. True live hairstyle replacement would require a dedicated real-time AR/face-tracking SDK or video-capable service; these camera colour filters do not pretend to change hair shape.


## Troubleshooting: YouCam suddenly says the key is missing

The ZIP intentionally excludes `.env` because it contains your private API key. If you replaced the whole `GroomAI` folder while updating, the old `.env` may have been removed. Restore your existing `YOUCAM_API_KEY` in the `.env` file next to `main.py`, then restart Uvicorn. `/api/health` now reports the expected `.env` path and whether the key was detected, without exposing the secret. The app can also read `.env` from the current working directory as a fallback.

Do not paste your API key into chat or commit `.env` to GitHub.

## Public style reference images

A small number of verified reference photographs are shown where the style name clearly matches the source image. They are inspiration/reference images only, not YouCam templates and not the user's generated preview. Sources and licences:

- Buzz cut / short fade example — Absock, CC0: https://commons.wikimedia.org/wiki/File:Photo_of_buzz_cut.jpg
- Curly hair example — Thomas & James Holroyd, public domain: https://commons.wikimedia.org/wiki/File:Man_with_curly_hair_by_T_%26_J_Holroyd_(1a).jpg
- Pompadour / swept-up example — Kimchifan, CC BY-SA 3.0: https://commons.wikimedia.org/wiki/File:Pompadour_hairstyle.jpg
- Comb-over example — Edmund F. Arras / DPLA, US public domain notice: https://commons.wikimedia.org/wiki/File:Portrait_of_a_man_with_a_distinctive_hairstyle_-_DPLA_-_21630bf48c60e8cb6a54bb1ae790c3bb.jpg
- Goatee example — Allamerican85, CC BY-SA 4.0: https://commons.wikimedia.org/wiki/File:Moustache_and_Goatee.jpg
- Moustache example — Chad fitz, CC BY-SA 3.0: https://commons.wikimedia.org/wiki/File:Mustache.jpg
- Full beard example — Jlesly, CC BY-SA 4.0: https://commons.wikimedia.org/wiki/File:Bearded_Man.jpg

Styles without a verified matching public reference intentionally show a labelled placeholder rather than a misleading photograph. If YouCam returns its own catalogue thumbnail, that image is shown instead.
