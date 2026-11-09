from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

CALIBRATION_PATH = Path(__file__).parent / "calibration.json"
SCREENSHOT_PATH = Path(__file__).parent / "data" / "screenshots" / "calibration_capture.png"

MARKERS = [
    {"label": "QCAL_ALPHA", "px": 0.20, "py": 0.20},
    {"label": "QCAL_BRAVO", "px": 0.80, "py": 0.20},
    {"label": "QCAL_CHARL", "px": 0.50, "py": 0.50},
    {"label": "QCAL_DELTA", "px": 0.20, "py": 0.80},
    {"label": "QCAL_ECHO0", "px": 0.80, "py": 0.80},
]
FONT_SIZE = 28


def _save_calibration(data: dict) -> None:
    CALIBRATION_PATH.write_text(json.dumps(data, indent=2))


def _capture_screenshot() -> bool:
    SCREENSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["screencapture", "-x", str(SCREENSHOT_PATH)],
            check=True,
            timeout=10,
        )
        return SCREENSHOT_PATH.exists()
    except Exception:
        return False


def run_calibration() -> None:
    from AppKit import (
        NSApplication,
        NSBackingStoreBuffered,
        NSColor,
        NSFont,
        NSScreen,
        NSTextField,
        NSTextAlignmentCenter,
        NSWindow,
    )
    from Foundation import NSMakeRect, NSRunLoop, NSDate

    print("\n── Automatic Display Calibration ──────────────────────────")
    print("  A full-screen calibration window will appear briefly.")
    print("  Do not move the mouse or switch windows.\n")

    app = NSApplication.sharedApplication()

    screen = NSScreen.mainScreen()
    frame = screen.frame()
    screen_w = int(frame.size.width)
    screen_h = int(frame.size.height)

    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        frame,
        0,
        NSBackingStoreBuffered,
        False,
    )
    window.setLevel_(9999)
    window.setBackgroundColor_(NSColor.whiteColor())
    window.setOpaque_(True)

    content_view = window.contentView()
    font = NSFont.userFixedPitchFontOfSize_(FONT_SIZE)

    targets: dict[str, tuple[int, int]] = {}
    for m in MARKERS:
        x = int(screen_w * m["px"])
        y = int(screen_h * m["py"])
        targets[m["label"]] = (x, y)

        ns_y = screen_h - y
        field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(x - 120, ns_y - 18, 240, 36)
        )
        field.setStringValue_(m["label"])
        field.setBezeled_(False)
        field.setDrawsBackground_(False)
        field.setEditable_(False)
        field.setSelectable_(False)
        field.setAlignment_(NSTextAlignmentCenter)
        field.setFont_(font)
        field.setTextColor_(NSColor.blackColor())
        content_view.addSubview_(field)

    print(f"  Screen size:  {screen_w} × {screen_h}")
    print(f"  Markers:      {len(targets)}")
    for label, (x, y) in targets.items():
        print(f"    {label}  →  ({x}, {y})")

    window.makeKeyAndOrderFront_(None)
    app.activateIgnoringOtherApps_(True)

    until = NSDate.dateWithTimeIntervalSinceNow_(2.0)
    NSRunLoop.currentRunLoop().runUntilDate_(until)

    print("\n  Taking screenshot …")
    if not _capture_screenshot():
        window.close()
        print("  ✗ Screenshot failed.")
        sys.exit(1)

    window.close()
    print("  ✓ Screenshot captured.")

    print("  Running OCR …")
    from src.tools.mapper_tools import ExtractScreenshotTextTool

    ocr_tool = ExtractScreenshotTextTool()
    ocr_json = ocr_tool._run(str(SCREENSHOT_PATH))
    if ocr_json.startswith("Error"):
        print(f"  ✗ OCR failed: {ocr_json}")
        sys.exit(1)

    try:
        elements = json.loads(ocr_json)
    except json.JSONDecodeError:
        print(f"  ✗ OCR produced invalid JSON: {ocr_json[:200]}")
        sys.exit(1)

    print(f"  ✓ OCR detected {len(elements)} elements.")

    offsets: list[dict] = []
    for label, (expected_x, expected_y) in targets.items():
        for el in elements:
            detected_text = el.get("text", "").strip()
            if label in detected_text or detected_text in label:
                det_x = el.get("center_x", 0)
                det_y = el.get("center_y", 0)
                dx = expected_x - det_x
                dy = expected_y - det_y
                offsets.append(
                    {
                        "label": label,
                        "expected": [expected_x, expected_y],
                        "detected": [det_x, det_y],
                        "offset": [dx, dy],
                    }
                )
                print(
                    f"    {label}: expected ({expected_x}, {expected_y}) "
                    f"detected ({det_x}, {det_y}) "
                    f"→ offset ({dx:+d}, {dy:+d})"
                )
                break
        else:
            print(f"    {label}: NOT FOUND by OCR — skipped")

    if len(offsets) < 2:
        print(
            "\n  ✗ Too few markers detected. Try adjusting font size or "
            "screen brightness."
        )
        sys.exit(1)

    ox = [o["offset"][0] for o in offsets]
    oy = [o["offset"][1] for o in offsets]

    def _remove_outliers(vals: list[float]) -> list[int]:
        sorted_v = sorted(vals)
        median = sorted_v[len(sorted_v) // 2]
        abs_devs = [abs(v - median) for v in vals]
        mad = sorted(abs_devs)[len(abs_devs) // 2] or 1.0
        return [i for i, v in enumerate(vals) if abs(v - median) <= 3 * mad]

    keep = set(_remove_outliers(ox)) & set(_remove_outliers(oy))
    if len(keep) < 2:
        keep = set(range(len(offsets)))

    clean = [offsets[i] for i in sorted(keep)]
    dropped = len(offsets) - len(clean)

    avg_x = sum(o["offset"][0] for o in clean) / len(clean)
    avg_y = sum(o["offset"][1] for o in clean) / len(clean)

    print("\n── Results ────────────────────────────────────────────────")
    print(f"  Markers matched:  {len(offsets)}")
    print(
        f"  Samples used:     {len(clean)}"
        + (f"  ({dropped} outlier(s) discarded)" if dropped else "")
    )
    print(f"  Avg X offset:     {avg_x:+.1f} px")
    print(f"  Avg Y offset:     {avg_y:+.1f} px")

    cal = {
        "offset_x": round(avg_x, 1),
        "offset_y": round(avg_y, 1),
        "samples": len(clean),
        "per_sample": clean,
    }

    _save_calibration(cal)
    print(f"\n  ✓ Calibration saved to {CALIBRATION_PATH}")
    print("───────────────────────────────────────────────────────────\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Automatic display calibration for click precision"
    )
    parser.add_argument("--reset", action="store_true", help="Delete saved calibration")
    args = parser.parse_args()

    if args.reset:
        if CALIBRATION_PATH.exists():
            CALIBRATION_PATH.unlink()
            print("Calibration reset.")
        else:
            print("No calibration file found.")
        return

    run_calibration()


if __name__ == "__main__":
    main()
