# AutoClip — turn long videos into short clips, on your own PC

AutoClip is a **desktop clipping app** for your computer. Drop in a long video (a podcast, stream, webinar, interview, lecture) and it finds the best moments and exports finished **vertical 9:16 clips with burned-in captions**, ready for Shorts, Reels and TikTok.

Everything runs locally: no uploads, no accounts, no API keys, no per-minute fees.

<p align="center">
  <b>Drop a video → AutoClip scores the best moments → export ready-to-post clips</b>
</p>

## ✨ What it does

| | |
|---|---|
| 🎯 **Finds the good parts** | Scores every moment for opening hook, self-contained story, pacing, energy and emphasis — then explains *why* each clip was picked. |
| 🗣️ **Transcribes your video** | Optional local Whisper (faster-whisper), or reuse a `.srt` you already have. No speech model? It still finds clips from audio energy and scene changes. |
| 📱 **Auto vertical reframe** | Face tracking keeps the speaker in frame for 9:16, with a blurred-background fallback when the shot can't fill the frame. |
| 💬 **Burned-in captions** | Word-by-word karaoke highlighting, or clean line captions. Fonts are auto-detected so captions never render blank. |
| 🔊 **Ready for social** | Loudness normalised to −14 LUFS, H.264 + AAC, faststart, 1080×1920 at 30 fps. |
| ⚡ **Fast** | Uses your GPU's hardware encoder when available, and trims instantly in *Original* mode. |
| 🔒 **Private** | Your videos never leave the machine. The server only listens on localhost. |

## 🚀 Quick start (Windows / macOS / Linux)

You need **Python 3.9+**. Nothing else — FFmpeg is installed automatically as part of the Python packages.

### 1. Get the app

```bash
git clone https://github.com/billionairefarmer1/autoclip_mvp.git
cd autoclip_mvp
```

### 2. Double-click the launcher for your system

| System | File |
|---|---|
| **Windows** | `AutoClip-Windows.bat` |
| **macOS** | `AutoClip-Mac.command` (right-click → Open the first time) |
| **Linux** | `AutoClip-Linux.sh` (or `./AutoClip-Linux.sh` in a terminal) |

The launcher creates a private Python environment, installs dependencies, and opens `http://localhost:8000` in your browser. Keep the window open while you work — closing it stops the app.

### 3. Use it

1. **Drop a video** onto the drop zone (or click *Choose a video*).
2. AutoClip analyses it and lists clip candidates, ranked by score.
3. Tick the ones you like → **Export** → download your vertical clips.

> **Tip:** the first time you run it, the launcher offers to install automatic transcription. Say **yes** — it makes clip picking dramatically better and enables word-by-line captions. It is a one-time ~120 MB download.

### Prefer the terminal?

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-studio.txt
python start.py                   # or: python start.py --help
```

Useful flags: `--port 8010`, `--no-browser`, `--dev` (Vite hot reload), `--rebuild-frontend`.

### Try it without footage

```bash
python scripts/make_demo_video.py --out demo/demo.mp4 --seconds 90
```

That writes `demo/demo.mp4` plus `demo/demo.srt` — drop the video in and you can walk the whole flow, captions included.

## 📋 Requirements

| | |
|---|---|
| **Python** | 3.9 or newer |
| **OS** | Windows 10/11, macOS 11+, or a modern Linux desktop |
| **Disk** | ~250 MB for the app; videos and clips take as much as you make |
| **Node.js** | Only needed to rebuild the UI from source (the launcher does this once) |
| **GPU** | Optional — NVIDIA/Apple/Intel/AMD hardware encoding is detected and used automatically |

## 🧠 How clip selection works

AutoClip runs a five-stage pipeline locally, with no LLM calls:

1. **Transcribe** — local Whisper (`faster-whisper`) with word timings, or your own `.srt`.
2. **Listen** — decode the audio track and compute energy, speech/silence, spectral change and emphasis at 50 ms resolution.
3. **Watch** — detect scene changes so cuts land on visual boundaries.
4. **Build candidates** — pack whole sentences into clip-sized windows, snapping boundaries to pauses and cuts. Clips never begin or end mid-word.
5. **Score & de-overlap** — blend six explainable signals:

| Signal | What it measures |
|---|---|
| `hook` | Does the first ~8 seconds earn attention? (questions, numbers, strong openers) |
| `completeness` | Does the clip start and finish a thought, with a payoff? |
| `pacing` | Words per second and dead air |
| `energy` | Loudness and dynamics (passion vs. noise) |
| `emphasis` | Peaks and spectrum swings on key lines |
| `length` | Fit to the ideal short-form length you set |

Every number is visible in the UI, and each clip carries plain-English reasons. Tune the thresholds in **Studio → Settings**.

## 🎬 What the export does

```
source.mp4 ──trim──▶ reframe ──▶ captions ──▶ loudness ──▶ clip.mp4
```

* **Trim** — frame-accurate seek. *Original* mode first tries a stream copy (instant) and re-encodes only if the cut would land visibly wrong.
* **Reframe** — Haar-cascade face detection samples the clip, then an EMA-smoothed piecewise-linear crop path follows the speaker. One FFmpeg pass, no intermediate renders. Falls back to centred crop, or blurred background fill.
* **Captions** — ASS subtitles with karaoke timing, fonts resolved to an installed family (libass silently drops unknown fonts, so AutoClip checks).
* **Loudness** — one-pass `loudnorm` to −14 LUFS / −1.5 dBTP.
* **Encode** — hardware encoder if one actually works (it is *tested*, not just listed), otherwise x264 CRF 20.

## 📁 Where your files live

```
autoclip_mvp/
├── uploads/studio/<job-id>/
│   ├── source.mp4          # your original video
│   ├── work/               # transcript.json, transcript.srt, scenes.json, analysis.json
│   └── clips/              # exported MP4s + thumbnails + clips.json
└── data/studio/
    ├── jobs.json           # project list (resumable after restart)
    └── settings.json       # studio preferences
