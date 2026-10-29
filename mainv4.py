"""Time-boxed finance, mathematics, physics, and coding research agent.

The agent deliberately uses short, independent turns instead of one giant
prompt. This keeps the working context bounded while preserving every result
in a local session folder.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from queue import Empty, Queue
from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

import mainv2


OUTPUT_DIR = Path(__file__).with_name("agent_runs")
MAX_CONTEXT_CHARS = 28_000
SPECIALIZATIONS = {
    "1": (
        "Quantitative Finance",
        "portfolio construction, derivatives, time series, risk, market microstructure, "
        "backtesting, and careful separation of assumptions from financial advice",
    ),
    "2": (
        "Mathematical Research",
        "proofs, numerical analysis, optimization, probability, statistics, and symbolic reasoning",
    ),
    "3": (
        "Theoretical Physics",
        "classical mechanics, electromagnetism, quantum ideas, relativity, and dimensional analysis",
    ),
    "4": (
        "Scientific Computing",
        "stable algorithms, simulation, error analysis, reproducibility, and performance",
    ),
    "5": (
        "Senior Coding Agent",
        "architecture, implementation, tests, debugging, security, and maintainable production code",
    ),
    "6": (
        "AI and Machine Learning",
        "model design, evaluation, data leakage, uncertainty, optimization, and deployment",
    ),
    "7": (
        "Data Engineering",
        "data modeling, pipelines, validation, provenance, observability, and scalable systems",
    ),
    "8": (
        "Security Engineering",
        "threat modeling, secure design, privacy, abuse cases, and defensive verification",
    ),
    "9": (
        "Critical Reviewer",
        "find hidden assumptions, counterexamples, contradictions, unsupported claims, and edge cases",
    ),
    "10": (
        "Research Strategist",
        "decompose ambiguous problems, rank experiments, identify unknowns, and plan efficient progress",
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a time-boxed iterative expert agent in DeepSeek."
    )
    parser.add_argument("--minutes", type=float, default=30)
    parser.add_argument(
        "--agents",
        type=int,
        default=4,
        help="Number of agents to run (1-10).",
    )
    parser.add_argument("--focus", choices=sorted(SPECIALIZATIONS), default=None)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--max-context-chars", type=int, default=MAX_CONTEXT_CHARS)
    return parser.parse_args()


def print_specializations() -> None:
    print("\nChoose an expert focus:")
    for key, (name, skills) in SPECIALIZATIONS.items():
        print(f"  {key}: {name} - {skills}")


def ask_focus(label: str, excluded: set[str] | None = None) -> tuple[str, str]:
    print_specializations()
    while True:
        selected = input(f"{label} focus number> ").strip()
        if selected in SPECIALIZATIONS and selected not in (excluded or set()):
            return SPECIALIZATIONS[selected]
        print("Choose a valid focus not already assigned.")


def open_page(context):
    page = context.new_page()
    page.bring_to_front()
    try:
        page.goto(mainv2.SITE_URL, wait_until="domcontentloaded", timeout=60_000)
    except PlaywrightTimeoutError:
        if "deepseek.com" not in page.url:
            raise RuntimeError("DeepSeek did not finish loading.")
    page.wait_for_selector("textarea, [contenteditable='true']", timeout=120_000)
    return page


def compact_context(task: str, best: str, recent: list[str], limit: int) -> str:
    text = (
        f"USER TASK:\n{task}\n\n"
        f"CURRENT BEST WORK:\n{best}\n\n"
        "RECENT REVIEW NOTES:\n" + "\n\n".join(recent)
    )
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n[Older context compacted to stay within the context budget.]"


def make_prompt(
    task: str,
    specialization: str,
    skills: str,
    phase: str,
    other_role: str,
    other_response: str,
    current_best: str,
    limit: int,
) -> str:
    context = compact_context(
        task, current_best, [f"{other_role}:\n{other_response}"], limit
    )
    return f"""You are an expert iterative problem-solving agent.
Primary role: {specialization}
Relevant skills: {skills}
Current phase: {phase}

