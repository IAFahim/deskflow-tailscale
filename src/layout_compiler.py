"""Deskflow v1.27.0 configuration compiler and multi-QR spatial layout solver."""

from __future__ import annotations

import math
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Dict, List, Optional, Tuple


def to_forward_slash_path(path: str) -> str:
    """Convert any local path into forward-slash form for Qt QSettings INI."""
    if "\\" in path:
        return PureWindowsPath(path).as_posix()
    return PurePosixPath(path).as_posix()


def normalize_code(raw: str) -> str:
    code = raw.strip().upper()
    if code.startswith("DF:"):
        code = code[3:]
    return code


def node_aspect_ratio(node: Dict[str, Any]) -> float:
    w = float(node.get("display_w") or 1920)
    h = float(node.get("display_h") or 1080)
    if h <= 0 or w <= 0:
        return 16.0 / 9.0
    return max(0.6, min(5.0, w / h))


def _clamp_int(val: float, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(round(val))))


def compute_links(items: List[Dict[str, Any]], names_by_id: Dict[str, str]) -> Dict[str, List[Dict[str, Any]]]:
    """Compute non-overlapping Deskflow edge links for each node item.

    Returns {src_id: [{"dir": ..., "s0": int, "s1": int, "dst_id": str, "dst_name": str, "d0": int, "d1": int}, ...]}
    """
    rects = []
    for it in items:
        nid = str(it["id"])
        if nid not in names_by_id:
            continue
        rects.append(
            {
                "id": nid,
                "name": names_by_id[nid],
                "x": float(it["x"]),
                "y": float(it["y"]),
                "w": max(0.4, float(it["w"])),
                "h": max(0.4, float(it["h"])),
            }
        )

    out: Dict[str, List[Dict[str, Any]]] = {r["id"]: [] for r in rects}
    max_gap = 0.65
    min_overlap_frac = 0.08

    for a in rects:
        raw_by_dir: Dict[str, List[Tuple[float, int, int, Dict[str, Any], int, int]]] = {
            "left": [],
            "right": [],
            "up": [],
            "down": [],
        }
        for b in rects:
            if a["id"] == b["id"]:
                continue

            # Vertical overlap for left/right adjacency
            y0 = max(a["y"], b["y"])
            y1 = min(a["y"] + a["h"], b["y"] + b["h"])
            y_ov = y1 - y0
            if y_ov > min_overlap_frac * min(a["h"], b["h"]):
                gap_right = b["x"] - (a["x"] + a["w"])
                if -0.25 <= gap_right <= max_gap:
                    s0 = _clamp_int((y0 - a["y"]) / a["h"] * 100.0, 0, 99)
                    s1 = _clamp_int((y1 - a["y"]) / a["h"] * 100.0, s0 + 1, 100)
                    d0 = _clamp_int((y0 - b["y"]) / b["h"] * 100.0, 0, 99)
                    d1 = _clamp_int((y1 - b["y"]) / b["h"] * 100.0, d0 + 1, 100)
                    raw_by_dir["right"].append((abs(gap_right), s0, s1, b, d0, d1))

                gap_left = a["x"] - (b["x"] + b["w"])
                if -0.25 <= gap_left <= max_gap:
                    s0 = _clamp_int((y0 - a["y"]) / a["h"] * 100.0, 0, 99)
                    s1 = _clamp_int((y1 - a["y"]) / a["h"] * 100.0, s0 + 1, 100)
                    d0 = _clamp_int((y0 - b["y"]) / b["h"] * 100.0, 0, 99)
                    d1 = _clamp_int((y1 - b["y"]) / b["h"] * 100.0, d0 + 1, 100)
                    raw_by_dir["left"].append((abs(gap_left), s0, s1, b, d0, d1))

            # Horizontal overlap for up/down adjacency
            x0 = max(a["x"], b["x"])
            x1 = min(a["x"] + a["w"], b["x"] + b["w"])
            x_ov = x1 - x0
            if x_ov > min_overlap_frac * min(a["w"], b["w"]):
                gap_down = b["y"] - (a["y"] + a["h"])
                if -0.25 <= gap_down <= max_gap:
                    s0 = _clamp_int((x0 - a["x"]) / a["w"] * 100.0, 0, 99)
                    s1 = _clamp_int((x1 - a["x"]) / a["w"] * 100.0, s0 + 1, 100)
                    d0 = _clamp_int((x0 - b["x"]) / b["w"] * 100.0, 0, 99)
                    d1 = _clamp_int((x1 - b["x"]) / b["w"] * 100.0, d0 + 1, 100)
                    raw_by_dir["down"].append((abs(gap_down), s0, s1, b, d0, d1))

                gap_up = a["y"] - (b["y"] + b["h"])
                if -0.25 <= gap_up <= max_gap:
                    s0 = _clamp_int((x0 - a["x"]) / a["w"] * 100.0, 0, 99)
                    s1 = _clamp_int((x1 - a["x"]) / a["w"] * 100.0, s0 + 1, 100)
                    d0 = _clamp_int((x0 - b["x"]) / b["w"] * 100.0, 0, 99)
                    d1 = _clamp_int((x1 - b["x"]) / b["w"] * 100.0, d0 + 1, 100)
                    raw_by_dir["up"].append((abs(gap_up), s0, s1, b, d0, d1))

        # Resolve any overlapping intervals on the same edge of `a` (prefer closer gap, then lower s0)
        for direction, cands in raw_by_dir.items():
            if not cands:
                continue
            # Sort by start coordinate, then closest gap
            cands.sort(key=lambda t: (t[1], t[0], -t[2]))
            accepted: List[Tuple[int, int, Dict[str, Any], int, int]] = []
            cursor = 0
            for _gap, s0, s1, b, d0, d1 in cands:
                orig_span = max(1, s1 - s0)
                ns0 = max(s0, cursor)
                if ns0 >= s1:
                    continue
                if ns0 > s0 and d1 > d0:
                    frac = (ns0 - s0) / orig_span
                    d0 = min(d1 - 1, d0 + int(round((d1 - d0) * frac)))
                accepted.append((ns0, s1, b, d0, d1))
                cursor = s1

            for s0, s1, b, d0, d1 in accepted:
                out[a["id"]].append(
                    {
                        "dir": direction,
                        "s0": s0,
                        "s1": s1,
                        "dst_id": b["id"],
                        "dst_name": b["name"],
                        "d0": d0,
                        "d1": d1,
                    }
                )
    return out