```

All of this is git-ignored and never leaves your machine. Deleting a project in the UI removes its folder.

## 🗂️ Project structure

```
autoclip_mvp/
├── AutoClip-Windows.bat / .command / .sh   # one-click launchers
├── start.py                     # desktop launcher (venv, ffmpeg, build, server)
├── backend_server.py            # FastAPI app: Studio API + legacy AI pipeline
├── scripts/make_demo_video.py   # synthetic test footage generator
│
├── src/
│   ├── media/       # FFmpeg discovery (bundled/system), probing, progress parsing
│   ├── transcribe/  # Whisper backends, SRT/VTT parsing, transcript model
│   ├── analysis/    # audio signals, scene detection, candidate scoring
│   ├── reframe/     # face tracking, crop-path planning, crop expressions
│   ├── captions/    # ASS generation, fonts, karaoke timing
│   ├── export/      # encoder choice, filter graph, rendering, thumbnails
│   ├── pipeline/    # offline.py (analyze + render) and the legacy AI steps
│   └── studio/      # job store, background worker, REST API, settings
│
├── frontend/src/
│   ├── pages/       # StudioPage, StudioJobPage, StudioSettingsPage (+ legacy pages)
│   └── components/studio/  # UploadPanel, JobCard, StatusPill
└── tests/           # 114 tests: parsing, scoring, reframing, rendering, API
```

## 🔌 API

The desktop UI is a thin client over a documented REST API — useful if you want to script AutoClip.

```
GET    /api/studio/health                     capability report (ffmpeg, ASR, fonts, encoders)
GET    /api/studio/settings                   read settings
PUT    /api/studio/settings                   update settings
POST   /api/studio/jobs                       upload a video (multipart) → job
POST   /api/studio/jobs/{id}/analyze          find clips (background)
POST   /api/studio/jobs/{id}/render           export clips (background)
GET    /api/studio/jobs/{id}                  status, progress, clips
GET    /api/studio/jobs/{id}/clips/{cid}/video        stream an exported clip
GET    /api/studio/jobs/{id}/clips/{cid}/download     download it
GET    /api/studio/jobs/{id}/clips/{cid}/srt          caption file for the clip
GET    /api/studio/jobs/{id}/transcript?format=srt    full transcript
GET    /api/studio/jobs/{id}/download-all            zip of every export
```

Interactive docs: <http://localhost:8000/docs>

### CLI usage

```bash
# Check the environment without starting the server
python -c "from src.media.ffmpeg import ffmpeg_version; print(ffmpeg_version())"

# Generate test footage
python scripts/make_demo_video.py --seconds 60

# Batch: analyse a folder and export every candidate clip
python - <<'PY'
from src.pipeline.offline import analyze, render_clips, AnalyzeOptions, RenderOptions
res = analyze("interview.mp4", "work/interview", AnalyzeOptions(srt_path="interview.srt"))
render_clips("interview.mp4", res.clips, "exports/interview", "work/interview",
             options=RenderOptions(mode="vertical", captions=True))
