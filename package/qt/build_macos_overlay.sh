#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-2.1-or-later
# Build & overlay Cocoa accessibility lifetime fixes inside a locked Pixi env.
# Invoke before FreeCAD CMake configure; final app-bundle signing remains unchanged.
set -euo pipefail

readonly qt_version='6.11.2'
readonly qt_source_sha256='5b2e00eccaf5a4d8c14134ffa0ea8dfd0a35ae1ffc7f8d87fa4305a1ed23cf22'
readonly vulkan_headers_version='1.4.357'
readonly vulkan_headers_sha256='7dc0dbcf1d49dd3d7da3761c251c6097dfbaac475321a4a8a99269d3d5abecdc'
readonly glib_version='2.90.0'
readonly glib_headers_sha256='c80cdb6c599de22c9779a4b9b9a76923ed27c697c3416aec171413d2cdddb66a'
readonly glib_headers_url='https://conda.anaconda.org/conda-forge/osx-arm64/glib-2.90.0-h37000cc_0.conda'
readonly qt_source_url="https://download.qt.io/official_releases/qt/6.11/6.11.2/submodules/qtbase-everywhere-src-${qt_version}.tar.xz"
readonly vulkan_headers_url="https://github.com/KhronosGroup/Vulkan-Headers/archive/refs/tags/v${vulkan_headers_version}.tar.gz"
readonly overlay_root="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/freecad-qtgui-overlay-${qt_version}"
readonly patch_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/patches"

fail() {
    printf 'Qt accessibility overlay: %s\n' "$*" >&2
    exit 1
}

rpaths() {
    otool -l "$1" | awk '/cmd LC_RPATH/{want=1; next} want && $1 == "path" {print $2; want=0}'
}

normalize_feature_contract() {
    local module="$1"
    local contract="$2"
    local line key values

    while IFS= read -r line; do
        case "$line" in
            "QT.${module}.enabled_features = "*|"QT.${module}.disabled_features = "*|"QT.${module}.CONFIG = "*)
                key="${line%% = *}"
                values="${line#* = }"
                printf '%s = ' "$key"
                printf '%s\n' "$values" | tr ' ' '\n' | sed '/^$/d' | LC_ALL=C sort -u | tr '\n' ' '
                printf '\n'
                ;;
        esac
    done < "$contract"
}

normalize_config_header() {
    awk '/^#define QT_FEATURE_/ { print $2 " " $3 }' "$1" | LC_ALL=C sort
}

normalize_rpaths() {
    local binary="$1"
    local prefix_binary="$2"
    local rpath

    while IFS= read -r rpath; do
        [[ -n "$rpath" ]] || continue
        /usr/bin/install_name_tool -delete_rpath "$rpath" "$binary"
    done < <(rpaths "$binary")
    while IFS= read -r rpath; do
        [[ -n "$rpath" ]] || continue
        /usr/bin/install_name_tool -add_rpath "$rpath" "$binary"
    done < <(rpaths "$prefix_binary")
}

check_exported_symbols() {
    local original="$1"
    local replacement="$2"
    local label="$3"
    local missing="$overlay_root/${label}-missing-symbols.txt"

    /usr/bin/nm -gU "$original" | awk 'NF { print $NF }' | LC_ALL=C sort -u > "$overlay_root/${label}-original-symbols.txt"
    /usr/bin/nm -gU "$replacement" | awk 'NF { print $NF }' | LC_ALL=C sort -u > "$overlay_root/${label}-replacement-symbols.txt"
    LC_ALL=C comm -23 "$overlay_root/${label}-original-symbols.txt" "$overlay_root/${label}-replacement-symbols.txt" > "$missing"
    if [[ -s "$missing" ]]; then
        cat "$missing" >&2
        fail "$label lost exported symbols from locked package"
    fi
}

