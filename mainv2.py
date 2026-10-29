"""DeepSeek terminal assistant with voice, new-chat, and image commands."""

from __future__ import annotations

import argparse
import io
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

from PIL import ImageGrab
import sounddevice as sd
import speech_recognition as sr
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


SITE_URL = "https://chat.deepseek.com/"
PROFILE_DIR = Path(__file__).with_name(".deepseek-browser")
CHROME_AUTOMATION_PROFILE = Path(__file__).with_name(".chrome-profile")
DEBUGGING_URL = "http://127.0.0.1:9222"
CHROME_PATH = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
PROMPT_SELECTORS = (
    'textarea[placeholder*="Message"]',
    'textarea[placeholder*="message"]',
    "textarea",
    '[contenteditable="true"]',
)
ASSISTANT_SELECTORS = (
    '[data-message-author-role="assistant"]',
    '[data-testid*="assistant"]',
    ".ds-markdown",
)


def find_prompt_box(page: Page):
    for selector in PROMPT_SELECTORS:
        locator = page.locator(selector).first
        if locator.count() and locator.is_visible():
            return locator
    raise RuntimeError("Could not find DeepSeek's message box.")


def assistant_text(page: Page) -> str:
    for selector in ASSISTANT_SELECTORS:
        messages = page.locator(selector)
        if messages.count():
            text = messages.last.inner_text().strip()
            if text:
                return text
    return ""


def wait_for_reply(page: Page, previous_reply: str) -> str:
    deadline = time.monotonic() + 180
    latest = ""
    stable_since = None
    print("DeepSeek is thinking", end="", flush=True)
    while time.monotonic() < deadline:
        current = assistant_text(page)
        if current and current != previous_reply:
            if current != latest:
                latest = current
                stable_since = time.monotonic()
            elif stable_since and time.monotonic() - stable_since >= 6:
                print(" done.")
                return latest
        print(".", end="", flush=True)
        page.wait_for_timeout(1_000)
    print()
    raise RuntimeError(
        "DeepSeek did not finish the reply within 180 seconds. "
        "The response may still be generating in Chrome."
    )


def attach_image(page: Page, image_path: str) -> None:
    path = Path(image_path.strip().strip('"')).expanduser().resolve()
    if not path.is_file():
        raise RuntimeError(f"Image file not found: {path}")
    inputs = page.locator('input[type="file"]')
    if inputs.count():
        inputs.first.set_input_files(str(path))
        return
    attach_selectors = (
        'button[aria-label*="attach" i]',
        'button[title*="attach" i]',
        '[data-testid*="attach" i]',
    )
    for selector in attach_selectors:
        button = page.locator(selector).first
        if button.count() and button.is_visible():
            try:
                with page.expect_file_chooser(timeout=3_000) as chooser_info:
                    button.click()
                chooser_info.value.set_files(str(path))
                return
            except PlaywrightTimeoutError:
                continue
    raise RuntimeError(
        "DeepSeek's image attachment control was not found. "
        "The site may have changed."
    )


def start_new_chat(page: Page) -> None:
    # The home route consistently creates a fresh conversation, even when the
    # sidebar is collapsed or DeepSeek changes the icon-only button markup.
    try:
        page.goto(SITE_URL, wait_until="domcontentloaded", timeout=60_000)
    except PlaywrightTimeoutError:
        if "deepseek.com" not in page.url:
            raise RuntimeError("DeepSeek did not finish loading a new chat.")
    page.wait_for_selector("textarea, [contenteditable='true']", timeout=120_000)


def listen_for_voice() -> str:
    seconds = 8
    sample_rate = 16_000
    print(f"Speak now (recording for up to {seconds} seconds)...")
    try:
        recording = sd.rec(
            int(seconds * sample_rate),
            samplerate=sample_rate,
            channels=1,
            dtype="int16",
        )
        sd.wait()
        wav = io.BytesIO()
        with wave.open(wav, "wb") as file:
            file.setnchannels(1)
            file.setsampwidth(2)
            file.setframerate(sample_rate)
            file.writeframes(recording.tobytes())
        audio = sr.AudioData(wav.getvalue(), sample_rate, 2)
        transcript = sr.Recognizer().recognize_google(audio)
    except Exception as exc:
        raise RuntimeError(
            "Voice input failed. Check microphone permissions and internet "
            "access, then try :voice again."
        ) from exc
    if not transcript.strip():
        raise RuntimeError("No speech was detected.")
    return transcript.strip()


def capture_screen() -> Path:
    image = ImageGrab.grab(all_screens=True)
    handle = tempfile.NamedTemporaryFile(
        prefix="deepseek-screen-", suffix=".png", delete=False
    )
    path = Path(handle.name)
    handle.close()
    image.save(path, format="PNG")
    return path


def capture_page(page: Page) -> Path:
    handle = tempfile.NamedTemporaryFile(
        prefix="deepseek-page-", suffix=".png", delete=False
    )
    path = Path(handle.name)
    handle.close()
    page.screenshot(path=str(path), full_page=False)
    return path