def compile_server_conf(nodes_map: Dict[str, Dict[str, Any]], layout: Dict[str, Any]) -> str:
    """Compile a Deskflow v1.27.0 deskflow-server.conf string."""
    items = layout.get("items") or []
    names_by_id: Dict[str, str] = {}
    ordered_ids: List[str] = []
    seen_names = set()

    for it in items:
        nid = str(it.get("id") or "")
        node = nodes_map.get(nid)
        if not node:
            continue
        cname = str(node.get("computer_name") or node.get("hostname") or nid).strip()
        if cname in seen_names:
            cname = f"{cname}-{nid}"
        seen_names.add(cname)
        names_by_id[nid] = cname
        ordered_ids.append(nid)

    if not ordered_ids:
        return ""

    links_by_id = compute_links(items, names_by_id)
    lines: List[str] = []
    lines.append("section: screens")
    for nid in ordered_ids:
        lines.append(f"\t{names_by_id[nid]}:")
    lines.append("end")
    lines.append("")
    lines.append("section: links")
    for nid in ordered_ids:
        lines.append(f"\t{names_by_id[nid]}:")
        for lk in links_by_id.get(nid, []):
            lines.append(
                f"\t\t{lk['dir']}({lk['s0']},{lk['s1']}) = {lk['dst_name']}({lk['d0']},{lk['d1']})"
            )
    lines.append("end")
    lines.append("")
    lines.append("section: options")
    lines.append("\trelativeMouseMoves = false")
    lines.append("\tclipboardSharing = true")
    lines.append("\tswitchCorners = none")
    lines.append("\tswitchCornerSize = 0")
    lines.append("\theartbeat = 5000")
    lines.append("end")
    lines.append("")
    return "\n".join(lines)


def compile_deskflow_ini(
    computer_name: str,
    role: str,
    local_ts_ip: str,
    master_ts_ip: Optional[str],
    server_conf_path: str,
    port: int = 24800,
) -> str:
    """Compile a Deskflow v1.27.0 QSettings INI (Deskflow.conf)."""
    lines = [
        "[core]",
        f"computerName={computer_name}",
        f"port={port}",
        "preventSleep=false",
    ]
    if role == "server":
        if local_ts_ip:
            lines.insert(2, f"interface={local_ts_ip}")
        lines.extend(
            [
                "",
                "[server]",
                "externalConfig=true",
                f"externalConfigFile={to_forward_slash_path(server_conf_path)}",
                "enableClipboard=true",
                "clipboardSize=3072",
                "relativeMouseMoves=false",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "[client]",
                f"remoteHost={master_ts_ip or ''}",
                "dynamicConnectionInterval=true",
            ]
        )

    lines.extend(
        [
            "",
            "[security]",
            "tlsEnabled=false",
            "checkPeerFingerprints=false",
            "",
        ]
    )
    return "\n".join(lines)