PY
```

## 🛠️ Development

```bash
# Backend (auto-reload)
python start.py --reload

# Frontend with hot reload (proxies /api to :8000)
cd frontend && npm install && npm run dev

# Dev tooling (pytest, flake8) is kept out of the runtime install
pip install -r requirements-dev.txt

# Tests (116 tests, includes real FFmpeg renders)
pytest tests/ -v

# The same lint gate CI runs
flake8 src/ tests/ start.py backend_server.py scripts/ --select=E9,F63,F7,F82

# Type-check + build the UI
cd frontend && npx tsc --noEmit && npm run build
```

**Adding a transcription backend:** implement a function in `src/transcribe/whisper_backends.py` returning a `Transcript`, then register it in `engine.py`. Audio-only mode must always remain functional.

**Adding a score signal:** add a component in `src/analysis/candidates.py` (`_score_window`), include it in the weights, and add a row to `SCORE_LABELS` in the UI so users can see it.

## ⚠️ Notes and limits

* Clips are only as good as the audio — noisy recordings confuse both speech detection and scoring. Use a lav mic when you can.
* Face tracking uses classic Haar cascades: excellent for frontal talking-head footage, weaker for profiles and crowded shots (it then keeps a centred crop, which is fine).
* Captions need a transcript. Without one you still get cuts, just no text.
* Very long videos (2 h+) work but take time: transcription dominates, roughly 5–15× realtime on a modern CPU with `base`.
* AutoClip is a desktop tool served over localhost. Do not expose port 8000 to the internet — there is no authentication.

## 🧩 Legacy: AI project pipeline (optional)

The original AutoClip feature set is still available under **AI Projects** in the header. It uses a cloud LLM (DashScope / Qwen or SiliconFlow) to outline a video, score moments, write titles and group clips into collections, plus Bilibili downloading.

It is **entirely optional** and separate from Studio: nothing in the desktop clipping flow calls it, and no API key is needed unless you open that tab.

```bash
# one-off setup for the legacy pipeline only
cp data/settings.example.json data/settings.json   # add your API key
pytest tests/ -v                                    # covers both pipelines
```

Use it if you already have an LLM provider and want AI-written titles/collection groupings. For everything else, Studio's offline engine is faster, free and private.

## 🐳 Docker (optional)

```bash
cp env.example .env      # only needed for the legacy AI pipeline
docker compose up --build
```

The image includes FFmpeg and a caption font. Exports land in `uploads/studio/` via a volume mount.

---

## 🐛 Troubleshooting

**The launcher says FFmpeg was not found.**
Run `pip install imageio-ffmpeg` in the same environment. Or install FFmpeg system-wide and make sure `ffmpeg -version` works in a terminal. A custom path can be set with the `AUTOCLIP_FFMPEG` environment variable.

**Captions are missing from the export.**
Captions need a transcript — check the project page: if it says *audio only*, install `faster-whisper` or attach a `.srt` and re-analyse. If the transcript exists but text looks blank, your chosen caption font is not installed; clear *Caption font family* in Settings to fall back to auto-detection.

**Clips are boring / picked from the wrong places.**
Install a speech model (transcription is by far the biggest quality jump), or raise *Minimum score*, shorten *Target length*, and set *Language* explicitly. Noisy audio hurts every signal — clean audio matters most.

**Exports are slow.**
Leave *Use hardware encoder* on. In Settings, keep CRF around 20–23, and prefer 1080×1920 over 4K output. *Original* mode is nearly instant because it stream-copies.

**"This job is already processing."**
Only one job runs at a time by design (FFmpeg saturates the CPU). Wait for the current one, or restart the app — interrupted jobs are marked *Interrupted* and can be re-analysed.

**Port 8000 is busy.**
`python start.py --port 8010`, or let the launcher pick the next free port automatically (`--port` is a preference, not a requirement).

**Nothing appears in my browser.**
The launcher prints the exact URL. If the UI was never built, run `cd frontend && npm install && npm run build`, or start with `python start.py --rebuild-frontend`.

**Windows SmartScreen warns about the .bat file.**
It is a plain text batch file you can open in Notepad. Click *More info → Run anyway*, or run `python start.py` directly.

## 📄 License

This project is licensed under the [MIT License](LICENSE).

## 🤝 Contributing

Welcome to contribute code! Please see [Contributing Guide](CONTRIBUTING.md) for details.

---

⭐ If this project helps you, please give it a star! 
