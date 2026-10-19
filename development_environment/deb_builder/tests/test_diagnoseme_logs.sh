#!/bin/bash
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
#
# Test of `diagnoseme-logs --collect` with a fake station (system paths,
# docker, lsusb, dmesg and dpkg). Run: bash test_diagnoseme_logs.sh
# The fake commands are written with literal $ expressions.
# shellcheck disable=SC2016

set -u
SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/diagnoseme-logs"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
FAILS=0

fail() {
    echo "FAIL: $*"
    FAILS=$((FAILS + 1))
}

# Fake station file system.
R="$WORK/root"
mkdir -p "$R/etc/diagnoseme" "$R/var/log/diagnoseme/rpcserver/old" \
    "$R/var/lib/diagnoseme/firmware/maui_v1_2" "$R/var/lib/diagnoseme/firmware/maui" \
    "$R/tmp/SN1_diagnoseme_logs" "$R/tmp/SN2_diagnoseme_logs" \
    "$WORK/home/diagnoseme_results/smt_functional/results"
printf 'STATION_ID="SMT LINE 1"\nMAUI_ADB_DEADLINE_S=40\n' >"$R/etc/diagnoseme/config.env"
echo "services: {}" >"$R/etc/diagnoseme/docker-compose.yaml"
echo "rpc log" >"$R/var/log/diagnoseme/rpcserver/rpcserver.log"
echo "old rpc log" >"$R/var/log/diagnoseme/rpcserver/old/rpcserver.log.1"
echo '{"version": "1.2"}' >"$R/var/lib/diagnoseme/firmware/maui_v1_2/STABLE.json"
echo '{"version": "1.1"}' >"$R/var/lib/diagnoseme/firmware/maui/STABLE.json"
echo "ALL_PASSED=True" >"$R/tmp/SN1_diagnoseme_logs/host_only_tests.log"
echo "ALL_PASSED=True" >"$R/tmp/SN2_diagnoseme_logs/host_only_tests.log"
echo "{}" >"$WORK/home/diagnoseme_results/smt_functional/results/SMT_FUNCTIONAL_SN1_1_PASS.json"
echo "{}" >"$WORK/home/diagnoseme_results/smt_functional/results/SMT_FUNCTIONAL_SN2_1_PASS.json"
echo 'PRETTY_NAME="Debian GNU/Linux 12"' >"$R/etc/os-release"

# Fake commands.
FAKE="$WORK/bin"
mkdir -p "$FAKE"
cat >"$FAKE/docker" <<'EOF'
#!/bin/bash
case "$*" in
    info) exit 0 ;;
    images*) printf 'diagnoseme-server:v1.2  1  2 days  1GB\nubuntu:22.04  2  3 days  70MB\nmaui-utils:v1.2  3  1 day  300MB\n' ;;
    *" ps -a") echo "NAME STATUS"; echo "diagnoseme-server Up" ;;
    *" logs --no-color --timestamps") echo "2026-10-19T07:00:00Z server started" ;;
    *) echo "unexpected docker $*" >&2; exit 1 ;;
esac
EOF
printf '#!/bin/bash\necho "lsusb $*"\n' >"$FAKE/lsusb"
printf '#!/bin/bash\nfor i in $(seq 1 400); do echo "dmesg line $i"; done\n' >"$FAKE/dmesg"
printf '#!/bin/bash\necho diagnoseme\n' >"$FAKE/dpkg-query"
printf '#!/bin/bash\necho "Package: $2"; echo "Version: 2.1.0"\n' >"$FAKE/dpkg"
printf '#!/bin/bash\nexit 1\n' >"$FAKE/sudo"
chmod +x "$FAKE"/*

# A PATH without zip (python3 fallback).
NOZIP="$WORK/nozip"
mkdir -p "$NOZIP"
for d in /usr/local/bin /usr/bin /bin; do
    for f in "$d"/*; do
        b="${f##*/}"
        [ "$b" = zip ] && continue
        [ -e "$NOZIP/$b" ] || [ -L "$NOZIP/$b" ] || ln -s "$f" "$NOZIP/$b"
    done
done

# Runs the collection in a new directory; sets ARCHIVE and LISTING.
collect() {
    local path="$1"
    shift
    local cwd
    cwd=$(mktemp -d "$WORK/cwd.XXXX")
    OUT=$(cd "$cwd" && env PATH="$path" HOME="${COLLECT_HOME:-$WORK/home}" TMPDIR="$WORK" \
        DIAGNOSEME_LOGS_ROOT="$R" bash "$SCRIPT" --collect "$@" 2>/dev/null)
    ARCHIVE=$(sed -n 's/^Logs collected: //p' <<<"$OUT")
    [ -n "$ARCHIVE" ] && [ -s "$ARCHIVE" ] || fail "no archive: $OUT"
    [ "$(dirname "$ARCHIVE")" = "$cwd" ] || fail "archive not in the cwd: $ARCHIVE"
    LISTING=$(python3 - "$ARCHIVE" <<'EOF'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as z:
    for n in z.namelist():
        print(n)
EOF
)
}

expect() {
    grep -q -- "$1" <<<"$LISTING" || fail "missing $1 in $ARCHIVE"
}

