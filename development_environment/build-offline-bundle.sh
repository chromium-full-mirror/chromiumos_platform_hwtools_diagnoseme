#!/bin/bash
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEB_BUILDER_DIR="${SCRIPT_DIR}/deb_builder"
REGISTRY="us-docker.pkg.dev/chromeos-hw-tools/diagnoseme"

EDITION=""
DOCKER_TAG=""
OUTPUT_DIR=""
MAUI_UTILS_TAR=""
ZIP_OUTPUT=true
SKIP_DOCKER=false
SKIP_FW=false
ALLOW_MISSING_FW=false
SKIP_DEB=false
BUILD_LOCAL=false
USE_LOCAL=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --edition)
            EDITION="$2"
            shift 2
            ;;
        --tag)
            DOCKER_TAG="$2"
            shift 2
            ;;
        --maui-utils-tar)
            MAUI_UTILS_TAR="$2"
            shift 2
            ;;
        --output-dir)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --skip-docker)
            SKIP_DOCKER=true
            shift
            ;;
        --skip-fw)
            SKIP_FW=true
            shift
            ;;
        --allow-missing-fw)
            ALLOW_MISSING_FW=true
            shift
            ;;
        --skip-deb)
            SKIP_DEB=true
            shift
            ;;
        --build-local)
            BUILD_LOCAL=true
            shift
            ;;
        --use-local|--no-pull|--skip-pull)
            USE_LOCAL=true
            shift
            ;;
        --no-zip)
            ZIP_OUTPUT=false
            shift
            ;;
        -h|--help)
            echo "Usage: build-offline-bundle.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --edition <id>      DiagnoseMe edition to bundle (required, see editions.json)"
            echo "  --tag <tag>         Docker image tag to export (default: <edition>-<commit>, the"
            echo "                      release build of the checked out commit, which also builds"
            echo "                      the deb; another tag is recorded in MANIFEST.json)"
            echo "  --output-dir <dir>  Output directory for bundle (default: offline_update_bundle/<edition>)"
            echo "  --maui-utils-tar <file>  developer override: bundle this local maui-utils.tar"
            echo "                      (docker save output of an unreleased build) instead of"
            echo "                      the stable release (Maui editions always bundle the"
            echo "                      stable maui-utils otherwise)"
            echo "  --use-local         Use existing local Docker images without pulling from cloud"
            echo "  --build-local       Rebuild Docker images from local workspace source code first"
            echo "  --skip-docker       Skip saving Docker images"
            echo "  --skip-fw           Skip downloading the edition's Maui firmware from GCS and"
            echo "                      the missing-firmware check (dist/firmware is still bundled)"
            echo "  --allow-missing-fw  Warn instead of failing when no firmware of a component was staged"
            echo "  --skip-deb          Skip building/copying Debian package"
            echo "  --no-zip            Do not generate a compressed .zip archive"
            echo "  -h, --help          Show this message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

EDITION_INFO=(python3 "${PROJECT_ROOT}/tools/edition_info.py")
if [ -z "${EDITION}" ]; then
    echo "ERROR: --edition <id> is required (shipped: $("${EDITION_INFO[@]}" list --shipped | tr '\n' ' '))."
    exit 1
fi
"${EDITION_INFO[@]}" validate "${EDITION}"
# Package of the edition; the station's offline update checks it.
DEB_PACKAGE="$("${EDITION_INFO[@]}" get "${EDITION}" deb_package)"
# Version of the deb (1.1.<YYYYMMDD>, tools/deb_version.sh), recorded in
# MANIFEST.json.
DEB_VERSION="$("${PROJECT_ROOT}/tools/deb_version.sh")"
if [ -n "${MAUI_UTILS_TAR}" ]; then
    MAUI_UTILS_TAR="$(realpath "${MAUI_UTILS_TAR}")"
fi
# Commit of the checkout the deb, firmware selection and maui-utils come from
# (DIAGNOSEME_COMMIT when there is no git checkout, e.g. in Cloud Build).
COMMIT="${DIAGNOSEME_COMMIT:-$(git -C "${PROJECT_ROOT}" rev-parse HEAD 2>/dev/null || true)}"
if [ -z "${DOCKER_TAG}" ]; then
    # The images of the release build of the same commit (cloudbuild_release
    # pushes <edition>-<commit>), so images and deb cannot come from
    # different commits.
    if [ -z "${COMMIT}" ]; then
        echo "ERROR: no git commit found; pass --tag <tag> (e.g. <edition>-<commit>)."
        exit 1
    fi
    DOCKER_TAG="${EDITION}-${COMMIT}"