def solve_layout_from_photo(
    detections: List[Dict[str, Any]],
    nodes_by_code: Dict[str, Dict[str, Any]],
    existing_master_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Convert photo QR detections [{code, cx, cy, size}] into a snapped 2D layout."""
    dedup: Dict[str, Dict[str, Any]] = {}
    for det in detections:
        code = normalize_code(str(det.get("code") or ""))
        if code not in nodes_by_code:
            continue
        size = float(det.get("size") or 100.0)
        if code not in dedup or size > dedup[code]["size"]:
            dedup[code] = {
                "code": code,
                "cx": float(det.get("cx") or 0.0),
                "cy": float(det.get("cy") or 0.0),
                "size": max(1.0, size),
            }

    if not dedup:
        return {"master_id": existing_master_id, "items": []}

    pts = list(dedup.values())
    sizes = sorted(p["size"] for p in pts)
    median_size = sizes[len(sizes) // 2]
    # Overlay QR card is ~42% of monitor height; scale pixels -> desk height units (1.0).
    px_per_unit = max(1.0, median_size / 0.42)

    boxes: List[Dict[str, Any]] = []
    for p in pts:
        node = nodes_by_code[p["code"]]
        ar = node_aspect_ratio(node)
        h = 1.0
        w = round(ar, 3)
        cx = p["cx"] / px_per_unit
        cy = p["cy"] / px_per_unit
        boxes.append(
            {
                "id": p["code"],
                "x": cx - w / 2.0,
                "y": cy - h / 2.0,
                "w": w,
                "h": h,
                "cx": cx,
                "cy": cy,
            }
        )

    if len(boxes) == 1:
        b = boxes[0]
        return {
            "master_id": b["id"],
            "items": [{"id": b["id"], "x": 0.0, "y": 0.0, "w": b["w"], "h": b["h"]}],
        }

    # If aspect ratios are wide (e.g., dual-monitor 3.55x1.0), horizontal centers from a photo
    # can compress horizontally. Separate overlaps first along the axis of larger center distance.
    def separate_overlaps(items: List[Dict[str, Any]], passes: int = 16) -> None:
        eps = 1e-4
        for _ in range(passes):
            moved = False
            for i in range(len(items)):
                for j in range(i + 1, len(items)):
                    a, b = items[i], items[j]
                    ox = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
                    oy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
                    if ox > eps and oy > eps:
                        moved = True
                        acx = a["x"] + a["w"] / 2.0
                        acy = a["y"] + a["h"] / 2.0
                        bcx = b["x"] + b["w"] / 2.0
                        bcy = b["y"] + b["h"] / 2.0
                        # Compare normalized overlap fractions to decide push direction
                        fx = ox / min(a["w"], b["w"])
                        fy = oy / min(a["h"], b["h"])
                        if fx <= fy:
                            shift = ox / 2.0
                            if acx <= bcx:
                                a["x"] -= shift
                                b["x"] += shift
                            else:
                                a["x"] += shift
                                b["x"] -= shift
                        else:
                            shift = oy / 2.0
                            if acy <= bcy:
                                a["y"] -= shift
                                b["y"] += shift
                            else:
                                a["y"] += shift
                                b["y"] -= shift
            if not moved:
                break

    separate_overlaps(boxes)

    # Row-align items whose vertical centers are close, then snap adjacent gaps cleanly.
    boxes.sort(key=lambda b: (b["y"], b["x"]))
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            dy = abs((a["y"] + a["h"] / 2.0) - (b["y"] + b["h"] / 2.0))
            if dy < 0.45:
                avg_y = (a["y"] + b["y"]) / 2.0
                a["y"] = avg_y
                b["y"] = avg_y
            else:
                dx = abs((a["x"] + a["w"] / 2.0) - (b["x"] + b["w"] / 2.0))
                if dx < 0.55:
                    avg_x = (a["x"] + b["x"]) / 2.0
                    a["x"] = avg_x
                    b["x"] = avg_x

    separate_overlaps(boxes)

    # Magnetic edge snap: close small horizontal/vertical gaps between neighboring screens
    for _ in range(3):
        for i in range(len(boxes)):
            for j in range(len(boxes)):
                if i == j:
                    continue
                a, b = boxes[i], boxes[j]
                y_ov = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
                if y_ov > 0.25:
                    gap = b["x"] - (a["x"] + a["w"])
                    if 0.0 < gap < 1.2:
                        b["x"] = a["x"] + a["w"]
                x_ov = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
                if x_ov > 0.25:
                    gap = b["y"] - (a["y"] + a["h"])
                    if 0.0 < gap < 0.9:
                        b["y"] = a["y"] + a["h"]
        separate_overlaps(boxes)

    # Shift origin to (0, 0) and round neatly
    min_x = min(b["x"] for b in boxes)
    min_y = min(b["y"] for b in boxes)
    items_out = [
        {
            "id": b["id"],
            "x": round(b["x"] - min_x, 3),
            "y": round(b["y"] - min_y, 3),
            "w": round(b["w"], 3),
            "h": round(b["h"], 3),
        }
        for b in sorted(boxes, key=lambda item: (round(item["y"], 2), round(item["x"], 2), item["id"]))
    ]

    ids_present = {it["id"] for it in items_out}
    master_id = existing_master_id if existing_master_id in ids_present else items_out[0]["id"]
    return {"master_id": master_id, "items": items_out}
