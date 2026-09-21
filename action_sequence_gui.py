# -*- coding: utf-8 -*-
"""
行动序列合成图生成器（GUI 版）
================================

把一段视频中运动物体在不同时刻的位置"抠出来"，叠加到同一张静态背景上，
得到一张 action-sequence / 轨迹合成图。

相比旧的 active_squence_run.py / active_squence_run_listed.py，本工具做了以下改进：

  1. 完整的图形界面（Tkinter + OpenCV + Pillow），无需改代码。
  2. 两种取帧方式：
       - 固定间隔：在 [起始, 结束] 区间内，从起始帧开始每隔 N 帧（或 N 秒）取一帧。
       - 手动选帧：逐帧/播放浏览视频，把当前帧加入列表；支持插入、删除、上移、下移。
  3. 生成过程可视化：实时显示 当前帧 / 前景遮罩 / 合成结果，并有进度条。
  4. 所有算法参数都可以在界面上调节（MOG2 参数、形态学核、面积阈值、多边形近似、
     背景采样帧数、随机种子等）。
  5. 修复旧代码中的若干隐藏 bug（详见文件末尾的"修复说明"）。

运行：  python action_sequence_gui.py
依赖：  numpy, opencv-python, Pillow（Python 自带 tkinter）
"""

import os
import sys
import queue
import threading
import traceback

import numpy as np
import cv2
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk


# ---------------------------------------------------------------------------
# 预览尺寸
# ---------------------------------------------------------------------------
MAIN_W, MAIN_H = 700, 400      # 大图（视频预览 / 合成结果）
SMALL_W, SMALL_H = 340, 200    # 小图（当前帧 / 前景遮罩）


def _downscale(img, max_w):
    """把图像等比缩小到最大宽度 max_w，用于减轻后台线程→界面队列的内存压力。"""
    if img is None:
        return None
    h, w = img.shape[:2]
    if w > max_w:
        s = max_w / float(w)
        img = cv2.resize(img, (max_w, int(round(h * s))), interpolation=cv2.INTER_AREA)
    return img


# ---------------------------------------------------------------------------
# 算法参数
# ---------------------------------------------------------------------------
class CompositeParams:
    def __init__(self):
        self.history = 100          # MOG2 背景模型历史帧数
        self.var_threshold = 40.0   # MOG2 方差阈值（越大越不敏感）
        self.kernel_size = 10       # 形态学闭运算椭圆核尺寸
        self.area_threshold = 200.0 # 轮廓面积过滤阈值（滤除噪点）
        self.epsilon = 0.01         # 多边形近似系数（占轮廓周长的比例）
        self.n_bg_frames = 15       # 背景估计采样帧数（取中值）
        self.seed = 42              # 随机种子（0 表示随机）