expect_not() {
    if grep -q -- "$1" <<<"$LISTING"; then
        fail "unexpected $1 in $ARCHIVE"
    fi
}

zip_content() {
    python3 -c 'import sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
print(z.read([n for n in z.namelist() if n.endswith(sys.argv[2])][0]).decode())' "$ARCHIVE" "$1"
}

# 1. One serial, with zip.
collect "$FAKE:$PATH" SN1
[[ "$(basename "$ARCHIVE")" =~ ^diagnoseme_logs_SMT_LINE_1_SN1_[0-9]{8}_[0-9]{6}\.zip$ ]] ||
    fail "bad name $ARCHIVE"
expect "diagnoseme_results/smt_functional/results/SMT_FUNCTIONAL_SN1_1_PASS.json"
expect_not "SMT_FUNCTIONAL_SN2"
expect "device_logs/SN1_diagnoseme_logs/host_only_tests.log"
expect_not "SN2_diagnoseme_logs"
expect "rpcserver/rpcserver.log"
expect "rpcserver/old/rpcserver.log.1"
expect "etc/config.env"
expect "firmware/maui_v1_2/STABLE.json"
expect "firmware/maui/STABLE.json"
expect "docker/compose_ps.txt"
expect "docker/compose_logs.txt"
expect "system/lsusb_tree.txt"
expect "system/os-release.txt"
expect "README.txt"
zip_content docker/images.txt | grep -q "maui-utils:v1.2" || fail "images"
zip_content docker/images.txt | grep -q "ubuntu" && fail "images not filtered"
zip_content docker/compose_logs.txt | grep -q "server started" || fail "compose logs"
zip_content system/lsusb_tree.txt | grep -q "lsusb -t" || fail "lsusb -t"
[ "$(zip_content system/dmesg_tail.txt | grep -c 'dmesg line')" = 300 ] || fail "dmesg tail"
zip_content system/dpkg_status.txt | grep -q "Version: 2.1.0" || fail "dpkg"

# 2. All serials, without zip (python3 -m zipfile).
collect "$FAKE:$NOZIP" ""
[[ "$(basename "$ARCHIVE")" =~ ^diagnoseme_logs_SMT_LINE_1_all_.*\.zip$ ]] || fail "bad name $ARCHIVE"
expect "SMT_FUNCTIONAL_SN2_1_PASS.json"
expect "device_logs/SN1_diagnoseme_logs/"
expect "device_logs/SN2_diagnoseme_logs/host_only_tests.log"

# 3. Unreadable dmesg and no config.env: host name, skipped gracefully.
printf '#!/bin/bash\necho "dmesg: read kernel buffer failed" >&2\nexit 1\n' >"$FAKE/dmesg"
mv "$R/etc/diagnoseme/config.env" "$WORK/config.env.bak"
collect "$FAKE:$PATH" SN2
HOST=$(hostname)
HOST=$(printf '%s' "$HOST" | tr -c 'A-Za-z0-9_.-' '_')
[[ "$(basename "$ARCHIVE")" == "diagnoseme_logs_${HOST}_SN2_"* ]] ||
    fail "bad name $ARCHIVE"
zip_content system/dmesg_tail.txt | grep -q "skipped" || fail "dmesg skip note"

# 4. Help and the other options still work.
bash "$SCRIPT" --help | grep -q -- "--collect" || fail "--help"
OUT=$(HOME="$WORK/home" bash "$SCRIPT" --device NOPE)
[ "$OUT" = "No logs found for device serial 'NOPE'." ] || fail "--device: $OUT"

# 5. Run with sudo (HOME=/root with an empty results directory): the results
# of the desktop user and of a station started as root are collected.
mkdir -p "$WORK/roothome/diagnoseme_results/highlevel" \
    "$R/home/diagnoseme/diagnoseme_results/smt_functional/results" \
    "$R/root/diagnoseme_results/fatp_tests/results"
echo "{}" >"$R/home/diagnoseme/diagnoseme_results/smt_functional/results/SMT_FUNCTIONAL_SN3_1_FAIL.json"
echo "{}" >"$R/root/diagnoseme_results/fatp_tests/results/FATP_TESTS_SN3_1_PASS.json"
mv "$WORK/home/diagnoseme_results" "$WORK/home_results.bak"
COLLECT_HOME="$WORK/roothome" collect "$FAKE:$PATH" SN3
expect "diagnoseme_results/smt_functional/results/SMT_FUNCTIONAL_SN3_1_FAIL.json"
expect "diagnoseme_results_root/fatp_tests/results/FATP_TESTS_SN3_1_PASS.json"
zip_content README.txt | grep -q "Results: $R/home/diagnoseme/diagnoseme_results, $R/root/diagnoseme_results" ||
    fail "README results: $(zip_content README.txt)"
OUT=$(HOME="$WORK/roothome" DIAGNOSEME_LOGS_ROOT="$R" bash "$SCRIPT" --device SN3)
grep -q "SMT_FUNCTIONAL_SN3_1_FAIL.json" <<<"$OUT" || fail "--device SN3: $OUT"

if [ "$FAILS" -eq 0 ]; then
    echo "PASS"
else
    echo "$FAILS failure(s)"
    exit 1
fi