Work rigorously. State assumptions, units, definitions, and uncertainty.
For finance, do not claim guaranteed returns and distinguish research from advice.
For mathematics and physics, check dimensions, limiting cases, and derivations.
For code, provide executable, tested, secure, maintainable solutions.
Do not merely praise earlier work: improve it or explain a concrete correction.
You are one half of a live expert research dialogue. Directly critique the
other agent's latest work and return an improved, self-contained artifact
for the next turn. Do not stop at agreement.

{context}
"""


def save_run(run_dir: Path, records: list[dict], final_text: str, metadata: dict) -> None:
    (run_dir / "transcript.jsonl").write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    (run_dir / "final.md").write_text(
        "# Iterative Agent Result\n\n"
        f"Task: {metadata['task']}\n\n"
        f"Agents: {', '.join(metadata.get('agents', []))}\n\n"
        f"Agent turns: {metadata.get('turns', 0)}\n\n"
        "## Final artifact\n\n"
        f"{final_text}\n",
        encoding="utf-8",
    )
    (run_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def start_control_reader(commands: Queue[str]) -> None:
    def read_commands() -> None:
        while True:
            try:
                line = input(
                    "\nControl (:pause | :resume | :prompt TEXT | :voice | "
                    ":upload | :status | :stop)> "
                ).strip()
            except (EOFError, KeyboardInterrupt):
                commands.put(":stop")
                return
            if line:
                commands.put(line)

    threading.Thread(target=read_commands, daemon=True).start()


def run_dialogue(
    pages: list,
    roles: list[tuple[str, str]],
    task: str,
    minutes: float,
    limit: int,
    run_dir: Path,
    initial_image: str | None = None,
) -> None:
    deadline = time.monotonic() + minutes * 60
    records: list[dict] = []
    commands: Queue[str] = Queue()
    start_control_reader(commands)
    paused = False
    stopped = False
    pending_image: str | None = initial_image
    image_sent: set[str] = set()
    print(
        "\nLive controls: :pause | :resume | :prompt TEXT | :voice | "
        ":upload [PATH] | :status | :stop"
    )
    best = "No solution has been produced yet."
    previous = [mainv2.assistant_text(page) for page in pages]
    responses = {
        role[0]: "No prior analysis. Establish assumptions and begin solving."
        for role in roles
    }
    turn = 0
    metadata = {
        "task": task,
        "agents": [role[0] for role in roles],
        "started_at": datetime.now().isoformat(),
        "minutes_budget": minutes,
        "turns": 0,
    }
    save_run(run_dir, records, best, metadata)

    while time.monotonic() < deadline:
        while True:
            try:
                command = commands.get_nowait()
            except Empty:
                break
            if command == ":pause":
                paused = True
                print("\nIterations paused at the next safe boundary.")
            elif command == ":resume":
                paused = False
                print("\nIterations resumed.")
            elif command == ":stop":
                stopped = True
                print("\nStopping after the current safe boundary.")
            elif command == ":status":
                remaining = max(0, int(deadline - time.monotonic()))
                print(
                    f"\nStatus: {'paused' if paused else 'running'}, "
                    f"turns={len(records)}, remaining_seconds={remaining}"
                )
            elif command == ":voice":
                print("\nListening for a new task...")
                task = mainv2.listen_for_voice()
                paused = True
                metadata["task"] = task
                image_sent.clear()
                print(
                    f"\nVoice task saved: {task}\n"
                    "Iterations are paused; use :resume when ready."
                )
            elif command.startswith(":upload "):
                image_path = command[8:].strip().strip('"')
                path = Path(image_path).expanduser().resolve()
                if not path.is_file():
                    print(f"\nImage file not found: {path}")
                else:
                    pending_image = str(path)
                    image_sent.clear()
                    print(f"\nImage queued for each agent: {path}")
            elif command == ":upload":
                image_path = input("Upload image path> ").strip().strip('"')
                path = Path(image_path).expanduser().resolve()
                if not path.is_file():
                    print(f"\nImage file not found: {path}")
                else:
                    pending_image = str(path)
                    image_sent.clear()
                    print(f"\nImage queued for each agent: {path}")
            elif command.startswith(":prompt "):
                new_task = command[8:].strip()
                if new_task:
                    task = new_task
                    paused = True
                    metadata["task"] = task
                    print(
                        "\nNew task saved. Iterations are paused; use :resume "
                        "when ready."
                    )
                else:
                    print("\nUsage: :prompt describe the new task")
            elif command.startswith(":"):
                print(
                    "\nUnknown control. Use :pause, :resume, :prompt TEXT, "
                    ":voice, :upload, :status, or :stop."
                )
        if stopped:
            break
        if paused:
            time.sleep(0.25)
            continue
        turn += 1
        phase = "derive, verify, challenge, and improve the current solution"
        for index, (role, skills) in enumerate(roles):
            if time.monotonic() >= deadline:
                break
            peer_text = "\n\n".join(
                f"{name}:\n{response}"
                for name, response in responses.items()
                if name != role
            )
            print(f"\n===== TURN {turn}: {role} =====")
            prompt = make_prompt(
                task, role, skills, phase, "the other three agents",
                peer_text, best, limit
            )
            image_for_turn = None
            if pending_image and role not in image_sent:
                image_for_turn = pending_image
            editor = mainv2.find_prompt_box(pages[index])
            if image_for_turn:
                mainv2.attach_image(pages[index], image_for_turn)
                image_sent.add(role)
            editor.fill(prompt)
            editor.press("Enter")
            response = mainv2.wait_for_reply(pages[index], previous[index])
            previous[index] = response
            responses[role] = response
            best = response
            records.append({"turn": turn, "agent": role, "response": response})
            print(f"{role}> {response}\n")
        metadata["turns"] = len(records)
        metadata["updated_at"] = datetime.now().isoformat()
        records.append(
            {"turn": turn, "checkpoint": True, "timestamp": datetime.now().isoformat()}
        )
        save_run(run_dir, records, best, metadata)
        print(f"\nSaved checkpoint: {run_dir}")
    metadata["finished_at"] = datetime.now().isoformat()
    save_run(run_dir, records, best, metadata)
    print(f"\nFinished after {len(records)} agent turns.")
    print(f"Final result: {run_dir / 'final.md'}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    if args.minutes <= 0 or args.minutes > 240:
        raise RuntimeError("--minutes must be greater than 0 and no more than 240.")
    if not 1 <= args.agents <= len(SPECIALIZATIONS):
        raise RuntimeError(
            f"--agents must be between 1 and {len(SPECIALIZATIONS)}."
        )
    selected: set[str] = set()
    roles: list[tuple[str, str]] = []
    for index in range(args.agents):
        if index == 0 and args.focus:
            key = args.focus
            role = SPECIALIZATIONS[key]
        else:
            role = ask_focus(f"Agent {chr(65 + index)}", selected)
            key = next(k for k, value in SPECIALIZATIONS.items() if value == role)
        selected.add(key)
        roles.append(role)
    print("\nInitial task input: type text, or use :voice / :upload.")
    task = input("Task for all agents> ").strip()
    initial_image = None
    if task == ":voice":
        task = mainv2.listen_for_voice()
    elif task == ":upload":
        image_path = input("Upload image path> ").strip().strip('"')
        path = Path(image_path).expanduser().resolve()
        if not path.is_file():
            raise RuntimeError(f"Image file not found: {path}")
        task = input("Task for all agents> ").strip()
        initial_image = str(path)
    if not task:
        raise RuntimeError("A task is required.")
    run_dir = OUTPUT_DIR / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        context, owns_context = mainv2.open_browser(playwright, args.profile)
        pages = [open_page(context) for _ in range(args.agents)]
        print(
            f"\n{args.agents} DeepSeek agent(s) are ready. Working for up to "
            f"{args.minutes:g} minutes."
        )
        try:
            run_dialogue(
                pages,
                roles,
                task,
                args.minutes,
                args.max_context_chars,
                run_dir,
                initial_image,
            )
        except KeyboardInterrupt:
            print("\nStopped by user; the latest checkpoint was already saved.")
        finally:
            if owns_context:
                context.close()
            else:
                pages[0].bring_to_front()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
