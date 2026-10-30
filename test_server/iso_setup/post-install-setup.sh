#!/bin/bash
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
#
# First boot setup of a DiagnoseMe station installed from the ISO. The
# preseed (test_server/preseed.cfg) installs it as
# /usr/local/bin/post-install-setup.sh, run by diagnoseme-setup.service:
# - loads the Docker images embedded in the ISO;
# - checks that the edition's DiagnoseMe package (the Package of the ISO's
#   deb) is installed; if not, it leaves /var/log/diagnoseme/INSTALL_FAILED
#   and a note on the desktop instead of failing silently;
# - online editions (MODE=online in /etc/diagnoseme/config.env, i.e.
#   servo-dolos) get the diagnoseme-deb apt source and key of the
#   pre-editions ISO and install the package from it, so the station keeps
#   getting package updates through apt. The Maui editions stay offline.
# The service is disabled once every step succeeded; otherwise it runs again
# on the next boot.

set -u

# Root of the station file system (tests only).
DM_ROOT="${DIAGNOSEME_ROOT:-}"
CONFIG_FILE="${DM_ROOT}/etc/diagnoseme/config.env"
ISO_DEB="${DM_ROOT}/var/lib/diagnoseme/deb/diagnoseme_amd64.deb"
IMAGES_DIR="${DM_ROOT}/var/lib/diagnoseme/images"
IMAGES_DONE="${DM_ROOT}/var/lib/diagnoseme/.iso-images-loaded"
LOG_DIR="${DM_ROOT}/var/log/diagnoseme"
INSTALL_LOG="${LOG_DIR}/install.log"
FAIL_MARKER="${LOG_DIR}/INSTALL_FAILED"
DESKTOP_NOTE="${DM_ROOT}/home/diagnoseme/Desktop/DIAGNOSEME_INSTALL_FAILED.txt"
APT_KEYRING="${DM_ROOT}/usr/share/keyrings/artifact-registry.gpg"
APT_SOURCE="${DM_ROOT}/etc/apt/sources.list.d/diagnoseme.list"
APT_KEY_URL="https://keyserver.ubuntu.com/pks/lookup?op=get&search=0x35BAA0B33E9EB396F59CA838C0BA5CE6DC6315A3"
APT_LINE="deb [signed-by=/usr/share/keyrings/artifact-registry.gpg] https://us-apt.pkg.dev/projects/chromeos-hw-tools diagnoseme-deb main"

log() {
    mkdir -p "$LOG_DIR"
    echo "$(date '+%F %T') $*" | tee -a "$INSTALL_LOG"
}

# Prints MODE of config.env (empty without a config.env).
station_mode() {
    [ -f "$CONFIG_FILE" ] || return 0
    (
        # shellcheck disable=SC1090
        . "$CONFIG_FILE" > /dev/null 2>&1
        printf '%s' "${MODE:-}"
    )
}

# Prints the package name of the ISO's deb (the edition's package).
iso_deb_package() {
    [ -f "$ISO_DEB" ] || return 0
    dpkg-deb -f "$ISO_DEB" Package 2>/dev/null
}

package_installed() {
    [ "$(dpkg-query -W -f='${Status}' "$1" 2>/dev/null)" = "install ok installed" ]
}

# Leaves a visible marker (log directory and desktop) for a failed install.
report_failure() {
    log "ERROR: $*"
    {
        echo "DiagnoseMe installation failed: $*"
        echo "See ${INSTALL_LOG#"$DM_ROOT"} and /var/log/diagnoseme/deb-install.log."
    } > "$FAIL_MARKER"
    if [ -d "$(dirname "$DESKTOP_NOTE")" ]; then
        cp "$FAIL_MARKER" "$DESKTOP_NOTE"
        chown diagnoseme:diagnoseme "$DESKTOP_NOTE" 2>/dev/null || true
    fi
}

clear_failure() {
    rm -f "$FAIL_MARKER" "$DESKTOP_NOTE"
}

load_images() {
    [ -f "$IMAGES_DONE" ] && return 0
    local img status=0
    for img in "$IMAGES_DIR"/*.tar.gz "$IMAGES_DIR"/*.tar; do
        [ -f "$img" ] || continue
        if ! docker load -i "$img" >> "$INSTALL_LOG" 2>&1; then
            log "Could not load the Docker image ${img}."
            status=1
        fi
    done
    [ "$status" -eq 0 ] && touch "$IMAGES_DONE"
    return "$status"
}

# Adds the diagnoseme-deb apt source and its key (online editions).
setup_apt_source() {
    mkdir -p "$(dirname "$APT_KEYRING")" "$(dirname "$APT_SOURCE")"
    if [ ! -s "$APT_KEYRING" ]; then
        local key
        key=$(mktemp)
        if ! curl -fsSL "$APT_KEY_URL" -o "$key" ||
            ! gpg --batch --yes --dearmor -o "$APT_KEYRING" "$key"; then
            rm -f "$key" "$APT_KEYRING"
            log "Could not download the diagnoseme-deb apt key."
            return 1
        fi
        rm -f "$key"
    fi
    echo "$APT_LINE" > "$APT_SOURCE"
}

# Installs (or upgrades) the package from the diagnoseme-deb apt source.
apt_install_package() {
    apt-get update >> "$INSTALL_LOG" 2>&1 &&
        DEBIAN_FRONTEND=noninteractive apt-get install -y "$1" >> "$INSTALL_LOG" 2>&1
}

main() {
    local ok=true package mode
    mkdir -p "$LOG_DIR"
    sleep 5
    if ! load_images; then
        ok=false
    fi

    mode=$(station_mode)
    package=$(iso_deb_package)
    if [ -z "$package" ]; then
        package=diagnoseme
    fi
    if [ "$mode" = "online" ]; then
        if setup_apt_source && apt_install_package "$package"; then
            log "Installed ${package} from the diagnoseme-deb apt source."
        else
            log "Could not install ${package} from the diagnoseme-deb apt source; retrying on the next boot."
            ok=false
        fi
    fi

    if package_installed "$package"; then
        clear_failure
    else
        report_failure "the package ${package} is not installed"
        ok=false
    fi

    if [ "$ok" = true ]; then
        systemctl disable diagnoseme-setup.service
        rm -f "$0"
    fi
}

# Unit tests source this script for its functions only.
if [ -n "${DIAGNOSEME_LIB_ONLY:-}" ]; then
    return 0
fi

main "$@"
