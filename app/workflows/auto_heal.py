"""Auto-heal watchdog: monitors Loki for errors, diagnoses by HTTP status code,
applies trace-driven backoff (BLOCKED/RETRYABLE/FATAL), and invokes opencode
for issues requiring human review.
Designed to run as a cron job every 15 minutes."""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import urllib.parse
import urllib.request
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from logger_setup import setup_logging
from loguru import logger
from otel_utils import init_otel

setup_logging("auto_heal")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
LOKI_URL = "http://localhost:3100/loki/api/v1/query_range"
GRAFANA_URL = "http://localhost:3000"
OBSIDIAN_VAULT = Path("/home/euclid/Documents/obsidian-vault")
GRAPHIFY_GRAPH = BASE_DIR / "graphify-out" / "graph.json"
OPECODE = os.path.expanduser("~/.opencode/bin/opencode")
VENV_PYTHON = BASE_DIR / "venv" / "bin" / "python3"
PYTHON = VENV_PYTHON if VENV_PYTHON.exists() else sys.executable

STATE_FILE = BASE_DIR / "data" / "auto_heal_state.json"
DEAD_PROPERTIES_FILE = BASE_DIR / "data" / "dead_properties.json"
MAX_RETRIES = 3
BACKOFF_MULTIPLIER = 2
INITIAL_BACKOFF_HOURS = 1
MAX_BACKOFF_HOURS = 24

HEAL_LOG = []


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str))