elif [ -n "${COMMIT}" ] && [ "${DOCKER_TAG}" != "${EDITION}-${COMMIT}" ]; then
    echo "WARNING: the images (:${DOCKER_TAG}) may come from another commit than the"
    echo "         deb (${COMMIT}); both are recorded in MANIFEST.json."
fi
OUTPUT_DIR="${OUTPUT_DIR:-${PROJECT_ROOT}/offline_update_bundle/${EDITION}}"
# Tag the station runs (config.env DOCKER_TAG; the edition id, release for
# servo-dolos).
STATION_TAG="$("${EDITION_INFO[@]}" get "${EDITION}" station_tag)"
# Every station tag of the edition (the edition id and its docker_tag_aliases,
# e.g. "servo-dolos release"). The old :maui tag of the ODM stations installed
# before the editions is deliberately not saved: the bundle installs the deb
# first, whose compose file and config.env use :<edition>, and a new image
# under :maui would only be picked up by the old compose file, which lacks
# the maui-utils staging mount.
read -r -a STATION_TAGS <<< "$("${EDITION_INFO[@]}" get "${EDITION}" docker_tags)"
# The edition's firmware components (editions.json), e.g. mcu+pdc for
# maui-v1.1; none for the Servo/Dolos editions.
read -r -a FW_COMPONENTS <<< "$("${EDITION_INFO[@]}" get "${EDITION}" firmware)"
# Components the bundle must contain with a valid STABLE.json (Maui v1.1:
# mcu, pdc).
read -r -a FW_STABLE_REQUIRED <<< "$("${EDITION_INFO[@]}" get "${EDITION}" firmware_stable_required)"

if [ -z "${OUTPUT_DIR}" ] || [ "${OUTPUT_DIR}" = "/" ]; then
    echo "ERROR: Refusing to use '${OUTPUT_DIR}' as output directory."
    exit 1
fi

rm -rf "${OUTPUT_DIR}"
mkdir -p "${OUTPUT_DIR}"
# Use an absolute path: later steps change the working directory.
OUTPUT_DIR="$(cd "${OUTPUT_DIR}" && pwd)"
BUNDLE_DIR="${OUTPUT_DIR}/diagnoseme-update"

echo "=================================================="
echo " Building DiagnoseMe Offline Update Bundle"
echo " Edition:      ${EDITION}"
echo " Target Tag:   ${DOCKER_TAG} (station tag :${STATION_TAG})"
echo " Destination:  ${BUNDLE_DIR}"
echo "=================================================="

mkdir -p "${BUNDLE_DIR}/images" "${BUNDLE_DIR}/deb"
for COMP in "${FW_COMPONENTS[@]}"; do
    mkdir -p "${BUNDLE_DIR}/firmware/${COMP}"
done

# Makes sure the image <img>:<tag> exists under both its local and registry
# name, pulling it from the registry first unless local images are requested.
resolve_image() {
    local img="$1"
    local reg_img="${REGISTRY}/${img}:${DOCKER_TAG}"
    local local_img="${img}:${DOCKER_TAG}"

    if [ "$BUILD_LOCAL" = false ] && [ "$USE_LOCAL" = false ]; then
        echo "  Pulling latest ${reg_img} from cloud..."
        if ! docker pull "${reg_img}"; then
            echo ""
            echo "ERROR: Failed to pull '${reg_img}' from Google Cloud Artifact Registry."
            echo "To fix:"
            echo "  1. Run: gcloud auth configure-docker us-docker.pkg.dev"
            echo "  2. Or if you want to package local images, pass --use-local or --build-local."
            exit 1
        fi
    fi

    # No fallback to another tag (e.g. the "latest" tag of a local build of
    # another edition): the bundle must contain the images of the requested
    # tag.
    if docker image inspect "${reg_img}" > /dev/null 2>&1; then
        docker tag "${reg_img}" "${local_img}" 2>/dev/null || true
    elif docker image inspect "${local_img}" > /dev/null 2>&1; then
        docker tag "${local_img}" "${reg_img}" 2>/dev/null || true
    else
        echo "ERROR: Docker image '${local_img}' not found locally."
        exit 1
    fi
}