# ---------------------------------------------------------------------------
# 合成引擎（与界面解耦，可单独测试）
# ---------------------------------------------------------------------------
class CompositeEngine:
    def __init__(self, video_path):
        self.video_path = video_path
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise RuntimeError("无法打开视频: " + video_path)
        self.fps = cap.get(cv2.CAP_PROP_FPS)
        self.frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        if self.fps <= 0 or np.isnan(self.fps):
            self.fps = 30.0
        if self.frame_count <= 0:
            raise RuntimeError("视频帧数为 0，无法处理")

    @property
    def duration(self):
        return self.frame_count / self.fps

    def read_frame_at(self, cap, idx):
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = cap.read()
        return frame if ret else None

    def estimate_background(self, cap, params):
        """在视频中随机采样若干帧并取中值，作为静态背景。"""
        rng = np.random.default_rng(None if params.seed == 0 else int(params.seed))
        n = max(1, int(params.n_bg_frames))
        idxs = rng.integers(0, self.frame_count, size=n)
        frames = []
        for i in idxs:
            f = self.read_frame_at(cap, int(i))
            if f is not None:
                frames.append(f)
        if not frames:
            raise RuntimeError("背景估计失败：无法读取采样帧")
        bg = np.median(np.stack(frames, axis=0), axis=0).astype(np.uint8)
        return bg

    def generate(self, target_indices, params, progress_cb=None, preview_cb=None,
                 cancel=None):
        """把 target_indices（0 起始帧号）中的物体叠加到背景上，返回合成图。

        progress_cb(ratio, frame_no, last, processed)  处理进度回调
        preview_cb(frame, mask, output)  每个目标帧处理完后的预览回调
        """
        target_indices = sorted({int(i) for i in target_indices})
        target_indices = [i for i in target_indices if 0 <= i < self.frame_count]
        if not target_indices:
            raise RuntimeError("没有有效的目标帧")

        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            raise RuntimeError("无法打开视频")
        try:
            bg = self.estimate_background(cap, params)

            # MOG2 背景减除器
            fgbg = cv2.createBackgroundSubtractorMOG2(
                history=int(params.history),
                varThreshold=float(params.var_threshold),
                detectShadows=False)

            # 形态学核（强制为奇数）
            k = max(1, int(params.kernel_size))
            if k % 2 == 0:
                k += 1
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))

            output = bg.copy()
            target_set = set(target_indices)
            first = target_indices[0]
            last = target_indices[-1]
            total = last - first + 1

            # 从第一个目标帧开始顺序读取，注意：对每一帧都调用 fgbg.apply，
            # 让背景模型在相邻帧之间得到充分训练（旧代码只在目标帧上训练，
            # 导致背景模型几乎没学到东西）。
            cap.set(cv2.CAP_PROP_POS_FRAMES, first)
            idx = first
            processed = 0
            while idx <= last:
                if cancel is not None and cancel.is_set():
                    raise InterruptedError("已取消")

                ret, frame = cap.read()
                if not ret:
                    break

                fgmask = fgbg.apply(frame)

                if idx in target_set:
                    fg = cv2.morphologyEx(fgmask, cv2.MORPH_CLOSE, kernel)
                    contours, _ = cv2.findContours(
                        fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

                    mask = np.zeros(frame.shape[:2], np.uint8)
                    for cnt in contours:
                        # 只保留面积足够大的轮廓（滤噪）
                        if cv2.contourArea(cnt) <= params.area_threshold:
                            continue
                        arclen = cv2.arcLength(cnt, True)
                        if arclen <= 0:
                            continue
                        eps = float(params.epsilon) * arclen
                        poly = cv2.approxPolyDP(cnt, eps, True)
                        # 关键：只绘制当前这个过滤后的多边形，
                        # 而不是把所有轮廓（含噪点）都画进 mask。
                        cv2.drawContours(mask, [poly], -1, 255, cv2.FILLED)

                    output[mask == 255] = frame[mask == 255]
                    processed += 1

                    if preview_cb is not None:
                        preview_cb(frame.copy(), mask.copy(), output.copy())

                idx += 1
                if progress_cb is not None and (idx - first) % 10 == 0:
                    progress_cb(min(1.0, (idx - first) / max(1, total)),
                                idx, last, processed)

            if progress_cb is not None:
                progress_cb(1.0, last, last, processed)
            return output
        finally:
            cap.release()


# ---------------------------------------------------------------------------
# 界面文案（中文 / English），key 通过 self.tr() 读取
# ---------------------------------------------------------------------------
TEXTS = {
    "title":        {"zh": "行动序列合成图生成器", "en": "Action Sequence Composer"},
    "lang":         {"zh": "语言", "en": "Language"},
    "open_video":   {"zh": "选择视频...", "en": "Open Video..."},
    "no_video":     {"zh": "未选择视频", "en": "No video selected"},

    "mode_group":   {"zh": "取帧模式", "en": "Frame selection mode"},
    "mode_fixed":   {"zh": "固定间隔", "en": "Fixed interval"},
    "mode_manual":  {"zh": "手动选帧", "en": "Manual selection"},

    "range_group":  {"zh": "作图时间区间（秒，留空结束=到视频末尾）",
                     "en": "Time range (seconds; blank end = to video end)"},
    "start":        {"zh": "起始", "en": "Start"},
    "end":          {"zh": "结束", "en": "End"},

    "interval_group": {"zh": "固定间隔", "en": "Fixed interval"},
    "interval":       {"zh": "间隔", "en": "Interval"},
    "unit_frame":     {"zh": "帧", "en": "frames"},
    "unit_second":    {"zh": "秒", "en": "seconds"},

    "algo_group":   {"zh": "算法参数", "en": "Algorithm parameters"},
    "p_history":    {"zh": "MOG2 history（背景历史帧数）", "en": "MOG2 history (background frames)"},
    "p_varth":      {"zh": "MOG2 varThreshold（灵敏度）", "en": "MOG2 varThreshold (sensitivity)"},
    "p_kernel":     {"zh": "形态学核大小（闭运算）", "en": "Morphology kernel size (closing)"},
    "p_area":       {"zh": "轮廓面积阈值（滤噪）", "en": "Contour area threshold (denoise)"},
    "p_eps":        {"zh": "多边形近似 epsilon", "en": "Polygon approx. epsilon"},
    "p_nbg":        {"zh": "背景采样帧数", "en": "Background sample frames"},
    "p_seed":       {"zh": "随机种子（0=随机）", "en": "Random seed (0=random)"},
    "reset_params": {"zh": "恢复默认参数", "en": "Reset to defaults"},

    "out_group":    {"zh": "输出文件", "en": "Output file"},
    "browse":       {"zh": "浏览...", "en": "Browse..."},
    "generate":     {"zh": "生成合成图", "en": "Generate composite"},
    "cancel":       {"zh": "取消", "en": "Cancel"},
    "status_ready": {"zh": "就绪", "en": "Ready"},

    "hint": {
        "zh": ("使用说明：\n"
               "· 固定间隔：在时间区间内每隔 N 帧（或秒）取一帧。\n"
               "· 手动选帧：用下方按钮逐帧/播放浏览，点击“添加/插入”\n"
               "  管理帧列表，最后生成。\n"
               "· 生成时右侧会实时显示 当前帧 / 前景遮罩 / 合成结果。"),
        "en": ("How to use:\n"
               "· Fixed interval: take a frame every N frames (or seconds).\n"
               "· Manual: browse frame-by-frame with the buttons below, use\n"
               "  Add/Insert to manage the list, then generate.\n"
               "· During generation the right side shows frame / mask / composite."),
    },

    "preview_group": {"zh": "预览（生成时实时更新）", "en": "Preview (live during generation)"},
    "main_panel":    {"zh": "视频预览 / 合成结果", "en": "Video preview / Composite result"},
    "frame_panel":   {"zh": "当前帧", "en": "Current frame"},
    "mask_panel":    {"zh": "前景遮罩", "en": "Foreground mask"},

    "ctrl_group":    {"zh": "浏览 / 播放", "en": "Browse / Play"},
    "play":          {"zh": "▶ 播放", "en": "▶ Play"},
    "pause":         {"zh": "⏸ 暂停", "en": "⏸ Pause"},
    "frame_pos":     {"zh": "帧 %d / %d  (%.2f s)", "en": "Frame %d / %d  (%.2f s)"},
    "video_info":    {"zh": "%d×%d | %.3f fps | %d 帧 | %.2f 秒",
                      "en": "%d×%d | %.3f fps | %d frames | %.2f s"},

    "list_group":    {"zh": "手动选帧列表（帧号为 1 起始）", "en": "Selected frames (1-based numbers)"},
    "add_current":   {"zh": "添加当前帧", "en": "Add current"},
    "insert_current": {"zh": "插入到选中前", "en": "Insert before selected"},
    "delete_selected": {"zh": "删除选中", "en": "Delete selected"},
    "move_up":       {"zh": "上移", "en": "Move up"},
    "move_down":     {"zh": "下移", "en": "Move down"},
    "clear_list":    {"zh": "清空列表", "en": "Clear list"},
    "auto_fill":     {"zh": "按间隔自动填充", "en": "Auto-fill by interval"},

    # 对话框 / 状态
    "err":          {"zh": "错误", "en": "Error"},
    "info":         {"zh": "提示", "en": "Info"},
    "done":         {"zh": "完成", "en": "Done"},
    "file_video":   {"zh": "视频文件", "en": "Video files"},
    "file_all":     {"zh": "所有文件", "en": "All files"},
    "dlg_open":     {"zh": "选择视频", "en": "Open video"},
    "dlg_save":     {"zh": "保存合成图", "en": "Save composite"},
    "cannot_open":  {"zh": "无法打开视频", "en": "Cannot open video"},
    "loaded":       {"zh": "已加载视频，可开始取帧", "en": "Video loaded. Ready to select frames."},
    "need_frame":   {"zh": "请先加载视频并浏览到目标帧", "en": "Please load a video and navigate to a frame first."},
    "need_video":   {"zh": "请先选择视频", "en": "Please open a video first."},
    "filled_n":     {"zh": "已按间隔填充 %d 帧", "en": "Auto-filled %d frames"},
    "no_targets": {
        "zh": "没有可用的目标帧。\n固定间隔：检查时间区间与间隔；\n手动选帧：先在列表里添加帧。",
        "en": "No target frames available.\nFixed interval: check time range and interval;\nManual: add frames to the list first.",
    },
    "generating":   {"zh": "生成中...", "en": "Generating..."},
    "processing":   {"zh": "处理帧 %d / %d", "en": "Processing frame %d / %d"},
    "done_frames":  {"zh": "完成，共合成 %d 帧", "en": "Done, %d frames composited"},
    "cancelled":    {"zh": "已取消", "en": "Cancelled"},
    "cancelling":   {"zh": "正在取消...", "en": "Cancelling..."},
    "write_failed": {"zh": "写入失败（检查路径与扩展名）", "en": "Write failed (check path and extension)"},
    "save_failed":  {"zh": "保存失败", "en": "Save failed"},
    "save_failed_f": {"zh": "保存失败: %s", "en": "Save failed: %s"},
    "done_saved":   {"zh": "完成，已保存: %s", "en": "Done. Saved to: %s"},
    "done_saved_box": {"zh": "合成图已保存到:\n%s", "en": "Composite saved to:\n%s"},
    "interval_hint_sec": {"zh": "≈ 每 %g 秒 = %d 帧（@ %.3f fps）",
                          "en": "≈ every %g s = %d frames (@ %.3f fps)"},
    "interval_hint_frame": {"zh": "≈ 每 %d 帧 = %.2f 秒（@ %.3f fps）",
                            "en": "≈ every %d frames = %.2f s (@ %.3f fps)"},
    "prog_err_title": {"zh": "程序错误", "en": "Program error"},
    "prog_err_msg": {"zh": "发生未处理的错误:\n\n%s\n\n详细信息已写入:\n%s",
                     "en": "Unhandled error:\n\n%s\n\nDetails written to:\n%s"},
}


# ---------------------------------------------------------------------------
# 主界面
# ---------------------------------------------------------------------------
class App:
    def __init__(self, root):
        self.root = root
        self.lang = "zh"          # 当前语言：zh / en
        self.lang_var = tk.StringVar(value="zh")
        root.title(self.tr("title"))
        root.geometry("1500x920")
        root.minsize(1240, 760)

        # 视频状态
        self.video_path = None
        self.engine = None
        self.cap = None
        self.fps = 30.0
        self.frame_count = 0
        self.duration = 0.0
        self.current_idx = 0          # 0 起始帧号
        self.current_frame = None

        # 播放状态
        self.playing = False
        self._play_after = None

        # 手动选帧列表（0 起始帧号）
        self.selected_frames = []

        # 生成状态
        self._generating = False
        self._gen_queue = queue.Queue()
        self._gen_cancel = threading.Event()
        self._gen_thread = None

        # 界面变量
        self.mode_var = tk.StringVar(value="fixed")
        self.start_var = tk.StringVar(value="0")
        self.end_var = tk.StringVar(value="")
        self.interval_var = tk.StringVar(value="30")
        self.interval_unit_var = tk.StringVar(value="frame")
        self.output_var = tk.StringVar(value="")

        self.history_var = tk.StringVar(value="100")
        self.varth_var = tk.StringVar(value="40")
        self.kernel_var = tk.StringVar(value="10")
        self.area_var = tk.StringVar(value="200")
        self.eps_var = tk.StringVar(value="0.01")
        self.nbg_var = tk.StringVar(value="15")
        self.seed_var = tk.StringVar(value="42")

        self._build_ui()
        self._on_mode_change()
        self._update_playback_state()

    # ------------------------------------------------------------------ UI
    def tr(self, key):
        """取当前语言文案。"""
        return TEXTS.get(key, {}).get(self.lang, key)

    def _build_ui(self):
        self.top_frame = ttk.Frame(self.root, padding=(10, 8))
        self.top_frame.pack(fill="x")

        self.open_btn = ttk.Button(self.top_frame, text=self.tr("open_video"),
                                   command=self.choose_video)
        self.open_btn.pack(side="left")
        self.path_label = ttk.Label(self.top_frame, text=self.tr("no_video"),
                                    foreground="gray")
        self.path_label.pack(side="left", padx=10)
        self.info_label = ttk.Label(self.top_frame, text="")
        self.info_label.pack(side="left", padx=10)

        # 语言切换（右上角）
        ttk.Label(self.top_frame, text=self.tr("lang")).pack(side="right")
        ttk.Radiobutton(self.top_frame, text="English", value="en",
                        variable=self.lang_var,
                        command=lambda: self._switch_language("en")).pack(side="right")
        ttk.Radiobutton(self.top_frame, text="中文", value="zh",
                        variable=self.lang_var,
                        command=lambda: self._switch_language("zh")).pack(side="right")

        self.body = ttk.Frame(self.root, padding=(10, 0, 10, 8))
        self.body.pack(fill="both", expand=True)
        self._build_panels()

    def _build_panels(self):
        """(重新)构建左右面板；切换语言时调用以刷新所有文案。"""
        for attr in ("left_frame", "right_frame"):
            f = getattr(self, attr, None)
            if f is not None and f.winfo_exists():
                f.destroy()

        self.left_frame = ttk.Frame(self.body, width=430)
        self.left_frame.pack(side="left", fill="y")
        self.left_frame.pack_propagate(False)
        self._build_left(self.left_frame)

        self.right_frame = ttk.Frame(self.body)
        self.right_frame.pack(side="left", fill="both", expand=True, padx=(10, 0))
        self._build_right(self.right_frame)

        self._restore_ui_state()

    def _switch_language(self, lang):
        if lang not in ("zh", "en") or lang == self.lang:
            return
        self.lang = lang
        self.lang_var.set(lang)
        self.root.title(self.tr("title"))
        self.open_btn.configure(text=self.tr("open_video"))
        self.path_label.configure(
            text=(os.path.basename(self.video_path) if self.video_path
                  else self.tr("no_video")),
            foreground=("black" if self.video_path else "gray"))
        self._update_info_label()
        self._build_panels()

    def _restore_ui_state(self):
        """重建面板后恢复滑块、预览、列表等状态（不丢用户设置）。"""
        if self.frame_count > 0:
            self.slider.configure(from_=1, to=max(1, self.frame_count))
            self.slider_var.set(min(max(1, self.current_idx + 1), self.frame_count))
        self._on_mode_change()
        self._refresh_list()
        self._update_frame_label()
        if self.current_frame is not None:
            self._render(self.main_label, self.current_frame, MAIN_W, MAIN_H)
            self._render(self.frame_label, self.current_frame, SMALL_W, SMALL_H)
        else:
            self._set_blank(self.main_label, MAIN_W, MAIN_H)
            self._set_blank(self.frame_label, SMALL_W, SMALL_H)
            self._set_blank(self.mask_label, SMALL_W, SMALL_H)
        self.status_label.configure(text=self.tr("status_ready"), foreground="gray")

    def _update_info_label(self):
        if self.engine is None:
            self.info_label.configure(text="")
        else:
            self.info_label.configure(
                text=self.tr("video_info")
                % (self.engine.width, self.engine.height, self.fps,
                   self.frame_count, self.duration))

    def _build_left(self, parent):
        # --- 模式 ---
        mode = ttk.LabelFrame(parent, text=self.tr("mode_group"), padding=8)
        mode.pack(fill="x", pady=(0, 6))
        ttk.Radiobutton(mode, text=self.tr("mode_fixed"), value="fixed",
                        variable=self.mode_var,
                        command=self._on_mode_change).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(mode, text=self.tr("mode_manual"), value="manual",
                        variable=self.mode_var,
                        command=self._on_mode_change).grid(row=0, column=1, sticky="w", padx=12)

        # --- 时间区间 ---
        rng = ttk.LabelFrame(parent, text=self.tr("range_group"), padding=8)
        rng.pack(fill="x", pady=(0, 6))
        rng.columnconfigure(1, weight=1)
        rng.columnconfigure(3, weight=1)
        ttk.Label(rng, text=self.tr("start")).grid(row=0, column=0, sticky="w")
        ttk.Entry(rng, textvariable=self.start_var, width=8).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Label(rng, text=self.tr("end")).grid(row=0, column=2, sticky="w", padx=(12, 0))
        ttk.Entry(rng, textvariable=self.end_var, width=8).grid(row=0, column=3, sticky="ew", padx=4)

        # --- 固定间隔参数 ---
        self.interval_frame = ttk.LabelFrame(parent, text=self.tr("interval_group"), padding=8)
        self.interval_frame.pack(fill="x", pady=(0, 6))
        self.interval_frame.columnconfigure(1, weight=1)
        ttk.Label(self.interval_frame, text=self.tr("interval")).grid(row=0, column=0, sticky="w")
        self.interval_entry = ttk.Entry(self.interval_frame, textvariable=self.interval_var, width=8)
        self.interval_entry.grid(row=0, column=1, sticky="ew", padx=4)
        self.frame_unit = ttk.Radiobutton(self.interval_frame, text=self.tr("unit_frame"),
                                          value="frame", variable=self.interval_unit_var)
        self.frame_unit.grid(row=0, column=2, sticky="w")
        self.sec_unit = ttk.Radiobutton(self.interval_frame, text=self.tr("unit_second"),
                                        value="second", variable=self.interval_unit_var)
        self.sec_unit.grid(row=0, column=3, sticky="w", padx=4)
        self.interval_hint = ttk.Label(self.interval_frame, text="", foreground="gray")
        self.interval_hint.grid(row=1, column=0, columnspan=4, sticky="w", pady=(2, 0))

        # --- 算法参数 ---
        algo = ttk.LabelFrame(parent, text=self.tr("algo_group"), padding=8)
        algo.pack(fill="x", pady=(0, 6))
        algo.columnconfigure(1, weight=1)

        self._param_row(algo, 0, self.tr("p_history"), self.history_var)
        self._param_row(algo, 1, self.tr("p_varth"), self.varth_var)
        self._param_row(algo, 2, self.tr("p_kernel"), self.kernel_var)
        self._param_row(algo, 3, self.tr("p_area"), self.area_var)
        self._param_row(algo, 4, self.tr("p_eps"), self.eps_var)
        self._param_row(algo, 5, self.tr("p_nbg"), self.nbg_var)
        self._param_row(algo, 6, self.tr("p_seed"), self.seed_var)

        ttk.Button(algo, text=self.tr("reset_params"), command=self._reset_params).grid(
            row=7, column=0, columnspan=2, sticky="w", pady=(4, 0))

        # --- 输出 ---
        out = ttk.LabelFrame(parent, text=self.tr("out_group"), padding=8)
        out.pack(fill="x", pady=(0, 6))
        out.columnconfigure(1, weight=1)
        self.output_entry = ttk.Entry(out, textvariable=self.output_var)
        self.output_entry.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Button(out, text=self.tr("browse"), command=self.choose_output).grid(
            row=0, column=2, padx=(4, 0))

        # --- 生成 ---
        gen = ttk.Frame(parent)
        gen.pack(fill="x", pady=(2, 0))
        self.gen_btn = ttk.Button(gen, text=self.tr("generate"), command=self.start_generate)
        self.gen_btn.pack(side="left")
        self.cancel_btn = ttk.Button(gen, text=self.tr("cancel"), command=self.cancel_generate,
                                     state="disabled")
        self.cancel_btn.pack(side="left", padx=6)
        self.status_label = ttk.Label(gen, text=self.tr("status_ready"), foreground="gray")
        self.status_label.pack(side="left", padx=6)

        self.progress = ttk.Progressbar(parent, mode="determinate", maximum=1.0)
        self.progress.pack(fill="x", pady=(6, 0))

        ttk.Label(parent, text=self.tr("hint"), foreground="gray", justify="left").pack(
            fill="x", pady=(8, 0))

    def _param_row(self, parent, row, label, var):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 6), pady=2)
        ttk.Entry(parent, textvariable=var, width=10).grid(row=row, column=1, sticky="ew", pady=2)

    def _build_right(self, parent):
        # 预览区：三张图
        preview = ttk.LabelFrame(parent, text=self.tr("preview_group"), padding=8)
        preview.pack(fill="both", expand=True)
        preview.columnconfigure(0, weight=1)
        preview.columnconfigure(1, weight=1)

        self.main_panel = ttk.LabelFrame(preview, text=self.tr("main_panel"), padding=4)
        self.main_panel.grid(row=0, column=0, columnspan=2, sticky="n", pady=(0, 6))
        self.main_label = ttk.Label(self.main_panel)
        self.main_label.pack()

        self.frame_panel = ttk.LabelFrame(preview, text=self.tr("frame_panel"), padding=4)
        self.frame_panel.grid(row=1, column=0, sticky="n", padx=(0, 3))
        self.frame_label = ttk.Label(self.frame_panel)
        self.frame_label.pack()

        self.mask_panel = ttk.LabelFrame(preview, text=self.tr("mask_panel"), padding=4)
        self.mask_panel.grid(row=1, column=1, sticky="n", padx=(3, 0))
        self.mask_label = ttk.Label(self.mask_panel)
        self.mask_label.pack()

        self._set_blank(self.main_label, MAIN_W, MAIN_H)
        self._set_blank(self.frame_label, SMALL_W, SMALL_H)
        self._set_blank(self.mask_label, SMALL_W, SMALL_H)

        # 播放控制
        ctrl = ttk.LabelFrame(parent, text=self.tr("ctrl_group"), padding=8)
        ctrl.pack(fill="x", pady=(6, 0))

        self.slider_var = tk.IntVar(value=1)
        self.slider = tk.Scale(ctrl, from_=1, to=1, orient=tk.HORIZONTAL,
                               variable=self.slider_var, showvalue=False,
                               command=self._on_slider_move)
        self.slider.pack(fill="x")
        self.slider.bind("<ButtonRelease-1>", self._on_slider_release)

        btns = ttk.Frame(ctrl)
        btns.pack(fill="x", pady=(4, 0))
        ttk.Button(btns, text="|◀", width=4, command=lambda: self.step_to(0)).pack(side="left")
        ttk.Button(btns, text="◀◀", width=4, command=lambda: self.step_by(-10)).pack(side="left", padx=2)
        ttk.Button(btns, text="◀", width=4, command=lambda: self.step_by(-1)).pack(side="left", padx=2)
        self.play_btn = ttk.Button(btns, text=self.tr("play"), width=8, command=self.play_toggle)
        self.play_btn.pack(side="left", padx=2)
        ttk.Button(btns, text="▶", width=4, command=lambda: self.step_by(1)).pack(side="left", padx=2)
        ttk.Button(btns, text="▶▶", width=4, command=lambda: self.step_by(10)).pack(side="left", padx=2)
        ttk.Button(btns, text="▶|", width=4, command=lambda: self.step_to(self.frame_count - 1)).pack(side="left", padx=2)

        self.frame_pos_label = ttk.Label(btns, text=self.tr("frame_pos") % (0, 0, 0.0))
        self.frame_pos_label.pack(side="left", padx=10)

        # 手动选帧列表
        lst = ttk.LabelFrame(parent, text=self.tr("list_group"), padding=8)
        lst.pack(fill="both", expand=True, pady=(6, 0))

        listwrap = ttk.Frame(lst)
        listwrap.pack(side="left", fill="both", expand=True)
        self.listbox = tk.Listbox(listwrap, selectmode=tk.EXTENDED, width=30, height=9)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(listwrap, orient="vertical", command=self.listbox.yview)
        sb.pack(side="right", fill="y")
        self.listbox.configure(yscrollcommand=sb.set)

        lb = ttk.Frame(lst)
        lb.pack(side="left", fill="y", padx=(8, 0))
        ttk.Button(lb, text=self.tr("add_current"), command=self.add_current).pack(fill="x", pady=1)
        ttk.Button(lb, text=self.tr("insert_current"), command=self.insert_current).pack(fill="x", pady=1)
        ttk.Button(lb, text=self.tr("delete_selected"), command=self.delete_selected).pack(fill="x", pady=1)
        ttk.Button(lb, text=self.tr("move_up"), command=lambda: self.move_selected(-1)).pack(fill="x", pady=1)
        ttk.Button(lb, text=self.tr("move_down"), command=lambda: self.move_selected(1)).pack(fill="x", pady=1)
        ttk.Button(lb, text=self.tr("clear_list"), command=self.clear_list).pack(fill="x", pady=1)
        ttk.Button(lb, text=self.tr("auto_fill"), command=self.auto_fill).pack(fill="x", pady=(8, 1))

    # ------------------------------------------------------------- 解析工具
    @staticmethod
    def _parse_float(s, default):
        s = str(s).strip()
        if s == "":
            return default
        try:
            return float(s)
        except ValueError:
            return default

    @staticmethod
    def _parse_int(s, default):
        try:
            return int(round(float(s)))
        except (ValueError, TypeError):
            return default

    # ------------------------------------------------------------- 选视频/输出
    def choose_video(self):
        path = filedialog.askopenfilename(
            title=self.tr("dlg_open"),
            filetypes=[(self.tr("file_video"), "*.mp4 *.mov *.avi *.mkv *.m4v *.MP4 *.MOV"),
                       (self.tr("file_all"), "*.*")])
        if not path:
            return
        self.load_video(path)

    def load_video(self, path):
        try:
            engine = CompositeEngine(path)
        except Exception as e:
            messagebox.showerror(self.tr("err"), "%s\n%s" % (self.tr("cannot_open"), str(e)))
            return
        if self.cap is not None:
            self.cap.release()
        self.cap = cv2.VideoCapture(path)
        if not self.cap.isOpened():
            messagebox.showerror(self.tr("err"), self.tr("cannot_open"))
            return

        self.video_path = path
        self.engine = engine
        self.fps = engine.fps
        self.frame_count = engine.frame_count
        self.duration = engine.duration

        self.path_label.configure(text=os.path.basename(path), foreground="black")
        self._update_info_label()

        self.slider.configure(from_=1, to=max(1, self.frame_count))
        self.slider_var.set(1)

        default_out = os.path.splitext(path)[0] + "_composite.jpg"
        self.output_var.set(default_out)

        self.show_frame(0)
        self.status_label.configure(text=self.tr("loaded"), foreground="gray")

    def choose_output(self):
        path = filedialog.asksaveasfilename(
            title=self.tr("dlg_save"),
            defaultextension=".jpg",
            initialfile=os.path.splitext(os.path.basename(self.video_path or "output"))[0]
                        + "_composite.jpg" if self.video_path else "output_composite.jpg",
            filetypes=[("JPEG", "*.jpg *.jpeg"), ("PNG", "*.png"),
                       (self.tr("file_all"), "*.*")])
        if path:
            self.output_var.set(path)

    # ------------------------------------------------------------- 显示
    def _set_blank(self, label, w, h):
        img = np.zeros((h, w, 3), np.uint8)
        self._render(label, img, w, h)

    def _render(self, label, img, max_w, max_h):
        if img is None:
            return
        if len(img.shape) == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        h, w = img.shape[:2]
        scale = min(max_w / w, max_h / h, 1.0)
        if scale < 1.0:
            img = cv2.resize(img, (int(w * scale), int(h * scale)),
                             interpolation=cv2.INTER_AREA)
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        photo = ImageTk.PhotoImage(Image.fromarray(img))
        label.configure(image=photo, text="")
        label.image = photo  # 保持引用，防止被回收

    def show_frame(self, idx):
        if self.cap is None or self.frame_count == 0:
            return
        idx = max(0, min(self.frame_count - 1, int(idx)))
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = self.cap.read()
        if not ret:
            return
        self.current_idx = idx
        self.current_frame = frame
        self.slider_var.set(idx + 1)
        self._update_frame_label()
        self._render(self.main_label, frame, MAIN_W, MAIN_H)
        self._render(self.frame_label, frame, SMALL_W, SMALL_H)

    def _update_frame_label(self):
        t = self.current_idx / self.fps
        self.frame_pos_label.configure(
            text=self.tr("frame_pos") % (self.current_idx + 1, self.frame_count, t))

    def _on_slider_move(self, val):
        # 拖动时只刷新文字，松手后再 seek，避免卡顿
        try:
            idx = int(float(val)) - 1
        except ValueError:
            return
        idx = max(0, min(self.frame_count - 1, idx))
        t = idx / self.fps
        self.frame_pos_label.configure(
            text=self.tr("frame_pos") % (idx + 1, self.frame_count, t))

    def _on_slider_release(self, event):
        self.show_frame(self.slider_var.get() - 1)

    # ------------------------------------------------------------- 播放
    def step_to(self, idx):
        self.stop_play()
        self.show_frame(idx)

    def step_by(self, delta):
        self.stop_play()
        self.show_frame(self.current_idx + delta)

    def play_toggle(self):
        if self.playing:
            self.stop_play()
        else:
            if self.cap is None:
                return
            self.playing = True
            self.play_btn.configure(text=self.tr("pause"))
            self._play_tick()

    def stop_play(self):
        self.playing = False
        if self._play_after is not None:
            try:
                self.root.after_cancel(self._play_after)
            except Exception:
                pass
            self._play_after = None
        self.play_btn.configure(text=self.tr("play"))

    def _play_tick(self):
        if not self.playing or self.cap is None:
            return
        ret, frame = self.cap.read()
        if not ret:
            self.stop_play()
            self.show_frame(self.frame_count - 1)
            return
        self.current_idx += 1
        if self.current_idx >= self.frame_count:
            self.current_idx = self.frame_count - 1
            self.stop_play()
            return
        self.current_frame = frame
        self.slider_var.set(self.current_idx + 1)
        self._update_frame_label()
        self._render(self.main_label, frame, MAIN_W, MAIN_H)
        self._render(self.frame_label, frame, SMALL_W, SMALL_H)
        delay = max(1, int(1000.0 / self.fps))
        self._play_after = self.root.after(delay, self._play_tick)

    # ------------------------------------------------------------- 帧列表
    def _refresh_list(self):
        self.listbox.delete(0, tk.END)
        for i in self.selected_frames:
            self.listbox.insert(tk.END, "%5d   %.2f s" % (i + 1, i / self.fps))

    def _list_selected_indices(self):
        return list(self.listbox.curselection())

    def add_current(self):
        self.stop_play()
        if self.current_frame is None:
            messagebox.showinfo(self.tr("info"), self.tr("need_frame"))
            return
        if self.current_idx not in self.selected_frames:
            self.selected_frames.append(self.current_idx)
            self.selected_frames.sort()
        self._refresh_list()

    def insert_current(self):
        self.stop_play()
        if self.current_frame is None:
            messagebox.showinfo(self.tr("info"), self.tr("need_frame"))
            return
        sel = self._list_selected_indices()
        if self.current_idx in self.selected_frames:
            self.selected_frames.remove(self.current_idx)
        if sel:
            pos = sel[0]
            self.selected_frames.insert(pos, self.current_idx)
        else:
            self.selected_frames.append(self.current_idx)
            self.selected_frames.sort()
        self._refresh_list()

    def delete_selected(self):
        sel = sorted(self._list_selected_indices(), reverse=True)
        for i in sel:
            if 0 <= i < len(self.selected_frames):
                del self.selected_frames[i]
        self._refresh_list()

    def move_selected(self, delta):
        sel = self._list_selected_indices()
        if not sel:
            return
        # 单次只移动第一个选中项，避免 EXTENDED 多选时逻辑混乱
        i = sel[0]
        j = i + delta
        if 0 <= j < len(self.selected_frames):
            self.selected_frames[i], self.selected_frames[j] = \
                self.selected_frames[j], self.selected_frames[i]
            self._refresh_list()
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(j)
            self.listbox.activate(j)

    def clear_list(self):
        self.selected_frames = []
        self._refresh_list()

    def auto_fill(self):
        """按当前时间区间与间隔参数，自动往列表里填充帧（手动模式可作起点）。"""
        if self.engine is None:
            messagebox.showinfo(self.tr("info"), self.tr("need_video"))
            return
        try:
            targets = self._compute_interval_targets()
        except RuntimeError as e:
            messagebox.showerror(self.tr("err"), str(e))
            return
        self.selected_frames = sorted(set(targets))
        self._refresh_list()
        self.status_label.configure(text=self.tr("filled_n") % len(self.selected_frames),
                                    foreground="gray")

    # ------------------------------------------------------------- 模式
    def _on_mode_change(self):
        if self.mode_var.get() == "fixed":
            state = "normal"
        else:
            state = "disabled"
        for w in (self.interval_entry, self.frame_unit, self.sec_unit):
            w.configure(state=state)
        self._update_interval_hint()

    def _update_interval_hint(self):
        val = self._parse_float(self.interval_var.get(), 30)
        if self.interval_unit_var.get() == "second":
            frames = max(1, round(val * self.fps)) if self.fps > 0 else 0
            self.interval_hint.configure(text=self.tr("interval_hint_sec")
                                         % (val, frames, self.fps))
        else:
            frames = max(1, round(val))
            if self.fps > 0:
                self.interval_hint.configure(text=self.tr("interval_hint_frame")
                                             % (frames, frames / self.fps, self.fps))
            else:
                self.interval_hint.configure(text="")

    def _reset_params(self):
        self.history_var.set("100")
        self.varth_var.set("40")
        self.kernel_var.set("10")
        self.area_var.set("200")
        self.eps_var.set("0.01")
        self.nbg_var.set("15")
        self.seed_var.set("42")

    # ------------------------------------------------------------- 取帧/参数
    def _compute_interval_targets(self):
        if self.engine is None:
            raise RuntimeError(self.tr("need_video"))
        fps = self.fps
        start_s = self._parse_float(self.start_var.get(), 0.0)
        end_s = self._parse_float(self.end_var.get(), self.duration)
        start_frame = max(0, min(self.frame_count - 1, int(round(start_s * fps))))
        end_frame = max(start_frame, min(self.frame_count - 1, int(round(end_s * fps))))

        val = self._parse_float(self.interval_var.get(), 30)
        if self.interval_unit_var.get() == "second":
            step = max(1, int(round(val * fps)))
        else:
            step = max(1, int(round(val)))
        targets = list(range(start_frame, end_frame + 1, step))
        if not targets:
            targets = [start_frame]
        return targets

    def _collect_targets(self):
        if self.mode_var.get() == "fixed":
            return self._compute_interval_targets()
        return sorted(set(self.selected_frames))

    def _collect_params(self):
        p = CompositeParams()
        p.history = max(1, self._parse_int(self.history_var.get(), 100))
        p.var_threshold = max(0.0, self._parse_float(self.varth_var.get(), 40.0))
        p.kernel_size = max(1, self._parse_int(self.kernel_var.get(), 10))
        p.area_threshold = max(0.0, self._parse_float(self.area_var.get(), 200.0))
        p.epsilon = max(0.0, self._parse_float(self.eps_var.get(), 0.01))
        p.n_bg_frames = max(1, self._parse_int(self.nbg_var.get(), 15))
        p.seed = max(0, self._parse_int(self.seed_var.get(), 42))
        return p

    # ------------------------------------------------------------- 生成
    def start_generate(self):
        if self._generating:
            return
        if self.engine is None:
            messagebox.showinfo(self.tr("info"), self.tr("need_video"))
            return
        try:
            targets = self._collect_targets()
        except RuntimeError as e:
            messagebox.showerror(self.tr("err"), str(e))
            return
        if not targets:
            messagebox.showinfo(self.tr("info"), self.tr("no_targets"))
            return
        params = self._collect_params()

        self.stop_play()
        self._generating = True
        self._gen_cancel.clear()
        self.gen_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.progress["value"] = 0
        self.status_label.configure(text=self.tr("generating"), foreground="black")

        self._gen_thread = threading.Thread(
            target=self._generate_worker, args=(targets, params), daemon=True)
        self._gen_thread.start()
        self.root.after(60, self._poll_generation)

    def _generate_worker(self, targets, params):
        def progress_cb(ratio, idx, last, processed):
            if ratio >= 1.0:
                msg = self.tr("done_frames") % processed
            else:
                msg = self.tr("processing") % (idx, last)
            self._gen_queue.put(("progress", ratio, msg))

        def preview_cb(frame, mask, output):
            # 后台线程里先缩小，避免往队列塞 4K 大图
            self._gen_queue.put(("preview",
                                 _downscale(frame, 640),
                                 _downscale(mask, 640),
                                 _downscale(output, 960)))

        try:
            engine = CompositeEngine(self.video_path)
            result = engine.generate(targets, params,
                                     progress_cb=progress_cb,
                                     preview_cb=preview_cb,
                                     cancel=self._gen_cancel)
            self._gen_queue.put(("done", result, None))
        except InterruptedError:
            self._gen_queue.put(("error", None, self.tr("cancelled")))
        except Exception as e:
            self._gen_queue.put(("error", None, str(e)))

    def _poll_generation(self):
        try:
            while True:
                item = self._gen_queue.get_nowait()
                kind = item[0]
                if kind == "progress":
                    self.progress["value"] = item[1]
                    self.status_label.configure(text=item[2], foreground="black")
                elif kind == "preview":
                    self._render(self.frame_label, item[1], SMALL_W, SMALL_H)
                    self._render(self.mask_label, item[2], SMALL_W, SMALL_H)
                    self._render(self.main_label, item[3], MAIN_W, MAIN_H)
                elif kind == "done":
                    self._finish_generate(item[1], None)
                    return
                elif kind == "error":
                    self._finish_generate(None, item[2])
                    return
        except queue.Empty:
            pass
        if self._generating:
            self.root.after(60, self._poll_generation)

    def _finish_generate(self, result, error):
        self._generating = False
        self.gen_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")

        if error is not None:
            self.status_label.configure(text=error, foreground="red")
            messagebox.showerror(self.tr("err"), error)
            return

        out = self.output_var.get().strip()
        if not out:
            out = os.path.splitext(self.video_path)[0] + "_composite.jpg"
            self.output_var.set(out)
        try:
            ok = cv2.imwrite(out, result)
            if not ok:
                raise RuntimeError(self.tr("write_failed"))
        except Exception as e:
            self.status_label.configure(text=self.tr("save_failed_f") % e, foreground="red")
            messagebox.showerror(self.tr("save_failed"), str(e))
            return

        self._render(self.main_label, result, MAIN_W, MAIN_H)
        self.status_label.configure(text=self.tr("done_saved") % out, foreground="green")
        messagebox.showinfo(self.tr("done"), self.tr("done_saved_box") % out)

    def cancel_generate(self):
        if self._generating:
            self._gen_cancel.set()
            self.status_label.configure(text=self.tr("cancelling"), foreground="red")

    def _update_playback_state(self):
        pass

    def on_close(self):
        self.stop_play()
        if self._generating:
            self._gen_cancel.set()
        if self.cap is not None:
            self.cap.release()
        self.root.destroy()


