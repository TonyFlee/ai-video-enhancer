# AI Video FPS Enhancer

A local web app that raises video frame rate with **real AI frame interpolation** (RIFE) and packages the
result for TikTok, Instagram Reels and YouTube Shorts.

- **Frontend:** Next.js 16 (App Router, TypeScript, Tailwind CSS v4)
- **Backend:** FastAPI (Python 3.12)
- **Processing:** FFmpeg (decode / scale / crop / encode / mux) + RIFE via ONNX Runtime
- **Acceleration:** CUDA execution provider on NVIDIA GPUs, NVENC H.264/H.265 encoding, automatic CPU fallback
- **Desktop app:** the whole stack packages into one Windows folder with a single `AI Video Enhancer.exe`
  (native WebView2 window, no terminal / browser / Python / Node.js on the user's machine) — see
  [section 6](#6-windows-desktop-app-packaged-exe)

Nothing is simulated: every output frame is either a decoded source frame or a frame predicted by the RIFE
network. The pipeline never copies a frame to fake a higher frame rate, and it never rewrites FPS metadata.

---

## 1. Features

| Area | What you get |
| --- | --- |
| Desktop app | Double-click **AI Video Enhancer.exe**: it starts the engine, serves the UI and opens a native window. Python, Node.js and FFmpeg are bundled — the user needs neither a terminal nor a browser |
| Upload | Drag & drop or file picker, client- and server-side validation (format, size, duration) |
| Interpolation | 30/24 → 60 fps, 30 → 120 fps, 60 → 120 fps, arbitrary 30/60/120/240 targets |
| Slow motion | 0.25× / 0.5× / 0.75× smooth slow motion, audio time-stretched to stay in sync |
| TikTok presets | 1080 × 1920 (9:16) for TikTok / Reels / Shorts, fill-and-crop or fit-and-pad, keep original size, or a **custom frame** (64–4096 px, e.g. 720 × 1280 — roughly twice as fast to interpolate) |
| Export | H.264 or H.265 MP4, AAC audio, NVENC GPU encoding when available, CQ/CRF or explicit bitrate |
| Monitoring | Live progress, processed/total frames, interpolation speed, ETA, elapsed time, cancel button |
| Processing detail | **Faster / Fastest** modes: the AI interpolates a smaller frame (75 % / 50 %) and the encoder upscales — same output resolution and frame rate, ~1.8× / ~3–4× quicker, slightly softer motion detail |
| Save location | Pick an output folder once (**Chrome/Edge**) and finished videos are streamed straight into it with a save progress bar; otherwise they go to the normal browser download folder |
| Preview | Synchronised original vs. enhanced players, fullscreen, playback-rate control, seek |
| Metadata | Original *and* enhanced resolution, FPS, duration, size, codec, encoder, frames added |
| Privacy | Files stay local; uploads and outputs are deleted automatically (default 4 h TTL, plus purge on startup) |

---

## 2. Requirements

| Component | Minimum | Notes |
| --- | --- | --- |
| OS | Windows 10/11 (x64) | Commands below are Windows PowerShell |
| Python | 3.11 – 3.12 | 3.12 tested |
| Node.js | 20+ | 24 tested |
| FFmpeg | 6.0+ with `h264_nvenc` for GPU encoding | The `gyan.dev` full build works |
| GPU (optional) | NVIDIA with CUDA 12 support, 4 GB+ VRAM | Tested on GTX 1650 4 GB; RIFE falls back to CPU automatically |
| RAM | 8 GB+ | Frame buffers are streamed, not accumulated |
| Disk | ≥ 6 GB free | CUDA 12 wheels (~2.5 GB) + temporary frame/export storage |

> **Building from source only.** The packaged desktop app ([section 6](#6-windows-desktop-app-packaged-exe))
> bundles the Python runtime, the models and FFmpeg. An end user needs none of the rows above apart from a
> GPU driver — and the WebView2 Runtime, which ships with Windows 10/11.

**GPU notes**

- RIFE runs through **ONNX Runtime 1.22** (`onnxruntime-gpu`). The CUDA execution provider needs the
  **CUDA 12 + cuDNN 9** runtime libraries. On Windows you do *not* need the CUDA toolkit: the
  `nvidia-*-cu12` wheels in `requirements.txt` ship the DLLs and `app/config.py` registers their
  `site-packages/.../bin` folders with `os.add_dll_directory` at import time. Without those wheels the
  app runs the same model on CPU (much slower) instead of failing.
- 4 GB cards can run out of VRAM on 1080 × 1920 frames. The engine estimates the working set before each
  pass and switches to **tiled inference** (overlapping horizontal bands) and, if that still fails, to CPU —
  the job continues instead of crashing. It also caps the CUDA arena at 85 % of the *currently free* VRAM,
  because Windows (WDDM) silently spills GPU allocations into system memory, which turns an out-of-memory
  situation into a slow path instead of the clean error the tiling fallback expects.
- **Other GPU workloads matter a lot.** A game, a 3D app or a video call sharing the card leaves far less
  VRAM *and* time for RIFE. On the 4 GB test card a 640 × 360 pass ran at ~7 fps on an idle GPU and dropped
  to ~0.05 fps while a game held ~3.4 GB and 98 % GPU utilisation. Close GPU-heavy apps for real throughput.
- Encoding uses NVENC when the GPU supports it (`h264_nvenc` / `hevc_nvenc`), verified with a real test
  encode at startup, and falls back to `libx264` / `libx265`.

---

## 3. Installation (Windows, exact commands)

Open **PowerShell** and run the steps in order.

### 3.1 Install the toolchain

```powershell
winget install --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
winget install --id Gyan.FFmpeg --silent --accept-package-agreements --accept-source-agreements
```

Close and reopen the terminal afterwards so `PATH` is refreshed, then verify:

```powershell
python --version
ffmpeg -version
ffprobe -version
```

### 3.2 Backend

```powershell
cd "D:\Project\AI Video Enhancer\backend"

python -m venv .venv
.\.venv\Scripts\Activate.ps1

# If PowerShell blocks the activation script:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

python -m pip install --upgrade pip
pip install -r requirements.txt
```

If you do not have an NVIDIA GPU, replace the GPU wheels with the CPU-only runtime:

```powershell
pip uninstall -y onnxruntime-gpu nvidia-cudnn-cu12 nvidia-cublas-cu12 nvidia-cuda-runtime-cu12 nvidia-cuda-nvrtc-cu12 nvidia-cufft-cu12 nvidia-cusparse-cu12 nvidia-cusolver-cu12 nvidia-nvjitlink-cu12
pip install onnxruntime==1.22.0
```

### 3.3 RIFE model

The interpolation model is ~21 MB and is **not** committed to the repository.

```powershell
python scripts\download_models.py
```

Expected result: `backend\models\RIFE_fp32_timestep.onnx` (~21.6 MB). The script also fetches the optional
midpoint-only export `RIFE_fp32.onnx`, which the engine uses as a fallback if the timestep model is missing.

The backend introspects the ONNX graph at load time and adapts to the common RIFE export shapes:

- **packed + timestep** (the default `RIFE_fp32_timestep.onnx`): one `[1, 6, H, W]` input with frame 0 in
  channels 0-2 and frame 1 in channels 3-5, plus a scalar `timestep`. Arbitrary interpolation phases are
  evaluated directly, which is the most accurate option for fractional factors such as 24 → 60 fps.
- **two-input**: separate `img0` / `img1` inputs, with an optional `timestep` scalar.
- **packed batch**: a `[2, 3, H, W]` stack instead of the channel-packed layout.
- **midpoint only** (`RIFE_fp32.onnx`): no timestep input, so fractional phases are produced by memoised
  binary subdivision between generated midpoints.

To use a different RIFE ONNX export, drop it in `backend\models\` and set the file name:

```powershell
$env:AVE_RIFE_MODEL = "my_rife.onnx"
```

#### Optional: FP16 model

`scripts/fp16_variants.py` converts the FP32 model to FP16 with ONNX Runtime's converter:

```powershell
python scripts\fp16_variants.py
```

On a GTX 1650 the FP16 model has the same throughput as FP32 (the card runs FP16 at 1:1) plus cast overhead,
so **FP32 stays the default**. Use FP16 only if your GPU has a real FP16 speed-up:

```powershell
$env:AVE_RIFE_MODEL = "RIFE_fp16_timestep.onnx"
```

### 3.4 Frontend

```powershell
cd "D:\Project\AI Video Enhancer\frontend"
npm install
```

---

## 4. Running in development

Two terminals. **Backend first:**

```powershell
cd "D:\Project\AI Video Enhancer\backend"
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

*Q: `uvicorn` not found?* Use `python -m uvicorn app.main:app --port 8000`.

**Frontend:**

```powershell
cd "D:\Project\AI Video Enhancer\frontend"
npm run dev
```

Open <http://localhost:3000>. The Next.js dev server proxies `/api/*` to `http://127.0.0.1:8000`
(see `next.config.ts`), so there is no CORS setup to worry about. Set `BACKEND_URL` to point elsewhere:

```powershell
$env:BACKEND_URL = "http://127.0.0.1:9000"
npm run dev
```

### Useful API endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Liveness probe |
| GET | `/api/system` | FFmpeg/GPU/NVENC/model capabilities, limits, presets |
| GET | `/api/system/model` | RIFE graph details (inputs, outputs, provider, tiling) |
| POST | `/api/probe` | Read metadata without creating a job |
| POST | `/api/jobs` | Upload + queue processing (multipart: `file`, `settings`) |
| GET | `/api/jobs/{id}` | Progress / status / metadata |
| POST | `/api/jobs/{id}/cancel` | Request cancellation (status goes `cancelling` → `cancelled`; partial output removed) |
| DELETE | `/api/jobs/{id}` | Delete a job and its files |
| GET | `/api/jobs/{id}/original` | Range-stream the uploaded video |
| GET | `/api/jobs/{id}/preview` | Range-stream the enhanced video |
| GET | `/api/jobs/{id}/download` | Download the enhanced MP4 |

Interactive docs: <http://127.0.0.1:8000/docs>

---

## 5. Production build

```powershell
cd "D:\Project\AI Video Enhancer\frontend"
npm run build
npm run start            # serves on http://localhost:3000

cd "D:\Project\AI Video Enhancer\backend"
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

> Keep the backend at **one worker**: the RIFE session is loaded in-process and jobs are serialised
> behind a semaphore so a single GPU is not oversubscribed.

---

## 6. Windows desktop app (packaged .exe)

For the people who will actually use the tool, everything ships as one folder with one executable: no
Python, no Node.js, no terminal. The exe starts the API in-process, serves the same web UI and opens it in
a **native window**.

### Build it

```powershell
cd "D:\Project\AI Video Enhancer\backend"
.\scripts\build_windows_app.ps1
```

The script installs the build deps into the backend venv, exports the frontend, copies `ffmpeg.exe` /
`ffprobe.exe` into the bundle and runs PyInstaller:

```
backend\dist\AI Video Enhancer\AI Video Enhancer.exe
```

| Flag | Effect |
| --- | --- |
| `-Clean` | Delete `.next`, `out`, `build` and `dist` first |
| `-SkipUi` | Reuse the existing `frontend\out` and skip the Next.js build |
| `-CpuOnly` | Leave out the CUDA/cuDNN DLLs (~2.3 GB) → much smaller app, CPU interpolation only |
| `-OutDir <dir>` | Build into another folder (default `backend\dist`). Needed when `dist` is held open by a *running* instance — Windows cannot overwrite a running exe |

The exe icon comes from `backend\packaging\app.ico`, which `backend\packaging\make_icon.py` regenerates
(`python packaging\make_icon.py`) — the same file is reused by the installer, so the shortcut and the
Add/Remove entry match the app.

### What goes in the bundle

| Part | Source |
| --- | --- |
| `ui\` | static export of the Next.js frontend (`frontend\out`) |
| `models\` | the RIFE ONNX models from `backend\models` |
| `bin\` | `ffmpeg.exe` + `ffprobe.exe` copied from the build machine |
| `nvidia\` | CUDA 12 / cuDNN 9 DLLs from the `nvidia-*-cu12` wheels, original layout preserved — trimmed to the subset ORT really loads (see below) |
| `_internal\` | Python runtime + FastAPI, uvicorn, onnxruntime, numpy, pywebview |

A GPU build is roughly **3.2 GB** — the kept CUDA 12 / cuDNN 9 DLLs are ~2.3 GB, FFmpeg ~0.45 GB,
the RIFE models ~50 MB, the rest is Python + onnxruntime. `-CpuOnly` is a few hundred MB.

**The CUDA trim.** The wheels ship ~3.3 GB of NVIDIA DLLs, but a real RIFE inference run only ever
loads 11 of them (`cusparse` and `cusolver` alone are ~900 MB and are never touched). `app.spec`
excludes the proven-dead set — after checking the keep-list against ORT's own
`onnxruntime/__init__.py::_get_nvidia_dll_paths()` — which cuts ~1 GB off both the installed size
and the download. To re-derive the evidence instead of trusting the list:

```powershell
python scripts\scan_nvidia_usage.py   # runs the production warmup path, lists which DLLs load
```

### Run it

Double-click **AI Video Enhancer.exe** (the same launcher also runs from source with `python -m app.desktop`
in `backend`, given a built `frontend/out`):

1. FastAPI starts on `127.0.0.1:8123` and the RIFE model warms up in the background.
2. A native **WebView2** window opens on the UI (a small splash shows while the engine loads).
3. Closing the window shuts the server down.

On the user's machine only two things must already be present: an **NVIDIA driver** (for GPU
interpolation — otherwise it runs on CPU) and the **Microsoft Edge WebView2 Runtime**, which is
preinstalled on Windows 10/11. If WebView2 or pywebview is missing the app still runs and opens in the
default browser instead, with a dialog explaining how to get the native window.

A second launch while the app is already running does not start a second engine — it just opens another
window on the existing one.

Writable data stays out of the install folder, so the app also works from `Program Files`:

| What | Where |
| --- | --- |
| uploads / outputs / temp | `%LOCALAPPDATA%\AI Video Enhancer\storage` |
| log | `%LOCALAPPDATA%\AI Video Enhancer\app.log` |
| startup trace | `%LOCALAPPDATA%\AI Video Enhancer\startup.log` (written before logging exists; also gets a native stack dump on a hard crash) |

The engine listens on **localhost only** — nothing is exposed to the network.

### Never opens a terminal

A windowed exe has no console of its own, so every console-mode child it starts (`ffmpeg.exe`,
`ffprobe.exe`, `nvidia-smi.exe` — one per health poll, one per upload probe, one per job) would
otherwise get a **brand-new console window** from Windows. That is the "terminal keeps popping up"
you can get from a naive PyInstaller build.

Every spawn therefore goes through `app/subproc.py`, which always passes `CREATE_NO_WINDOW`, and the
UI's frequent probes are memoised so they do not even respawn a process:

| Spawn | When | Guard |
| --- | --- | --- |
| `ffmpeg -version`, `ffprobe -version` | `/api/health` (polled by the UI) | probed once, cached |
| `nvidia-smi` | `/api/system` (polled for VRAM/GPU name) | cached, `CREATE_NO_WINDOW` |
| `ffmpeg -encoders` + NVENC probe | capability detection | `CREATE_NO_WINDOW`, cached |
| `ffprobe` on your file | every upload | `CREATE_NO_WINDOW` |
| decode + encode `ffmpeg` | every job | `CREATE_NO_WINDOW` |

`tests/test_units.py::test_children_never_open_console_windows` fails the build if any module calls
`subprocess.run`/`Popen` directly, and `scripts/verify_no_console_windows.py` launches the packaged
exe on a free port, hammers those endpoints and counts real console windows via Win32 — it must
report `"pass": true`:

```
cd backend
.\.venv\Scripts\python.exe scripts\verify_no_console_windows.py --exe "dist\AI Video Enhancer\AI Video Enhancer.exe" --no-gpu
```

(`--no-gpu` hides the GPU from the test instance so it cannot compete with a job running in the app
you are actually using.)

### Distribute it

Two options.

**1. A real Windows installer (recommended).** `scripts/build_installer.ps1` wraps the packaged folder in
a normal setup program:

```powershell
cd backend
.\scripts\build_installer.ps1                  # -> installer_output\AI-Video-Enhancer-Setup-1.0.0.exe
.\scripts\build_installer.ps1 -Version 1.1.0   # bump the version shown in Settings > Apps
```

It needs [Inno Setup 6](https://jrsoftware.org/isinfo.php) — free, and one command to install:

```powershell
winget install --id JRSoftware.InnoSetup -e --scope user
```

The build is a single LZMA pass over the whole bundle, so expect a few minutes and roughly a 2.5 GB
setup file (the sources are ~4.1 GB). What a first-time user then gets is the ordinary Windows flow —
no zip to unpack, no admin prompt:

| Screen | What happens |
| --- | --- |
| Welcome | "This will install AI Video Enhancer 1.0.0" |
| Terms | *I accept* / *I don't accept* (text from `packaging/EULA.txt`) |
| Install location | defaults to `%LOCALAPPDATA%\Programs\AI Video Enhancer` — no UAC |
| Install | progress bar while it copies |
| Finished | optional desktop shortcut + *Launch AI Video Enhancer* |

It also registers the app in **Settings > Apps** with an uninstaller, creates the Start-menu entry,
closes a running instance before upgrading, and checks for the WebView2 runtime (if it is missing it
says so instead of failing — the app still opens in your browser).

| File | Purpose |
| --- | --- |
| `packaging/installer.iss` | the Inno Setup script (wizard pages, shortcuts, WebView2 check) |
| `packaging/EULA.txt` | terms shown on the accept screen |
| `packaging/make_icon.py` | generates `packaging/app.ico` for the exe, shortcuts and installer |
| `scripts/build_installer.ps1` | finds `ISCC.exe`, compiles, reports the result |

**2. A zip.** Right-click the `AI Video Enhancer` folder → *Send to* → *Compressed (zipped) folder*. It
works (the exe needs its sibling `_internal` directory, and the UI's *Save location* picker keeps working
because localhost is a secure context), but the user has to unzip it and find the exe themselves.

**3. Hosting the download (GitHub Releases + this site).** The setup file is shipped as a GitHub
release asset: free CDN, a **real** per-asset download counter, and no multi-GB file in git (GitHub
caps release files at **2 GB** — which is exactly why the CUDA trim above exists; the solid
LZMA2-packed setup stays under that line).

1. Push the app to a GitHub repo, create a release (`v1.0.0`) and upload
   `AI-Video-Enhancer-Setup-1.0.0.exe` as the asset.
2. Set the portfolio env vars (`fl33-portfolio/.env` locally, the same names in Vercel → Settings →
   Environment Variables):

   | Variable | Value |
   | --- | --- |
   | `NEXT_PUBLIC_AVE_DOWNLOAD_URL` | `https://fl33.yeungthott.xyz/AI-Video-Enhancer-Setup-1.0.0.exe` |
   | `AVE_RELEASE_URL` | the `https://github.com/<you>/<repo>/releases/download/...` URL of the asset |

   The first is the download button on the project page. The second powers the pretty short link (a
   302 redirect from `next.config.ts`) and the download-count badge — the site reads the asset's
   `download_count` from the GitHub API and **hides** the number when it cannot fetch it, rather
   than inventing one.

> Whatever you ship, host it somewhere that allows multi-gigabyte files and lets you verify the
> bytes — the project page publishes the setup's SHA-256 so a visitor can check the download
> themselves.

---

## 7. Configuration

Environment variables (all optional):

| Variable | Default | Meaning |
| --- | --- | --- |
| `AVE_RIFE_MODEL` | `RIFE_fp32_timestep.onnx` | Model file name inside `backend/models` |
| `AVE_MODELS_DIR` | `backend/models` | Model directory (`<bundle>/models` in the packaged app) |
| `AVE_STORAGE_DIR` | `backend/storage` | Uploads / outputs / temp root (`%LOCALAPPDATA%\AI Video Enhancer\storage` in the packaged app) |
| `AVE_TILE_HEIGHT` | `384` | Tile height for tiled inference; raise it on cards with more VRAM, lower it if cuDNN workspaces thrash |
| `AVE_MAX_UPLOAD_GB` | `4` | Upload size limit |
| `AVE_MAX_DURATION_MIN` | `30` | Maximum source duration in minutes |
| `AVE_JOB_TTL_HOURS` | `4` | Age after which job files are deleted |
| `AVE_FFMPEG` / `AVE_FFPROBE` | `ffmpeg` / `ffprobe` | Binary names or absolute paths (a bundled `bin\ffmpeg.exe` wins over `PATH`) |
| `AVE_PROXY_MAX_BODY` | `4gb` | Next.js proxy body buffer (`experimental.proxyClientMaxBodySize`) for `/api/*`; must cover your upload size |
| `BACKEND_URL` | `http://127.0.0.1:8000` | Proxy target used by the Next.js dev server |
| `AVE_PORT` | `8123` | Desktop launcher port |
| `AVE_NO_WINDOW` | off | `1` = headless: run the engine with no desktop window (scripts, unattended jobs). Closing the window normally quits the app and cancels jobs in flight |
| `AVE_SERVE_UI` | off in a source checkout, on when frozen | Serve the statically exported UI from FastAPI (the desktop app does this) |
| `AVE_UI_DIR` | `<bundle>/ui` | Static UI directory when `AVE_SERVE_UI` is on; point it at `frontend/out` to preview the real desktop build in dev |
| `AVE_LOCAL_DIR` | `%LOCALAPPDATA%\AI Video Enhancer` | Where the packaged app keeps storage and `app.log` |
| `AVE_CPU_ONLY` | off | Build-time only: `build_windows_app.ps1 -CpuOnly` sets it to drop the CUDA DLLs |

> `AVE_PROXY_MAX_BODY` trades memory for upload size: Next.js buffers each proxied request body in RAM,
> so a multi-GB upload needs roughly that much free memory too. Jobs, streaming and downloads are unaffected.

---

## 8. How the pipeline works

```
upload ──► ffprobe validation ──► job (background thread, 1 concurrent)
                                      │
        ┌─────────────────────────────┴──────────────────────────────┐
        │ ffmpeg -i source -vf scale/crop/pad -f rawvideo rgb24 ─►   │  decode
        │ stdio frames                                               │
        └─────────────────────────────┬──────────────────────────────┘
                                      ▼
                     RIFE ONNX Runtime (CUDA ▸ tiled ▸ CPU)
              for each pair: emit source frame + synthetic frames
                                      │
        ┌─────────────────────────────┴──────────────────────────────┐
        │ ffmpeg -f rawvideo -framerate TARGET -i - -i source -map … │  encode
        │ h264_nvenc │ hevc_nvenc │ libx264 │ libx265 + AAC audio    │
        └─────────────────────────────┬──────────────────────────────┘
                                      ▼
                          output MP4 (+faststart) ─► preview / download
```

Details worth knowing:

- **Exact frame timing.** Output frame `u` samples the source timeline at `u / factor`. For non-integer
  factors (24 → 60 fps = 2.5×) the interpolator is queried at the correct fractional timesteps instead of
  duplicating frames, so the audio never drifts.
- **Slow motion.** `factor = target / (speed × source)`; the encoder writes at `target` fps and audio is
  time-stretched with `atempo`, so a 0.5× clip is twice as long with synchronised audio.
- **Geometry first.** Scale/crop/pad happen in the decode filter chain, so RIFE only ever sees the final
  output resolution (cheaper and consistent with the exported dimensions).
- **Processing detail (speed vs detail).** `computeScale` (UI: *Processing detail*) decouples the size the AI
  renders from the size that is exported: at `0.75`/`0.5` the decode/scale chain targets 75 %/50 % of the
  output, RIFE interpolates that smaller frame, and the encoder upscales it back with a lanczos `-vf scale`
  to the requested output size. Because RIFE cost tracks pixel count this is ~1.8× / ~4× cheaper, and on
  memory-starved cards the smaller frame often fits without tiling at all.
- **Streaming.** Frames move through pipes; the app never holds the whole video in memory.
- **VRAM-aware GPU use.** The CUDA arena is capped at 85 % of the currently free VRAM and, if a full-frame
  pass is estimated not to fit, the engine tiles the frame into overlapping horizontal bands (default tile
  height 384 px, `AVE_TILE_HEIGHT`). A genuine out-of-memory error also switches to tiling and, if that
  fails, to CPU — the job keeps going instead of crashing.
- **Failure handling.** Corrupt/unsupported files are rejected at probe time; FFmpeg stderr is drained and
  surfaced on failure; disk space is checked before encoding; partial outputs are deleted on error/cancel.
- **Cancel.** The cancel button sets a flag that the pipeline checks between frames; the job reports
  `cancelling` until the loop actually stops, then `cancelled`. The killed encoder is reaped and the
  partial MP4 is removed (retried, because Windows keeps the file handle open until the process exits).
  A job still waiting for the single GPU slot settles to `cancelled` immediately instead of waiting for
  the running job to release it. Frame size/orientation is fixed when a job starts, so a running job
  cannot be re-targeted — cancel and start a new one with the new settings.
- **Cleanup.** Outputs/uploads are removed after `AVE_JOB_TTL_HOURS`, and `storage/` is purged on startup.

---

## 9. Troubleshooting

| Symptom | Fix |
| --- | --- |
| `ffmpeg was not found on PATH` | Reopen the terminal after `winget install`, or set `AVE_FFMPEG` to the full `ffmpeg.exe` path |
| `RIFE model not found` | Run `python scripts\download_models.py` in `backend` |
| `CUDAExecutionProvider` missing in `/api/system/model` | GPU wheels not installed or CUDA/cuDNN mismatch; re-run step 3.2 (`onnxruntime-gpu` + the `nvidia-*-cu12` wheels). Check with `python scripts\check_cuda.py`. The app still works on CPU |
| Job very slow (a few frames/s or less) | Check the `AI device` row — CPU fallback means no GPU provider. Close GPU-heavy apps (games, 3D, video calls): on the 4 GB test card a game holding ~3.4 GB dropped throughput ~100x; the dashboard shows an amber warning when free VRAM is low. Set **Processing detail** to *Faster* / *Fastest* (roughly 1.8× / 4× quicker at the same output size), use the `fast` quality preset and H.264, or a smaller output frame via **Custom size** (RIFE cost scales with pixel count). |
| Slow even though the device is CUDA | Watch for `using tiles` in the backend log. Tiling is expected on 4 GB cards at 1080 × 1920; tune `AVE_TILE_HEIGHT` (384 was fastest on the test card, 640 thrashed) and free VRAM by closing other apps |
| `out of memory` warnings, then it continues | Expected: the engine auto-switches to tiled inference, then CPU. The CUDA arena is capped at 85 % of free VRAM so over-allocation raises instead of silently spilling into system memory |
| Video looks identical to the original | Check the enhanced FPS in the stats panel. TikTok/Reels may re-encode uploads to 30 fps server-side |
| Upload fails with `Request body exceeded 10MB` / `500` on `/api/probe` or `/api/jobs` | Next.js buffers proxied request bodies (10 MB by default), which truncates real video files. `next.config.ts` sets `experimental.proxyClientMaxBodySize` to `4gb` (override with `AVE_PROXY_MAX_BODY`); restart `npm run dev` after changing it |
| Browser cannot seek in previews | Previews support HTTP range requests; make sure you are accessing them through the Next.js proxy (`/api/...`) rather than opening the FastAPI port directly with a restrictive browser policy |
| No "Choose folder…" button, or saving to a folder fails | Folder pickers need the File System Access API: Chrome/Edge desktop on `localhost` or HTTPS. Firefox/Safari fall back to the normal download button. The grant is per folder and is re-requested after a browser restart; the chosen folder is remembered in IndexedDB |
| PowerShell refuses `Activate.ps1` | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` |
| Backend stops accepting connections right after startup (`WinError 64 ... Accept failed on a socket` in the log) | A client reset a connection while the RIFE model was still loading. Restart the backend and don't poll the port during the first ~1 minute (model load + first CUDA pass) |
| Double-clicking `AI Video Enhancer.exe` does nothing (or a window flashes and closes) | A windowed build has no console, so nothing is printed. Read `%LOCALAPPDATA%\AI Video Enhancer\app.log`; `startup.log` in the same folder is an even earlier, flushed trace (plus a native stack dump if it crashed). The exe also needs its `_internal` folder next to it: move/copy the whole `AI Video Enhancer` folder, not just the exe |
| "Microsoft Edge WebView2 Runtime is required" dialog, app opens in the browser instead | The native window needs the WebView2 Runtime (preinstalled on Windows 10/11). Install the Evergreen Runtime, or keep using the browser window it opened |
| Terminal / command-prompt windows keep popping up while the app runs | That is `ffmpeg`/`ffprobe`/`nvidia-smi` being spawned without `CREATE_NO_WINDOW`. Every spawn must go through `app/subproc.py`; `tests/test_units.py::test_children_never_open_console_windows` fails if a module calls `subprocess.run` directly, and `scripts/verify_no_console_windows.py` counts real console windows in the packaged app |
| The packaged app is ~3.2 GB | Expected: the kept CUDA 12 / cuDNN 9 DLLs are ~2.3 GB and FFmpeg ~0.45 GB (the dead-weight CUDA DLLs are already trimmed — see §6). Build with `-CpuOnly` for a few-hundred-MB CPU-only app |
| SmartScreen / antivirus flags the exe | PyInstaller one-folder builds are frequently false-positive flagged and the exe is unsigned. Choose *More info → Run anyway*, or add an exclusion; code-signing removes the warning |
| Want to see the exact packaged UI while developing | Export the frontend (`$env:NEXT_EXPORT=1; npm run build`), then run the backend with `$env:AVE_SERVE_UI=1; $env:AVE_UI_DIR="D:\Project\AI Video Enhancer\frontend\out"; uvicorn app.main:app --port 8123` |

**Verify real interpolation** without the UI:

```powershell
cd "D:\Project\AI Video Enhancer\backend"
.\.venv\Scripts\Activate.ps1
python -c "from app.rife import RifeEngine; from app.config import RIFE_MODEL_PATH; e=RifeEngine(RIFE_MODEL_PATH); print(e.describe()); e.warmup(); print('warmup ok on', e.device)"
```

`ffprobe` on the exported file should report double the source FPS *and* roughly double the frame count —
that only happens when new frames were actually generated.

### Verification scripts

| Script | What it proves |
| --- | --- |
| `scripts/e2e_test.py` | Full GPU pipeline on generated clips (TikTok 1080 × 1920, 30 → 120 fps, slow motion) and checks that the interpolated frames are genuinely new, not duplicates |
| `scripts/api_test.py` | All HTTP endpoints against a running server, including range streaming, download naming, cancel-with-no-partial-output and delete (`--base` to test through the Next.js proxy) |
| `scripts/desktop_app_test.py` | The desktop app end to end (packaged exe or `app.desktop`): bundled FFmpeg, RIFE on the bundled CUDA, NVENC, then two real jobs — the default path and `fit=original` at 50% processing detail, checking the exact interpolated frame count and that audio/duration are preserved (`--base` for another URL) |
| `scripts/verify_no_console_windows.py` | Launches the packaged exe on a free port, hits `/api/health` + `/api/system` (the calls that spawn `ffmpeg`/`ffprobe`/`nvidia-smi`) and counts console windows through Win32. Fails if the app pops a terminal |
| `scripts/check_cuda.py` | ONNX Runtime providers and whether the CUDA provider really initialises |
| `scripts/check_tiling_quality.py` | Tiled vs full-frame output difference (confirms tiling does not create seams) |
| `scripts/bench_one.py` | One RIFE configuration in a fresh process (model, size, tile height) |
| `scripts/bench_processing_detail.py` | Full-detail vs *Fastest* processing on the same clip — proves the speed-up and that the output geometry is unchanged |
| `scripts/fp16_variants.py` | FP16 conversion variants |
| `tests/test_units.py` | Unit suite: `python -m pytest -q` |
| `scripts/build_windows_app.ps1` | Builds the packaged desktop app (`dist\AI Video Enhancer\AI Video Enhancer.exe`) end to end |
| `scripts/build_installer.ps1` | Wraps the packaged app in a Windows setup program (`installer_output\AI-Video-Enhancer-Setup-<version>.exe`) with Inno Setup — see §6 *Distribute it* |
| `packaging/make_icon.py` | Generates `packaging/app.ico` (the exe, the shortcuts and the installer all use it) without needing an imaging library |
| `scripts/scan_nvidia_usage.py` | Runs the production RIFE warmup and reports which bundled NVIDIA DLLs are actually loaded — the evidence behind the ~1 GB CUDA trim in `app.spec` |

---

## 10. Project layout

```
AI Video Enhancer/
├─ backend/
│  ├─ app/
│  │  ├─ config.py      # paths, limits, env overrides (source + frozen layout)
│  │  ├─ desktop.py     # desktop launcher: uvicorn thread + native WebView2 window
│  │  ├─ probe.py       # ffprobe wrapper + validation
│  │  ├─ ffmpeg.py      # encoder detection, filter chains, encode args
│  │  ├─ rife.py        # ONNX session, provider selection, tiling, fallback
│  │  ├─ pipeline.py    # decode → interpolate → encode streaming pipeline
│  │  ├─ jobs.py        # background jobs, progress, cancel, cleanup
│  │  ├─ schemas.py     # request/response models + presets
│  │  ├─ main.py        # FastAPI app and routes (incl. range streaming + static UI)
│  │  └─ subproc.py     # CREATE_NO_WINDOW wrappers - every ffmpeg/ffprobe/nvidia-smi spawn goes through here
│  ├─ packaging/        # PyInstaller spec, entry point, installer.iss, EULA.txt, make_icon.py, bundled bin/ (ffmpeg)
│  ├─ models/           # RIFE_fp32_timestep.onnx (downloaded; + optional midpoint/fp16)
│  ├─ scripts/          # download_models, api_test, desktop_app_test, verify_no_console_windows, e2e_test, build_windows_app.ps1, build_installer.ps1, …
│  ├─ tests/            # unit suite (pytest)
│  ├─ storage/          # uploads, outputs, temp (auto-cleaned)
│  └─ requirements.txt
├─ frontend/
│  ├─ src/app/          # layout, dashboard page, global styles
│  ├─ src/components/   # Dropzone, SettingsPanel, ProgressPanel, ComparePlayer, …
│  ├─ src/lib/          # api client, types, formatters
│  ├─ out/              # static export used by the packaged app (NEXT_EXPORT=1)
│  └─ next.config.ts    # /api proxy to FastAPI (dev) / static export (desktop)
└─ README.md
```

---

## 11. Limits and honest caveats

- 120 fps output is supported, but platforms will likely transcode uploads — 60 fps is the safest target.
- RIFE can produce artefacts around fast motion, occlusions, cuts and flicker; slow motion at 0.25×
  magnifies this. Scene cuts are not detected, so a cut between two frames can produce a short blend.
- CPU interpolation is roughly two orders of magnitude slower than GPU; a 4 GB GPU needs tiling for
  1080 × 1920, which costs speed (~0.5 fps on the GTX 1650 test card). *Faster*/*Fastest* processing detail
  reduces how many pixels the AI touches and is the main lever when the output size must stay 1080 × 1920.
- **GPU time is shared.** Throughput depends heavily on what else is using the card: the same 640 × 360
  pass ran at ~7 fps on an idle GTX 1650 and ~0.05 fps while a game held the GPU. Close GPU-heavy apps for
  realistic speeds, and expect the first frame of a job to include model load + cuDNN autotuning.
- The job queue is in-memory: restarting the backend cancels running jobs and cleans the storage folders.
- The packaged desktop app is a **one-folder** bundle (~2–3 GB with CUDA), not a single self-contained file:
  keep `AI Video Enhancer.exe` together with its `_internal` folder. It is unsigned, so SmartScreen may warn
  on first run.
- Single-user, single-machine tool. Add authentication before exposing it to a network.
