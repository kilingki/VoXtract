# VoXtract

**English** · [한국어](README.ko.md)

VoXtract is a self-hosted web app for transcribing recordings, including Samsung Galaxy Voice Recorder files. Upload a recording, search the transcript, and tap a timestamp to hear that part of the original audio.

**The app interface is in Korean.**

## Features

- Upload M4A (AAC) or WAV (PCM) recordings up to two hours long. Saved recordings and jobs persist across server restarts.
- Transcribe audio with Qwen3-ASR, then use Qwen3-ForcedAligner to map words to the original recording.
- Search the transcript, jump to timestamps, and follow highlighted text as the audio plays.
- Export a standalone HTML transcript for offline reading, UTF-8 plain text, or versioned JSON with timing data.
- Use VoXtract on a Galaxy phone over a private Tailscale network and install it from Chrome.

## Quick start

### Run the web app

The tested setup uses Ubuntu on WSL2. You need Python 3.12 or later, `uv`, and FFmpeg. Make sure both `ffmpeg` and `ffprobe` are on the server's PATH.

```sh
git clone https://github.com/kilingki/VoXtract.git
cd VoXtract
uv sync --locked
uv run --locked python -m backend.app
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) to try uploading a recording. The app binds to loopback by default and stores recordings, job metadata, and results under `data/jobs/`. It keeps 1 GiB of disk space in reserve.

### Transcribe a recording

**Transcription requires all three companion services to be running**, with both Qwen runtimes registered in InferSwap. You can open the web app and upload recordings before setting up inference.

| Service | Role |
| --- | --- |
| [InferSwap](https://github.com/kilingki/InferSwap) | Routes inference requests and manages GPU model loading and unloading. |
| [Qwen3-ASR runtime](https://github.com/kilingki/qwen-asr-vllm-docker-config) | Transcribes the recording. |
| [Qwen3-ForcedAligner runtime](https://github.com/kilingki/qwen-fa-docker-config) | Aligns the transcript to the original audio. |

Follow the setup instructions in each companion repository. If VoXtract is already running, stop it, then restart it with the InferSwap URL reachable from the machine or WSL instance running VoXtract. Replace the example address if your gateway listens elsewhere.

```sh
VOXTRACT_INFERSWAP_URL=http://127.0.0.1:8095 uv run --locked python -m backend.app
```

The default InferSwap model IDs are `qwen-asr` and `qwen-fa`, with `qwen3-asr` as the ASR served name. If your registration differs, see the [example environment settings](.env.example).

1. Upload an M4A or WAV recording, or choose an existing recording from the job list.
2. Select **전사 시작** (Start transcription). The job list shows preparation, transcription, and alignment progress.
3. Select **결과 보기** (View results) to search, play, or download the transcript.

### Tested setup

The end-to-end workflow was tested with a roughly 52-minute Korean recording from a Galaxy phone. The reference host runs Windows with Ubuntu on WSL2, an Intel Core i5-12600K, 64 GB of RAM, and an NVIDIA GeForce RTX 3090 with 24 GB of VRAM.

## Use it from a Galaxy phone

1. Connect the Windows host and the phone to the same private Tailscale network. Check that `http://127.0.0.1:8000/api/health` responds on Windows.
2. Run the following in an elevated Windows terminal. If prompted, enable HTTPS for your tailnet. The HTTPS URL shown by `serve status` is subject to your tailnet's access rules. See the [Tailscale Serve docs](https://tailscale.com/docs/features/tailscale-serve).

   ```powershell
   tailscale serve --bg http://127.0.0.1:8000
   tailscale serve status
   ```

3. Open the HTTPS URL in Chrome on the phone. Use **Add to Home screen → Install** to launch VoXtract from your home screen.

Keep the host and VoXtract running to upload recordings, browse saved jobs, or play audio. You can read a downloaded HTML transcript offline.

## Development

Configure VoXtract through environment variables. To use a `.env` file in Bash, copy [.env.example](.env.example), edit it, then load it explicitly before starting the app.

```sh
set -a
. ./.env
set +a
```

Run the checks from the repository root.

```sh
uv run --locked python -m unittest discover -s tests -v
uv run --locked python -m compileall -q backend tests
```

The tests use temporary storage, generated audio, and a mock InferSwap service.

- [Development guidelines](AGENTS.md)

## License

[MIT](LICENSE) · Copyright (c) 2026 kilingki