[[ "$(uname -s)" == Darwin ]] || fail 'requires macOS'
[[ "$(uname -m)" == arm64 ]] || fail 'requires arm64'
[[ -n "${CONDA_PREFIX:-}" ]] || fail 'requires active Pixi/Conda environment'
for patch_name in 0001-cocoa-a11y-remove-cache-entry-before-notify.patch 0003-cocoa-a11y-preserve-borrowed-table-interfaces.patch; do
    [[ -f "$patch_dir/$patch_name" ]] || fail "missing patch: $patch_name"
done
[[ -x "${CONDA_PREFIX}/bin/cmake" ]] || fail 'missing CMake in active environment'
[[ -x "${CONDA_PREFIX}/bin/ninja" ]] || fail 'missing Ninja in active environment'
[[ -x /usr/bin/nm ]] || fail 'missing macOS nm'
[[ -x /usr/bin/install_name_tool ]] || fail 'missing macOS install_name_tool'
[[ -f "${CONDA_PREFIX}/lib/libQt6Gui.6.dylib" ]] || fail 'active environment lacks QtGui 6 dylib'
[[ -f "${CONDA_PREFIX}/lib/libQt6Core.6.dylib" ]] || fail 'active environment lacks QtCore 6 dylib'

core_version_file="${CONDA_PREFIX}/lib/cmake/Qt6Core/Qt6CoreConfigVersionImpl.cmake"
[[ -f "$core_version_file" ]] || fail 'missing Qt6Core CMake version contract'
actual_qt_version="$(awk '/^set\(PACKAGE_VERSION "/ { gsub(/^set\(PACKAGE_VERSION "|"\)$/, ""); print; exit }' "$core_version_file")"
[[ "$actual_qt_version" == "$qt_version" ]] || fail "requires Qt ${qt_version}; found ${actual_qt_version:-unknown}"

# Fork portable builds set this before feature probes. QtGui must use this floor.
: "${MACOSX_DEPLOYMENT_TARGET:=13.3}"
export MACOSX_DEPLOYMENT_TARGET
[[ "$MACOSX_DEPLOYMENT_TARGET" == 13.3 ]] || fail "requires MACOSX_DEPLOYMENT_TARGET=13.3; found $MACOSX_DEPLOYMENT_TARGET"

prefix_gui="${CONDA_PREFIX}/lib/libQt6Gui.6.dylib"
prefix_core="${CONDA_PREFIX}/lib/libQt6Core.6.dylib"
prefix_cocoa="${CONDA_PREFIX}/lib/qt6/plugins/platforms/libqcocoa.dylib"
[[ -f "$prefix_cocoa" ]] || fail 'active environment lacks Cocoa platform plugin'
prefix_core_sha256="$(shasum -a 256 "$prefix_core" | awk '{print $1}')"
prefix_gui_id="$(otool -D "$prefix_gui" | tail -n 1 | tr -d '[:space:]')"
prefix_core_id="$(otool -D "$prefix_core" | tail -n 1 | tr -d '[:space:]')"
[[ "$prefix_gui_id" == '@rpath/libQt6Gui.6.dylib' ]] || fail "unexpected prefix QtGui install name: $prefix_gui_id"
[[ "$prefix_core_id" == '@rpath/libQt6Core.6.dylib' ]] || fail "unexpected prefix QtCore install name: $prefix_core_id"

prefix_gui_pri="${CONDA_PREFIX}/lib/qt6/mkspecs/modules/qt_lib_gui.pri"
prefix_core_pri="${CONDA_PREFIX}/lib/qt6/mkspecs/modules/qt_lib_core.pri"
prefix_gui_config="${CONDA_PREFIX}/include/qt6/QtGui/qtgui-config.h"
prefix_gui_private_config="${CONDA_PREFIX}/include/qt6/QtGui/${qt_version}/QtGui/private/qtgui-config_p.h"
prefix_core_config="${CONDA_PREFIX}/include/qt6/QtCore/qtcore-config.h"
prefix_core_private_config="${CONDA_PREFIX}/include/qt6/QtCore/${qt_version}/QtCore/private/qtcore-config_p.h"
[[ -f "$prefix_gui_pri" ]] || fail 'missing prefix QtGui feature contract'
[[ -f "$prefix_core_pri" ]] || fail 'missing prefix QtCore feature contract'
[[ -f "$prefix_gui_config" && -f "$prefix_gui_private_config" ]] || fail 'missing prefix QtGui feature headers'
[[ -f "$prefix_core_config" && -f "$prefix_core_private_config" ]] || fail 'missing prefix QtCore feature headers'

