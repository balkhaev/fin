#!/usr/bin/env bash
set -euxo pipefail

TARGET_BASE=b99abea4e0a82a95406c7b7d06940c31e59fa8e6
TARGET_BRANCH=agent/runtime-ops
PATCH_B64_SHA256=dfe5c9040fd93bbc19da1409e8ef4242f0970d8ce6aa6a2713547b78cb85ef5f
PATCH_XZ_SHA256=44b70eaf5d23c7f4f5d51144a2f892ed89e6c438983d742df53230225dc1f5e7
PATCH_SHA256=b3c7134a6e1b1905075957ac656cbaaf543a646c6136834d8792b0d5acf206ef
WORK=/tmp/runtime-ops-work

cat .runtime-ops-hardening/xz_00.b64 \
    .runtime-ops-hardening/xz_01.b64 \
    .runtime-ops-hardening/xz_02.b64 \
    .runtime-ops-hardening/xz_03.b64 \
  | tr -d '\n\r' > /tmp/runtime-ops-hardening.patch.xz.b64
echo "$PATCH_B64_SHA256  /tmp/runtime-ops-hardening.patch.xz.b64" | sha256sum -c -
base64 -d /tmp/runtime-ops-hardening.patch.xz.b64 > /tmp/runtime-ops-hardening.patch.xz
echo "$PATCH_XZ_SHA256  /tmp/runtime-ops-hardening.patch.xz" | sha256sum -c -
xz -dc /tmp/runtime-ops-hardening.patch.xz > /tmp/runtime-ops-hardening.patch
echo "$PATCH_SHA256  /tmp/runtime-ops-hardening.patch" | sha256sum -c -

cat >/tmp/hardened-files.sha256 <<'EOF'
fbca0d2d7fb3f3cea1f7b4c318a3d2d9680357dcfb15968fc3ccb80f7b69aeb4  .github/workflows/runtime-ops.yml
a61ce7d526912d5da20b6844707621db206ac1ed0e208fb3cf5354d73ef8cffc  docs/checkpoints/runtime-v1/OPERATIONS_CONTRACT.json
4a35d2c01b6afcec15b7b3b14847a1c2be12299dcad6310a31e3eab32295615a  docs/checkpoints/runtime-v1/OPERATIONS_RUNBOOK_RU.md
69001c8bec492865225d13dd3834d95d52079d2ea968eaa1c2a9c91bbc94651d  src/finruntime/cli.py
af32382dce282c48e250985bc4962b19004d0e10fb30f1b7c636f43060890fe9  src/finruntime/journal/__init__.py
86e7a44e13003be906f974f8691ec7c609a86b93dda0a648b001bc0eec6ee64d  src/finruntime/journal/atomic.py
7213b775ab91dcd130669ce00f662e652898043a07e7125d4e79745e592fde0e  src/finruntime/operations/__init__.py
7d0fae25a4f469c09a4f9098a8553a63e4909957be1888f25506ef8cf5535152  src/finruntime/operations/cycle.py
42467ddab8157aa009cba270e81d4684f050984638b655decb621fb0d71a8a63  src/finruntime/portfolio/risk.py
30d4dddaf8c2f54dce1bb7764c139dc4856f87a1752547f1c01d6f38f8843bbe  src/finruntime/portfolio/reconciliation.py
031fdc93c2d47e72c4087e5f28db828d44805ddbb1837436e316db9864581dd1  tests/runtime/test_journal.py
385c2ddc33c628f7cde0b3d6f30ed1f0ee310a7b0d63231e5c3f31260b617ae5  tests/runtime/test_operations.py
EOF

verify_tree() {
  local tree=$1
  (
    cd "$tree"
    sha256sum -c /tmp/hardened-files.sha256
    PYTHONPATH=src python -m compileall -q src/finruntime tests/runtime
    PYTHONPATH=src python -m unittest discover -s tests/runtime -p 'test_*.py' -v
    PYTHONPATH=src python -m finruntime self-test
    PYTHONPATH=src python scripts/verify_runtime.py --full
    PYTHONPATH=src python -m finruntime --help | grep -F 'paper-cycle'
    PYTHONPATH=src python -m finruntime --help | grep -F 'init-account'
    PYTHONPATH=src python -m finruntime --help | grep -F 'status'
    find src/finruntime tests/runtime -type d -name '__pycache__' -prune -exec rm -rf {} + || true
    find src/finruntime tests/runtime -type f -name '*.pyc' -delete || true
    bad=$(find src/finruntime/operations src/finruntime/journal src/finruntime/io.py src/finruntime/cli.py tests/runtime \
      -type f \( -name '*.zip' -o -name '*.tar' -o -name '*.gz' -o -name '*.xz' \
      -o -name '*.b64' -o -name '*.pyfrag' -o -name '*.pyc' \) -print || true)
    test -z "$bad" || { printf '%s\n' "$bad"; exit 1; }
    ! grep -R --line-number --fixed-strings 'submit_order' src/finruntime
    ! grep -R --line-number --fixed-strings 'mode="live"' src/finruntime
  )
}

git fetch origin "$TARGET_BRANCH"
REMOTE_HEAD=$(git rev-parse "origin/$TARGET_BRANCH")
rm -rf "$WORK"

if test "$REMOTE_HEAD" != "$TARGET_BASE"; then
  git worktree add --detach "$WORK" "$REMOTE_HEAD"
  verify_tree "$WORK"
  echo "Target branch already contains the exact hardened bytes: $REMOTE_HEAD"
  exit 0
fi

git worktree add --detach "$WORK" "$TARGET_BASE"
cd "$WORK"
git apply --check /tmp/runtime-ops-hardening.patch
git apply /tmp/runtime-ops-hardening.patch

test "$(git diff --name-only | wc -l)" -eq 12
git diff --name-only | sort > /tmp/changed-files.txt
cat >/tmp/expected-files.txt <<'EOF'
.github/workflows/runtime-ops.yml
docs/checkpoints/runtime-v1/OPERATIONS_CONTRACT.json
docs/checkpoints/runtime-v1/OPERATIONS_RUNBOOK_RU.md
src/finruntime/cli.py
src/finruntime/journal/__init__.py
src/finruntime/journal/atomic.py
src/finruntime/operations/__init__.py
src/finruntime/operations/cycle.py
src/finruntime/portfolio/reconciliation.py
src/finruntime/portfolio/risk.py
tests/runtime/test_journal.py
tests/runtime/test_operations.py
EOF
sort -o /tmp/expected-files.txt /tmp/expected-files.txt
diff -u /tmp/expected-files.txt /tmp/changed-files.txt
verify_tree "$WORK"

cd "$WORK"
git config user.name github-actions[bot]
git config user.email 41898282+github-actions[bot]@users.noreply.github.com
git add \
  .github/workflows/runtime-ops.yml \
  docs/checkpoints/runtime-v1/OPERATIONS_CONTRACT.json \
  docs/checkpoints/runtime-v1/OPERATIONS_RUNBOOK_RU.md \
  src/finruntime/cli.py \
  src/finruntime/journal/__init__.py \
  src/finruntime/journal/atomic.py \
  src/finruntime/operations/__init__.py \
  src/finruntime/operations/cycle.py \
  src/finruntime/portfolio/risk.py \
  src/finruntime/portfolio/reconciliation.py \
  tests/runtime/test_journal.py \
  tests/runtime/test_operations.py
git diff --cached --check
git commit -m 'Harden atomic paper-cycle recovery and journal [skip ci]'
git push origin HEAD:"$TARGET_BRANCH"
