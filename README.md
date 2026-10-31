# DeepSeek Terminal Assistants

Python command-line tools that automate a signed-in [DeepSeek](https://chat.deepseek.com/) browser session with Playwright. The scripts open separate DeepSeek tabs, submit prompts, and print the responses in the terminal.

## Included scripts

| Script | Purpose |
| --- | --- |
| `main.py` | Basic interactive or one-shot terminal chat. |
| `mainv2.py` | Chat with microphone input, file/image attachment, screen capture, and browser-page capture. |
| `main_screen.py` | Consent-based screen or browser-view capture assistant, including a timed watch mode. |
| `mainv3.py` | Two DeepSeek personas that discuss a user-provided topic. |
| `mainv4.py` | Time-boxed, multi-agent research loop that saves transcripts and final results under `agent_runs/`. |
| `run.bat` | Windows launcher for `main.py`. |

## Prerequisites

- Windows with Google Chrome installed at `C:\Program Files\Google\Chrome\Application\chrome.exe`, or use the script-managed Playwright profile with `--profile`.
- Python 3.10 or newer.
- A DeepSeek account. Sign in in the browser window when prompted.
- Microphone permission for `:voice` commands.

## Setup

From the project directory, create and activate a virtual environment, install the dependencies, then install Playwright's Chromium browser:

```bat
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt Pillow
playwright install chromium
```

`Pillow` is installed separately above because screen-capture features import `PIL.ImageGrab`.

## Quick start

Run the basic assistant interactively:

```bat
python main.py
```

Or send one prompt and exit:

```bat
python main.py "Explain the difference between a list and a tuple in Python."
```

Use `--profile` to launch a dedicated Playwright-managed browser profile instead of connecting to Chrome's debugging port:

```bat
python main.py --profile
```

Without `--profile`, the scripts try to connect to Chrome at `http://127.0.0.1:9222`; if it is unavailable, they launch Chrome with a separate local automation profile. Your regular Chrome profile is not modified.

## Feature usage

### Enhanced terminal chat

```bat
python mainv2.py
```

In interactive mode, these commands are available:

```text
:voice             Record up to eight seconds and transcribe speech
:upload            Queue a file path for the next message
:image PATH        Queue an image by path
:screen QUESTION   Capture all visible monitors and ask a question about them
:page QUESTION     Capture the active DeepSeek browser viewport
:new               Start a fresh DeepSeek chat
exit               End the terminal session
```

Screen and page images are captured only after the corresponding command and are deleted after that request completes.

### Screen assistant

```bat
python main_screen.py
```

Use `:help` for commands. `:watch N QUESTION` captures the screen every `N` seconds (limited to 5–300 seconds) until interrupted with `Ctrl+C`.

### Two-agent discussion

```bat
python mainv3.py --rounds 3
```

Choose two roles from the terminal, enter a topic, and the agents will exchange the requested number of rounds. Omit `--rounds` to continue until `Ctrl+C`.

### Multi-agent research run

```bat
python mainv4.py --minutes 30 --agents 4
```

Choose a specialization for each agent and enter the shared task. During a run, use `:pause`, `:resume`, `:prompt TEXT`, `:voice`, `:upload [PATH]`, `:status`, or `:stop`. Each run writes `transcript.jsonl`, `final.md`, and `metadata.json` to `agent_runs/<timestamp>/`.

## Notes and limitations

- These tools rely on DeepSeek's web interface and its page selectors. A DeepSeek UI change, CAPTCHA, sign-in prompt, or network issue can prevent a run from completing.
- The assistant opens new tabs and leaves them open when using an existing Chrome debugging session.
- Treat browser automation and generated answers as assistance, not a replacement for reviewing important results.

## License

See [LICENSE](LICENSE).