def load_dead_properties():
    if DEAD_PROPERTIES_FILE.exists():
        try:
            return json.loads(DEAD_PROPERTIES_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return []
    return []


def save_dead_properties(entries):
    DEAD_PROPERTIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    DEAD_PROPERTIES_FILE.write_text(json.dumps(entries, indent=2))


def loki_query(query, limit=50):
    """Run a LogQL query against Loki and return results."""
    params = urllib.parse.urlencode({
        "query": query,
        "limit": limit,
        "start": int((datetime.now(timezone.utc) - timedelta(minutes=30)).timestamp()) * 1_000_000_000,
        "end": int(datetime.now(timezone.utc).timestamp()) * 1_000_000_000,
    })
    url = f"{LOKI_URL}?{params}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError) as e:
        logger.error(f"Loki query failed: {e}")
        return None


def get_recent_errors():
    """Fetch recent ERROR-level logs grouped by module."""
    result = loki_query('{job="realtor"} | level="ERROR"')
    if not result:
        return []
    streams = result.get("data", {}).get("result", [])
    errors_by_module = {}
    for stream in streams:
        labels = stream.get("stream", stream.get("metric", {}))
        module = labels.get("module", "unknown")
        values = stream.get("values", [])
        if not values:
            continue
        count = len(values)
        latest = values[-1][1] if values else ""
        filename = labels.get("filename", "")
        errors_by_module[module] = {
            "module": module,
            "filename": filename,
            "count": count,
            "latest": latest,
        }
    return list(errors_by_module.values())


def get_error_details(module_filter=None):
    """Fetch full error log lines for diagnosis."""
    query = '{job="realtor"} | level="ERROR"'
    if module_filter:
        query += f' |~ "{module_filter}"'
    result = loki_query(query, limit=20)
    if not result:
        return []
    lines = []
    for stream_obj in result.get("data", {}).get("result", []):
        for ts, msg in stream_obj.get("values", []):
            lines.append(msg)
    return lines


def parse_http_status(text):
    """
    Scan text for HTTP status indicators.
    Returns (status_code, decision_type) or (None, None).
    """
    if not text:
        return None, None

    text_lower = text.lower()

    if re.search(r'\b403\b', text) or 'forbidden' in text_lower or 'waf' in text_lower:
        return 403, "BLOCKED"
    if re.search(r'\b500\b', text) or re.search(r'\b502\b', text) or re.search(r'\b503\b', text) or 'server error' in text_lower or 'internal server error' in text_lower:
        return 500, "RETRYABLE"
    if 'timeout' in text_lower or 'timed out' in text_lower:
        return 0, "RETRYABLE"
    if re.search(r'\b404\b', text) or 'not found' in text_lower:
        return 404, "FATAL"

    return None, None


def extract_urls(text):
    """Extract any http/https URLs from text."""
    return re.findall(r'https?://[^\s"\'<>]+', text)


def mark_property_dead(module, error_text):
    """Extract URL from error and mark it as dead."""
    urls = extract_urls(error_text)
    if not urls:
        logger.info(f"[HEAL_DECISION: FATAL] No URL found in error for {module}, skipping dead mark")
        return

    dead = load_dead_properties()
    now = datetime.now(timezone.utc).isoformat()
    new_entries = []
    for url in urls:
        entry = {"url": url, "source": module, "detected": now}
        if entry not in dead:
            dead.append(entry)
            new_entries.append(entry)
    save_dead_properties(dead)
    for e in new_entries:
        logger.info(f"[HEAL_DECISION: FATAL] Marked dead property: {e['url']} (source: {module})")


def decision_matrix(module, status_code, decision_type, error_text, state):
    """
    Determine the action for a given error.
    Returns (decision, action_dict).
    """
    module_state = state.get(module, {})
    decision = module_state.get("decision")
    now = datetime.now(timezone.utc)

    if decision == "BLOCKED":
        next_retry = module_state.get("next_retry_after")
        if next_retry:
            try:
                retry_time = datetime.fromisoformat(next_retry)
                if now < retry_time:
                    return "BLOCKED", {
                        "action": "stand_down",
                        "reason": f"backoff active until {next_retry}",
                        "retry_count": module_state.get("retry_count", 0),
                        "backoff_hours": module_state.get("backoff_hours", 1),
                    }
            except ValueError:
                pass
        decision = None

    if decision == "FATAL":
        return "FATAL", {"action": "skip", "reason": "previously marked fatal"}

    if decision_type == "BLOCKED":
        current_backoff = module_state.get("backoff_hours", INITIAL_BACKOFF_HOURS)
        next_backoff = min(current_backoff * BACKOFF_MULTIPLIER, MAX_BACKOFF_HOURS)
        next_retry = (now + timedelta(hours=current_backoff)).isoformat()
        state[module] = {
            "decision": "BLOCKED",
            "status_code": status_code,
            "retry_count": module_state.get("retry_count", 0),
            "backoff_hours": next_backoff,
            "last_seen": now.isoformat(),
            "next_retry_after": next_retry,
        }
        return "BLOCKED", {
            "action": "stand_down",
            "reason": f"{status_code or 'WAF'} block detected",
            "backoff_hours": current_backoff,
            "next_retry": next_retry,
        }

    if decision_type == "RETRYABLE":
        retry_count = module_state.get("retry_count", 0) + 1
        state[module] = {
            "decision": "RETRYABLE",
            "status_code": status_code,
            "retry_count": retry_count,
            "backoff_hours": module_state.get("backoff_hours", INITIAL_BACKOFF_HOURS),
            "last_seen": now.isoformat(),
            "next_retry_after": None,
        }
        if retry_count <= MAX_RETRIES:
            return "RETRYABLE", {
                "action": "retry",
                "retry_count": retry_count,
                "max_retries": MAX_RETRIES,
                "reason": f"{status_code or 'timeout'} — attempt {retry_count}/{MAX_RETRIES}",
            }
        else:
            current_backoff = module_state.get("backoff_hours", INITIAL_BACKOFF_HOURS)
            next_backoff = min(current_backoff * BACKOFF_MULTIPLIER, MAX_BACKOFF_HOURS)
            next_retry = (now + timedelta(hours=current_backoff)).isoformat()
            state[module] = {
                "decision": "BLOCKED",
                "status_code": status_code,
                "retry_count": retry_count,
                "backoff_hours": next_backoff,
                "last_seen": now.isoformat(),
                "next_retry_after": next_retry,
            }
            return "BLOCKED", {
                "action": "escalate",
                "reason": f"retry exhausted ({retry_count} attempts), backoff {current_backoff}h",
                "backoff_hours": current_backoff,
                "next_retry": next_retry,
            }

    if decision_type == "FATAL":
        state[module] = {
            "decision": "FATAL",
            "status_code": status_code,
            "retry_count": module_state.get("retry_count", 0),
            "backoff_hours": module_state.get("backoff_hours", INITIAL_BACKOFF_HOURS),
            "last_seen": now.isoformat(),
            "next_retry_after": None,
        }
        return "FATAL", {"action": "flag_dead", "reason": f"{status_code} — content not found"}

    return None, {"action": "unknown", "reason": "no status code parsed"}


def remediate_scraper_error(module, filename):
    """Re-run the failed scraper script for a RETRYABLE error."""
    if "fetch_" not in filename and "fetch_" not in module:
        return None

    scraper_map = {
        "fetch_zillow": [PYTHON, "app/data_providers/residential/fetch_zillow_crawl4ai.py", "--db", "--max-pages", "1"],
        "fetch_fsbo": [PYTHON, "app/data_providers/residential/fetch_fsbo.py", "--db"],
        "fetch_landandfarm": [PYTHON, "-m", "data_center.commercial.fetch_landandfarm", "--state", "TX", "--min-acres", "5"],
    }

    script = None
    for key, cmd in scraper_map.items():
        if key in filename or key in module:
            script = cmd
            break

    if not script:
        return None

    logger.info(f"Retry: re-running {' '.join(str(s) for s in script)}")
    try:
        r = subprocess.run(
            [str(s) for s in script],
            capture_output=True, text=True, timeout=300,
            cwd=BASE_DIR,
        )
        success = r.returncode == 0
        logger.info(f"Retry {'succeeded' if success else 'failed'} (exit {r.returncode})")
        return {
            "action": "re-run scraper",
            "script": " ".join(str(s) for s in script),
            "success": success,
            "output": r.stdout.strip()[-500:] if r.stdout else "",
            "error": r.stderr.strip()[-500:] if r.stderr else "",
        }
    except subprocess.TimeoutExpired:
        logger.warning("Retry timed out after 300s")
        return {"action": "re-run scraper", "script": " ".join(str(s) for s in script), "success": False, "error": "timeout"}
    except Exception as e:
        logger.error(f"Retry exception: {e}")
        return {"action": "re-run scraper", "script": " ".join(str(s) for s in script), "success": False, "error": str(e)}


def remediate_script_error(module, filename, error_text):
    """Fix script errors like missing dependencies or config."""
    if "clean_city" in filename or "clean_city" in module:
        logger.info("Auto-remediation: re-running clean_city_data.py")
        try:
            r = subprocess.run(
                [str(PYTHON), "scripts/clean_city_data.py"],
                capture_output=True, text=True, timeout=60, cwd=BASE_DIR,
            )
            success = r.returncode == 0
            logger.info(f"Clean city data {'succeeded' if success else 'failed'}")
            return {"action": "re-run clean_city_data", "success": success, "output": r.stdout.strip()[-300:], "error": r.stderr.strip()[-300:]}
        except Exception as e:
            return {"action": "re-run clean_city_data", "success": False, "error": str(e)}

    if "ModuleNotFoundError" in error_text:
        missing_module = error_text.split("No module named")[-1].strip().strip("'")
        logger.info(f"Auto-remediation: installing missing module {missing_module}")
        try:
            r = subprocess.run(
                [str(PYTHON), "-m", "pip", "install", "--break-system-packages", missing_module],
                capture_output=True, text=True, timeout=60,
            )
            success = r.returncode == 0
            if success:
                logger.info(f"Installed missing module: {missing_module}")
            return {"action": f"pip install {missing_module}", "success": success, "error": r.stderr.strip()[-300:]}
        except Exception as e:
            return {"action": f"pip install {missing_module}", "success": False, "error": str(e)}

    return None


def invoke_opencode_for_complex_issue(diagnosis):
    """For complex issues (BLOCKED or exhausted RETRYABLE), invoke opencode CLI."""
    report_path = BASE_DIR / "output" / f"auto_heal_{datetime.now():%Y%m%d_%H%M%S}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    report = f"""# Auto-Heal Diagnostic Report

Generated: {datetime.now().isoformat()}

## Errors Detected

"""
    for err in diagnosis.get("errors", []):
        report += f"- **{err['module']}** ({err['count']} occurrences)\n  `{err['latest'][:200]}`\n"

    report += "\n## Decisions\n\n"
    for d in diagnosis.get("decisions", []):
        report += f"- [{d['decision']}] {d['module']}: {d['reason']}\n"

    report += "\n## Remediation Attempts\n\n"
    for action in diagnosis.get("remediation", []):
        status = "OK" if action.get("success") else "FAIL"
        report += f"- {status} {action.get('action', '?')}: {action.get('error', 'ok')[:200]}\n"

    if diagnosis.get("unresolved"):
        report += "\n## Unresolved Issues (Requires King's Review)\n\n"
        for err in diagnosis["unresolved"]:
            report += f"- {err['module']}: {err['latest'][:300]}\n"

    report_path.write_text(report)
    logger.info(f"Diagnostic report written to {report_path}")

    if diagnosis.get("unresolved") and OPECODE:
        logger.info("Invoking opencode for unresolved issues...")
        try:
            subprocess.Popen(
                [OPECODE, str(report_path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            logger.success(f"opencode invoked on {report_path}")
            return {"action": "opencode invoked", "report": str(report_path)}
        except Exception as e:
            logger.error(f"Failed to invoke opencode: {e}")
            return {"action": "opencode failed", "error": str(e), "report": str(report_path)}

    return None


def main():
    tracer = init_otel("realtor-auto-heal")
    logger.info("=" * 50)
    logger.info("AUTO-HEAL WATCHDOG [HEAL_CONTROLLER_V2]")
    logger.info("=" * 50)

    state = load_state()

    errors = get_recent_errors()

    if not errors:
        logger.info("No recent errors found — system healthy")
        logger.info("HEALTH_OK | auto_heal errors=0")
        return 0

    logger.warning(f"Found {len(errors)} error groups in last 30m")

    diagnosis = {"errors": errors, "decisions": [], "remediation": [], "unresolved": []}

    for err in errors:
        module = err["module"]
        latest_text = err["latest"]
        full_text = latest_text

        status_code, decision_type = parse_http_status(full_text)

        if decision_type is None:
            logger.info(f"[HEAL_DECISION: UNKNOWN] {module} — no recognizable HTTP status, falling through to generic handler")
            error_lines = get_error_details(module)
            full_error = "\n".join(error_lines)
            status_code, decision_type = parse_http_status(full_error)

        decision, info = decision_matrix(module, status_code, decision_type, full_text, state)

        diagnosis["decisions"].append({
            "module": module,
            "decision": decision,
            "reason": info.get("reason", ""),
        })

        logger.info(f"[HEAL_DECISION: {decision}] {module} — {info.get('reason', 'no reason')}")

        if decision == "BLOCKED" and info.get("action") == "stand_down":
            HEAL_LOG.append(f"HEAL_BLOCKED {module}: {info['reason']}")
            diagnosis["unresolved"].append(err)
            continue

        if decision == "BLOCKED" and info.get("action") == "escalate":
            HEAL_LOG.append(f"HEAL_ESCALATED {module}: {info['reason']}")
            diagnosis["unresolved"].append(err)
            continue

        if decision == "RETRYABLE" and info.get("action") == "retry":
            result = remediate_scraper_error(module, err.get("filename", ""))
            if result:
                diagnosis["remediation"].append(result)
                if result.get("success"):
                    logger.success(f"Auto-healed: {module} — retry succeeded")
                    HEAL_LOG.append(f"HEAL_OK {module}: retry {info['retry_count']}/{info['max_retries']} succeeded")
                    if module in state:
                        del state[module]
                else:
                    logger.warning(f"Auto-heal failed: {module} — retry {info['retry_count']}/{info['max_retries']} failed")
            else:
                result = remediate_script_error(module, err.get("filename", ""), full_text)
                if result:
                    diagnosis["remediation"].append(result)
                    if result.get("success"):
                        logger.success(f"Auto-healed: {module} — script error fixed")
                        HEAL_LOG.append(f"HEAL_OK {module}: {result['action']}")
                        if module in state:
                            del state[module]
                    else:
                        diagnosis["unresolved"].append(err)
                else:
                    diagnosis["unresolved"].append(err)
            continue

        if decision == "FATAL":
            mark_property_dead(module, full_text)
            HEAL_LOG.append(f"HEAL_FATAL {module}: {info['reason']}")
            continue

        error_lines = get_error_details(module)
        full_error = "\n".join(error_lines)
        result = remediate_script_error(module, err.get("filename", ""), full_error)
        if result:
            diagnosis["remediation"].append(result)
            if result.get("success"):
                logger.success(f"Auto-healed: {module} — {result['action']}")
                HEAL_LOG.append(f"HEAL_OK {module}: {result['action']}")
            else:
                diagnosis["unresolved"].append(err)
        else:
            diagnosis["unresolved"].append(err)

    save_state(state)

    if diagnosis["unresolved"]:
        logger.warning(f"{len(diagnosis['unresolved'])} error(s) requiring human review — invoking opencode")
        invoke_opencode_for_complex_issue(diagnosis)

    if HEAL_LOG:
        for entry in HEAL_LOG:
            logger.info(entry)
    else:
        logger.info("No auto-heal actions taken")

    total_errors = sum(e["count"] for e in errors)
    healed = sum(1 for a in diagnosis["remediation"] if a.get("success"))
    with tracer.start_as_current_span("auto-heal-result") as span:
        span.set_attribute("errors_total", total_errors)
        span.set_attribute("healed_count", healed)
        span.set_attribute("unresolved_count", len(diagnosis["unresolved"]))

    blocked = sum(1 for d in diagnosis["decisions"] if d["decision"] == "BLOCKED")
    fatal = sum(1 for d in diagnosis["decisions"] if d["decision"] == "FATAL")

    if healed > 0 and total_errors <= healed:
        logger.info(f"HEALTH_OK | auto_heal errors={total_errors} healed={healed}")
    elif diagnosis["unresolved"]:
        logger.warning(f"HEALTH_DEGRADED | auto_heal errors={total_errors} healed={healed} unresolved={len(diagnosis['unresolved'])} blocked={blocked} fatal={fatal}")
    else:
        logger.info(f"HEALTH_OK | auto_heal errors={total_errors} healed={healed}")

    logger.info("=" * 50)
    logger.info("AUTO-HEAL WATCHDOG FINISHED")
    logger.info("=" * 50)
    return 0


if __name__ == "__main__":
    sys.exit(main())