mkdir -p "$overlay_root"
archive="$overlay_root/qtbase-everywhere-src-${qt_version}.tar.xz"
source_root="$overlay_root/qtbase-everywhere-src-${qt_version}"
vulkan_headers_archive="$overlay_root/Vulkan-Headers-${vulkan_headers_version}.tar.gz"
vulkan_headers_root="$overlay_root/Vulkan-Headers-${vulkan_headers_version}"
vulkan_include_dir="$vulkan_headers_root/include"
glib_headers_archive="$overlay_root/glib-${glib_version}-headers.conda"
glib_include_dir="$overlay_root/glib-${glib_version}-include"
build_root="$overlay_root/build"
targets_file="$overlay_root/ninja-targets.txt"
symbols_file="$overlay_root/qtgui-symbols.txt"
prefix_gui_features="$overlay_root/prefix-qtgui-features.txt"
built_gui_features="$overlay_root/built-qtgui-features.txt"
prefix_core_features="$overlay_root/prefix-qtcore-features.txt"
built_core_features="$overlay_root/built-qtcore-features.txt"
prefix_gui_header_features="$overlay_root/prefix-qtgui-header-features.txt"
built_gui_header_features="$overlay_root/built-qtgui-header-features.txt"
prefix_gui_private_features="$overlay_root/prefix-qtgui-private-features.txt"
built_gui_private_features="$overlay_root/built-qtgui-private-features.txt"
prefix_core_header_features="$overlay_root/prefix-qtcore-header-features.txt"
built_core_header_features="$overlay_root/built-qtcore-header-features.txt"
prefix_core_private_features="$overlay_root/prefix-qtcore-private-features.txt"
built_core_private_features="$overlay_root/built-qtcore-private-features.txt"

if [[ ! -f "$archive" ]]; then
    curl --fail --location --retry 3 --output "$archive" "$qt_source_url"
fi
[[ "$(shasum -a 256 "$archive" | awk '{print $1}')" == "$qt_source_sha256" ]] \
    || fail 'official qtbase source SHA-256 mismatch'

if [[ ! -d "$source_root" ]]; then
    tar -xf "$archive" -C "$overlay_root"
fi
[[ -f "$source_root/src/gui/accessible/qaccessiblecache_mac.mm" ]] || fail 'qtbase source layout mismatch'

# Qt's locked GUI feature set requires Vulkan headers, while Pixi's runtime
# loader package intentionally does not provide development headers. Keep these
# checksum-pinned Khronos headers in CI scratch; do not alter prefix libraries.
if [[ ! -f "$vulkan_headers_archive" ]]; then
    curl --fail --location --retry 3 --output "$vulkan_headers_archive" "$vulkan_headers_url"
fi
[[ "$(shasum -a 256 "$vulkan_headers_archive" | awk '{print $1}')" == "$vulkan_headers_sha256" ]] \
    || fail 'Vulkan-Headers source SHA-256 mismatch'
if [[ ! -d "$vulkan_headers_root" ]]; then
    tar -xzf "$vulkan_headers_archive" -C "$overlay_root"
fi
vulkan_header="$vulkan_include_dir/vulkan/vulkan_core.h"
[[ -f "$vulkan_header" ]] || fail 'Vulkan-Headers source layout mismatch'
vulkan_header_patch="$(awk '/^#define VK_HEADER_VERSION / { print $3; exit }' "$vulkan_header")"
[[ "$vulkan_header_patch" == "${vulkan_headers_version##*.}" ]] \
    || fail "Vulkan-Headers version mismatch: $vulkan_header_patch"

