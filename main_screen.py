"""Consent-based screen and browser assistant for DeepSeek.

The assistant captures an image only after an explicit ``:screen`` or
``:page`` command. It sends that image to the DeepSeek web app already open
in Chrome; it does not upload screenshots in the background or execute
computer actions on its own.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PIL import ImageGrab
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


SITE_URL = "https://chat.deepseek.com/"
# Reuse the profile where the existing DeepSeek assistant stores its login.
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
        box = page.locator(selector).first
        if box.count() and box.is_visible():
            return box
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
    stable_since = 0.0
    while time.monotonic() < deadline:
        current = assistant_text(page)
        if current and current != previous_reply:
            if current != latest:
                latest = current
                stable_since = time.monotonic()
            elif time.monotonic() - stable_since >= 4:
                return latest
        page.wait_for_timeout(1_000)
    raise RuntimeError("DeepSeek did not finish the reply within 180 seconds.")


def attach_image(page: Page, image_path: Path) -> None:
    inputs = page.locator('input[type="file"]')
    if inputs.count():
        inputs.first.set_input_files(str(image_path))
        return
    for selector in (
        'button[aria-label*="attach" i]',
        'button[title*="attach" i]',
        '[data-testid*="attach" i]',
    ):
        button = page.locator(selector).first
        if button.count() and button.is_visible():
            try:
                with page.expect_file_chooser(timeout=3_000) as chooser_info:
                    button.click()
                chooser_info.value.set_files(str(image_path))
                return
            except PlaywrightTimeoutError:
                continue
    raise RuntimeError("DeepSeek's image attachment control was not found.")


def send_prompt(page: Page, prompt: str, image_path: Path | None = None) -> str:
    if image_path is not None:
        attach_image(page, image_path)
        page.wait_for_timeout(500)
    previous_reply = assistant_text(page)
    editor = find_prompt_box(page)
    editor.fill(prompt)
    editor.press("Enter")
    return wait_for_reply(page, previous_reply)


def capture_screen() -> Path:
    """Capture all visible monitors into a temporary PNG owned by this run."""
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


def load_deepseek_page(context) -> Page:
    page = context.new_page()
    page.bring_to_front()
    try:
        page.goto(SITE_URL, wait_until="domcontentloaded", timeout=60_000)
    except PlaywrightTimeoutError:
        if "deepseek.com" not in page.url:
            raise RuntimeError("DeepSeek did not finish loading.")
    try:
        page.wait_for_selector("textarea, [contenteditable='true']", timeout=120_000)
    except PlaywrightTimeoutError as exc:
        raise RuntimeError(
            "DeepSeek's message box is unavailable. Sign in or complete any CAPTCHA."
        ) from exc
    return page


def run_command(page: Page, command: str) -> bool:
    """Run one interactive command. Return False when the session should end."""
    value = command.strip()
    if not value:
        return True
    if value.lower() in {"exit", "quit", ":q"}:
        return False
    if value.lower() in {":help", "help"}:
        print(
            "\n:screen [question]  Capture all monitors and ask DeepSeek\n"
            ":page [question]    Capture the current browser viewport\n"
            ":watch N question  Capture the screen every N seconds (Ctrl+C to stop)\n"
            ":new                Open a fresh DeepSeek conversation\n"
            "exit                Close this session\n"
        )
        return True
    if value.lower() == ":new":
        page.goto(SITE_URL, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_selector("textarea, [contenteditable='true']", timeout=120_000)
        print("Started a new DeepSeek chat.")
        return True

    command_name, _, prompt = value.partition(" ")
    command_name = command_name.lower()
    if command_name not in {":screen", ":page", ":watch"}:
        prompt = value
        image_path = None
    elif command_name == ":watch":
        interval_text, _, watch_prompt = prompt.partition(" ")
        try:
            interval = max(5, min(300, int(interval_text)))
        except ValueError as exc:
            raise RuntimeError("Usage: :watch 10 describe the screen") from exc
        print("Screen watch started; press Ctrl+C to stop.")
        try:
            while True:
                image_path = capture_screen()
                try:
                    reply = send_prompt(
                        page,
                        watch_prompt or "Describe important changes on this screen.",
                        image_path,
                    )
                    print(f"\nDeepSeek> {reply}")
                finally:
                    image_path.unlink(missing_ok=True)
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\nScreen watch stopped.")
        return True
    else:
        image_path = capture_screen() if command_name == ":screen" else capture_page(page)
        prompt = prompt or "Describe what is visible and tell me what I should do next."

    try:
        print("Sending the requested image to DeepSeek...")
        print(f"\nDeepSeek> {send_prompt(page, prompt, image_path)}")
    finally:
        if image_path is not None:
            image_path.unlink(missing_ok=True)
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Consent-based DeepSeek screen assistant.")
    parser.add_argument("prompt", nargs="?", help="Ask DeepSeek about the screen once.")
    parser.add_argument("--profile", action="store_true", help="Use a separate persistent Chrome profile.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    with sync_playwright() as playwright:
        context, owns_context = open_browser(playwright, args.profile)
        try:
            page = load_deepseek_page(context)
            print(
                "DeepSeek screen assistant ready.\n"
                "Screenshots are captured only for :screen, :page, or :watch.\n"
                "Use :help for commands."
            )
            if args.prompt:
                run_command(page, ":screen " + args.prompt)
            else:
                while run_command(page, input("\nYou> ")):
                    pass
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
        finally:
            if owns_context:
                context.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