def _log_path():
    """错误日志路径：打包成 exe 时放在 exe 旁边，否则放在脚本旁边。"""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "composer_error.log")


def _install_excepthook():
    """打包成无控制台的 exe 后，捕获未处理异常：写入日志 + 弹窗提示。"""

    def hook(exc_type, exc_value, exc_tb):
        msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            with open(_log_path(), "a", encoding="utf-8") as f:
                f.write("\n" + "=" * 60 + "\n" + msg)
        except Exception:
            pass
        try:
            messagebox.showerror(
                "程序错误 / Program error",
                "发生未处理的错误 / Unhandled error:\n\n%s\n\n"
                "详细信息已写入 / Details written to:\n%s"
                % (exc_value, _log_path()))
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    def thread_hook(args):
        hook(args.exc_type, args.exc_value, args.exc_traceback)

    sys.excepthook = hook
    threading.excepthook = thread_hook


def main():
    _install_excepthook()
    root = tk.Tk()
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()


# ===========================================================================
# 旧代码隐藏 bug 修复说明
# ===========================================================================
# 1. 背景减除器只在目标帧上调用 fgbg.apply()：MOG2 需要连续帧训练背景，
#    旧代码导致背景模型几乎没学到东西。→ 现在对区间内每一帧都训练。
# 2. cv2.drawContours(mask, contours, ...) 画了"所有"轮廓（含面积<=阈值的噪点），
#    面积过滤形同虚设。→ 现在只绘制过滤后的单个多边形 [poly]。
# 3. 固定间隔用 `frame_count % frame_interval == 0` 判断，起始帧与间隔不对齐
#    （例如起始帧=5、间隔=30 时实际取 30/60/90...）。→ 现在从起始帧开始每隔 N 帧取。
# 4. output_image = medianFrame 是别名引用（依赖后续不再用 medianFrame 才不报错）。
#    → 现在显式 bg.copy()。
# 5. 帧号 0 起始 / 1 起始混用造成多处 off-by-one。→ 内部统一 0 起始，界面显示 1 起始。
# 6. 背景采样 np.random 不可复现，且可能采样到含物体的帧。→ 加入随机种子与采样数参数。
# 7. mask 用三通道 zeros_like(frame) 再判断 mask[:,:,0]==255，冗余。→ 单通道 mask。
# 8. 未处理 fps==0、帧数为 0、视频打不开等异常。→ 增加校验与错误提示。
# 9. try/except 包裹 waitKey 吞掉错误、无意义。→ 删除。
# 10. 预览固定 resize 到 960×540，会拉伸非 16:9 视频。→ 按比例缩放。
# ===========================================================================