# libglib supplies public headers, but glibconfig.h lives in its matching
# development package. Extract only that generated header to CI scratch;
# the environment's libraries & package metadata remain untouched.
glib_pc="${CONDA_PREFIX}/lib/pkgconfig/glib-2.0.pc"
[[ -f "$glib_pc" ]] || fail 'missing locked GLib version contract'
[[ "$(awk '/^Version:/ { print $2; exit }' "$glib_pc")" == "$glib_version" ]] \
    || fail "requires locked GLib ${glib_version}"
if [[ ! -f "$glib_headers_archive" ]]; then
    curl --fail --location --retry 3 --output "$glib_headers_archive" "$glib_headers_url"
fi
[[ "$(shasum -a 256 "$glib_headers_archive" | awk '{print $1}')" == "$glib_headers_sha256" ]] \
    || fail 'GLib development package SHA-256 mismatch'
"${CONDA_PREFIX}/bin/python" - "$glib_headers_archive" "$glib_include_dir" "$CONDA_PREFIX" <<'PY'
import io
from pathlib import Path
import subprocess
import sys
import tarfile
import zipfile

archive, output, prefix = sys.argv[1:]
with zipfile.ZipFile(archive) as package:
    payloads = [name for name in package.namelist() if name.startswith("pkg-") and name.endswith(".tar.zst")]
    if len(payloads) != 1:
        raise SystemExit("unexpected GLib development package layout")
    payload = subprocess.run(
        [str(Path(prefix) / "bin/zstd"), "--decompress", "--stdout"],
        input=package.read(payloads[0]), capture_output=True, check=True,
    ).stdout
with tarfile.open(fileobj=io.BytesIO(payload)) as contents:
    header = contents.extractfile("lib/glib-2.0/include/glibconfig.h")
    if header is None:
        raise SystemExit("GLib development package lacks glibconfig.h")
    output_path = Path(output)
    output_path.mkdir(parents=True, exist_ok=True)
    (output_path / "glibconfig.h").write_bytes(header.read())
PY

for patch_name in 0001-cocoa-a11y-remove-cache-entry-before-notify.patch 0003-cocoa-a11y-preserve-borrowed-table-interfaces.patch; do
    patch_file="$patch_dir/$patch_name"
    if patch --batch --dry-run --forward -p1 -d "$source_root" < "$patch_file" >/dev/null; then
        patch --batch --forward -p1 -d "$source_root" < "$patch_file"
    elif ! patch --batch --dry-run --reverse -p1 -d "$source_root" < "$patch_file" >/dev/null; then
        fail "accessibility patch does not apply: $patch_name"
    fi
done

grep -Fq 'accessibleElements.take(axid)' "$source_root/src/gui/accessible/qaccessiblecache_mac.mm" \
    || fail 'patched source verification failed'

# Match qt-main-feedstock's Qt 6.11.2 macOS configuration. Qt6Gui brings up a
# source-matched QtCore build dependency; prefix QtCore remains locked.
"${CONDA_PREFIX}/bin/cmake" -S "$source_root" -B "$build_root" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$CONDA_PREFIX" \
    -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON \
    -DCMAKE_OSX_DEPLOYMENT_TARGET=13.3 \
    -DCMAKE_PREFIX_PATH="$CONDA_PREFIX" \
    -DCMAKE_FIND_FRAMEWORK=LAST \
    -DCMAKE_INSTALL_RPATH:STRING="${CONDA_PREFIX}/lib" \
    -DCMAKE_UNITY_BUILD=ON \
    -DCMAKE_UNITY_BUILD_BATCH_SIZE=32 \
    -DINSTALL_BINDIR=lib/qt6/bin \
    -DINSTALL_PUBLICBINDIR=bin \
    -DINSTALL_LIBEXECDIR=lib/qt6 \
    -DINSTALL_DOCDIR=share/doc/qt6 \
    -DINSTALL_ARCHDATADIR=lib/qt6 \
    -DINSTALL_DATADIR=share/qt6 \
    -DINSTALL_INCLUDEDIR=include/qt6 \
    -DINSTALL_MKSPECSDIR=lib/qt6/mkspecs \
    -DINSTALL_EXAMPLESDIR=share/doc/qt6/examples \
    -DFEATURE_system_sqlite=ON \
    -DFEATURE_cxx20_format=OFF \
    -DFEATURE_framework=OFF \
    -DFEATURE_gssapi=OFF \
    -DFEATURE_enable_new_dtags=OFF \
    -DFEATURE_openssl_linked=ON \
    -DFEATURE_vulkan=ON \
    -DVulkan_INCLUDE_DIR:PATH="$vulkan_include_dir" \
    -DGLIB2_INTERNAL_INCLUDE_DIR:PATH="$glib_include_dir" \
    -DQT_BUILD_EXAMPLES=OFF \
    -DQT_BUILD_TESTS=OFF

