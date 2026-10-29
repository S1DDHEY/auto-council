"""Send prompts to DeepSeek from a VS Code terminal.

By default this connects to a normal Chrome window through Chrome DevTools.
The browser must be started with the debugging command shown by --help.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

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
    """Return the site's prompt editor, or raise a useful error."""
    for selector in PROMPT_SELECTORS:
        locator = page.locator(selector).first
        if locator.count() and locator.is_visible():
            return locator
    raise RuntimeError(
        "Could not find DeepSeek's message box. The site may have changed; "
        "refresh the page and try again."
    )


def assistant_text(page: Page) -> str:
    """Read the latest assistant message using stable selectors when possible."""
    for selector in ASSISTANT_SELECTORS:
        messages = page.locator(selector)
        if messages.count():
            text = messages.last.inner_text().strip()
            if text:
                return text
    return ""


def wait_for_reply(page: Page, previous_reply: str) -> str:
    """Wait until a new, non-empty reply has finished rendering."""
    try:
        page.wait_for_function(
            """previous => {
                const selectors = [
                    '[data-message-author-role="assistant"]',
                    '[data-testid*="assistant"]',
                    '.ds-markdown'
                ];
                for (const selector of selectors) {
                    const nodes = document.querySelectorAll(selector);
                    if (nodes.length) {
                        const text = nodes[nodes.length - 1].innerText.trim();
                        if (text && text !== previous) return true;
                    }
                }
                return false;
            }""",
            arg=previous_reply,
            timeout=120_000,
        )
    except PlaywrightTimeoutError as exc:
        raise RuntimeError(
            "DeepSeek did not return a reply within 120 seconds. "
            "Check the browser for a CAPTCHA, login prompt, or network error."
        ) from exc

    # Streaming can continue briefly after the first text appears.
    page.wait_for_timeout(800)
    return assistant_text(page)


def send_prompt(page: Page, prompt: str) -> str:
    editor = find_prompt_box(page)
    previous_reply = assistant_text(page)
    editor.fill(prompt)
    editor.press("Enter")
    return wait_for_reply(page, previous_reply)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Interactive DeepSeek terminal assistant."
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        help="Send one prompt and exit. Omit it for interactive mode.",
    )
    parser.add_argument(
        "--profile",
        action="store_true",
        help="Use the script's separate browser profile instead of normal Chrome.",
    )
    return parser.parse_args()


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
                str(PROFILE_DIR),
                headless=False,
                viewport={"width": 1280, "height": 900},
            ),
            True,
        )

    context = connect_to_chrome(playwright)
    if context is not None:
        return context, False

    if not CHROME_PATH.exists():
        raise RuntimeError(
            f"Chrome was not found at {CHROME_PATH}.\n"
            "Use `python main.py --profile` if you prefer the script-managed profile."
        )

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

    raise RuntimeError(
        "Chrome started, but its debugging connection did not become available. "
        "Close Chrome completely and run this script again. "
        f"Chrome's automation profile is {CHROME_AUTOMATION_PROFILE}."
    )


def main() -> int:
    args = parse_args()
    with sync_playwright() as playwright:
        context, owns_context = open_browser(playwright, args.profile)
        # Always use a new tab so existing Chrome tabs and conversations are
        # never changed.
        page = context.new_page()
        page.bring_to_front()
        page.goto(SITE_URL, wait_until="domcontentloaded")

        print("Opened a new DeepSeek tab in Chrome.")
        print("If required, sign in in the browser.")
        print("Waiting for the DeepSeek message box...")
        try:
            page.wait_for_selector("textarea, [contenteditable='true']", timeout=120_000)
        except PlaywrightTimeoutError as exc:
            if owns_context:
                context.close()
            page_title = page.title()
            raise RuntimeError(
                "The DeepSeek message box did not become available "
                f"(page title: {page_title!r}). "
                "Check the new Chrome tab for sign-in, CAPTCHA, or a network "
                "error, then run the script again."
            ) from exc

        interactive = args.prompt is None
        if interactive:
            print("\nReady. Type your prompt and press Enter.")
            print("Type exit, quit, or :q to stop.")

        prompts = [args.prompt] if args.prompt else None
        try:
            while prompts is None or prompts:
                prompt = (
                    prompts.pop(0)
                    if prompts is not None
                    else input("\nYou> ").strip()
                )
                if not prompt:
                    if prompts is not None:
                        break
                    continue
                if prompt.lower() in {"exit", "quit", ":q"}:
                    break

                print("DeepSeek is thinking...\n")
                reply = send_prompt(page, prompt)
                if not reply:
                    raise RuntimeError(
                        "A reply was submitted, but its text could not be read."
                    )
                print(f"DeepSeek> {reply}")
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
        finally:
            if owns_context:
                context.close()
            else:
                # The new tab is intentionally left open in the user's Chrome.
                page.bring_to_front()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)