def send_prompt(page: Page, prompt: str, image_path: str | None = None) -> str:
    if image_path:
        attach_image(page, image_path)
        page.wait_for_timeout(500)
    editor = find_prompt_box(page)
    previous_reply = assistant_text(page)
    editor.fill(prompt)
    editor.press("Enter")
    return wait_for_reply(page, previous_reply)


def connect_to_chrome(playwright):
    try:
        browser = playwright.chromium.connect_over_cdp(DEBUGGING_URL)
    except Exception:
        return None
    if not browser.contexts:
        browser.close()
        return None
    return browser.contexts[0]


def open_browser(playwright, use_profile: bool):
    if use_profile:
        return (
            playwright.chromium.launch_persistent_context(
                str(PROFILE_DIR), headless=False, viewport={"width": 1280, "height": 900}
            ),
            True,
        )
    context = connect_to_chrome(playwright)
    if context is not None:
        return context, False
    if not CHROME_PATH.exists():
        raise RuntimeError(f"Chrome was not found at {CHROME_PATH}.")
    subprocess.Popen(
        [
            str(CHROME_PATH),
            "--remote-debugging-port=9222",
            "--user-data-dir=" + str(CHROME_AUTOMATION_PROFILE),
            "--no-first-run",
            "--no-default-browser-check",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(30):
        time.sleep(1)
        context = connect_to_chrome(playwright)
        if context is not None:
            return context, False
    raise RuntimeError("Chrome debugging connection did not become available.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DeepSeek assistant v2.")
    parser.add_argument("prompt", nargs="?", help="Optional one-shot prompt.")
    parser.add_argument("--profile", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    with sync_playwright() as playwright:
        context, owns_context = open_browser(playwright, args.profile)
        page = context.new_page()
        page.bring_to_front()
        try:
            page.goto(SITE_URL, wait_until="domcontentloaded", timeout=60_000)
        except PlaywrightTimeoutError:
            if "deepseek.com" not in page.url:
                raise RuntimeError("DeepSeek did not finish loading.")
        context.grant_permissions(["microphone"], origin=SITE_URL.rstrip("/"))
        print("+" + "-" * 62 + "+")
        print("| DeepSeek terminal chat | new tab ready                 |")
        print("+" + "-" * 62 + "+")
        print("Waiting for the DeepSeek message box...")
        try:
            page.wait_for_selector(
                "textarea, [contenteditable='true']", timeout=120_000
            )
        except PlaywrightTimeoutError as exc:
            if owns_context:
                context.close()
            raise RuntimeError(
                "The DeepSeek message box did not become available. "
                "Check sign-in, CAPTCHA, or network status."
            ) from exc

        pending_image: str | None = None
        try:
            if args.prompt:
                reply = send_prompt(page, args.prompt)
                print(f"DeepSeek> {reply}")
            else:
                print(
                    "\nCommands: :voice | :upload | :new | :help | exit\n"
                    "Type a message below and press Enter."
                )
                while True:
                    value = input("\nYou> ").strip()
                    if not value:
                        continue
                    command, _, argument = value.partition(" ")
                    command = command.lower()
                    if command in {"exit", "quit", ":q"}:
                        break
                    if command in {":help", "help"}:
                        print(
                            "\n:voice       Record up to 8 seconds from your microphone\n"
                            ":upload      Choose an image/file path to attach\n"
                            ":screen TEXT Capture all monitors and ask DeepSeek\n"
                            ":page TEXT   Capture the browser viewport and ask DeepSeek\n"
                            ":new         Start a fresh DeepSeek chat\n"
                            ":image PATH  Attach an image by path\n"
                            "exit         Close this terminal session"
                        )
                        continue
                    if command in {":new", "new"}:
                        start_new_chat(page)
                        pending_image = None
                        print("Started a new chat.")
                        continue
                    if command in {":voice", "voice"}:
                        value = listen_for_voice()
                        print(f"You (voice)> {value}")
                    elif command in {":upload", "upload"}:
                        argument = input("Upload path> ").strip()
                        if not argument:
                            print("No file selected.")
                            continue
                        pending_image = argument
                        print(f"File ready: {Path(argument.strip(chr(34))).expanduser()}")
                        continue
                    elif command in {":image", "image"}:
                        if not argument:
                            print("Usage: :image C:\\path\\to\\image.png")
                            continue
                        pending_image = argument
                        print(f"Image ready: {Path(argument).expanduser()}")
                        continue
                    elif command in {":screen", "screen", ":page", "page"}:
                        image_path = (
                            capture_screen()
                            if command.lstrip(":") == "screen"
                            else capture_page(page)
                        )
                        try:
                            question = argument or (
                                "Describe what is visible and tell me what I should do next."
                            )
                            reply = send_prompt(page, question, str(image_path))
                            print(f"DeepSeek> {reply}")
                        finally:
                            image_path.unlink(missing_ok=True)
                        continue
                    elif command in {":send", "send"} and pending_image:
                        value = argument
                    elif command.startswith(":"):
                        print("Unknown command. Use :voice, :new, or :image <path>.")
                        continue

                    reply = send_prompt(page, value, pending_image)
                    pending_image = None
                    print(f"DeepSeek> {reply}")
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
        finally:
            if owns_context:
                context.close()
            else:
                page.bring_to_front()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
