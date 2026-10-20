# Movia

A MovieBox-inspired personal movie library built with **Python, SQLite, HTML, CSS, and JavaScript**. No packages to install, API keys, subscriptions, paid databases, or cloud accounts are required.

## Start

Requires Python 3.10 or newer (already available on this computer).

```bash
cd movia
python3 server.py
```

Open **http://localhost:8000** in your browser. Keep the terminal open; press Ctrl+C to stop the app. If port 8000 is busy:

```bash
python3 server.py --port 8080
```

## What works

- Responsive film browsing with search, genre filters, and sorting.
- Four credited Blender open films with externally hosted video streams.
- Local accounts with hashed passwords and persistent, revocable sessions.
- Separate watchlists, playback history, and personal libraries per account.
- Video playback with native controls, seeking, fullscreen, and resume.
- Personal films added by direct HTTPS video URL or file upload (MP4, WebM, OGV; 512 MB maximum).
- Authenticated streaming of uploads with HTTP byte ranges, so seeking works.
- Removal of personal films, including uploaded files and related saved data.
- Film information, creator attribution, and source links.

Choose **Sign in → Create a free account** to save your library. No email is needed. Passwords must be at least eight characters. The initial version does not include password recovery.

## Cost and scope

The software and database run on your existing computer with no service charges. Existing electricity, internet/data, and disk costs still apply. External starter films require internet access and their hosts may become unavailable. Uploaded films and the app interface work without internet while the local server is running.

This is a working local first version, not the commercial MovieBox service or its content catalog. It does not include a licensed commercial film catalog, TV episode management, transcoding, cloud synchronization, or a native mobile app. You can add videos you own or have permission to use. A direct video URL must point to a browser-playable file, not a YouTube/watch-page URL. MP4 with H.264/AAC and WebM are the most practical formats; support depends on your browser.

The server deliberately listens only on `127.0.0.1`. Open it on this computer. Phone-size layouts are supported, but other devices cannot connect to this local-only server. Public hosting is a separate step and is not configured. Do not expose this development server directly to the internet; a public service needs production hosting, HTTPS, proper upload quotas, and operational safeguards.

## Storage and backups

The first launch creates:

```text
data/movia.sqlite3  — accounts, sessions, catalog, watchlists, viewing progress
data/media/         — uploaded videos
```

Stop Movia, then copy the entire `data/` folder to back up your library. SQLite's temporary `-wal` and `-shm` files may exist while the app is running; backing up after stopping avoids an inconsistent copy. Your data is excluded by `.gitignore`.

To choose another storage location:

```bash
MOVIA_DATA_DIR=/path/to/your/storage python3 server.py
```

Anyone with filesystem access to this computer can access these files. Browser accounts separate in-app access; they do not encrypt the database or media on disk.

## Project structure

```text
server.py             Python HTTP server, authentication, SQLite, media streaming
catalog.json          Starter film catalog (refreshed on startup)
static/index.html     Application interface
static/styles.css     Responsive theme
static/app.js         Search, accounts, watchlists, player, uploads
static/assets/        Local film artwork and app icons
tests/test_server.py  Isolated HTTP/database integration tests
```

All database queries that include user input use parameters. Sessions use random tokens stored as hashes, HttpOnly/SameSite cookies, and a 30-day expiry. Passwords use PBKDF2-HMAC-SHA256 with individual salts and 600,000 iterations. Mutating requests check origins; uploads and static-file paths are constrained; private videos require an authenticated owner. These protections support local use, not an audited internet-facing service.

## Test

```bash
python3 -m unittest discover -s tests -v
```

Tests use temporary databases and an ephemeral localhost port. They cover accounts, session persistence, account isolation, watchlists, playback progress, input validation, origin checks, uploads, byte ranges, and deletion.

## Film and artwork credits

Movia is independent and is not affiliated with MovieBox or Blender Foundation. Film files are streamed from external mirrors; poster images and stills are stored locally and displayed using CSS cropping and overlays. The Sintel hero still is mirrored and enlarged in the interface. Original works retain their respective licenses.

| Film | Attribution | License and original project |
| --- | --- | --- |
| Sintel (2010) | © Blender Foundation | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/), [Project Durian](https://durian.blender.org/) |
| Big Buck Bunny (2008) | © Blender Foundation / www.bigbuckbunny.org | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/), [Project Peach](https://peach.blender.org/about/) |
| Tears of Steel (2012) | © Blender Foundation | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/), [Project Mango](https://mango.blender.org/about/) |
| Elephants Dream (2006) | © Blender Foundation / Netherlands Media Art Institute | [CC BY 2.5](https://creativecommons.org/licenses/by/2.5/), [Project Orange](https://orange.blender.org/) |

Asset sources: [Sintel poster and movie](https://archive.org/details/Sintel), [Sintel still](https://media.w3.org/2010/05/sintel/poster.png), [Big Buck Bunny still](https://media.w3.org/2010/05/bunny/poster.png), [Tears of Steel still and movie](https://archive.org/details/Tears-of-Steel), [Elephants Dream still and movie](https://archive.org/details/ElephantsDream). The application’s logo and placeholder illustration are original SVGs.