# qmake module contracts are generated at configure time. Compare both Gui &
# Core before building: QtGui will load locked prefix Core at runtime.
built_gui_pri="$(find "$build_root" -path '*/mkspecs/modules/qt_lib_gui.pri' -type f -print -quit)"
built_core_pri="$(find "$build_root" -path '*/mkspecs/modules/qt_lib_core.pri' -type f -print -quit)"
built_gui_config="$build_root/src/gui/qtgui-config.h"
built_gui_private_config="$build_root/src/gui/qtgui-config_p.h"
built_core_config="$build_root/src/corelib/qtcore-config.h"
built_core_private_config="$build_root/src/corelib/qtcore-config_p.h"
[[ -n "$built_gui_pri" && -f "$built_gui_pri" ]] || fail 'missing configured QtGui feature contract'
[[ -n "$built_core_pri" && -f "$built_core_pri" ]] || fail 'missing configured QtCore feature contract'
[[ -f "$built_gui_config" && -f "$built_gui_private_config" ]] || fail 'missing configured QtGui feature headers'
[[ -f "$built_core_config" && -f "$built_core_private_config" ]] || fail 'missing configured QtCore feature headers'
normalize_feature_contract gui "$prefix_gui_pri" > "$prefix_gui_features"
normalize_feature_contract gui "$built_gui_pri" > "$built_gui_features"
normalize_feature_contract core "$prefix_core_pri" > "$prefix_core_features"
normalize_feature_contract core "$built_core_pri" > "$built_core_features"
diff -u "$prefix_gui_features" "$built_gui_features" || fail 'QtGui feature contract differs from locked prefix'
diff -u "$prefix_core_features" "$built_core_features" || fail 'QtCore feature contract differs from locked prefix'
normalize_config_header "$prefix_gui_config" > "$prefix_gui_header_features"
normalize_config_header "$built_gui_config" > "$built_gui_header_features"
normalize_config_header "$prefix_gui_private_config" > "$prefix_gui_private_features"
normalize_config_header "$built_gui_private_config" > "$built_gui_private_features"
normalize_config_header "$prefix_core_config" > "$prefix_core_header_features"
normalize_config_header "$built_core_config" > "$built_core_header_features"
normalize_config_header "$prefix_core_private_config" > "$prefix_core_private_features"
normalize_config_header "$built_core_private_config" > "$built_core_private_features"
diff -u "$prefix_gui_header_features" "$built_gui_header_features" || fail 'QtGui public feature header differs from locked prefix'
diff -u "$prefix_gui_private_features" "$built_gui_private_features" || fail 'QtGui private feature header differs from locked prefix'
diff -u "$prefix_core_header_features" "$built_core_header_features" || fail 'QtCore public feature header differs from locked prefix'
diff -u "$prefix_core_private_features" "$built_core_private_features" || fail 'QtCore private feature header differs from locked prefix'

# Qt's target spelling differs between generators/releases. Prefer Qt6Gui but
# inspect Ninja's configured targets instead of assuming either alias exists.
"${CONDA_PREFIX}/bin/ninja" -C "$build_root" -t targets all > "$targets_file"
qt_gui_target=''
for candidate in Qt6Gui Gui; do
    if grep -Eq "^${candidate}:" "$targets_file"; then
        qt_gui_target="$candidate"
        break
    fi
