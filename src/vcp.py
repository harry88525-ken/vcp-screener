# -*- coding: utf-8 -*-
"""
A-4 VCP 形態偵測引擎（雙尺度）
==============================
粗尺度：ATR-自適應 ZigZag（k×ATR）抓 swing 高低點 → 收縮序列（數 T）
細尺度：近 N 日窗口抓樞紐緊度 + 量縮
輸出：VCP 評分 A/B/C + 收縮序列 + 樞紐價 / 停損價 / 風險% / 突破狀態

設計依據：Notion VCP Trading Strategy A-4 + Phase 0 POC 校準（k≈3.5、雙尺度、近高 gate）。
本檔只做「形態」一件事；趨勢/RS/基本面/籌碼/族群在各自模組，最後由 screener 整合。

純函式設計，子步驟（contraction/pivot/score/risk）可獨立單元測試，不依賴 FinMind。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

import numpy as np
import pandas as pd

import config as C


# ─────────────────────────────────────────────────────────────
# 指標
# ─────────────────────────────────────────────────────────────
def wilder_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    """Wilder ATR。回傳與輸入等長陣列（前 period 段用逐步平均暖機）。"""
    n = len(high)
    tr = np.empty(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )
    atr = np.empty(n)
    if n <= period:
        atr[:] = np.cumsum(tr) / (np.arange(n) + 1)
        return atr
    atr[:period] = np.cumsum(tr[:period]) / (np.arange(period) + 1)
    seed = tr[:period].mean()
    atr[period - 1] = seed
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


# ─────────────────────────────────────────────────────────────
# 粗尺度：ATR-ZigZag swing 抓點
# ─────────────────────────────────────────────────────────────
def zigzag_pivots(high: np.ndarray, low: np.ndarray, atr: np.ndarray, k: float) -> list[dict]:
    """
    ATR 門檻 ZigZag。回傳交替的 swing 點：[{'idx','price','kind'('H'/'L')}, ...]
    反轉確認條件：價格自當前極值反向移動 ≥ k×ATR。
    最後一段未確認的極值不輸出（由細尺度樞紐窗另行處理）。
    """
    n = len(high)
    if n < 2:
        return []
    pivots: list[dict] = []
    up_ext, up_i = high[0], 0      # 自上一低點以來的最高高
    dn_ext, dn_i = low[0], 0       # 自上一高點以來的最低低
    trend = 0                      # 0 未定 / 1 上 / -1 下
    for i in range(1, n):
        thr = k * atr[i]
        if trend == 1:
            if high[i] > up_ext:
                up_ext, up_i = high[i], i
            elif up_ext - low[i] >= thr:
                pivots.append({"idx": up_i, "price": float(up_ext), "kind": "H"})
                trend, dn_ext, dn_i = -1, low[i], i
        elif trend == -1:
            if low[i] < dn_ext:
                dn_ext, dn_i = low[i], i
            elif high[i] - dn_ext >= thr:
                pivots.append({"idx": dn_i, "price": float(dn_ext), "kind": "L"})
                trend, up_ext, up_i = 1, high[i], i
        else:  # trend == 0，建立初始方向
            if high[i] - dn_ext >= thr:
                pivots.append({"idx": dn_i, "price": float(dn_ext), "kind": "L"})
                trend, up_ext, up_i = 1, high[i], i
            elif up_ext - low[i] >= thr:
                pivots.append({"idx": up_i, "price": float(up_ext), "kind": "H"})
                trend, dn_ext, dn_i = -1, low[i], i
            else:
                if high[i] > up_ext:
                    up_ext, up_i = high[i], i
                if low[i] < dn_ext:
                    dn_ext, dn_i = low[i], i
    return pivots


# ─────────────────────────────────────────────────────────────
# 收縮序列（A-4.2）
# ─────────────────────────────────────────────────────────────
def extract_contractions(pivots: list[dict], lookback_start_idx: int) -> list[dict]:
    """
    從 swing 點抽出「基底收縮序列」。
    基底起點 = 回看窗內「最高的高點」；之後每個 H→L 配對 = 一段收縮。
    每段深度 T = (段高 − 段低) / 段高。
    """
    p = [x for x in pivots if x["idx"] >= lookback_start_idx]
    highs = [(j, x) for j, x in enumerate(p) if x["kind"] == "H"]
    if not highs:
        return []
    start_j = max(highs, key=lambda t: t[1]["price"])[0]
    seq = p[start_j:]
    contractions: list[dict] = []
    i = 0
    while i < len(seq) - 1:
        a, b = seq[i], seq[i + 1]
        if a["kind"] == "H" and b["kind"] == "L":
            depth = (a["price"] - b["price"]) / a["price"]
            contractions.append(
                {"high_idx": a["idx"], "high": a["price"],
                 "low_idx": b["idx"], "low": b["price"], "depth": float(depth)}
            )
            i += 2
        else:
            i += 1
    return contractions


def validate_contractions(contractions: list[dict]) -> dict:
    """回傳 {'valid':bool, 'reasons':[...], 'depths':[...], 'n':int, 'duration':int}"""
    reasons: list[str] = []
    depths = [c["depth"] for c in contractions]
    n = len(contractions)
    if not (C.CONTRACTION_MIN <= n <= C.CONTRACTION_MAX):
        reasons.append(f"段數 {n} 不在 {C.CONTRACTION_MIN}-{C.CONTRACTION_MAX}")
    if n >= 1:
        if depths[-1] > C.LAST_CONTRACTION_MAX:
            reasons.append(f"末段 {depths[-1]:.1%} > {C.LAST_CONTRACTION_MAX:.0%}")
        if depths[0] > C.BASE_DEPTH_MAX:
            reasons.append(f"基底深度 {depths[0]:.1%} > {C.BASE_DEPTH_MAX:.0%}")
        if depths[0] > 0 and depths[-1] / depths[0] >= C.LAST_OVER_FIRST_MAX:
            reasons.append(f"末段/首段 {depths[-1]/depths[0]:.2f} ≥ {C.LAST_OVER_FIRST_MAX}")
    for i in range(n - 1):
        if depths[i + 1] > depths[i] + C.CONTRACTION_MONOTONIC_TOL:
            reasons.append(f"第 {i+2} 段未收窄 ({depths[i+1]:.1%} > {depths[i]:.1%})")
            break
    duration = 0
    if n >= 1:
        duration = contractions[-1]["low_idx"] - contractions[0]["high_idx"]
        if not (C.BASE_DURATION_MIN_DAYS <= duration <= C.BASE_DURATION_MAX_DAYS):
            reasons.append(f"基底長度 {duration} 日不在 {C.BASE_DURATION_MIN_DAYS}-{C.BASE_DURATION_MAX_DAYS}")
    return {"valid": len(reasons) == 0, "reasons": reasons,
            "depths": depths, "n": n, "duration": duration}


# ─────────────────────────────────────────────────────────────
# 細尺度：樞紐 + 量縮（A-4.3）
# ─────────────────────────────────────────────────────────────
def analyze_pivot(df: pd.DataFrame) -> dict:
    """近 PIVOT_WINDOW 日樞紐緊度 + 量縮。df 需有 high/low/close/volume，已按日期排序。"""
    w = C.PIVOT_WINDOW
    recent = df.iloc[-w:]
    pivot_high = float(recent["high"].max())
    pivot_low = float(recent["low"].min())
    width = (pivot_high - pivot_low) / pivot_high if pivot_high > 0 else 1.0
    high_pos = int(np.argmax(recent["high"].to_numpy())) / max(w - 1, 1)  # 0=窗首,1=窗尾
    high_at_start = high_pos <= C.PIVOT_HIGH_AT_START_FRAC

    # 突破判斷專用的「前期樞紐高」＝近 PIVOT_WINDOW 日但『排除當日』(截至前一日)。
    # 含當日版的 pivot_high 已把當日 high 算進去，而當日 high >= close，
    # 使 close > pivot_high 幾乎永遠 False → today_breakout 常年空（線上 BREAKOUT 清單長期為 0）。
    # 正確時序：前一日盤整 tight setup → 當日收盤突破『前一日為止』的樞紐高（與 backtest 同步）。
    prev_highs = df["high"].iloc[-(w + 1):-1]
    prev_pivot_high = float(prev_highs.max()) if len(prev_highs) else pivot_high

    vol = df["volume"].to_numpy()
    avg_vol = float(vol[-C.VOLUME_AVG_WINDOW:].mean())
    recent_vol = float(vol[-w:].mean())
    # 50 日均量斜率（線性回歸斜率 < 0 = 量在縮）
    seg = vol[-C.VOLUME_AVG_WINDOW:]
    slope = float(np.polyfit(np.arange(len(seg)), seg, 1)[0]) if len(seg) >= 2 else 0.0
    vol_contraction = (recent_vol < avg_vol) and (slope < 0)

    return {"pivot_high": pivot_high, "pivot_low": pivot_low, "width": float(width),
            "prev_pivot_high": prev_pivot_high,
            "high_at_start": bool(high_at_start), "avg_vol": avg_vol,
            "recent_vol": recent_vol, "vol_slope": slope,
            "vol_contraction": bool(vol_contraction),
            "tight": width < C.PIVOT_WIDTH_MAX}


# ─────────────────────────────────────────────────────────────
# 突破（A-4.4）
# ─────────────────────────────────────────────────────────────
def detect_breakout(df: pd.DataFrame, pivot_high: float, avg_vol: float) -> dict:
    """當日是否突破樞紐高 + 量增。
    pivot_high 必須是『前期樞紐高』(不含當日，見 analyze_pivot 的 prev_pivot_high)，
    否則當日 high 已含在內，close 永遠突不破 → today_breakout 恆為 False。
    """
    last = df.iloc[-1]
    close, volume = float(last["close"]), float(last["volume"])
    above = close > pivot_high
    vol_ok = volume >= C.BREAKOUT_VOLUME_MULT * avg_vol
    extended = close > pivot_high * (1 + C.EXTENDED_ABOVE_PIVOT)
    if extended:
        status = "已延伸"
    elif above and vol_ok:
        status = "突破中"
    else:
        status = "待突破"
    return {"status": status, "above": bool(above),
            "vol_ok": bool(vol_ok), "today_breakout": bool(above and vol_ok and not extended)}


# ─────────────────────────────────────────────────────────────
# 風控（A-9，進場/停損/風險%）
# ─────────────────────────────────────────────────────────────
def risk_metrics(pivot_high: float, pivot_low: float, last_contraction_low: float) -> dict:
    """進場=樞紐高；停損=樞紐低與末段低取較高者（較緊）；風險%=(進場−停損)/進場。"""
    entry = pivot_high
    stop = max(pivot_low, last_contraction_low)
    if stop >= entry:
        stop = pivot_low
    risk_pct = (entry - stop) / entry if entry > 0 else 0.0
    return {"entry": float(entry), "stop": float(stop), "risk_pct": float(risk_pct)}


# ─────────────────────────────────────────────────────────────
# 評分（A/B/C）
# ─────────────────────────────────────────────────────────────
def score_vcp(seq_valid: bool, n: int, vol_contraction: bool,
              rs_line_new_high: bool, pivot_width: float, high_at_start: bool) -> str:
    """
    緊度派評分（gate 已過才呼叫）。收縮序列＝加分，非門檻。
    A（最佳）= 乾淨收斂序列(3-4段) + 量縮 + RS線新高 + 樞紐<6% + 高在區間起點
    B = 大致符合；C = 僅守住「近高 + 樞紐緊」最低標
    """
    ideal_n = C.CONTRACTION_IDEAL_LOW <= n <= C.CONTRACTION_IDEAL_HIGH
    pts = 0
    pts += 2 if (seq_valid and ideal_n) else (1 if seq_valid else 0)   # 乾淨序列最多 +2
    pts += 1 if vol_contraction else 0
    pts += 1 if rs_line_new_high else 0
    pts += 1 if pivot_width < C.PIVOT_WIDTH_IDEAL else 0
    pts += 1 if high_at_start else 0
    if pts >= 5:
        return "A"
    if pts >= 3:
        return "B"
    return "C"


# ─────────────────────────────────────────────────────────────
# 頂層整合
# ─────────────────────────────────────────────────────────────
@dataclass
class VCPResult:
    is_vcp: bool
    grade: str = ""
    contractions: list[float] = field(default_factory=list)
    n_contractions: int = 0
    pivot_high: float = 0.0
    pivot_low: float = 0.0
    entry: float = 0.0
    stop: float = 0.0
    risk_pct: float = 0.0
    reward_risk: float = 0.0
    vol_contraction: bool = False
    seq_clean: bool = False
    pivot_width: float = 0.0
    breakout_status: str = ""
    today_breakout: bool = False
    rs_line_new_high: bool = False
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def analyze(df: pd.DataFrame, rs_line_new_high: bool = False,
            dist_52w_high: Optional[float] = None,
            next_target: Optional[float] = None) -> VCPResult:
    """
    df：單檔日線，欄位 date/open/high/low/close/volume，已按日期升序，至少 ~60 根。
    dist_52w_high：距 52 週高（負值；-0.097 = 低於高點 9.7%）。None 則不做近高 gate。
    next_target：估算風報比用的上方目標（例如前高）；None 則 reward_risk=0。
    """
    if len(df) < C.PIVOT_WINDOW + 5:
        return VCPResult(is_vcp=False, reasons=["資料不足"])

    # 近高 gate（POC 證實有效）
    if dist_52w_high is not None and dist_52w_high < C.DIST_52W_HIGH_MIN:
        return VCPResult(is_vcp=False, reasons=[f"距52週高 {dist_52w_high:.1%} 超過 {C.DIST_52W_HIGH_MIN:.0%}"])

    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    atr = wilder_atr(high, low, close, C.ATR_PERIOD)

    # 收縮序列＝品質訊號（細尺度 CONTRACTION_K），非 gate
    pivots = zigzag_pivots(high, low, atr, C.CONTRACTION_K)
    lookback_start = max(0, len(df) - C.BASE_LOOKBACK)
    contractions = extract_contractions(pivots, lookback_start)
    v = validate_contractions(contractions)

    piv = analyze_pivot(df)
    # 突破比對『前期樞紐高』(不含當日)；entry/risk 仍用含當日樞紐高(piv["pivot_high"])。
    brk = detect_breakout(df, piv["prev_pivot_high"], piv["avg_vol"])

    last_low = contractions[-1]["low"] if contractions else piv["pivot_low"]
    rk = risk_metrics(piv["pivot_high"], piv["pivot_low"], last_low)
    reward_risk = 0.0
    if next_target and rk["risk_pct"] > 0 and next_target > rk["entry"]:
        reward = (next_target - rk["entry"]) / rk["entry"]
        reward_risk = reward / rk["risk_pct"]

    # 緊度派 gate：近高(上方已過) + 樞紐緊。收縮序列不擋。
    is_vcp = bool(piv["tight"])
    grade = score_vcp(v["valid"], v["n"], piv["vol_contraction"],
                      rs_line_new_high, piv["width"], piv["high_at_start"]) if is_vcp else ""

    reasons: list[str] = []
    if not piv["tight"]:
        reasons.append(f"樞紐寬 {piv['width']:.1%} ≥ {C.PIVOT_WIDTH_MAX:.0%}")

    return VCPResult(
        is_vcp=is_vcp, grade=grade,
        contractions=[round(d, 4) for d in v["depths"]],
        n_contractions=v["n"],
        pivot_high=piv["pivot_high"], pivot_low=piv["pivot_low"],
        entry=rk["entry"], stop=rk["stop"], risk_pct=rk["risk_pct"],
        reward_risk=round(reward_risk, 2),
        vol_contraction=piv["vol_contraction"], seq_clean=bool(v["valid"]),
        pivot_width=round(piv["width"], 4),
        breakout_status=brk["status"], today_breakout=brk["today_breakout"],
        rs_line_new_high=bool(rs_line_new_high), reasons=reasons,
    )
