"""Explicit offline access to an already reviewed archival reference.

No archive downloads, extraction, engine copying, code patching or order APIs.
Callers must provide the saved artifact and explicitly acknowledge its known bug.
"""
from __future__ import annotations
import hashlib
import importlib
import json
import sys
from pathlib import Path

BASE_RESULT = '157fe890756519f3faba4903a88950d24a890a8cecfa0fad4ae4e849d0fd9645'
LADDER_RESULT = 'f38757a0bfcc95196cc6c4bff1fcad8194f0a9202473ebea34f3374fc4222e68'
ACCOUNT_SHA = 'b67c939a829c0c2366964ed8ac97747f5747b8664fceee679ff0dd3b0a023cee'
RUNNER_SHA = 'c7c61ad49c347a5a8500e1310cca7b17be96208c96617beeb1f1b92caba55cd3'


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def load_reference(root: Path, *, acknowledged: bool = False):
    if not acknowledged:
        raise PermissionError('Historical research only: provide --allow-archived-reference explicitly. Known valuation bug remains.')
    root = Path(root).resolve()
    core = root / 'current_code'
    base = json.loads((root / 'reconciled/report/results.json').read_text())
    ladder = json.loads((root / 'ladder-first/results.json').read_text())
    if digest(base) != BASE_RESULT or digest(ladder) != LADDER_RESULT:
        raise ValueError('Frozen historical evidence identity mismatch')
    expected = {'research/relative_futures/' + name: sha for name, sha in base['source_sha256'].items()}
    expected['research/relative_futures/account.py'] = ACCOUNT_SHA
    expected['research/opportunity_runner/study.py'] = RUNNER_SHA
    lock = json.loads((root / 'proofs/SOURCE_LOCK.json').read_text())
    # The pre-result locks describe additional imported allocation/audit helpers.
    for name in ('research/opportunity_budget/policy.py', 'research/opportunity_budget/study.py'):
        expected[name] = lock[name]
    for name, wanted in expected.items():
        file = (core / name).resolve()
        if not file.is_relative_to(core) or hashlib.sha256(file.read_bytes()).hexdigest() != wanted:
            raise ValueError('Archived implementation changed: ' + name)
    # Extend the research namespace only inside this explicit historical command.
    # Existing main modules keep priority; no archived files are written into main.
    import research
    old_path = str(core / 'research')
    if old_path not in research.__path__:
        research.__path__.append(old_path)
    modules = {}
    for name in ('relative_futures.data', 'relative_futures.account', 'relative_futures.study',
                 'opportunity_runner.study', 'opportunity_budget.study', 'relative_futures_checks.candidates'):
        module = importlib.import_module('research.' + name)
        if not Path(module.__file__).resolve().is_relative_to(core):
            raise RuntimeError('Unexpected reference module origin: ' + name)
        modules[name] = module
    frames, audit = modules['relative_futures.data'].load(root / 'reconciled/supplemented/reconciled')
    if audit != base['source']:
        raise ValueError('Prepared market snapshot differs from the checked evidence')
    return modules, frames, audit, ladder, expected
