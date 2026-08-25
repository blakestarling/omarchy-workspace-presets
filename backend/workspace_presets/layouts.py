"""Pure capture/replay models for Hyprland's built-in tiled layouts."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from .errors import ValidationError


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def center_x(self) -> float:
        return self.x + self.w / 2

    @property
    def center_y(self) -> float:
        return self.y + self.h / 2

    def union(self, other: "Rect") -> "Rect":
        x = min(self.x, other.x)
        y = min(self.y, other.y)
        return Rect(x, y, max(self.right, other.right) - x, max(self.bottom, other.bottom) - y)

    def public(self) -> dict:
        return {"x": round(self.x), "y": round(self.y), "width": round(self.w), "height": round(self.h)}


def rect_for(window: dict) -> Rect:
    at = window.get("at", [0, 0])
    size = window.get("size", [0, 0])
    return Rect(float(at[0]), float(at[1]), float(size[0]), float(size[1]))


def normalized_geometry(rect: Rect, workarea: dict) -> dict:
    width = max(float(workarea.get("width", 1)), 1)
    height = max(float(workarea.get("height", 1)), 1)
    return {
        "x": (rect.x - float(workarea.get("x", 0))) / width,
        "y": (rect.y - float(workarea.get("y", 0))) / height,
        "width": rect.w / width,
        "height": rect.h / height,
    }


def denormalized_geometry(geometry: dict, source: dict, target: dict) -> dict:
    exact = geometry.get("pixels", {})
    same = all(
        round(float(source.get(key, 0))) == round(float(target.get(key, 0)))
        for key in ("width", "height")
    ) and abs(float(source.get("scale", 1)) - float(target.get("scale", 1))) < 0.001
    if same and exact:
        x = float(target.get("x", 0)) + float(exact.get("x", 0)) - float(source.get("x", 0))
        y = float(target.get("y", 0)) + float(exact.get("y", 0)) - float(source.get("y", 0))
        width = float(exact.get("width", 100))
        height = float(exact.get("height", 100))
    else:
        normal = geometry.get("normalized", {})
        x = float(target.get("x", 0)) + float(normal.get("x", 0)) * float(target.get("width", 1))
        y = float(target.get("y", 0)) + float(normal.get("y", 0)) * float(target.get("height", 1))
        width = float(normal.get("width", 0.5)) * float(target.get("width", 1))
        height = float(normal.get("height", 0.5)) * float(target.get("height", 1))
    width = max(80, min(width, float(target.get("width", width))))
    height = max(60, min(height, float(target.get("height", height))))
    x = max(float(target.get("x", 0)), min(x, float(target.get("x", 0)) + float(target.get("width", 1)) - width))
    y = max(float(target.get("y", 0)), min(y, float(target.get("y", 0)) + float(target.get("height", 1)) - height))
    return {"x": round(x), "y": round(y), "width": round(width), "height": round(height)}


def _bounds(items: Iterable[dict]) -> Rect:
    iterator = iter(items)
    first = next(iterator)
    result = first["rect"]
    for item in iterator:
        result = result.union(item["rect"])
    return result


def _partition(items: list[dict], axis: str) -> list[tuple[float, list[dict], list[dict], float]]:
    center_key = (lambda item: item["rect"].center_x) if axis == "x" else (lambda item: item["rect"].center_y)
    ordered = sorted(items, key=center_key)
    candidates: list[tuple[float, list[dict], list[dict], float]] = []
    for index in range(1, len(ordered)):
        first, second = ordered[:index], ordered[index:]
        first_end = max((item["rect"].right if axis == "x" else item["rect"].bottom) for item in first)
        second_start = min((item["rect"].x if axis == "x" else item["rect"].y) for item in second)
        if first_end <= second_start + 2:
            gap = max(0.0, second_start - first_end)
            parent = _bounds(items)
            parent_start = parent.x if axis == "x" else parent.y
            parent_extent = parent.w if axis == "x" else parent.h
            boundary = (first_end + second_start) / 2
            ratio = 2 * (boundary - parent_start) / max(parent_extent, 1)
            separation = gap / max(parent_extent, 1)
            candidates.append((separation, first, second, min(1.9, max(0.1, ratio))))
    return candidates


def infer_dwindle(items: list[dict]) -> dict | None:
    """Infer Hyprland's guillotine tree from non-overlapping goal rectangles."""
    if not items:
        return None
    if len(items) == 1:
        return {"kind": "leaf", "slotId": items[0]["slotId"]}
    candidates: list[tuple[float, str, list[dict], list[dict], float]] = []
    for axis in ("x", "y"):
        for separation, first, second, ratio in _partition(items, axis):
            candidates.append((separation, axis, first, second, ratio))
    if not candidates:
        raise ValidationError("Dwindle geometry is not a guillotine partition")
    # Prefer a visible divider; then prefer the most balanced top-level split.
    candidates.sort(
        key=lambda item: (item[0], -abs(len(item[2]) - len(item[3]))), reverse=True
    )
    _, axis, first, second, ratio = candidates[0]
    return {
        "kind": "split",
        "axis": axis,
        "direction": "r" if axis == "x" else "d",
        "ratio": round(ratio, 6),
        "first": infer_dwindle(first),
        "second": infer_dwindle(second),
    }


