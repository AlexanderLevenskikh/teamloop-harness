import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import your_ai_team as team


class BrandingTests(unittest.TestCase):
    def test_brand_validator_passes(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / 'scripts' / 'validate-branding.py')],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn('YOUR_AI_TEAM_BRANDING_PASS', result.stdout)

    def test_canonical_cli_files_exist(self):
        for name in ('your-ai-team', 'your-ai-team.sh', 'your-ai-team.ps1'):
            self.assertTrue((ROOT / 'scripts' / name).exists(), name)
        legacy_cli = 'your' + '-team.sh'
        self.assertFalse((ROOT / 'scripts' / legacy_cli).exists())

    def test_opencode_materialization_uses_canonical_command(self):
        proposal = team.accept(team.propose('Почини баг', backend='opencode'))
        with tempfile.TemporaryDirectory() as td:
            team.materialize(proposal, 'opencode', td)
            config = json.loads((Path(td) / 'opencode.json').read_text())
            self.assertIn('your-ai-team', config['command'])
            legacy_command = 'your' + '-team'
            self.assertNotIn(legacy_command, config['command'])
            self.assertTrue((Path(td) / '.opencode' / 'commands' / 'your-ai-team.md').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