# Prints the edition baked into the server image $1 (DIAGNOSEME_EDITION).
image_edition() {
    docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$1" \
        | sed -n 's/^DIAGNOSEME_EDITION=//p' | head -n 1
}

# Image ids and registry digests of the bundle (MANIFEST.json "images").
IMAGES_INFO="${OUTPUT_DIR}/images.tsv"
: > "${IMAGES_INFO}"

# 1. Export Docker Images
if [ "$SKIP_DOCKER" = false ]; then
    echo "[1/4] Resolving and Exporting Docker Images (:tag ${DOCKER_TAG})..."

    if [ "$BUILD_LOCAL" = true ]; then
        echo "  Rebuilding Docker images (:tag ${DOCKER_TAG}) from local workspace source..."
        BUILD_ARGS=(--edition "${EDITION}" --tag "${DOCKER_TAG}")
        if [ -n "${MAUI_UTILS_TAR}" ]; then
            BUILD_ARGS+=(--maui-utils-tar "${MAUI_UTILS_TAR}")
        fi
        "${PROJECT_ROOT}/development_environment/build-diagnoseme.sh" "${BUILD_ARGS[@]}"
    fi

    # Determine fast compression utility (multi-threaded pigz if available, else gzip)
    if command -v pigz > /dev/null 2>&1; then
        GZIP_CMD=(pigz -p "$(nproc 2>/dev/null || echo 4)")
        echo "  Using parallel compression: pigz ($(nproc 2>/dev/null || echo 4) cores)"
    else
        GZIP_CMD=(gzip)
        echo "  Using standard compression: gzip"
    fi

    for IMG in ui server envoy; do
        resolve_image "${IMG}"
        REG_IMG="${REGISTRY}/${IMG}:${DOCKER_TAG}"
        LOCAL_IMG="${IMG}:${DOCKER_TAG}"
        if [ "${IMG}" = "server" ]; then
            IMAGE_EDITION="$(image_edition "${LOCAL_IMG}")"
            if [ "${IMAGE_EDITION}" != "${EDITION}" ]; then
                echo "ERROR: ${LOCAL_IMG} is the '${IMAGE_EDITION:-unknown}' edition, not ${EDITION}."
                exit 1
            fi
        fi
        printf '%s\t%s\t%s\t%s\n' "${IMG}" "${DOCKER_TAG}" \
            "$(docker image inspect --format '{{.Id}}' "${LOCAL_IMG}")" \
            "$(docker image inspect --format '{{join .RepoDigests " "}}' "${LOCAL_IMG}" 2>/dev/null || true)" \
            >> "${IMAGES_INFO}"
        IMG_ID=$(docker image inspect --format '{{.Id}}' "${LOCAL_IMG}" 2>/dev/null | cut -d: -f2 | head -c 12 || echo "unknown")
        IMG_DATE=$(docker image inspect --format '{{.Created}}' "${LOCAL_IMG}" 2>/dev/null | cut -d. -f1 || echo "unknown")
        # Also save the image under every station tag of the edition.
        SAVE_IMGS=("${REG_IMG}" "${LOCAL_IMG}")
        for TAG in "${STATION_TAGS[@]}"; do
            if [ "${TAG}" != "${DOCKER_TAG}" ]; then
                STATION_IMG="${REGISTRY}/${IMG}:${TAG}"
                docker tag "${LOCAL_IMG}" "${STATION_IMG}"
                SAVE_IMGS+=("${STATION_IMG}")
            fi
        done
        TAR_FILE="${BUNDLE_DIR}/images/${IMG}_${STATION_TAG}.tar.gz"
        echo "  -> Packaging ${LOCAL_IMG} (ID: ${IMG_ID}, Created: ${IMG_DATE}) -> ${TAR_FILE}..."
        docker save "${SAVE_IMGS[@]}" | "${GZIP_CMD[@]}" > "${TAR_FILE}"
    done
else
    echo "[1/4] Skipping Docker image export."
fi

