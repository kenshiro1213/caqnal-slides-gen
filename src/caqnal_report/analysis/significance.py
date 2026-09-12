"""統計的有意差（仕様書 §4-3）。p 値は補助情報。判定は差分・n と併記する。"""
from __future__ import annotations

import math

import numpy as np
from scipy import stats


def welch_p(a, b) -> float:
    """平均差の Welch t 検定（両側）。n<2 や分散 0 では NaN。"""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    a = a[~np.isnan(a)]
    b = b[~np.isnan(b)]
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    if a.std(ddof=1) == 0 and b.std(ddof=1) == 0:
        return float("nan")
    return float(stats.ttest_ind(a, b, equal_var=False).pvalue)


def two_proportion_p(k1: float, n1: float, k2: float, n2: float) -> float:
    if n1 <= 0 or n2 <= 0:
        return float("nan")
    p1, p2 = k1 / n1, k2 / n2
    p = (k1 + k2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return float("nan")
    z = (p1 - p2) / se
    return float(2 * (1 - stats.norm.cdf(abs(z))))


def sig_flag(diff: float, p: float, alpha: float = 0.05) -> int:
    """+1 有意に高い / -1 有意に低い / 0 有意差なし・判定不能"""
    if diff is None or p is None or np.isnan(diff) or np.isnan(p) or p >= alpha:
        return 0
    return 1 if diff > 0 else -1
