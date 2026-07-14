#!/usr/bin/env python3
"""Reject former public branding outside the explicit migration record."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {
    Path('docs/MIGRATION-0.4.0-TO-0.4.1.md'),
    Path('scripts/release-package.sh'),  # excludes old archives too
    Path('scripts/version.py'),          # source compatibility note
    Path('scripts/validate-branding.py'), # contains the forbidden-pattern definitions
    Path('docs/releases/0.4.1-your-ai-team-rename.md'),
}
SKIP_DIRS = {'.git', '__pycache__', '.pytest_cache', 'node_modules'}
TEXT_SUFFIXES = {'.md', '.txt', '.py', '.sh', '.ps1', '.json', '.jsonc', '.toml', '.yaml', '.yml', ''}
PATTERNS = {
    'former product phrase': re.compile(r'TeamLoopHarness|TeamLoop Harness|\bTeamLoop\b|\bTLH\b'),
    'former repository slug': re.compile(r'teamloop-harness', re.I),
    'former public CLI': re.compile(r'(?<![A-Za-z0-9_-])(?:/|scripts/)?your-team(?:\.sh|\.ps1|\.md)?(?![A-Za-z0-9_-])'),
    'former guide filename': re.compile(r'TEAMLOOP\.md'),
}

violations = []
for path in ROOT.rglob('*'):
    if not path.is_file() or any(part in SKIP_DIRS for part in path.parts):
        continue
    rel = path.relative_to(ROOT)
    if rel in ALLOWED or path.suffix.lower() not in TEXT_SUFFIXES:
        continue
    try:
        text = path.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        continue
    for label, pattern in PATTERNS.items():
        for match in pattern.finditer(text):
            line = text.count('\n', 0, match.start()) + 1
            violations.append(f'{rel}:{line}: {label}: {match.group(0)!r}')

if violations:
    print('YOUR_AI_TEAM_BRANDING_FAIL')
    print('\n'.join(violations))
    sys.exit(1)
print('YOUR_AI_TEAM_BRANDING_PASS')
