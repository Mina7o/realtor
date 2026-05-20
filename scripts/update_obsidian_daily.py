"""Auto-generate daily Obsidian note from git log + project state.
Run at 5 PM daily via cron."""
import datetime
import os
import subprocess
import sys
from pathlib import Path

OBSIDIAN_DAILY = Path(os.path.expanduser("~/Documents/obsidian-vault/Daily"))
PROJECT_DIR = Path(os.path.expanduser("~/Documents/proj/realtor"))


def get_today_git_log():
    today = datetime.date.today().isoformat()
    result = subprocess.run(
        ["git", "log", f"--after={today}T00:00:00", f"--before={today}T23:59:59",
         "--oneline", "--stat"],
        capture_output=True, text=True, cwd=PROJECT_DIR
    )
    return result.stdout.strip()


def get_uncommitted_files():
    result = subprocess.run(
        ["git", "status", "--short"],
        capture_output=True, text=True, cwd=PROJECT_DIR
    )
    return result.stdout.strip()


def main():
    today = datetime.date.today().isoformat()
    filepath = OBSIDIAN_DAILY / f"{today}.md"

    git_log = get_today_git_log()
    uncommitted = get_uncommitted_files()

    lines = [f"# {today}", "", "## Realtor", "", "### Auto-Summary"]
    if git_log:
        lines.append("")
        lines.append("**Committed today:**")
        lines.append("```")
        lines.append(git_log)
        lines.append("```")
    if uncommitted:
        lines.append("")
        lines.append("**Uncommitted changes:**")
        lines.append("```")
        lines.append(uncommitted)
        lines.append("```")
    if not git_log and not uncommitted:
        lines.append("")
        lines.append("No git activity detected today.")

    lines.append("")

    if filepath.exists():
        with open(filepath) as f:
            existing = f.read()
        if "## Auto-Summary" not in existing:
            with open(filepath, "a") as f:
                f.write("\n" + "\n".join(lines[2:]) + "\n")
        print(f"Appended to {filepath}")
    else:
        with open(filepath, "w") as f:
            f.write("\n".join(lines) + "\n")
        print(f"Created {filepath}")


if __name__ == "__main__":
    main()