def dwindle_replay(tree: dict | None) -> list[dict]:
    """Return deterministic add operations for a saved Dwindle tree."""
    if not tree:
        return []
    operations: list[dict] = []

    def first_leaf(node: dict) -> str:
        return node["slotId"] if node["kind"] == "leaf" else first_leaf(node["first"])

    operations.append({"op": "add", "slotId": first_leaf(tree)})

    def expand(node: dict) -> None:
        if node["kind"] == "leaf":
            return
        anchor = first_leaf(node["first"])
        new_slot = first_leaf(node["second"])
        operations.append(
            {
                "op": "split",
                "anchor": anchor,
                "slotId": new_slot,
                "direction": node["direction"],
                "ratio": node["ratio"],
            }
        )
        expand(node["first"])
        expand(node["second"])

    expand(tree)
    return operations


def capture_layout(
    name: str,
    targets: list[dict],
    metadata: dict[str, dict],
    *,
    options: dict | None = None,
) -> dict:
    options = options or {}
    if name == "dwindle":
        items = [
            {"slotId": item["slotId"], "rect": item["rect"]}
            for item in targets
        ]
        return {"name": name, "tree": infer_dwindle(items)}
    if name == "master":
        orientation = str(options.get("orientation", "left"))
        horizontal_stack = orientation in {"left", "right", "center"}
        groups: dict[bool, list[dict]] = defaultdict(list)
        for item in targets:
            meta = metadata.get(str(item.get("stableId")), {})
            groups[bool(meta.get("isMaster"))].append(
                {"slotId": item["slotId"], "rect": item["rect"], "meta": meta}
            )
        key = (lambda item: (item["rect"].y, item["rect"].x)) if horizontal_stack else (lambda item: (item["rect"].x, item["rect"].y))
        masters = sorted(groups[True], key=key)
        stack = sorted(groups[False], key=key)
        first_meta = (masters or stack)[0]["meta"] if (masters or stack) else {}
        return {
            "name": name,
            "orientation": orientation,
            "masterFactor": float(first_meta.get("percMaster", 0.55)),
            "masters": [item["slotId"] for item in masters],
            "stack": [item["slotId"] for item in stack],
            "sizes": {
                item["slotId"]: float(item["meta"].get("percSize", 1.0))
                for item in masters + stack
            },
        }
    if name == "scrolling":
        columns: dict[int, list[dict]] = defaultdict(list)
        widths: dict[int, float] = {}
        for item in targets:
            meta = metadata.get(str(item.get("stableId")), {})
            column = int(meta.get("columnIndex", len(columns)))
            index = int(meta.get("indexInColumn", 0))
            columns[column].append({"slotId": item["slotId"], "index": index})
            widths[column] = float(meta.get("columnWidth", 0.5))
        saved_columns = []
        for index in sorted(columns):
            members = sorted(columns[index], key=lambda item: item["index"])
            saved_columns.append(
                {
                    "width": widths[index],
                    "slots": [member["slotId"] for member in members],
                }
            )
        return {
            "name": name,
            "direction": str(options.get("direction", "right")),
            "columns": saved_columns,
            "tapeOffset": float(options.get("tapeOffset", 0)),
        }
    if name == "monocle":
        ordered = sorted(targets, key=lambda item: int(item.get("focusHistoryID", 0)), reverse=True)
        return {"name": name, "order": [item["slotId"] for item in ordered]}
    raise ValidationError(f"Unsupported layout {name!r}")


def target_order(layout: dict) -> list[str]:
    name = layout.get("name")
    if name == "dwindle":
        seen: list[str] = []
        for operation in dwindle_replay(layout.get("tree")):
            slot_id = operation.get("slotId")
            if slot_id and slot_id not in seen:
                seen.append(slot_id)
        return seen
    if name == "master":
        return [*layout.get("masters", []), *layout.get("stack", [])]
    if name == "scrolling":
        return [slot for column in layout.get("columns", []) for slot in column.get("slots", [])]
    if name == "monocle":
        return list(layout.get("order", []))
    raise ValidationError(f"Unsupported layout {name!r}")
