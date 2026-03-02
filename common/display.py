"""
Terminal display utilities: box drawing and formatting.
"""

import numpy as np

W = 72


def fmt(val, decimals=2, suffix=""):
    """Format a number, return 'N/A' for NaN."""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return "N/A"
    return f"{val:.{decimals}f}{suffix}"


def box_top():
    return "\u2554" + "\u2550" * W + "\u2557"


def box_mid():
    return "\u2560" + "\u2550" * W + "\u2563"


def box_bot():
    return "\u255a" + "\u2550" * W + "\u255d"


def box_line(text=""):
    text = str(text)
    if len(text) > W - 4:
        text = text[:W - 4]
    return "\u2551  " + text.ljust(W - 4) + "  \u2551"
