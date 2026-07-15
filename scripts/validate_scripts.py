#!/usr/bin/env python3
"""Cross-platform script validator for YourAITeam.

Validates every supported script surface in one deterministic pass:
- PowerShell syntax (when pwsh/powershell is available) plus static contract checks
- Bash syntax (when bash is available)
- Python bytecode compilation
- extensionless shell shim structure and target existence
- UTF-8 decoding, CRLF-sensitive shell files, shebangs, and known mojibake

The command is intentionally read-only. It emits human-readable output by default
and JSON with --json. Missing optional runtimes are reported as UNAVAILABLE, not
silently counted as PASS. Use --require-shells to make unavailable parsers fail.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
import datetime
from typing import Any, Dict, Iterable, List, Optional

KNOWN_MOJIBAKE = (
    "\ufffd",
    "\u0421\u201a\u0420\u2122\u0420\u00b0",
    "\u0421\u201a\u0420\u0452\u0420\u00a4",
    "\u0421\u201a\u0420\u2013\u0420\u045e",
    "\u0421\u201a\u0420\u00a6\u0432\u2022\u045c",
)
INVALID_PS_ATTRIBUTE = re.compile(
    r"\[\s*Parameter\s*\([^)]*\bValueFromRemaining\s*=",
    re.IGNORECASE | re.DOTALL,
)
SHELL_SHEBANG = "#!/usr/bin/env bash"


def _record(kind: str, path: Path, status: str, detail: str = "") -> Dict[str, str]:
    return {
        "kind": kind,
        "path": path.as_posix(),
        "status": status,
        "detail": detail,
    }


def _read_utf8(path: Path) -> tuple[Optional[str], Optional[str]]:
    try:
        return path.read_text(encoding="utf-8-sig"), None
    except UnicodeDecodeError as exc:
        return None, f"not valid UTF-8: {exc}"


def _powershell_executable() -> Optional[str]:
    return shutil.which("pwsh") or shutil.which("powershell") or shutil.which("powershell.exe")


def _bash_executable() -> Optional[str]:
    return shutil.which("bash")


def _validate_powershell(path: Path, executable: Optional[str]) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    content, error = _read_utf8(path)
    if error:
        return [_record("powershell", path, "FAIL", error)]
    assert content is not None
    if INVALID_PS_ATTRIBUTE.search(content):
        results.append(_record(
            "powershell",
            path,
            "FAIL",
            "invalid Parameter attribute ValueFromRemaining=; use ValueFromRemainingArguments=",
        ))
    for marker in KNOWN_MOJIBAKE:
        if marker in content:
            results.append(_record("powershell", path, "FAIL", f"known mojibake marker found: {marker!r}"))
            break
    if executable is None:
        if not results:
            results.append(_record("powershell", path, "UNAVAILABLE", "pwsh/powershell not found; static checks passed"))
        return results

    # ParseFile is safer than executing the script and catches attribute/parser errors.
    ps_code = (
        "$tokens=$null;$errors=$null;"
        "[System.Management.Automation.Language.Parser]::ParseFile(" 
        "$args[0],[ref]$tokens,[ref]$errors)|Out-Null;"
        "if($errors.Count -gt 0){$errors|ForEach-Object{$_.Message};exit 1}"
    )
    proc = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-Command", ps_code, str(path)],
        text=True,
        capture_output=True,
        timeout=30,
    )
    if proc.returncode != 0:
        detail = (proc.stdout + proc.stderr).strip() or f"PowerShell parser exit {proc.returncode}"
        results.append(_record("powershell", path, "FAIL", detail))
    elif not results:
        results.append(_record("powershell", path, "PASS", "PowerShell parser accepted file"))
    return results


def _validate_bash(path: Path, executable: Optional[str], require_executable: bool) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    raw = path.read_bytes()
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        return [_record("bash", path, "FAIL", f"not valid UTF-8: {exc}")]
    if "\r\n" in content:
        results.append(_record("bash", path, "FAIL", "CRLF line endings are unsafe for Unix/WSL shell execution"))
    if not content.startswith(SHELL_SHEBANG):
        results.append(_record("bash", path, "FAIL", f"missing canonical shebang {SHELL_SHEBANG}"))
    for marker in KNOWN_MOJIBAKE:
        if marker in content:
            results.append(_record("bash", path, "FAIL", f"known mojibake marker found: {marker!r}"))
            break
    if require_executable and not os.access(path, os.X_OK):
        results.append(_record("bash", path, "FAIL", "file is not executable after installation"))
    if executable is None:
        if not results:
            results.append(_record("bash", path, "UNAVAILABLE", "bash not found; static checks passed"))
        return results
    proc = subprocess.run([executable, "-n", str(path)], text=True, capture_output=True, timeout=30)
    if proc.returncode != 0:
        results.append(_record("bash", path, "FAIL", (proc.stdout + proc.stderr).strip()))
    elif not results:
        results.append(_record("bash", path, "PASS", "bash -n accepted file"))
    return results


def _validate_python(path: Path) -> List[Dict[str, str]]:
    content, error = _read_utf8(path)
    if error:
        return [_record("python", path, "FAIL", error)]
    assert content is not None
    for marker in KNOWN_MOJIBAKE:
        if marker in content:
            return [_record("python", path, "FAIL", f"known mojibake marker found: {marker!r}")]
    try:
        with tempfile.TemporaryDirectory(prefix="your-ai-team-pycompile-") as temp_dir:
            pyc = str(Path(temp_dir) / (path.name + "c"))
            py_compile.compile(str(path), cfile=pyc, doraise=True)
    except py_compile.PyCompileError as exc:
        return [_record("python", path, "FAIL", str(exc))]
    return [_record("python", path, "PASS", "py_compile accepted file")]


def _shim_target(content: str) -> Optional[str]:
    # Current shims use either exec bash "$(dirname "$0")/name.sh" or
    # exec "$(dirname "$0")/name.sh". Keep this deliberately narrow.
    match = re.search(r'\$\(dirname "\$0"\)/(?:[^"\s]+)', content)
    if not match:
        return None
    return match.group(0).split("/")[-1]


def _validate_shim(path: Path, bash: Optional[str], require_executable: bool) -> List[Dict[str, str]]:
    results = _validate_bash(path, bash, require_executable)
    content, error = _read_utf8(path)
    if error or content is None:
        return results
    target_name = _shim_target(content)
    if not target_name:
        results.append(_record("shim", path, "FAIL", "cannot resolve delegated wrapper target"))
        return results
    target = path.parent / target_name
    if not target.is_file():
        results.append(_record("shim", path, "FAIL", f"delegated target does not exist: {target_name}"))
    else:
        results.append(_record("shim", path, "PASS", f"delegates to {target_name}"))
    return results


def discover(root: Path) -> Dict[str, List[Path]]:
    scripts = root / "scripts"
    tests = root / "tests"
    powershell = sorted(scripts.glob("*.ps1"))
    bash = sorted(scripts.glob("*.sh"))
    python = sorted(scripts.glob("*.py"))
    shims = sorted(p for p in scripts.iterdir() if p.is_file() and "." not in p.name)
    # Test launchers are part of the supported executable surface.
    if (tests / "run-tests.ps1").is_file():
        powershell.append(tests / "run-tests.ps1")
    if (tests / "run-tests.sh").is_file():
        bash.append(tests / "run-tests.sh")
    return {
        "powershell": powershell,
        "bash": bash,
        "python": python,
        "shims": shims,
    }


def validate(root: Path, require_shells: bool = False, require_executable: bool = False) -> Dict[str, Any]:
    groups = discover(root)
    ps = _powershell_executable()
    bash = _bash_executable()
    results: List[Dict[str, str]] = []
    for path in groups["powershell"]:
        results.extend(_validate_powershell(path, ps))
    for path in groups["bash"]:
        results.extend(_validate_bash(path, bash, require_executable))
    for path in groups["python"]:
        results.extend(_validate_python(path))
    for path in groups["shims"]:
        results.extend(_validate_shim(path, bash, require_executable))

    failures = [item for item in results if item["status"] == "FAIL"]
    unavailable = [item for item in results if item["status"] == "UNAVAILABLE"]
    required_unavailable_failures = len(unavailable) if require_shells else 0
    if require_shells and unavailable:
        failures.extend(
            _record(item["kind"], Path(item["path"]), "FAIL", "required parser unavailable")
            for item in unavailable
        )
    counts = {
        kind: len(paths) for kind, paths in groups.items()
    }
    summary = {
        "schemaVersion": 1,
        "contract": "your-ai-team.script-validation/v1",
        "checkedAtUtc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "status": "FAIL" if failures else "PASS",
        "root": str(root),
        "files": counts,
        "checks": {
            "pass": sum(1 for item in results if item["status"] == "PASS"),
            "fail": len([item for item in results if item["status"] == "FAIL"]) + required_unavailable_failures,
            "unavailable": len(unavailable),
        },
        "runtimes": {
            "python": sys.executable,
            "bash": bash or "UNAVAILABLE",
            "powershell": ps or "UNAVAILABLE",
        },
        "results": results,
    }
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate all YourAITeam script surfaces")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--require-shells", action="store_true", help="fail if bash or PowerShell parser is unavailable")
    parser.add_argument("--require-executable", action="store_true", help="require shell files/shims to have executable bits")
    parser.add_argument("--verbose", action="store_true", help="print every PASS/UNAVAILABLE result in human output")
    args = parser.parse_args(argv)
    summary = validate(Path(args.root).resolve(), args.require_shells, args.require_executable)
    if args.as_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print("YourAITeam script validation")
        print(f"Root: {summary['root']}")
        print(f"PowerShell files: {summary['files']['powershell']}")
        print(f"Bash files:       {summary['files']['bash']}")
        print(f"Python files:     {summary['files']['python']}")
        print(f"Command shims:    {summary['files']['shims']}")
        for item in summary["results"]:
            if item["status"] == "FAIL" or args.verbose:
                print(f"[{item['status']}] {item['kind']} {item['path']}: {item['detail']}")
        checks = summary["checks"]
        if checks["unavailable"] and not args.verbose:
            print(f"Unavailable parser checks: {checks['unavailable']} (use --verbose for details)")
        print(f"Checks: PASS {checks['pass']}, FAIL {checks['fail']}, UNAVAILABLE {checks['unavailable']}")
        print(f"Overall: {summary['status']}")
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
