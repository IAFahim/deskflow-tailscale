"""Lightweight temporary high-contrast QR overlay for PC pairing."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import List

_SRC_DIR = Path(__file__).resolve().parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

import qr_gen  # noqa: E402


def _show_tk(matrix: List[List[int]], code: str, label: str, timeout: int) -> None:
    import tkinter as tk

    root = tk.Tk()
    root.title(f"Deskflow Pair {code}")
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg="#ffffff")

    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    card_size = max(280, min(560, int(screen_h * 0.42)))
    n = len(matrix)
    quiet = 4
    mod_px = max(4, (card_size - 40) // (n + quiet * 2))
    qr_px = mod_px * (n + quiet * 2)
    win_w = qr_px + 44
    win_h = qr_px + 96
    x = max(0, (screen_w - win_w) // 2)
    y = max(0, (screen_h - win_h) // 2)
    root.geometry(f"{win_w}x{win_h}+{x}+{y}")

    frame = tk.Frame(root, bg="#ffffff", highlightbackground="#111827", highlightthickness=4)
    frame.pack(fill="both", expand=True)

    canvas = tk.Canvas(frame, width=qr_px, height=qr_px, bg="#ffffff", highlightthickness=0)
    canvas.pack(padx=18, pady=(18, 6))

    offset = quiet * mod_px
    for r, row in enumerate(matrix):
        for c, val in enumerate(row):
            if val:
                x0 = offset + c * mod_px
                y0 = offset + r * mod_px
                canvas.create_rectangle(x0, y0, x0 + mod_px, y0 + mod_px, fill="#000000", outline="")

    caption = f"{code}  ·  {label}" if label else code
    lbl = tk.Label(frame, text=caption, bg="#ffffff", fg="#0f172a", font=("Segoe UI", 18, "bold"))
    lbl.pack(pady=(0, 2))
    hint = tk.Label(frame, text="Scan with Deskflow Web UI  ·  ESC to close", bg="#ffffff", fg="#475569", font=("Segoe UI", 9))
    hint.pack(pady=(0, 10))

    root.bind("<Escape>", lambda _e: root.destroy())
    root.bind("<Button-1>", lambda _e: root.destroy())
    root.after(max(1, timeout) * 1000, root.destroy)
    root.mainloop()


def _show_gtk3(matrix: List[List[int]], code: str, label: str, timeout: int) -> None:
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gdk, GLib, Gtk

    win = Gtk.Window()
    win.set_title(f"Deskflow Pair {code}")
    win.set_decorated(False)
    win.set_keep_above(True)
    win.set_position(Gtk.WindowPosition.CENTER)

    screen_h = 1080
    display = Gdk.Display.get_default()
    if display is not None:
        mon = display.get_primary_monitor() or (display.get_monitor(0) if display.get_n_monitors() > 0 else None)
        if mon is not None:
            screen_h = mon.get_geometry().height

    card_size = max(280, min(560, int(screen_h * 0.42)))
    n = len(matrix)
    quiet = 4
    mod_px = max(4, (card_size - 40) // (n + quiet * 2))
    qr_px = mod_px * (n + quiet * 2)
    win_w = qr_px + 48
    win_h = qr_px + 96
    win.set_default_size(win_w, win_h)

    area = Gtk.DrawingArea()
    area.set_size_request(win_w, win_h)

    def on_draw(_widget, cr):
        # White card with dark border
        cr.set_source_rgb(1.0, 1.0, 1.0)
        cr.paint()
        cr.set_source_rgb(0.07, 0.09, 0.15)
        cr.set_line_width(6.0)
        cr.rectangle(3, 3, win_w - 6, win_h - 6)
        cr.stroke()

        # QR modules
        ox = (win_w - qr_px) / 2.0 + quiet * mod_px
        oy = 20.0 + quiet * mod_px
        cr.set_source_rgb(0.0, 0.0, 0.0)
        for r, row in enumerate(matrix):
            for c, val in enumerate(row):
                if val:
                    cr.rectangle(ox + c * mod_px, oy + r * mod_px, mod_px, mod_px)
        cr.fill()

        # Caption text
        caption = f"{code}  ·  {label}" if label else code
        cr.set_source_rgb(0.06, 0.09, 0.16)
        cr.select_font_face("Sans", 0, 1)
        cr.set_font_size(22.0)
        ext = cr.text_extents(caption)
        cr.move_to((win_w - ext.width) / 2.0, 20.0 + qr_px + 34.0)
        cr.show_text(caption)

        sub = "Scan with Deskflow Web UI  ·  ESC to close"
        cr.set_source_rgb(0.35, 0.41, 0.52)
        cr.select_font_face("Sans", 0, 0)
        cr.set_font_size(12.0)
        ext2 = cr.text_extents(sub)
        cr.move_to((win_w - ext2.width) / 2.0, 20.0 + qr_px + 58.0)
        cr.show_text(sub)
        return False

    area.connect("draw", on_draw)
    win.add(area)
    win.add_events(Gdk.EventMask.KEY_PRESS_MASK | Gdk.EventMask.BUTTON_PRESS_MASK)
    win.connect("key-press-event", lambda _w, e: Gtk.main_quit() if e.keyval == Gdk.KEY_Escape else None)
    win.connect("button-press-event", lambda _w, _e: Gtk.main_quit())
    win.connect("destroy", lambda _w: Gtk.main_quit())
    GLib.timeout_add_seconds(max(1, timeout), Gtk.main_quit)
    win.show_all()
    Gtk.main()


def main() -> None:
    parser = argparse.ArgumentParser(description="Display pairing QR overlay")
    parser.add_argument("--code", required=True)
    parser.add_argument("--label", default="")
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--headless-check", action="store_true")
    args = parser.parse_args()

    matrix = qr_gen.encode_qr(args.code)
    if args.headless_check:
        print(f"QR {args.code}: {len(matrix)}x{len(matrix)}")
        return

    # Prefer tkinter on Windows; prefer GTK3 on Linux Wayland/X11, falling back to tkinter.
    if os.name == "nt":
        _show_tk(matrix, args.code, args.label, args.timeout)
        return

    try:
        _show_gtk3(matrix, args.code, args.label, args.timeout)
        return
    except Exception:
        pass

    try:
        _show_tk(matrix, args.code, args.label, args.timeout)
        return
    except Exception:
        pass

    # Headless fallback: write SVG
    svg_path = Path("/tmp/deskflow-qr.svg")
    n = len(matrix)
    rects = "".join(
        f'<rect x="{c + 4}" y="{r + 4}" width="1" height="1" fill="#000"/>'
        for r, row in enumerate(matrix)
        for c, v in enumerate(row)
        if v
    )
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n + 8} {n + 8}"><rect width="100%" height="100%" fill="#fff"/>{rects}</svg>'
    svg_path.write_text(svg, encoding="utf-8")


if __name__ == "__main__":
    main()
