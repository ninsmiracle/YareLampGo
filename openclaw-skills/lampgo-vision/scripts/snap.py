#!/usr/bin/env python3
"""Capture a camera frame using OpenCV. Linux-compatible (no imagesnap dependency).

Usage:
    python3 snap.py <output_path> [--device <index>] [--width <W>] [--height <H>]

Returns JSON: {"ok": true, "path": "...", "size": 12345, "device": 0}
"""

import argparse
import json
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", help="Output image path (e.g. /tmp/snap.jpg)")
    parser.add_argument("--device", type=int, default=0, help="Camera device index")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    args = parser.parse_args()

    try:
        import cv2
    except ImportError:
        print(json.dumps({"ok": False, "error": "OpenCV not installed. Run: uv add opencv-python"}))
        sys.exit(1)

    cap = cv2.VideoCapture(args.device)
    if not cap.isOpened():
        print(json.dumps({"ok": False, "error": f"Cannot open camera device {args.device}"}))
        sys.exit(1)

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)

    # Warm up (discard first frames)
    for _ in range(5):
        cap.read()

    ret, frame = cap.read()
    cap.release()

    if not ret or frame is None:
        print(json.dumps({"ok": False, "error": "Failed to capture frame"}))
        sys.exit(1)

    cv2.imwrite(args.output, frame)
    import os
    size = os.path.getsize(args.output)
    print(json.dumps({"ok": True, "path": args.output, "size": size, "device": args.device}))


if __name__ == "__main__":
    main()
