#!/usr/bin/env python3
#
# SPDX-FileCopyrightText: 2016 The CyanogenMod Project
# SPDX-FileCopyrightText: 2017-2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

import argparse
import os
import subprocess
import sys


# ── Paths ────────────────────────────────────────────────────────────────────

MY_DIR = os.path.dirname(os.path.realpath(__file__))
ANDROID_ROOT = os.path.join(MY_DIR, "..", "..", "..")
HELPER = os.path.join(ANDROID_ROOT, "tools", "extract-utils", "extract_utils.sh")


# ── Sanity check ─────────────────────────────────────────────────────────────

if not os.path.isfile(HELPER):
    print(f"Unable to find helper script at {HELPER}", file=sys.stderr)
    sys.exit(1)


# ── Blob fixup ────────────────────────────────────────────────────────────────

PATCHELF = os.environ.get("PATCHELF", "patchelf")

LIBCRYPTO_SHIM = "libcrypto_shim.so"
BLOBS_NEEDING_CRYPTO_SHIM = {
    "vendor/lib64/libwvhidl.so",
    "vendor/lib64/mediadrm/libwvdrmengine.so",
}


def blob_fixup(blob_path: str, file_path: str) -> bool:
    """
    Apply fixups to a blob.

    Args:
        blob_path: Relative vendor path of the blob (used for matching).
        file_path: Actual filesystem path to the blob file.
                   Pass an empty string for a dry-run check.

    Returns:
        True  – fixup was handled (blob matched a rule).
        False – blob did not match any rule.
    """
    if blob_path in BLOBS_NEEDING_CRYPTO_SHIM:
        if file_path == "":          # dry-run: just report that we handle it
            return True
        # Check whether the shim is already listed as a DT_NEEDED entry.
        result = subprocess.run(
            ["grep", "-q", LIBCRYPTO_SHIM, file_path],
            capture_output=True,
        )
        if result.returncode != 0:   # shim not yet present → add it
            subprocess.run(
                [PATCHELF, "--add-needed", LIBCRYPTO_SHIM, file_path],
                check=True,
            )
        return True

    return False


def blob_fixup_dry(blob_path: str) -> bool:
    """Dry-run variant: only checks whether blob_path matches a fixup rule."""
    return blob_fixup(blob_path, "")


# ── Helper wrappers ───────────────────────────────────────────────────────────

def _run_bash(script: str, env: dict | None = None) -> None:
    """Source the extract-utils helper and run an inline bash snippet."""
    merged_env = {**os.environ, **(env or {})}
    subprocess.run(
        ["bash", "-c", f'source "{HELPER}" && {script}'],
        check=True,
        env=merged_env,
    )


def setup_vendor(device: str, vendor: str, android_root: str,
                 is_common: bool, clean_vendor: bool) -> None:
    clean_flag = "true" if clean_vendor else "false"
    common_flag = "true" if is_common else "false"
    _run_bash(
        f'setup_vendor "{device}" "{vendor}" "{android_root}" '
        f'{common_flag} {clean_flag}'
    )


def extract(file_list: str, src: str, kang: str, section: str) -> None:
    kang_arg = "--kang" if kang else ""
    _run_bash(f'extract "{file_list}" "{src}" {kang_arg} --section "{section}"')


def extract_firmware(file_list: str, src: str) -> None:
    _run_bash(f'extract_firmware "{file_list}" "{src}"')


# ── Argument parsing ──────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract proprietary blobs for LineageOS device trees."
    )
    parser.add_argument(
        "src",
        nargs="?",
        default="adb",
        help="Source of blobs: a device path, a zip file, or 'adb' (default).",
    )
    parser.add_argument(
        "--only-common",
        action="store_true",
        help="Extract only the common device blobs.",
    )
    parser.add_argument(
        "--only-firmware",
        action="store_true",
        help="Extract only firmware blobs.",
    )
    parser.add_argument(
        "--only-target",
        action="store_true",
        help="Extract only the target device blobs.",
    )
    parser.add_argument(
        "-n", "--no-cleanup",
        dest="clean_vendor",
        action="store_false",
        default=True,
        help="Do not sanitize the vendor folder before extraction.",
    )
    parser.add_argument(
        "-k", "--kang",
        action="store_true",
        help="Enable kang mode (keep original blob hashes).",
    )
    parser.add_argument(
        "-s", "--section",
        default="",
        help="Extract only a specific section; implies --no-cleanup.",
    )
    return parser.parse_args()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()

    # --section implies no cleanup (mirrors original bash behaviour)
    if args.section:
        args.clean_vendor = False

    kang = "--kang" if args.kang else ""

    # Read DEVICE_COMMON / VENDOR / DEVICE from the environment, just as the
    # original script relies on them being set by the caller / device tree.
    device_common = os.environ.get("DEVICE_COMMON", "")
    vendor = os.environ.get("VENDOR", "")
    vendor_common = os.environ.get("VENDOR_COMMON", vendor)
    device = os.environ.get("DEVICE", "")

    # ── Common blobs ──────────────────────────────────────────────────────────
    if not args.only_firmware and not args.only_target:
        setup_vendor(device_common, vendor_common, ANDROID_ROOT,
                     is_common=True, clean_vendor=args.clean_vendor)

        proprietary_files = os.path.join(MY_DIR, "proprietary-files.txt")
        extract(proprietary_files, args.src, kang, args.section)

    # ── Target / device-specific blobs ───────────────────────────────────────
    device_proprietary = os.path.join(
        MY_DIR, "..", "..", vendor, device, "proprietary-files.txt"
    )

    if not args.only_common and os.path.isfile(device_proprietary) \
            and os.path.getsize(device_proprietary) > 0:

        # Re-source the device-specific extract-files script so that
        # DEVICE / VENDOR variables get updated for the target device.
        device_extract_script = os.path.join(
            MY_DIR, "..", "..", vendor, device, "extract-files.sh"
        )
        if os.path.isfile(device_extract_script):
            subprocess.run(
                ["bash", "-c", f'source "{device_extract_script}"'],
                check=True,
            )

        setup_vendor(device, vendor, ANDROID_ROOT,
                     is_common=False, clean_vendor=args.clean_vendor)

        if not args.only_firmware:
            extract(device_proprietary, args.src, kang, args.section)

        firmware_txt = os.path.join(
            MY_DIR, "..", "..", vendor, device, "proprietary-firmware.txt"
        )
        if not args.section and os.path.isfile(firmware_txt):
            extract_firmware(firmware_txt, args.src)

    # ── Regenerate makefiles ──────────────────────────────────────────────────
    setup_makefiles = os.path.join(MY_DIR, "setup-makefiles.sh")
    subprocess.run(["bash", setup_makefiles], check=True)


if __name__ == "__main__":
    main()
