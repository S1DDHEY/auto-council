"""Two-agent DeepSeek debate room with 100 selectable agent modes."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

import mainv2


AGENT_MODES = [
    "Software Architect", "Senior Python Developer", "Frontend Developer",
    "Backend Developer", "Full Stack Developer", "Mobile Developer",
    "DevOps Engineer", "Cloud Architect", "Security Engineer",
    "Data Engineer", "Machine Learning Engineer", "AI Researcher",
    "Data Scientist", "Database Administrator", "QA Engineer",
    "Test Automation Engineer", "Site Reliability Engineer", "Network Engineer",
    "Systems Programmer", "Embedded Systems Engineer", "Game Developer",
    "Blockchain Developer", "API Designer", "UI/UX Designer",
    "Product Designer", "Technical Writer", "Code Reviewer",
    "Performance Engineer", "Reliability Engineer", "Release Manager",
    "Project Manager", "Product Manager", "Scrum Master", "Business Analyst",
    "Requirements Analyst", "Solutions Consultant", "Technical Support Lead",
    "Research Scientist", "Statistician", "Economist", "Financial Analyst",
    "Investment Strategist", "Risk Analyst", "Operations Researcher",
    "Marketing Strategist", "Growth Strategist", "Brand Strategist",
    "Content Strategist", "SEO Specialist", "Copywriter", "Editor",
    "Journalist", "Fact Checker", "Teacher", "Tutor",
    "Curriculum Designer", "Career Coach", "Interview Coach",
    "Executive Coach", "Communication Coach", "Debate Coach", "Philosopher",
    "Ethicist", "Historian", "Political Analyst", "Legal Researcher",
    "Policy Analyst", "Public Relations Advisor", "Negotiation Advisor",
    "Leadership Advisor", "Psychology Researcher", "Sociology Researcher",
    "Medical Research Assistant", "Nutrition Research Assistant",
    "Fitness Coach", "Travel Planner", "Research Librarian",
    "Creative Writer", "Story Editor", "Screenwriter", "Game Narrative Designer",
    "Brainstorming Facilitator", "Innovation Consultant", "Design Thinker",
    "Critical Thinker", "Skeptical Reviewer", "Devil's Advocate",
    "Scientific Reviewer", "Decision Analyst", "Planning Specialist",
    "Productivity Coach", "Personal Assistant", "Documentation Engineer",
    "Open Source Maintainer", "Technical Interviewer", "Code Mentor",
    "Startup Advisor", "Entrepreneurship Mentor", "General Problem Solver",
    "Quality Auditor",
]

def choose_role(label: str) -> str:
    print(f"\nChoose {label} from 100 agent modes:")
    for start in range(0, len(AGENT_MODES), 5):
        options = AGENT_MODES[start:start + 5]
        print("  " + " | ".join(
            f"{start + index + 1}: {name}" for index, name in enumerate(options)
        ))
    while True:
        selected = input(f"{label} number> ").strip()
        try:
            index = int(selected) - 1
        except ValueError:
            index = -1
        if 0 <= index < len(AGENT_MODES):
            return AGENT_MODES[index]
        print("Enter a number from 1 to 100.")


def open_agent_tab(context, role: str):
    page = context.new_page()
    page.bring_to_front()
    try:
        page.goto(mainv2.SITE_URL, wait_until="domcontentloaded", timeout=60_000)
    except PlaywrightTimeoutError:
        if "deepseek.com" not in page.url:
            raise RuntimeError(f"DeepSeek did not load for {role}.")
    page.wait_for_selector(
        "textarea, [contenteditable='true']", timeout=120_000
    )
    return page


def ask_agent(page, role: str, message: str, previous_reply: str) -> str:
    editor = mainv2.find_prompt_box(page)
    editor.fill(
        f"You are acting as the {role}. Stay in this role. "
        "Give a useful, reasoned answer and respond directly to the other agent "
        "when their message is included below.\n\n"
        f"{message}"
    )
    editor.press("Enter")
    return mainv2.wait_for_reply(page, previous_reply)


def run_conversation(
    page_a, role_a: str, page_b, role_b: str, topic: str, rounds: int | None
) -> None:
    print(f"\nConversation: {role_a} <-> {role_b}")
    print(f"Topic: {topic}\n")
    previous_a = mainv2.assistant_text(page_a)
    previous_b = mainv2.assistant_text(page_b)
    message_a = f"The user asks: {topic}\nGive your opening analysis."

    round_number = 1
    while rounds is None or round_number <= rounds:
        print(f"\n===== ROUND {round_number}: {role_a} =====")
        response_a = ask_agent(page_a, role_a, message_a, previous_a)
        previous_a = response_a
        print(f"{role_a}> {response_a}\n")

        message_b = (
            f"The user asks: {topic}\n"
            f"{role_a} said:\n{response_a}\n\n"
            "Analyze this and add, correct, or challenge it."
        )
        print(f"===== ROUND {round_number}: {role_b} =====")
        response_b = ask_agent(page_b, role_b, message_b, previous_b)
        previous_b = response_b
        print(f"{role_b}> {response_b}\n")
        message_a = (
            f"The user asks: {topic}\n"
            f"{role_b} said:\n{response_b}\n\n"
            "Continue the discussion and respond to the other agent."
        )
        round_number += 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Open two DeepSeek tabs and run an ongoing two-agent conversation."
    )
    parser.add_argument("--profile", action="store_true")
    parser.add_argument(
        "--rounds",
        type=int,
        default=None,
        help="Optional limit for testing. Without it, continue until Ctrl+C.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.rounds is not None and not 1 <= args.rounds <= 100:
        raise RuntimeError("Rounds must be between 1 and 100.")
    with sync_playwright() as playwright:
        context, owns_context = mainv2.open_browser(playwright, args.profile)
        try:
            role_a = choose_role("Agent A")
            role_b = choose_role("Agent B")
            topic = input("\nTopic or prompt> ").strip()
            if not topic:
                raise RuntimeError("A topic or prompt is required.")
            page_a = open_agent_tab(context, role_a)
            page_b = open_agent_tab(context, role_b)
            print("\nBoth DeepSeek tabs are ready.")
            if args.rounds is None:
                print("Continuous mode is active. Press Ctrl+C to stop.")
            run_conversation(page_a, role_a, page_b, role_b, topic, args.rounds)
            print("\nConversation stopped. Both tabs remain open in Chrome.")
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
        finally:
            if owns_context:
                context.close()
            elif "page_a" in locals():
                page_a.bring_to_front()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
