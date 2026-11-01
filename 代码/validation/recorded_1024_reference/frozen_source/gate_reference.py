"""Integer v1.4 raw balanced gate; no candidate-dependent background."""
from decimal import Decimal, localcontext
from fractions import Fraction
import numpy as np
POWER_FRAC = 58
POWER_FLOOR = 29
RATIO_FRAC = 32
with localcontext() as context:
    context.prec = 60
    RATIO_Q32 = round(Decimal(10) ** (Decimal(21)/10) * (1 << RATIO_FRAC))


def decision(power, peak_bin, numeric_valid=True):
    if not numeric_valid or not 13 <= peak_bin <= 243:
        return dict(valid=False, strong=False, background_twice=0)
    p = list(map(int, power))
    if any(v < 0 or v >= 1 << 64 for v in p):
        raise ValueError('unsigned64 powers required')
    left = sorted(p[peak_bin-12:peak_bin-2])
    right = sorted(p[peak_bin+3:peak_bin+13])
    background_twice = max(left[4]+left[5], right[4]+right[5], 2*POWER_FLOOR)
    strong = (max(p[peak_bin], POWER_FLOOR) << (RATIO_FRAC+1)) >= RATIO_Q32*background_twice
    return dict(valid=True, strong=strong, background_twice=background_twice,
                peak_power=p[peak_bin], ratio_integer=RATIO_Q32)
