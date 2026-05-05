#!/bin/sh
# Tests for scripts/install_watcher_addon.sh
#
# Run from the repo root:   sh tests/test_install_watcher_addon.sh
# Or as a CI step.
#
# Network-dependent tests auto-skip if GitHub is unreachable. Set
# SKIP_NETWORK_TESTS=1 to skip them unconditionally.

set -u

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/install_watcher_addon.sh"

PASS=0
FAIL=0
SKIP=0

red()    { printf '\033[31m%s\033[0m' "$*"; }
green()  { printf '\033[32m%s\033[0m' "$*"; }
yellow() { printf '\033[33m%s\033[0m' "$*"; }

pass() { PASS=$((PASS+1)); printf '  %s %s\n' "$(green PASS)" "$1"; }
fail() { FAIL=$((FAIL+1)); printf '  %s %s\n' "$(red   FAIL)" "$1"; [ -n "${2:-}" ] && printf '       %s\n' "$2"; }
skip() { SKIP=$((SKIP+1)); printf '  %s %s\n' "$(yellow SKIP)" "$1"; }

assert_eq() {
    # assert_eq <name> <expected> <actual>
    if [ "$2" = "$3" ]; then
        pass "$1"
    else
        fail "$1" "expected: $2 / actual: $3"
    fi
}

assert_contains() {
    # assert_contains <name> <needle> <haystack>
    case "$3" in
        *"$2"*) pass "$1" ;;
        *)      fail "$1" "expected to contain: $2" ;;
    esac
}

assert_file_exists() {
    # assert_file_exists <name> <path>
    if [ -f "$2" ]; then
        pass "$1"
    else
        fail "$1" "missing file: $2"
    fi
}

# --- Section 1: script structure / preflight (no network) -------------------

printf '\n== Script structure ==\n'

assert_file_exists "installer script exists" "$SCRIPT"

shebang="$(head -n1 "$SCRIPT")"
assert_eq "shebang is POSIX sh" "#!/bin/sh" "$shebang"

if sh -n "$SCRIPT" 2>/dev/null; then
    pass "POSIX sh -n syntax check"
else
    fail "POSIX sh -n syntax check"
fi

# Bashisms guard (best-effort; not exhaustive). Skips comments and
# heredoc bodies are not parsed semantically here -- a hit is still
# worth flagging for human review.
bashisms="$(grep -nE '\[\[|^[[:space:]]*local |<<<|\$\{[A-Za-z_]+,,\}|\$\{[A-Za-z_]+\^\^\}' "$SCRIPT" \
            | grep -v '^[[:space:]]*#' || true)"
if [ -z "$bashisms" ]; then
    pass "no obvious bashisms in installer"
else
    fail "no obvious bashisms in installer" "$bashisms"
fi

printf '\n== Usage / error paths ==\n'

help_out="$(sh "$SCRIPT" --help 2>&1)"; help_rc=$?
assert_eq "--help exits 0" "0" "$help_rc"
assert_contains "--help prints Usage:" "Usage:" "$help_out"
assert_contains "--help mentions --force" "--force" "$help_out"
assert_contains "--help mentions --ref" "--ref" "$help_out"
assert_contains "--help mentions --ma-id" "--ma-id" "$help_out"
assert_contains "--help mentions --python-version" "--python-version" "$help_out"

bad_out="$(sh "$SCRIPT" --bogus-option 2>&1)"; bad_rc=$?
if [ "$bad_rc" -ne 0 ]; then
    pass "unknown option exits non-zero"
else
    fail "unknown option exits non-zero" "exit code was 0"
fi
assert_contains "unknown option mentions the option" "--bogus-option" "$bad_out"

missing_dir_out="$(sh "$SCRIPT" --addons-dir /nonexistent/path/does/not/exist 2>&1)"; missing_dir_rc=$?
if [ "$missing_dir_rc" -ne 0 ]; then
    pass "missing --addons-dir target exits non-zero"
else
    fail "missing --addons-dir target exits non-zero" "exit code was 0"
fi
assert_contains "missing dir error mentions the path" "/nonexistent/path/does/not/exist" "$missing_dir_out"

# --- Section 2: end-to-end install (network) --------------------------------

printf '\n== End-to-end install ==\n'

network_ok=0
if [ "${SKIP_NETWORK_TESTS:-0}" = "1" ]; then
    skip "network tests disabled via SKIP_NETWORK_TESTS=1"
elif ! command -v curl >/dev/null 2>&1; then
    skip "curl not available -- skipping network tests"
elif ! curl -fsS --max-time 10 -o /dev/null https://codeload.github.com 2>/dev/null; then
    skip "GitHub unreachable -- skipping network tests"
