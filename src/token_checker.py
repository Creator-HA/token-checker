#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Token Checker (Windows Python版) v4
Claude Code と Codex のレート制限をシステムトレイで常時表示
"""

import os, sys, json, time, threading, winreg, ctypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Tuple, List, Set
import tkinter as tk
from tkinter import filedialog

import requests
from PIL import Image, ImageDraw
import pystray

APP_NAME    = "Token Checker"
APP_VERSION = "4.0"
ICON_SIZE   = 64

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).parent
else:
    APP_DIR = Path(__file__).parent

LOG_FILE    = APP_DIR / "token_checker.log"
CONFIG_FILE = APP_DIR / "config.json"

# ── カラー ──────────────────────────────────────────────────────
C_BG    = "#0f172a"; C_BG2 = "#1e293b"; C_FG  = "#f1f5f9"
C_FG2   = "#94a3b8"; C_SEP = "#334155"; C_ERR = "#f87171"
C_WARN  = "#fbbf24"; C_HDR = "#1e40af"; C_BTN = "#1e40af"
C_BTN2  = "#374151"; C_CLAUDE = "#a78bfa"; C_CODEX = "#34d399"

# ================================================================
# 多重起動ガード (Windows 名前付きミューテックス)
# ================================================================
_MUTEX_HANDLE = None  # プロセス終了まで保持（GC解放防止）

def ensure_single_instance() -> bool:
    """2つ目の起動を検出したら False を返す"""
    global _MUTEX_HANDLE
    kernel32 = ctypes.windll.kernel32
    _MUTEX_HANDLE = kernel32.CreateMutexW(None, False, "Global\\TokenCheckerSingleInstance")
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(_MUTEX_HANDLE)
        _MUTEX_HANDLE = None
        return False
    return True

# ================================================================
# ロギング
# ================================================================
def _mask_path(p) -> str:
    """ホームディレクトリを ~ に置換してログのパスを匿名化"""
    try:
        return "~/" + str(Path(p).relative_to(Path.home())).replace("\\", "/")
    except ValueError:
        return Path(p).name


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

# ================================================================
# 設定 (A2: 更新間隔 / A1: アラート閾値)
# ================================================================
_DEFAULT_CONFIG = {
    "interval_sec": 300,
    "alert_thresholds": [20, 10],
}

class Config:
    def __init__(self):
        self._data = dict(_DEFAULT_CONFIG)
        self.load()

    def load(self):
        try:
            if CONFIG_FILE.exists():
                d = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                self._data.update(d)
        except Exception as e:
            log(f"[Config] 読み込みエラー: {e}")

    def save(self):
        try:
            CONFIG_FILE.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            log(f"[Config] 保存エラー: {e}")

    @property
    def interval_sec(self) -> int:
        return max(60, int(self._data.get("interval_sec", 300)))

    @interval_sec.setter
    def interval_sec(self, v: int):
        self._data["interval_sec"] = int(v)

    @property
    def alert_thresholds(self) -> List[int]:
        return sorted(self._data.get("alert_thresholds", [20, 10]), reverse=True)

    @alert_thresholds.setter
    def alert_thresholds(self, v: List[int]):
        self._data["alert_thresholds"] = list(v)

    @property
    def claude_creds_path(self) -> Optional[str]:
        return self._data.get("claude_creds_path") or None

    @claude_creds_path.setter
    def claude_creds_path(self, v: Optional[str]):
        if v:
            self._data["claude_creds_path"] = v
        else:
            self._data.pop("claude_creds_path", None)

    @property
    def codex_auth_path(self) -> Optional[str]:
        return self._data.get("codex_auth_path") or None

    @codex_auth_path.setter
    def codex_auth_path(self, v: Optional[str]):
        if v:
            self._data["codex_auth_path"] = v
        else:
            self._data.pop("codex_auth_path", None)

# ================================================================
# スタートアップ登録 (A3: Windows レジストリ)
# ================================================================
_STARTUP_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_STARTUP_VAL = "TokenChecker"

def startup_is_registered() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _STARTUP_KEY) as k:
            winreg.QueryValueEx(k, _STARTUP_VAL)
            return True
    except OSError:
        return False

def set_startup(enable: bool):
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _STARTUP_KEY, 0, winreg.KEY_SET_VALUE
        ) as k:
            if enable:
                exe = str(Path(sys.executable).resolve())
                winreg.SetValueEx(k, _STARTUP_VAL, 0, winreg.REG_SZ, exe)
                log(f"[Startup] 登録: {_mask_path(exe)}")
            else:
                try:
                    winreg.DeleteValue(k, _STARTUP_VAL)
                    log("[Startup] 解除")
                except FileNotFoundError:
                    pass
    except Exception as e:
        log(f"[Startup] レジストリ操作失敗: {e}")

# ================================================================
# Claude 認証 & API
# ================================================================
def get_claude_token(custom_path: Optional[str] = None) -> Optional[str]:
    candidates = []
    if custom_path:
        candidates.append(Path(custom_path))
    candidates += [
        Path.home() / ".claude" / ".credentials.json",
        Path.home() / ".claude" / "credentials.json",
        Path(os.environ.get("APPDATA", "")) / "Claude" / "credentials.json",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Claude" / "credentials.json",
    ]
    for p in candidates:
        try:
            if not p.exists():
                continue
            data = json.loads(p.read_text(encoding="utf-8"))
            log(f"[Claude] ファイル読み込み: {_mask_path(p)}  top-keys={list(data.keys())}")
            for top in ("claudeAiOauth", "claude_ai_oauth", "oauth"):
                nested = data.get(top)
                if isinstance(nested, dict):
                    t = nested.get("accessToken") or nested.get("access_token")
                    if t:
                        log(f"[Claude] トークン取得成功 ({top}.accessToken)")
                        return t
            for k in ("accessToken", "access_token", "oauthToken", "token"):
                if data.get(k):
                    log(f"[Claude] トークン取得成功 (flat:{k})")
                    return data[k]
            log(f"[Claude] 既知キーが見つかりません: {list(data.keys())}")
        except Exception as e:
            log(f"[Claude] {_mask_path(p)} 読み込みエラー: {e}")
    log("[Claude] 認証ファイルが見つかりません")
    return None


def fetch_claude_usage(token: str) -> Tuple[Optional[Dict], Optional[str]]:
    try:
        r = requests.get(
            "https://api.anthropic.com/api/oauth/usage",
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": "oauth-2025-04-20",
            },
            timeout=15,
        )
        log(f"[Claude API] status={r.status_code}")
        if r.status_code == 200:
            data = r.json()
            log(f"[Claude API] keys={list(data.keys())}")
            return data, None
        return None, f"HTTP {r.status_code}"
    except requests.Timeout:
        return None, "タイムアウト"
    except Exception as e:
        return None, str(e)

# ================================================================
# Codex 認証 & API
# ================================================================
def get_codex_auth(custom_path: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    candidates = []
    if custom_path:
        candidates.append(Path(custom_path))
    codex_home = os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))
    candidates += [
        Path(codex_home) / "auth.json",
        Path.home() / ".codex" / "auth.json",
    ]
    for p in candidates:
        try:
            if not p.exists():
                continue
            data = json.loads(p.read_text(encoding="utf-8"))
            log(f"[Codex] auth.json読み込み: {_mask_path(p)}  top-keys={list(data.keys())}")
            token = None
            tokens_obj = data.get("tokens")
            if isinstance(tokens_obj, dict):
                token = tokens_obj.get("access_token") or tokens_obj.get("accessToken")
            if not token:
                token = data.get("access_token") or data.get("accessToken")
            account_id = (
                data.get("account_id") or data.get("accountId") or data.get("user_id") or ""
            )
            if token:
                log(f"[Codex] トークン取得成功 account_id={account_id!r}")
                return token, account_id
            log(f"[Codex] トークンキーが見つかりません: {list(data.keys())}")
        except Exception as e:
            log(f"[Codex] {_mask_path(p)} 読み込みエラー: {e}")
    log("[Codex] auth.jsonが見つかりません")
    return None, None


def fetch_codex_usage(token: str, account_id: str) -> Tuple[Optional[Dict], Optional[str]]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    if account_id:
        headers["ChatGPT-Account-Id"] = account_id
    try:
        r = requests.get(
            "https://chatgpt.com/backend-api/wham/usage",
            headers=headers,
            timeout=15,
        )
        log(f"[Codex API] status={r.status_code}")
        if r.status_code == 200:
            data = r.json()
            log(f"[Codex API] keys={list(data.keys())}")
            return data, None
        return None, f"HTTP {r.status_code}"
    except requests.Timeout:
        return None, "タイムアウト"
    except Exception as e:
        return None, str(e)

# ================================================================
# データモデル
# ================================================================
class Window:
    def __init__(self, pct: float, reset_at: Optional[str] = None,
                 used: Optional[int] = None, limit: Optional[int] = None):
        self._pct = min(max(float(pct), 0.0), 1.0)
        self.reset_at = reset_at
        self.used  = int(used)  if used  is not None else None
        self.limit = int(limit) if limit is not None else None

    @property
    def pct(self) -> float:
        return self._pct

    @property
    def reset_str(self) -> Optional[str]:
        if not self.reset_at:
            return None
        try:
            dt  = datetime.fromisoformat(self.reset_at.replace("Z", "+00:00"))
            sec = int((dt - datetime.now(timezone.utc)).total_seconds())
            if sec <= 0:
                return "まもなくリセット"
            h, r = divmod(sec, 3600)
            m = r // 60
            return f"{h}時間{m}分後" if h else f"{m}分後"
        except Exception:
            return None


def _extract_win(obj: Dict, *keys) -> Optional[Window]:
    for k in keys:
        w = obj.get(k)
        if w and isinstance(w, dict):
            log(f"[parse] key='{k}' subkeys={list(w.keys())}")
            pct = None; used = None; limit = None
            for pk in ("utilization", "used_percent"):
                v = w.get(pk)
                if v is not None:
                    fv = float(v)
                    pct = fv / 100.0 if fv > 1.0 else fv
                    break
            if pct is None:
                used  = w.get("used",  w.get("tokens_used",    w.get("messages_used")))
                limit = w.get("limit", w.get("tokens_limit",   w.get("messages_limit")))
                if used is not None and limit:
                    pct = float(used) / float(max(int(limit), 1))
            if pct is None:
                continue
            reset = w.get("reset_at", w.get("resets_at", w.get("window_end")))
            if not reset:
                ras = w.get("reset_after_seconds")
                if ras:
                    from datetime import timedelta
                    reset = (datetime.now(timezone.utc) +
                             timedelta(seconds=int(ras))).isoformat()
            return Window(pct, reset, used, limit)
    return None


class AppData:
    def __init__(self):
        self.claude_5h:     Optional[Window] = None
        self.claude_weekly: Optional[Window] = None
        self.codex_5h:      Optional[Window] = None
        self.codex_weekly:  Optional[Window] = None
        self.claude_err:    Optional[str]    = None  # API エラー（赤表示）
        self.codex_err:     Optional[str]    = None
        self.claude_absent: bool             = False  # 未インストール（グレー表示）
        self.codex_absent:  bool             = False
        self.last_updated:  Optional[datetime] = None
        self.raw_claude:    Optional[Dict]   = None
        self.raw_codex:     Optional[Dict]   = None
        self._alerted:      Set[str]         = set()

    def parse_claude(self, raw: Dict):
        self.raw_claude    = raw
        self.claude_5h     = _extract_win(raw, "usage_5h", "five_hour", "short_window", "hourly", "hour")
        self.claude_weekly = _extract_win(raw, "usage_weekly", "weekly", "long_window", "week", "seven_day")

    def parse_codex(self, raw: Dict):
        self.raw_codex = raw
        rl = raw.get("rate_limit", {})
        if isinstance(rl, dict):
            log(f"[Codex parse] rate_limit keys={list(rl.keys())}")
        self.codex_5h     = _extract_win(rl, "primary_window",   "five_hour", "short_window", "hourly")
        self.codex_weekly = _extract_win(rl, "secondary_window", "weekly",    "long_window",  "week", "seven_day")
        if not self.codex_5h and not self.codex_weekly:
            log("[Codex parse] rate_limit直下に未ヒット。トップレベルを試す")
            self.codex_5h     = _extract_win(raw, "primary_window",   "five_hour", "short_window")
            self.codex_weekly = _extract_win(raw, "secondary_window", "weekly",    "long_window", "seven_day")

    @property
    def claude_pct(self) -> float:
        pcts = [w.pct for w in (self.claude_5h, self.claude_weekly) if w]
        return max(pcts) if pcts else 0.0

    @property
    def codex_pct(self) -> float:
        pcts = [w.pct for w in (self.codex_5h, self.codex_weekly) if w]
        return max(pcts) if pcts else 0.0

    def check_alerts(self, thresholds: List[int]) -> List[Tuple[str, str, float, int]]:
        """アラートが必要なウィンドウを返す。同一閾値は一度だけ通知し、回復後にリセット"""
        alerts = []
        windows = [
            ("Claude", "5時間",  self.claude_5h),
            ("Claude", "週次",   self.claude_weekly),
            ("Codex",  "5時間",  self.codex_5h),
            ("Codex",  "週次",   self.codex_weekly),
        ]
        for svc, label, w in windows:
            if not w:
                continue
            rem_pct = (1.0 - w.pct) * 100
            for thr in thresholds:
                key = f"{svc}_{label}_{thr}"
                if rem_pct <= thr and key not in self._alerted:
                    self._alerted.add(key)
                    alerts.append((svc, label, rem_pct, thr))
                elif rem_pct > thr + 5:
                    self._alerted.discard(key)
        return alerts

    def debug_json(self) -> str:
        return json.dumps(
            {"claude": self.raw_claude, "codex": self.raw_codex},
            ensure_ascii=False, indent=2,
        )

# ================================================================
# アイコン描画
# ================================================================
def _remaining_color(remaining: float) -> tuple:
    if remaining > 0.30: return (80, 200, 130)
    if remaining > 0.10: return (245, 160, 30)
    return (240, 80, 70)


def make_icon(claude_rem: float, codex_rem: float) -> Image.Image:
    sz   = ICON_SIZE
    img  = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    half = sz // 2; r = half // 2 - 3; thick = max(4, sz // 10)

    def donut(cx, cy, remaining, bg, fg):
        box = [cx - r, cy - r, cx + r, cy + r]
        draw.arc(box, 0, 360, fill=bg, width=thick)
        if remaining > 0.005:
            draw.arc(box, -90, -90 + 360 * min(remaining, 1.0), fill=fg + (255,), width=thick)

    donut(half // 2,       half, claude_rem, (140, 80, 220, 55), _remaining_color(claude_rem))
    donut(half + half // 2, half, codex_rem,  (20, 180, 120, 55), _remaining_color(codex_rem))
    return img

# ================================================================
# トースト通知 (A1: tkinter ベース・追加ライブラリ不要)
# ================================================================
def show_toast(root: tk.Tk, title: str, msg: str, color: str = C_WARN):
    def _show():
        t = tk.Toplevel(root)
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        t.configure(bg=color)

        inner = tk.Frame(t, bg=C_BG2, padx=14, pady=10)
        inner.pack(padx=2, pady=2, fill="both", expand=True)
        tk.Label(inner, text=title, font=("Segoe UI", 10, "bold"),
                 fg=color, bg=C_BG2).pack(anchor="w")
        tk.Label(inner, text=msg, font=("Segoe UI", 9),
                 fg=C_FG, bg=C_BG2, wraplength=280, justify="left").pack(anchor="w", pady=(4, 0))

        t.update_idletasks()
        w = t.winfo_reqwidth(); h = t.winfo_reqheight()
        sw = t.winfo_screenwidth(); sh = t.winfo_screenheight()
        t.geometry(f"{w}x{h}+{sw - w - 20}+{sh - h - 60}")
        t.after(6000, t.destroy)

    root.after(0, _show)

# ================================================================
# ポップアップ UI
# ================================================================
def _bar(parent, remaining: float, color: str, width=130, height=10) -> tk.Canvas:
    c = tk.Canvas(parent, width=width, height=height, bg=C_SEP, highlightthickness=0)
    fw = int(width * max(0.0, min(remaining, 1.0)))
    if fw > 0:
        bc = color if remaining > 0.30 else (C_WARN if remaining > 0.10 else C_ERR)
        c.create_rectangle(0, 0, fw, height, fill=bc, outline="")
    return c


def _lbl(parent, text, font, fg, **kw):
    return tk.Label(parent, text=text, font=font, fg=fg, bg=C_BG, **kw)


def build_popup(win: tk.Toplevel, data: AppData, on_refresh=None, on_settings=None):
    win.title(APP_NAME)
    win.configure(bg=C_BG)
    win.resizable(False, False)
    win.attributes("-topmost", True)

    # ── ヘッダー ──────────────────────────────────────────
    hdr = tk.Frame(win, bg=C_HDR, padx=14, pady=10)
    hdr.pack(fill="x")
    tk.Label(hdr, text=f"🔢  {APP_NAME}",
             bg=C_HDR, fg="white", font=("Segoe UI", 13, "bold")).pack(side="left")

    # ── ボディ ────────────────────────────────────────────
    body = tk.Frame(win, bg=C_BG, padx=16, pady=12)
    body.pack(fill="both", expand=True)

    def section(title, color, rows, err, absent):
        f = tk.Frame(body, bg=C_BG); f.pack(fill="x", pady=2)
        _lbl(f, title, ("Segoe UI", 11, "bold"), color).pack(anchor="w", pady=(0, 4))
        if absent:
            _lbl(f, "未インストール", ("Segoe UI", 9), C_FG2).pack(anchor="w", padx=8)
            return
        if err:
            _lbl(f, f"⚠  {err}", ("Segoe UI", 9), C_ERR).pack(anchor="w", padx=8)
            return
        any_data = False
        for label, w in rows:
            if w:
                any_data = True
                row = tk.Frame(f, bg=C_BG); row.pack(fill="x", pady=1)
                _lbl(row, label, ("Segoe UI", 9), C_FG2,
                     width=16, anchor="w").pack(side="left")
                _bar(row, 1.0 - w.pct, color).pack(side="left", padx=4)
                _lbl(row, f"残り{(1.0 - w.pct) * 100:.1f}%",
                     ("Segoe UI", 9), C_FG, width=8).pack(side="left")
                detail = ""
                if w.used is not None and w.limit is not None:
                    detail += f"   {w.used:,} / {w.limit:,}"
                if w.reset_str:
                    detail += f"   ⏱ {w.reset_str}"
                if detail.strip():
                    _lbl(f, detail.strip(), ("Segoe UI", 8), C_FG2
                         ).pack(anchor="w", padx=24, pady=(0, 2))
        if not any_data:
            _lbl(f, "データがありません", ("Segoe UI", 9), C_FG2).pack(anchor="w", padx=8)

    section("Claude Code", C_CLAUDE,
            [("5時間ウィンドウ", data.claude_5h), ("週次ウィンドウ", data.claude_weekly)],
            data.claude_err, data.claude_absent)

    tk.Frame(body, bg=C_SEP, height=1).pack(fill="x", pady=8)

    section("Codex", C_CODEX,
            [("5時間ウィンドウ", data.codex_5h), ("週次ウィンドウ", data.codex_weekly)],
            data.codex_err, data.codex_absent)

    # ── フッター ──────────────────────────────────────────
    ftr = tk.Frame(win, bg=C_BG2, padx=14, pady=8)
    ftr.pack(fill="x")
    ts = data.last_updated.strftime("%Y-%m-%d %H:%M:%S") if data.last_updated else "---"
    tk.Label(ftr, text=f"最終更新: {ts}", bg=C_BG2, fg=C_FG2,
             font=("Segoe UI", 8)).pack(side="left")

    bf = tk.Frame(ftr, bg=C_BG2); bf.pack(side="right")

    def btn(text, bg, cmd):
        tk.Button(bf, text=text, command=cmd, bg=bg, fg="white", relief="flat",
                  padx=6, pady=2, font=("Segoe UI", 9),
                  cursor="hand2").pack(side="left", padx=2)

    if on_settings:
        btn("⚙ 設定", C_BTN2, on_settings)
    if on_refresh:
        btn("🔄 更新", C_BTN, on_refresh)

    def copy_debug():
        try:
            win.clipboard_clear(); win.clipboard_append(data.debug_json()); win.update()
            log("[Debug] JSONをクリップボードにコピー")
        except Exception as e:
            log(f"[Debug] コピー失敗: {e}")

    btn("📋 ログ", C_BTN2, copy_debug)
    btn("✕ 閉じる", C_BTN2, win.destroy)

    win.update_idletasks()
    w = win.winfo_reqwidth(); h = win.winfo_reqheight()
    sw = win.winfo_screenwidth(); sh = win.winfo_screenheight()
    win.geometry(f"{w}x{h}+{sw - w - 20}+{sh - h - 60}")

# ================================================================
# 設定画面 (B2: GUI設定)
# ================================================================
_INTERVAL_OPTIONS  = [(60, "1分"), (300, "5分"), (900, "15分"), (1800, "30分")]
_THRESHOLD_OPTIONS = [30, 20, 10, 5]


def open_settings_window(root: tk.Tk, config: Config, on_saved=None):
    win = tk.Toplevel(root)
    win.title("設定")
    win.configure(bg=C_BG)
    win.resizable(False, False)
    win.attributes("-topmost", True)

    hdr = tk.Frame(win, bg=C_HDR, padx=14, pady=8)
    hdr.pack(fill="x")
    tk.Label(hdr, text="⚙  設定", bg=C_HDR, fg="white",
             font=("Segoe UI", 12, "bold")).pack(side="left")

    body = tk.Frame(win, bg=C_BG, padx=20, pady=14)
    body.pack(fill="both", expand=True)

    def section_label(text):
        tk.Label(body, text=text, font=("Segoe UI", 10, "bold"),
                 fg=C_CLAUDE, bg=C_BG).pack(anchor="w", pady=(12, 4))

    # ── 更新間隔 (A2) ──
    section_label("更新間隔")
    interval_var = tk.IntVar(value=config.interval_sec)
    iv_frame = tk.Frame(body, bg=C_BG); iv_frame.pack(anchor="w")
    for sec, label in _INTERVAL_OPTIONS:
        tk.Radiobutton(
            iv_frame, text=label, variable=interval_var, value=sec,
            bg=C_BG, fg=C_FG, selectcolor=C_BG2, activebackground=C_BG,
            font=("Segoe UI", 9),
        ).pack(side="left", padx=(0, 12))

    # ── アラート閾値 (A1) ──
    section_label("残量アラート（残り何%以下で通知するか）")
    thr_vars: Dict[int, tk.BooleanVar] = {}
    thr_frame = tk.Frame(body, bg=C_BG); thr_frame.pack(anchor="w")
    for thr in _THRESHOLD_OPTIONS:
        v = tk.BooleanVar(value=(thr in config.alert_thresholds))
        thr_vars[thr] = v
        tk.Checkbutton(
            thr_frame, text=f"{thr}%", variable=v,
            bg=C_BG, fg=C_FG, selectcolor=C_BG2, activebackground=C_BG,
            font=("Segoe UI", 9),
        ).pack(side="left", padx=(0, 10))
    tk.Label(body, text="※ 残量が回復（閾値＋5%超）したら再通知します",
             font=("Segoe UI", 8), fg=C_FG2, bg=C_BG).pack(anchor="w", pady=(2, 0))

    # ── スタートアップ (A3) ──
    section_label("スタートアップ")
    startup_var = tk.BooleanVar(value=startup_is_registered())
    tk.Checkbutton(
        body, text="Windows 起動時に自動的に起動する",
        variable=startup_var,
        bg=C_BG, fg=C_FG, selectcolor=C_BG2, activebackground=C_BG,
        font=("Segoe UI", 9),
    ).pack(anchor="w")

    # ── 認証ファイルのパス ──
    section_label("認証ファイルのパス（空欄で自動検索）")

    def path_row(label: str, default: Optional[str], filetypes):
        frame = tk.Frame(body, bg=C_BG); frame.pack(fill="x", pady=(0, 6))
        tk.Label(frame, text=label, font=("Segoe UI", 9), fg=C_FG2,
                 bg=C_BG, width=10, anchor="w").pack(side="left")
        var = tk.StringVar(value=default or "")
        entry = tk.Entry(frame, textvariable=var, font=("Segoe UI", 9),
                         bg=C_BG2, fg=C_FG, insertbackground=C_FG,
                         relief="flat", width=32)
        entry.pack(side="left", padx=(4, 4))

        def browse():
            path = filedialog.askopenfilename(
                parent=win,
                title=f"{label}を選択",
                filetypes=filetypes + [("すべてのファイル", "*.*")],
            )
            if path:
                var.set(path)

        tk.Button(frame, text="📂", command=browse,
                  bg=C_BTN2, fg="white", relief="flat", padx=4, pady=1,
                  font=("Segoe UI", 9), cursor="hand2").pack(side="left")
        return var

    claude_path_var = path_row(
        "Claude", config.claude_creds_path,
        [("JSON ファイル", "*.json")],
    )
    codex_path_var = path_row(
        "Codex", config.codex_auth_path,
        [("JSON ファイル", "*.json")],
    )
    tk.Label(body, text="※ 空欄の場合はデフォルトのディレクトリを自動検索します",
             font=("Segoe UI", 8), fg=C_FG2, bg=C_BG).pack(anchor="w")

    # ── フッター ──
    tk.Frame(win, bg=C_SEP, height=1).pack(fill="x", pady=(8, 0))
    ftr = tk.Frame(win, bg=C_BG2, padx=14, pady=8)
    ftr.pack(fill="x")

    def save():
        config.interval_sec      = interval_var.get()
        config.alert_thresholds  = [t for t, v in thr_vars.items() if v.get()]
        config.claude_creds_path = claude_path_var.get().strip() or None
        config.codex_auth_path   = codex_path_var.get().strip() or None
        config.save()
        set_startup(startup_var.get())
        log(f"[Settings] 保存: interval={config.interval_sec}s "
            f"thresholds={config.alert_thresholds} startup={startup_var.get()}")
        if on_saved:
            on_saved()
        win.destroy()

    tk.Button(ftr, text="✓ 保存", command=save,
              bg=C_BTN, fg="white", relief="flat", padx=10, pady=3,
              font=("Segoe UI", 9), cursor="hand2").pack(side="right", padx=2)
    tk.Button(ftr, text="キャンセル", command=win.destroy,
              bg=C_BTN2, fg="white", relief="flat", padx=10, pady=3,
              font=("Segoe UI", 9), cursor="hand2").pack(side="right", padx=2)

    win.update_idletasks()
    w = win.winfo_reqwidth(); h = win.winfo_reqheight()
    sw = win.winfo_screenwidth(); sh = win.winfo_screenheight()
    win.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

# ================================================================
# メインアプリ
# ================================================================
class App:
    def __init__(self):
        self.config      = Config()
        self.data        = AppData()
        self.icon        = None
        self.root        = None
        self._running    = True
        self._popup      = None
        self._wake_event = threading.Event()

    def refresh(self):
        # ── Claude ──────────────────────────────────────
        token = get_claude_token(self.config.claude_creds_path)
        if token:
            self.data.claude_absent = False
            raw, err = fetch_claude_usage(token)
            if raw:
                self.data.parse_claude(raw); self.data.claude_err = None
            else:
                self.data.claude_5h = self.data.claude_weekly = None
                self.data.claude_err = err or "API取得失敗"
        else:
            self.data.claude_absent = True
            self.data.claude_5h = self.data.claude_weekly = None
            self.data.claude_err = None

        # ── Codex ───────────────────────────────────────
        cx_token, cx_id = get_codex_auth(self.config.codex_auth_path)
        if cx_token:
            self.data.codex_absent = False
            raw, err = fetch_codex_usage(cx_token, cx_id or "")
            if raw:
                self.data.parse_codex(raw); self.data.codex_err = None
            else:
                self.data.codex_5h = self.data.codex_weekly = None
                self.data.codex_err = err or "API取得失敗"
        else:
            self.data.codex_absent = True
            self.data.codex_5h = self.data.codex_weekly = None
            self.data.codex_err = None

        self.data.last_updated = datetime.now()

        # ── アイコン更新 ──────────────────────────────
        if self.icon:
            c_rem = 1.0 - self.data.claude_5h.pct if self.data.claude_5h else 1.0
            x_rem = 1.0 - self.data.codex_5h.pct  if self.data.codex_5h  else 1.0
            self.icon.icon  = make_icon(c_rem, x_rem)
            self.icon.title = (
                f"{APP_NAME}\n"
                f"Claude残り: {int(c_rem * 100)}% | Codex残り: {int(x_rem * 100)}%"
            )

        # ── アラート判定 (A1) ──────────────────────────
        if self.root and self.config.alert_thresholds:
            for svc, label, rem_pct, thr in self.data.check_alerts(self.config.alert_thresholds):
                show_toast(
                    self.root,
                    f"⚠ {svc} 残量警告",
                    f"{label}ウィンドウの残量が {rem_pct:.1f}% です（閾値: {thr}%）",
                    color=C_ERR,
                )

        # ── ポップアップ再描画 ────────────────────────
        if self.root:
            self.root.after(0, self._rebuild_popup_if_open)

    def _refresh_loop(self):
        while self._running:
            try:
                self.refresh()
            except Exception as e:
                log(f"[Error] refresh: {e}")
            self._wake_event.clear()
            self._wake_event.wait(timeout=self.config.interval_sec)

    # ── ポップアップ制御 ─────────────────────────────────────
    def _open_popup(self):
        if self._popup and self._popup.winfo_exists():
            self._popup.lift(); return
        self._popup = tk.Toplevel(self.root)
        build_popup(self._popup, self.data,
                    on_refresh=self._trigger_refresh,
                    on_settings=self._open_settings)

    def _rebuild_popup_if_open(self):
        if self._popup and self._popup.winfo_exists():
            for w in self._popup.winfo_children():
                w.destroy()
            build_popup(self._popup, self.data,
                        on_refresh=self._trigger_refresh,
                        on_settings=self._open_settings)

    def _trigger_refresh(self):
        self._wake_event.set()

    def _open_settings(self):
        open_settings_window(
            self.root, self.config,
            on_saved=lambda: log("[Settings] 設定を反映"),
        )

    # ── トレイコールバック (pystrayスレッドから呼ばれる) ─────────
    def _on_open(self, icon, item):
        self.root.after(0, self._open_popup)

    def _on_refresh(self, icon, item):
        self._trigger_refresh()

    def _on_settings(self, icon, item):
        self.root.after(0, self._open_settings)

    def _on_quit(self, icon, item):
        self._running = False
        self._wake_event.set()
        icon.stop()
        self.root.after(0, self.root.quit)

    def run(self):
        log(f"[App] 起動 v{APP_VERSION} interval={self.config.interval_sec}s "
            f"thresholds={self.config.alert_thresholds}")

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.protocol("WM_DELETE_WINDOW", lambda: None)

        menu = pystray.Menu(
            pystray.MenuItem("使用率を確認", self._on_open, default=True),
            pystray.MenuItem("今すぐ更新",   self._on_refresh),
            pystray.MenuItem("設定",         self._on_settings),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("終了",         self._on_quit),
        )
        self.icon = pystray.Icon(APP_NAME, make_icon(0.0, 0.0), APP_NAME, menu)
        self.icon.run_detached()

        self.root.after(1000,
            lambda: threading.Thread(target=self._refresh_loop, daemon=True).start())

        self.root.mainloop()
        log("[App] 終了")


if __name__ == "__main__":
    if not ensure_single_instance():
        ctypes.windll.user32.MessageBoxW(
            0,
            "Token Checker はすでに起動しています。\nタスクバーのトレイアイコンを確認してください。",
            "Token Checker",
            0x40,  # MB_ICONINFORMATION
        )
        sys.exit(0)
    App().run()
