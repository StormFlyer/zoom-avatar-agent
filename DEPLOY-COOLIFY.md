# Putting the avatar agent on a Coolify server

This runs the control panel and the agent on your own server, so you can open the
panel from any browser and the avatar does not depend on a PC being switched on.
Coolify provides the https address and the certificate. The panel adds a login.

## What was and was not tested

- **Tested** (on a Linux machine with Docker, using fake keys): the image builds;
  the panel refuses to start without a password; the login works and every page
  except `/healthz` needs it; uploading a photo; building a knowledge base from a
  text file (this downloads a 65 MB search model on first use); the data surviving
  a container restart; the agent starting and stopping from the panel.
- **Not tested**: Coolify itself, Traefik, a real domain, real LiveKit, Fish Audio
  or LemonSlice keys, and an avatar actually joining a meeting. With fake keys the
  agent starts but LiveKit refuses it (the log says `401`), so the dot never turns
  green. Expect to fix small things on the first real run.

## Before you start

- A Coolify server that is already working, with a GitHub source available.
- A DNS **A record** for the address you want, for example `avatar.sentientflow.co.uk`,
  pointing at the server's IP address. Wait until it resolves.
- Your keys: LiveKit (URL, API key, API secret), LemonSlice, Fish Audio.
- A strong password for the panel. Anyone with it can start the agent and spend
  your API credits.

## Set it up in Coolify

1. In your Coolify project choose **+ New** > **Application** > **Public Repository**.
2. Repository: `https://github.com/StormFlyer/zoom-avatar-agent`. Branch: `main`.
3. **Build Pack**: `Dockerfile`. Leave the Dockerfile location as `/Dockerfile`.
4. **Ports Exposes**: `8765`.
5. **Domains**: `https://avatar.sentientflow.co.uk` (your own address, starting with
   `https://`). Coolify gets the certificate by itself.
6. **Persistent Storage** > add a **Volume**. Name it `avatar-data` and set the
   destination path to `/data`. Without this, your photos, voices, knowledge bases
   and meeting notes are lost every time you redeploy.
7. **Environment Variables**. Add each one below. Tick **Runtime only** if Coolify
   offers it (nothing here is needed while building).

   | Name | Value |
   | --- | --- |
   | `BEHIND_PROXY` | `1` |
   | `PANEL_USER` | a login name, for example `ivan` |
   | `PANEL_PASSWORD` | your strong password |
   | `AGENT_MODE` | `start` |
   | `LIVEKIT_URL` | `wss://your-project.livekit.cloud` |
   | `LIVEKIT_API_KEY` | from LiveKit |
   | `LIVEKIT_API_SECRET` | from LiveKit |
   | `AGENT_NAME` | `zoom-bot` (or your own name) |
   | `LEMONSLICE_API_KEY` | from LemonSlice |
   | `FISH_API_KEY` | from Fish Audio |
   | `OWNER_NAME` | who the avatar stands in for |
   | `BOT_NAME` | the name shown in the meeting (optional) |

   Only if you want the meeting notes emailed: `NOTES_EMAIL_TO`, `NOTES_EMAIL_FROM`,
   `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_TLS`. Use port
   `587` with `starttls`. Hetzner blocks port 25 and may block 465 for new accounts.

   The image already sets `BEHIND_PROXY=1` and `AGENT_MODE=start`. Enter them anyway
   so the settings are visible in Coolify. The full list of settings is in
   `.env.example`.
8. Press **Deploy**. The first build takes a few minutes. When the status is
   **Running (healthy)**, it is ready.

## First run

1. Open your address. The browser asks for the login: the `PANEL_USER` and
   `PANEL_PASSWORD` you set.
2. Add a photo of the face you want under the face section.
3. Press **Start agent** (top right). Wait for the dot to turn green. If it never
   does, see Troubleshooting.
4. Choose the face, voice, tone and knowledge base, paste the meeting link and press
   **Send avatar to meeting**.

## Where your data lives

Everything you create is in the `/data` volume: `avatars/`, `knowledge/`,
`meetings/` (transcripts and notes), `brand/`, `logs/`, `presets.json` and the
downloaded search model. Redeploying keeps it. Deleting the volume in Coolify
deletes it. To back up, copy the volume's folder from the server, or download
meeting notes from the panel.

Your API keys are **not** in the volume. They live only in Coolify's environment
variables.

## Updating

Press **Redeploy** in Coolify, or turn on **Auto Deploy** so every change to `main`
rebuilds the app. A redeploy stops the agent. Never redeploy during a meeting.
After it, press **Start agent** again.

## Troubleshooting

- **The dot never turns green.** Open the log at the bottom of the panel, or
  Coolify > Logs. A line with `401` or `failed to connect to livekit` means the
  `LIVEKIT_URL`, key or secret is wrong. Fix it in Coolify, redeploy, start the
  agent again. The agent gives up after about two minutes, and the dot goes grey.
- **The container says `ERROR: /data is not writable`.** The storage is a folder
  owned by another user. Use a Coolify **Volume** (step 6), not a folder on the host.
- **The container keeps restarting with `BEHIND_PROXY=1 ... PANEL_PASSWORD`.** The
  password is missing. Add `PANEL_PASSWORD` and redeploy.
- **Recording a voice from the browser does nothing.** Browsers only allow the
  microphone on `https://`. Open the panel by the `https://` address, not the IP.
- **The first knowledge base is slow.** It downloads a 65 MB search model once. It
  is kept in the volume afterwards.
- **Memory.** Measured with the agent stopped: about 130 MB idle, 250 MB right after
  a knowledge-base build. With the agent running, the container used about 1 GB on an
  8-core machine, because the agent keeps one idle worker process per CPU core. Plan
  for 2 GB or more of free memory.
- **No ports to open** other than 80 and 443, which Coolify already uses. The agent
  only makes outgoing connections.
