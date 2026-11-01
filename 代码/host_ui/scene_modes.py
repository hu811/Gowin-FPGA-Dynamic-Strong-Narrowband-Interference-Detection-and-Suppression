"""Editable four-center presets and offline reference-pair selection.

These helpers only configure the test source and its display measurements. They
do not classify observed FPGA centers or supply grouping labels to the board.
"""
from __future__ import annotations

from scene import default_config


MODE_LABELS = {"two_pairs": "两对双峰", "mixed": "两单＋一双"}
GROUP_GAP_HZ = 750.0


def preset_config(mode: str) -> dict:
    """Return a fresh, schema-compatible C4 source configuration."""
    if mode not in MODE_LABELS:
        raise ValueError(f"unknown scene mode: {mode!r}")
    config = default_config(4)
    if mode == "mixed":
        frequencies = (1511.3, 3272.7, 3397.7, 5847.7)
        starts = (0.25, 0.258, 0.259, 0.266)
        for event, hz, start in zip(config["events"], frequencies, starts):
            event.update(enabled=True, frequency_hz=hz, end_frequency_hz=hz,
                mode="fixed", start_s=start, change_s=(start + event["end_s"]) / 2,
                js_db=20.0)
    return config


def reference_pairs(events: list[dict]) -> list[tuple[int, int]]:
    """Find exactly two-member connected groups of enabled starting frequencies.

    Adjacent sorted frequencies no more than 750 Hz apart are connected. A
    group with three or more members is not split into artificial pairs. The
    returned zero-based source-row indices are solely for offline measurements;
    event timing/drift and actual detector estimates may give different groups.
    """
    ordered = sorted((float(event["frequency_hz"]), index)
        for index, event in enumerate(events) if event.get("enabled", False))
    groups = []
    for hz, index in ordered:
        if not groups or hz - groups[-1][-1][0] > GROUP_GAP_HZ:
            groups.append([])
        groups[-1].append((hz, index))
    pairs = [tuple(sorted(index for _, index in group))
        for group in groups if len(group) == 2]
    return sorted(pairs)