else
    network_ok=1
fi

if [ "$network_ok" = "1" ]; then
    TMP_ADDONS="$(mktemp -d)"
    trap 'rm -rf "$TMP_ADDONS"' EXIT INT TERM

    install_out="$(sh "$SCRIPT" \
        --force \
        --addons-dir "$TMP_ADDONS" \
        --ma-id addon_TESTID_music_assistant \
        --python-version python3.99 2>&1)"
    install_rc=$?

    assert_eq "install exits 0" "0" "$install_rc"
    assert_contains "install output reports completion" "Install complete" "$install_out"
    assert_contains "install output reports next steps" "Next steps:" "$install_out"

    ADDON="$TMP_ADDONS/ma_provider_watcher"
    assert_file_exists "config.yaml created"  "$ADDON/config.yaml"
    assert_file_exists "build.yaml created"   "$ADDON/build.yaml"
    assert_file_exists "Dockerfile created"   "$ADDON/Dockerfile"
    assert_file_exists "run.sh created"       "$ADDON/run.sh"
    assert_file_exists "ytmusic_free/__init__.py copied"   "$ADDON/ytmusic_free/__init__.py"
    assert_file_exists "ytmusic_free/manifest.json copied" "$ADDON/ytmusic_free/manifest.json"

    if [ -x "$ADDON/run.sh" ]; then
        pass "run.sh is executable"
    else
        # chmod is best-effort (silently ignored on filesystems without exec bit)
        skip "run.sh executable bit (filesystem may not support it)"
    fi

    config="$(cat "$ADDON/config.yaml")"
    assert_contains "config.yaml has slug"      "slug: ma_provider_watcher" "$config"
    assert_contains "config.yaml has docker_api" "docker_api: true"          "$config"
    assert_contains "config.yaml has boot auto"  "boot: auto"                "$config"

    runsh="$(cat "$ADDON/run.sh")"
    assert_contains "run.sh has substituted MA ID" 'MA="addon_TESTID_music_assistant"' "$runsh"
    assert_contains "run.sh has substituted Python version" "/python3.99/" "$runsh"
    assert_contains "run.sh shebang is bash" "#!/usr/bin/env bash" "$runsh"

    if bash -n "$ADDON/run.sh" 2>/dev/null; then
        pass "generated run.sh passes bash -n"
    elif command -v bash >/dev/null 2>&1; then
        fail "generated run.sh passes bash -n"
    else
        skip "bash not available to syntax-check generated run.sh"
    fi

    dockerfile="$(cat "$ADDON/Dockerfile")"
    assert_contains "Dockerfile copies provider" "COPY ytmusic_free/ /provider/ytmusic_free/" "$dockerfile"
    assert_contains "Dockerfile installs docker-cli" "docker-cli" "$dockerfile"

    # --- Idempotency ---
    printf '\n== Idempotency ==\n'

    # Plant a sentinel file to detect overwrite behavior.
    printf 'SENTINEL_FROM_PREVIOUS_INSTALL\n' > "$ADDON/SENTINEL"

    # Without --force, "n" answer should abort and leave the install untouched.
    abort_out="$(printf 'n\n' | sh "$SCRIPT" --addons-dir "$TMP_ADDONS" --ma-id x --python-version python3.13 2>&1)"
    abort_rc=$?
    if [ "$abort_rc" -ne 0 ]; then
        pass "re-install without --force and 'n' answer aborts"
    else
        fail "re-install without --force and 'n' answer aborts" "exit code was 0"
    fi
    assert_contains "abort output mentions --force" "--force" "$abort_out"
    assert_file_exists "aborted re-install leaves sentinel intact" "$ADDON/SENTINEL"

    # With --force, sentinel must be gone afterwards.
    sh "$SCRIPT" --force --addons-dir "$TMP_ADDONS" \
        --ma-id addon_FORCED_music_assistant --python-version python3.42 \
        >/dev/null 2>&1
    if [ ! -f "$ADDON/SENTINEL" ]; then
        pass "--force overwrites prior install (sentinel removed)"
    else
        fail "--force overwrites prior install (sentinel removed)"
    fi
    runsh2="$(cat "$ADDON/run.sh" 2>/dev/null || echo '')"
    assert_contains "--force re-substitutes MA ID" 'MA="addon_FORCED_music_assistant"' "$runsh2"
    assert_contains "--force re-substitutes Python version" "/python3.42/" "$runsh2"
fi

# --- Summary ----------------------------------------------------------------

printf '\n== Summary ==\n'
printf '  passed:  %s\n' "$PASS"
printf '  failed:  %s\n' "$FAIL"
printf '  skipped: %s\n' "$SKIP"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
exit 0