done
[[ -n "$qt_gui_target" ]] || fail 'configured qtbase has no QtGui build target'
grep -Eq '^QCocoaIntegrationPlugin:' "$targets_file" || fail 'configured qtbase has no Cocoa plugin build target'
"${CONDA_PREFIX}/bin/cmake" --build "$build_root" --target "$qt_gui_target" QCocoaIntegrationPlugin

built_gui="$build_root/lib/libQt6Gui.${qt_version}.dylib"
[[ -f "$built_gui" ]] || fail "missing built QtGui dylib: $built_gui"
built_gui_id="$(otool -D "$built_gui" | tail -n 1 | tr -d '[:space:]')"
[[ "$built_gui_id" == "$prefix_gui_id" ]] || fail "QtGui install name mismatch: $built_gui_id"

# Qt's raw build RPATH contains its temporary build prefix. Replace it with
# exact locked-prefix entries before comparison & overlay.
normalize_rpaths "$built_gui" "$prefix_gui"
diff -u <(rpaths "$prefix_gui") <(rpaths "$built_gui") \
    || fail 'QtGui runtime rpaths differ from locked prefix'

built_cocoa="$build_root/plugins/platforms/libqcocoa.dylib"
[[ -f "$built_cocoa" ]] || fail "missing built Cocoa plugin: $built_cocoa"
[[ "$(otool -D "$built_cocoa" | sed '1d')" == "$(otool -D "$prefix_cocoa" | sed '1d')" ]] \
    || fail 'Cocoa plugin install name differs from locked prefix'
normalize_rpaths "$built_cocoa" "$prefix_cocoa"
diff -u <(rpaths "$prefix_cocoa") <(rpaths "$built_cocoa") \
    || fail 'Cocoa plugin runtime rpaths differ from locked prefix'
check_exported_symbols "$prefix_gui" "$built_gui" qtgui
check_exported_symbols "$prefix_cocoa" "$built_cocoa" qcocoa

# Keep Core & QtWidgets from the locked Pixi package. Only the two libraries
# containing patched accessibility code are replaced.
cp "$built_gui" "${CONDA_PREFIX}/lib/libQt6Gui.${qt_version}.dylib"
ln -sfn "libQt6Gui.${qt_version}.dylib" "${CONDA_PREFIX}/lib/libQt6Gui.6.dylib"
ln -sfn "libQt6Gui.${qt_version}.dylib" "${CONDA_PREFIX}/lib/libQt6Gui.dylib"
cp "$built_cocoa" "$prefix_cocoa"

/usr/bin/nm -gU "${CONDA_PREFIX}/lib/libQt6Gui.6.dylib" > "$symbols_file"
grep -Fq '__ZN16QAccessibleCache23removeAccessibleElementEj' "$symbols_file" \
    || fail 'overlaid QtGui lacks QAccessibleCache::removeAccessibleElement'
[[ "$(otool -D "$prefix_core" | tail -n 1 | tr -d '[:space:]')" == "$prefix_core_id" ]] \
    || fail 'locked QtCore install name changed'
[[ "$(shasum -a 256 "$prefix_core" | awk '{print $1}')" == "$prefix_core_sha256" ]] \
    || fail 'locked QtCore binary changed'
otool -L "$prefix_cocoa" > "$overlay_root/qcocoa-dependencies.txt"
grep -Fq '@rpath/libQt6Gui.6.dylib' "$overlay_root/qcocoa-dependencies.txt" \
    || fail 'Cocoa plugin no longer links QtGui overlay'
"${CONDA_PREFIX}/bin/python" - "$prefix_gui" "$prefix_cocoa" <<'PY'
import ctypes
import os
import sys

handles = [ctypes.CDLL(path, mode=os.RTLD_NOW | os.RTLD_LOCAL) for path in sys.argv[1:]]
print("QtGui & Cocoa plugin loaded against locked dependencies")
PY
printf 'Qt accessibility overlay ready: Qt %s, source SHA-256 %s, deployment target %s, target %s\n' \
    "$qt_version" "$qt_source_sha256" "$MACOSX_DEPLOYMENT_TARGET" "$qt_gui_target"