# 2. Maui firmware of the edition: local dist/firmware, then the current
# stable versions from GCS
if [ ${#FW_COMPONENTS[@]} -eq 0 ]; then
    echo "[2/4] The ${EDITION} edition has no Maui firmware."
else
    echo "[2/4] Preparing Maui firmware (${FW_COMPONENTS[*]}; copying local dist/firmware)..."
    for COMP in "${FW_COMPONENTS[@]}"; do
        if [ -d "${PROJECT_ROOT}/dist/firmware/${COMP}" ]; then
            cp -r "${PROJECT_ROOT}/dist/firmware/${COMP}/"* "${BUNDLE_DIR}/firmware/${COMP}/" 2>/dev/null || true
        fi
    done
    if [ "$SKIP_FW" = false ]; then
        echo "      Downloading the latest stable Maui firmware from GCS..."
        # The stable.<maui_device>.* markers of gs://maui-firmware (see
        # tools/maui_fw_fetch.py).
        # The components of firmware_stable_required must have a stable
        # marker (--require).
        REQUIRE_ARGS=()
        for COMP in "${FW_STABLE_REQUIRED[@]}"; do
            REQUIRE_ARGS+=(--require "${COMP}")
        done
        python3 "${PROJECT_ROOT}/tools/maui_fw_fetch.py" \
            --device "$("${EDITION_INFO[@]}" get "${EDITION}" maui_device)" \
            --dest-root "${BUNDLE_DIR}/firmware" "${REQUIRE_ARGS[@]}" "${FW_COMPONENTS[@]}" \
            || echo "WARNING: Downloading the latest stable Maui firmware failed."

        for COMP in "${FW_COMPONENTS[@]}"; do
            if [ -z "$(ls -A "${BUNDLE_DIR}/firmware/${COMP}" 2>/dev/null)" ]; then
                if [ "$ALLOW_MISSING_FW" = true ]; then
                    echo "WARNING: No ${COMP^^} firmware in ${BUNDLE_DIR}/firmware/${COMP}."
                else
                    echo "ERROR: No ${COMP^^} firmware in ${BUNDLE_DIR}/firmware/${COMP}."
                    echo "Put the firmware into dist/firmware/${COMP}/ or pass --skip-fw / --allow-missing-fw."
                    exit 1
                fi
            fi
        done
    else
        echo "      Skipping the Maui firmware download (--skip-fw)."
    fi
    # The station selects the images of these components by their STABLE.json
    # (it refuses several images without one), so they must come with a valid
    # one (image present, md5 matching); also for --skip-fw.
    if [ ${#FW_STABLE_REQUIRED[@]} -gt 0 ] && ! python3 "${PROJECT_ROOT}/tools/maui_fw_fetch.py" \
            --dest-root "${BUNDLE_DIR}/firmware" --verify "${FW_STABLE_REQUIRED[@]}"; then
        if [ "$ALLOW_MISSING_FW" = true ]; then
            echo "WARNING: ${FW_STABLE_REQUIRED[*]} without a valid STABLE.json (--allow-missing-fw)."
        else
            echo "ERROR: ${FW_STABLE_REQUIRED[*]} need a valid STABLE.json (the stable markers of gs://maui-firmware, or dist/firmware/<component>/ with its STABLE.json)."
            exit 1
        fi
    fi
fi

# 3. Build / Package Debian Package
if [ "$SKIP_DEB" = false ]; then
    echo "[3/4] Building DiagnoseMe Debian package (${EDITION} edition)..."
    pushd "${PROJECT_ROOT}" > /dev/null
    # Maui editions bundle the stable maui-utils image into the deb.
    if [ "$("${EDITION_INFO[@]}" get "${EDITION}" maui_utils)" = "true" ]; then
        MAUI_UTILS_ARGS=()
        if [ -n "${MAUI_UTILS_TAR}" ]; then
            MAUI_UTILS_ARGS+=(--tar "${MAUI_UTILS_TAR}")
        fi
        ./tools/fetch_maui_utils.sh "${MAUI_UTILS_ARGS[@]}"
    fi
    # Only the editions with Servo v4p1 tooling (realtek_tools) need the
    # Realtek tools in dist/.
    ./tools/fetch_rtk_tools.sh "${EDITION}"
    # Builder image named per edition (parallel bundle builds), removed again.
    DEB_BUILDER_IMAGE="diagnoseme-deb-builder-${EDITION}"
    docker build --build-arg EDITION="${EDITION}" \
        --build-arg DEB_VERSION="${DEB_VERSION}" \
        -t "${DEB_BUILDER_IMAGE}" -f development_environment/deb_builder/Dockerfile .
    CONTAINER_ID=$(docker create "${DEB_BUILDER_IMAGE}")
    docker cp "${CONTAINER_ID}:/diagnoseme.deb" "${BUNDLE_DIR}/deb/diagnoseme_amd64.deb"
    docker rm "${CONTAINER_ID}" > /dev/null 2>&1 || true
    docker rmi "${DEB_BUILDER_IMAGE}" > /dev/null 2>&1 || true
    popd > /dev/null
    if [ ! -f "${BUNDLE_DIR}/deb/diagnoseme_amd64.deb" ]; then
        echo "ERROR: diagnoseme_amd64.deb was not generated."
        exit 1
    fi
else
    echo "[3/4] Skipping Debian package build."
    DEB_VERSION=""
fi

# 4. Generate install-update.sh and MANIFEST.json
echo "[4/4] Generating installation launcher and MANIFEST.json..."

# Ship the offline update tool with the bundle, so stations without (or with
# an older) diagnoseme package can apply it.
install -m 755 "${DEB_BUILDER_DIR}/diagnoseme-offline-update" "${BUNDLE_DIR}/diagnoseme-offline-update"

cat << 'EOF' > "${BUNDLE_DIR}/install-update.sh"
#!/bin/bash
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$EUID" -ne 0 ]; then
    echo "Please run as root (or via sudo): sudo ./install-update.sh"
    exit 1
fi

# Options such as --switch-edition are passed on.
exec bash "$DIR/diagnoseme-offline-update" "$@" "$DIR"
EOF
chmod +x "${BUNDLE_DIR}/install-update.sh"

# Compute SHA256 Checksums
BUNDLE_DIR="${BUNDLE_DIR}" EDITION="${EDITION}" DEB_PACKAGE="${DEB_PACKAGE}" DEB_VERSION="${DEB_VERSION}" \
    STATION_TAG="${STATION_TAG}" COMMIT="${COMMIT}" IMAGES_INFO="${IMAGES_INFO}" \
    python3 - << 'EOF'
import datetime
import hashlib
import json
import os

bundle_dir = os.environ["BUNDLE_DIR"]
manifest = {
    "version": "1.0.0",
    "build_date": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    # The station's diagnoseme-offline-update refuses a bundle of another
    # edition (unless --switch-edition) and checks the deb's Package field.
    "edition": os.environ["EDITION"],
    "deb_package": os.environ["DEB_PACKAGE"],
    # Version of the bundled deb (null with --skip-deb).
    "deb_version": os.environ["DEB_VERSION"] or None,
    "docker_tag": os.environ["STATION_TAG"],
    # Commit of the deb, firmware selection and maui-utils (the checkout).
    "commit": os.environ["COMMIT"] or "unknown",
    # The bundled images: exported tag, image id and registry digests.
    "images": {},
    "files": {}
}
with open(os.environ["IMAGES_INFO"], encoding="utf-8") as f:
    for line in f:
        name, tag, image_id, digests = (line.rstrip("\n").split("\t") + [""] * 4)[:4]
        manifest["images"][name] = {
            "tag": tag,
            "id": image_id,
            "repo_digests": digests.split(),
        }


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


for root, _, filenames in os.walk(bundle_dir):
    for fn in sorted(filenames):
        if fn == "MANIFEST.json":
            continue
        full_path = os.path.join(root, fn)
        rel_path = os.path.relpath(full_path, bundle_dir)
        manifest["files"][rel_path] = sha256_file(full_path)

with open(os.path.join(bundle_dir, "MANIFEST.json"), "w") as f:
    json.dump(manifest, f, indent=2)

print("MANIFEST.json generated with", len(manifest["files"]), "file entries.")
EOF
rm -f "${IMAGES_INFO}"

# 5. Compress to ZIP if requested
if [ "$ZIP_OUTPUT" = true ]; then
    ZIP_NAME="${OUTPUT_DIR}/diagnoseme-offline-bundle-${EDITION}-$(date +'%Y%m%d').zip"
    echo "Creating compressed ZIP archive: ${ZIP_NAME}..."
    pushd "${OUTPUT_DIR}" > /dev/null
    zip -r "${ZIP_NAME}" "diagnoseme-update"
    popd > /dev/null
    echo "Bundle archive ready: ${ZIP_NAME}"
fi

echo "=================================================="
echo " Offline update bundle generated successfully!"
echo " Ready to copy '${BUNDLE_DIR}' to a USB memory drive."
echo "=================================================="
