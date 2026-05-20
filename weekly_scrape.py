"""Weekly scrape orchestrator: run every Monday to fetch new listings."""
import datetime
import subprocess
import sys
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_PYTHON = os.path.join(SCRIPT_DIR, "venv", "bin", "python3")
PYTHON = VENV_PYTHON if os.path.exists(VENV_PYTHON) else sys.executable


def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")
    sys.stdout.flush()


def run(cmd, label):
    log(f"Starting: {label}")
    log(f"  Command: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=SCRIPT_DIR)
    for line in result.stdout.strip().split("\n"):
        if line.strip():
            log(f"  {line.strip()}")
    if result.returncode != 0:
        log(f"  FAILED (exit {result.returncode})")
        for line in result.stderr.strip().split("\n"):
            if line.strip():
                log(f"  STDERR: {line.strip()}")
    else:
        if result.stderr.strip():
            for line in result.stderr.strip().split("\n"):
                if line.strip():
                    log(f"  STDERR: {line.strip()}")
        log(f"  OK")
    return result.returncode


def main():
    log("=" * 60)
    log("WEEKLY SCRAPE STARTED")
    log("=" * 60)

    errors = 0

    # 1. Zillow Fort Mill (3 pages)
    c = run([
        PYTHON, "fetch_zillow_crawl4ai.py",
        "--city", "Fort Mill", "--state", "SC",
        "--max-pages", "3", "--db",
    ], "Zillow Fort Mill")
    if c != 0:
        errors += 1

    # 2. Zillow Weddington (3 pages)
    c = run([
        PYTHON, "fetch_zillow_crawl4ai.py",
        "--city", "Weddington", "--state", "NC",
        "--max-pages", "3", "--db",
    ], "Zillow Weddington")
    if c != 0:
        errors += 1

    # 3. Zillow Waxhaw (3 pages)
    c = run([
        PYTHON, "fetch_zillow_crawl4ai.py",
        "--city", "Waxhaw", "--state", "NC",
        "--max-pages", "3", "--db",
    ], "Zillow Waxhaw")
    if c != 0:
        errors += 1

    # 4. Zillow Indian Trail (3 pages)
    c = run([
        PYTHON, "fetch_zillow_crawl4ai.py",
        "--city", "Indian Trail", "--state", "NC",
        "--max-pages", "3", "--db",
    ], "Zillow Indian Trail")
    if c != 0:
        errors += 1

    # 5. Zillow Monroe (3 pages)
    c = run([
        PYTHON, "fetch_zillow_crawl4ai.py",
        "--city", "Monroe", "--state", "NC",
        "--max-pages", "3", "--db",
    ], "Zillow Monroe")
    if c != 0:
        errors += 1

    # 6. FSBO
    c = run([
        PYTHON, "fetch_fsbo.py",
        "--db",
    ], "FSBO")
    if c != 0:
        errors += 1

    # 7. Find deals
    c = run([
        PYTHON, "find_deals.py",
        "--json", "weekly_deals.json",
    ], "Find deals")
    if c != 0:
        errors += 1

    log("=" * 60)
    if errors:
        log(f"WEEKLY SCRAPE FINISHED with {errors} error(s)")
    else:
        log("WEEKLY SCRAPE COMPLETED SUCCESSFULLY")
    log("=" * 60)
    return errors


if __name__ == "__main__":
    sys.exit(main())
