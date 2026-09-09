"""MCP 服务层：注册《项目方案.md》4.2 节完整工具表。

统一返回结构：{ok, message, data, duration_ms}（screenshot 例外，直接返回图片）。
所有工具经 safe_tool 转译异常；急停状态下拒绝执行新动作（emergency_stop/self_test 除外）。
"""

from __future__ import annotations

import datetime
import hashlib
import os
import threading
import time
from io import BytesIO

from fastmcp import FastMCP
from fastmcp.utilities.types import Image
from PIL import Image as PILImage

from core.action import (
    ActionInterrupted,
    mouse_drag as action_mouse_drag,
    mouse_long_press as action_mouse_long_press,
)
from core.analyze import analyze
from core.coords import (
    get_cursor_pos,
    screen_info as query_screen_info,
    set_dpi_awareness,
    virtual_screen,
)
from core.keyboard import hotkey, key_press, release_all, type_text
from core.mouse import (
    button_down,
    button_up,
    click,
    double_click,
    held_buttons,
    move_to,
    release_all_buttons,
    right_click,
    scroll,
)
from core.screen import screenshot_png
from safety import panic
from server import config as config_mod
from server.schemas import (
    make_result,
    safe_tool,
    validate_button,
    validate_key_name,
    validate_keys,
    validate_ms,
    validate_region,
    validate_scope,
    validate_text,
)
from server.log_setup import ensure_logging
from vision import vlm_adapter

# VLM 结果短时缓存：key=(region, structured, prompt_hash)，避免同一布局反复识别
# 条目带写入时间戳；过期条目定期清理，容量超限时淘汰最旧条目，避免无限增长。
_vlm_cache: dict[tuple, tuple[float, dict]] = {}
_vlm_cache_lock = threading.Lock()
_VLM_CACHE_MAX_ENTRIES = 128


def _vlm_cache_sweep(now: float, ttl: float) -> None:
    """清理过期/超限缓存条目（调用方需持有 _vlm_cache_lock）。"""
    if ttl <= 0:
        # 缓存已关闭：旧配置残留的条目一并清空
        _vlm_cache.clear()
        return
    expired = [key for key, (ts, _) in _vlm_cache.items() if now - ts > ttl]
    for key in expired:
        del _vlm_cache[key]
    overflow = len(_vlm_cache) - _VLM_CACHE_MAX_ENTRIES
    if overflow > 0:
        # 仍超限时按写入时间淘汰最旧条目
        oldest = sorted(_vlm_cache, key=lambda k: _vlm_cache[k][0])[:overflow]
        for key in oldest:
            del _vlm_cache[key]


def _interruptible_wait(ms: int) -> None:
    """可被急停中断的等待。"""
    deadline = time.perf_counter() + ms / 1000
    while True:
        if panic.interrupted():
            raise ActionInterrupted("等待被急停中断")
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return
        time.sleep(min(0.05, remaining))


def _save_screenshot(png: bytes) -> None:
    """按 config screen.save_screenshots=true 把截图落盘到 logs/screenshots/（best-effort）。"""
    try:
        shots_dir = config_mod.ROOT / "logs" / "screenshots"
        shots_dir.mkdir(parents=True, exist_ok=True)
        name = f"screenshot-{datetime.datetime.now():%Y%m%d-%H%M%S-%f}.png"
        (shots_dir / name).write_bytes(png)
    except Exception:
        pass


def _norm_text(s) -> str:
    """归一化文本用于匹配：去空白、转小写（中文不受影响）。"""
    return "".join(str(s or "").split()).lower()


def _rect_overlap(a, b) -> bool:
    if not a or not b or len(a) != 4 or len(b) != 4:
        return False
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    return ax1 < bx2 and ax2 > bx1 and ay1 < by2 and ay2 > by1


def _dedupe_matches(matches: list[dict]) -> list[dict]:
    """按 rect 去重（OCR / UIA / VLM 不同来源可能命中同一位置）。"""
    seen: list[tuple] = []
    result: list[dict] = []
    for m in matches:
        key = tuple(m.get("rect") or [])
        if key and key in seen:
            continue
        if key:
            seen.append(key)
        result.append(m)
    return result


def _vlm_config() -> dict:
    """返回 VLM 配置（api_key 支持环境变量 SILICONFLOW_API_KEY 覆盖，避免密钥入库）。"""
    cfg = dict(config_mod.get_config()["screen"]["vlm"])
    cfg["api_key"] = os.environ.get("SILICONFLOW_API_KEY") or cfg.get("api_key", "")
    return cfg


def _vlm_region_png(
    region: list[int] | None, max_dim: int = 1280
) -> tuple[bytes, tuple[int, int], tuple[int, int]]:
    """按区域裁剪截图并降采样（region 为空时返回全屏）。

    返回 (发送用 png, 原始图片尺寸, 区域左上角偏移)；坐标换算以原始尺寸为准，
    因此模型返回的 0~1000 归一化坐标会映射回原始屏幕像素。
    """
    from core import screen

    if region is None:
        png = screenshot_png()
        img = PILImage.open(BytesIO(png))
        orig_size = img.size
    else:
        img = screen.grab_region(tuple(region))
        orig_size = img.size
    if max(orig_size) > max_dim:
        scale = max_dim / max(orig_size)
        new_size = (max(1, round(orig_size[0] * scale)), max(1, round(orig_size[1] * scale)))
        img = img.resize(new_size, PILImage.LANCZOS)
    buf = BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue(), orig_size, (region[0], region[1]) if region else (0, 0)


def _vlm_describe(region: list[int] | None, prompt: str | None, structured: bool) -> dict:
    """调用外接 VLM；未配置时返回明确提示。"""
    vlm_cfg = _vlm_config()
    if not vlm_cfg["enabled"] or not vlm_adapter.is_configured(
        vlm_cfg["endpoint"], vlm_cfg["api_key"], vlm_cfg["model"]
    ):
        return {
            "ok": False,
            "message": "未配置 VLM：请在 config.json 的 screen.vlm 中配置 endpoint/api_key/model"
            "（screen.vlm.enabled 需为 true；api_key 也可用环境变量 SILICONFLOW_API_KEY 提供）"
            "（注意：启用后截图会发送到该端点）。",
        }
    png, img_size, offset = _vlm_region_png(region)
    common = dict(
        endpoint=vlm_cfg["endpoint"],
        api_key=vlm_cfg["api_key"],
        model=vlm_cfg["model"],
        timeout_s=vlm_cfg["timeout_s"],
        max_tokens=vlm_cfg["max_tokens"],
    )
    if structured:
        return vlm_adapter.locate_objects(
            image_png=png,
            image_size=img_size,
            offset=offset,
            prompt=prompt,
            **common,
        )
    return vlm_adapter.describe(image_png=png, prompt=prompt, **common)


def _annotate_vlm_result(result: dict, structured: bool) -> dict:
    """给 VLM 结果标注坐标系，避免相对坐标被误用于鼠标工具。"""
    if not result.get("ok"):
        return result
    if structured:
        result["coords"] = {
            "space": "absolute",
            "unit": "physical_pixels",
            "source": "structured_vlm",
            "note": "objects.box 为虚拟桌面物理像素坐标，可直接用于鼠标工具",
        }
    else:
        result["coords"] = {
            "space": "region_relative",
            "unit": "region_pixels",
            "source": "describe",
            "note": "描述文本中的坐标仅为所请求区域的相对值，不可直接用于鼠标工具；需要坐标请用 structured=true",
        }
    return result


def _vlm_cached(region: list[int] | None, prompt: str | None, structured: bool) -> dict:
    """带短时缓存的 VLM 调用（仅缓存成功结果；TTL 见 screen.vlm.cache_ttl_s）。"""
    vlm_cfg = _vlm_config()
    ttl = int(vlm_cfg.get("cache_ttl_s", 5) or 0)
    prompt_hash = hashlib.sha1((prompt or "").encode("utf-8")).hexdigest()
    key = (tuple(region) if region else None, structured, prompt_hash)
    now = time.monotonic()
    with _vlm_cache_lock:
        _vlm_cache_sweep(now, ttl)
        hit = _vlm_cache.get(key) if ttl > 0 else None
        if hit and now - hit[0] <= ttl:
            return hit[1]
    result = _annotate_vlm_result(_vlm_describe(region, prompt, structured=structured), structured)
    if result.get("ok") and ttl > 0:
        with _vlm_cache_lock:
            # 缓存时间以完成时刻为准，避免长请求期间 TTL 被耗尽
            now = time.monotonic()
            _vlm_cache[key] = (now, result)
            _vlm_cache_sweep(now, ttl)
    return result


def create_server() -> FastMCP:
    """创建并注册完整工具表的 FastMCP 服务。"""
    set_dpi_awareness()
    mcp = FastMCP("computer-controller", version="0.3.0")
    cfg = config_mod.get_config()
    ensure_logging(level=cfg.get("log", {}).get("level", "info"))
    input_cfg = cfg["input"]
    screen_cfg = cfg["screen"]

    @mcp.tool()
    @safe_tool
    def cursor_position() -> dict:
        """查询当前鼠标位置（物理像素坐标）。"""
        t0 = time.perf_counter()
        x, y = get_cursor_pos()
        return make_result(True, "查询成功", {"x": x, "y": y}, t0)

    @mcp.tool()
    @safe_tool
    def mouse_move(x: int, y: int, duration_ms: int = 0) -> dict:
        """移动鼠标到指定坐标。x/y 为物理像素（虚拟桌面坐标系）；duration_ms>0 时平滑移动。"""
        t0 = time.perf_counter()
        validate_ms("duration_ms", duration_ms, 0, 60000)
        move_to(x, y, duration_ms=duration_ms, move_step_ms=input_cfg["move_step_ms"])
        return make_result(True, "移动完成", {"x": x, "y": y}, t0)

    @mcp.tool()
    @safe_tool
    def mouse_click(
        x: int | None = None,
        y: int | None = None,
        button: str = "left",
        times: int = 1,
    ) -> dict:
        """单击/双击。button 可选 left/right/middle；times 可选 1（单击）或 2（双击）；不传坐标则在当前位置点击。"""
        t0 = time.perf_counter()
        validate_button(button)
        validate_ms("times", times, 1, 3)
        click(
            x,
            y,
            button=button,
            times=times,
            click_interval_ms=input_cfg["click_interval_ms"],
        )
        return make_result(True, "点击完成", {"button": button, "times": times}, t0)

    @mcp.tool()
    @safe_tool
    def mouse_double_click(x: int | None = None, y: int | None = None) -> dict:
        """双击左键。x/y 可选。"""
        t0 = time.perf_counter()
        double_click(x, y)
        return make_result(True, "双击完成", {"x": x, "y": y}, t0)

    @mcp.tool()
    @safe_tool
    def mouse_right_click(x: int | None = None, y: int | None = None) -> dict:
        """右键单击。x/y 可选。"""
        t0 = time.perf_counter()
        right_click(x, y)
        return make_result(True, "右键完成", {"x": x, "y": y}, t0)

    @mcp.tool()
    @safe_tool
    def mouse_down(button: str = "left", x: int | None = None, y: int | None = None) -> dict:
        """按住鼠标按钮（不自动松开），配合 mouse_up 使用；急停会统一释放。"""
        t0 = time.perf_counter()
        validate_button(button)
        button_down(button, x=x, y=y)
        return make_result(True, "已按住", {"button": button, "held": sorted(held_buttons())}, t0)

    @mcp.tool()
    @safe_tool
    def mouse_up(button: str = "left") -> dict:
        """松开鼠标按钮。"""
        t0 = time.perf_counter()
        validate_button(button)
        button_up(button)
        return make_result(True, "已松开", {"button": button, "held": sorted(held_buttons())}, t0)

    @mcp.tool()
    @safe_tool
    def mouse_scroll(delta: int, x: int | None = None, y: int | None = None) -> dict:
        """滚动鼠标滚轮。delta 正数向上、负数向下（120 为一格）；x/y 可选，同时给出时先移动到该位置再滚动。"""
        t0 = time.perf_counter()
        validate_ms("delta", delta, -20000, 20000)
        scroll(delta, x=x, y=y)
        return make_result(True, "滚动完成", {"delta": delta, "x": x, "y": y}, t0)

    @mcp.tool()
    @safe_tool
    def mouse_long_press(
        x: int | None = None,
        y: int | None = None,
        button: str = "left",
        hold_ms: int = 500,
    ) -> dict:
        """长按鼠标：移动到目标（可选）→ 按下 → 保持指定时长 → 松开。可被 emergency_stop 中断。"""
        t0 = time.perf_counter()
        validate_button(button)
        validate_ms("hold_ms", hold_ms, 1, 60000)
        data = action_mouse_long_press(
            x,
            y,
            button=button,
            hold_ms=hold_ms,
            timeout_s=input_cfg["max_action_duration_s"],
        )
        return make_result(True, "长按完成", data, t0)

    @mcp.tool()
    @safe_tool
    def mouse_drag(
        from_x: int,
        from_y: int,
        to_x: int,
        to_y: int,
        button: str = "left",
        duration_ms: int = 500,
        hold_before_ms: int = 0,
    ) -> dict:
        """拖拽：从起点按住鼠标（可先停留 hold_before_ms），插值移动到终点后松开。可被 emergency_stop 中断。"""
        t0 = time.perf_counter()
        validate_button(button)
        validate_ms("duration_ms", duration_ms, 0, 60000)
        validate_ms("hold_before_ms", hold_before_ms, 0, 60000)
        data = action_mouse_drag(
            from_x,
            from_y,
            to_x,
            to_y,
            button=button,
            duration_ms=duration_ms,
            hold_before_ms=hold_before_ms,
            move_step_ms=input_cfg["move_step_ms"],
            timeout_s=input_cfg["max_action_duration_s"],
        )
        return make_result(True, "拖拽完成", data, t0)

    @mcp.tool()
    @safe_tool
    def keyboard_type(text: str, interval_ms: int = 0, use_clipboard: bool = False) -> dict:
        """向当前焦点窗口输入文本（支持中文与 emoji）。interval_ms 为每字符间隔；大段文本可用 use_clipboard=true 加速。"""
        t0 = time.perf_counter()
        validate_text(text)
        validate_ms("interval_ms", interval_ms, 0, 5000)
        type_text(text, interval_ms=interval_ms, use_clipboard=use_clipboard)
        return make_result(True, "输入完成", {"chars": len(text), "use_clipboard": use_clipboard}, t0)

    @mcp.tool()
    @safe_tool
    def keyboard_hotkey(keys: list[str], hold_ms: int = 0) -> dict:
        """按下组合快捷键（如 ["ctrl","c"]）。按键按顺序按下、逆序松开。"""
        t0 = time.perf_counter()
        validate_keys(keys)
        validate_ms("hold_ms", hold_ms, 0, 60000)
        hotkey(keys, hold_ms=hold_ms)
        return make_result(True, "快捷键完成", {"keys": keys, "hold_ms": hold_ms}, t0)

    @mcp.tool()
    @safe_tool
    def keyboard_key(key: str, hold_ms: int = 0) -> dict:
        """点按单个按键（如 enter/tab/esc）；hold_ms>0 时为长按。"""
        t0 = time.perf_counter()
        validate_key_name(key)
        validate_ms("hold_ms", hold_ms, 0, 60000)
        key_press(key, hold_ms=hold_ms)
        return make_result(True, "按键完成", {"key": key, "hold_ms": hold_ms}, t0)

    @mcp.tool()
    @safe_tool
    def wait(ms: int) -> dict:
        """等待指定毫秒数（可被 emergency_stop 中断）。"""
        t0 = time.perf_counter()
        validate_ms("ms", ms, 0, 600000)
        _interruptible_wait(ms)
        return make_result(True, "等待完成", {"ms": ms}, t0)

    @mcp.tool()
    @safe_tool(allow_during_panic=True)
    def emergency_stop() -> dict:
        """急停：中止一切动作、释放所有按键与鼠标按钮、鼠标回到屏幕中心；此后拒绝新指令直到复位。"""
        t0 = time.perf_counter()
        panic.request_interrupt()
        released_keys = release_all()
        released_buttons = release_all_buttons()
        vx, vy, vw, vh = virtual_screen()
        move_to(vx + vw // 2, vy + vh // 2, duration_ms=200)
        return make_result(
            True,
            "已急停（此后拒绝新指令，需复位后继续）",
            {"released_keys": released_keys, "released_buttons": released_buttons},
            t0,
        )

    @mcp.tool()
    @safe_tool(allow_during_panic=True)
    def self_test(dry_run: bool = True) -> dict:
        """自检：dry_run=true 只检查引擎可用性，不真实操作；dry_run=false 额外执行少量安全真实动作。"""
        t0 = time.perf_counter()
        from core import ocr, screen, uia

        info = query_screen_info()
        try:
            screen.grab()
            screenshot_ok = True
        except Exception:
            screenshot_ok = False
        report = {
            "screen": info,
            "screenshot": screenshot_ok,
            "uia": uia.available(),
            "uia_reason": uia.last_error(),
            "ocr": ocr.available(),
            "ocr_reason": ocr.last_error(),
            "keyboard_mapping": "ok",
            "panic_active": panic.interrupted(),
            "panic_hotkey": panic.hotkey_ok(),
        }
        if not dry_run:
            before = get_cursor_pos()
            vx, vy, vw, vh = virtual_screen()
            move_to(vx + vw // 2, vy + vh // 2, duration_ms=200)
            moved_ok = get_cursor_pos() == (vx + vw // 2, vy + vh // 2)
            move_to(*before, duration_ms=200)
            report["real_actions"] = {"mouse_move_center": moved_ok}
        ok = bool(report["screenshot"]) and bool(report["uia"]) and bool(report["ocr"])
        return make_result(ok, "自检完成", report, t0)

    @mcp.tool()
    @safe_tool
    def screenshot() -> Image:
        """截取全屏并返回 PNG 图片（与鼠标使用同一物理像素坐标系）。"""
        png = screenshot_png()
        if screen_cfg["save_screenshots"]:
            _save_screenshot(png)
        return Image(data=png, format="png")

    @mcp.tool()
    @safe_tool
    def screen_info() -> dict:
        """返回屏幕布局信息（虚拟桌面范围、主屏分辨率、DPI 缩放）。"""
        t0 = time.perf_counter()
        return make_result(True, "查询成功", query_screen_info(), t0)

    @mcp.tool()
    @safe_tool
    def screen_analyze(
        scope: str = "full",
        with_uia: bool | None = None,
        with_ocr: bool | None = None,
        with_vlm: bool | None = None,
    ) -> dict:
        """结构化屏幕理解：返回窗口列表、可交互元素、文本（均含物理像素坐标），可直接用于鼠标工具。scope 可选 full/window；with_vlm=true 时额外调用外接 VLM 补充整体描述（默认不附带，避免慢速外部请求）。"""
        t0 = time.perf_counter()
        validate_scope(scope)
        uia_enabled = screen_cfg["uia"]["enabled"] if with_uia is None else with_uia
        ocr_enabled = screen_cfg["ocr"]["enabled"] if with_ocr is None else with_ocr
        data = analyze(scope=scope, with_uia=uia_enabled, with_ocr=ocr_enabled)
        # 优化：默认不自动触发外部 VLM（截图外发 + 8~30s 延迟）；需要时显式 with_vlm=true
        vlm_enabled = False if with_vlm is None else with_vlm
        if vlm_enabled:
            if scope == "window":
                active = next((w for w in data.get("windows", []) if w.get("active")), None)
                region = active["rect"] if active else None
            else:
                region = None
            data["vlm"] = _vlm_cached(region, None, structured=False)
        return make_result(True, "屏幕理解完成", data, t0)

    @mcp.tool()
    @safe_tool
    def vlm_describe(
        region: list[int] | None = None,
        prompt: str | None = None,
        structured: bool = False,
    ) -> dict:
        """调用外接 VLM 描述屏幕/区域（默认关闭）。region 为 [x1,y1,x2,y2]；structured=true 时做结构化目标/图标识别并返回带坐标的 objects 列表（坐标为虚拟桌面物理像素，可直接用于鼠标工具）。未配置时返回明确提示。"""
        t0 = time.perf_counter()
        if region is not None:
            validate_region(region)
        result = _vlm_cached(region, prompt, structured=structured)
        message = result.get("message")
        if message is None:
            message = "VLM 结构化识别完成" if structured else "VLM 描述完成"
        return make_result(result.get("ok", False), message, result, t0)

    @mcp.tool()
    @safe_tool
    def find_text(
        text: str,
        region: list[int] | None = None,
        with_vlm: bool = True,
    ) -> dict:
        """按文本定位屏幕上的元素：依次走本地 OCR → UIA 名称 → 外接 VLM 兜底，返回匹配位置（物理像素坐标，可直接用于鼠标工具）。region 可选限定搜索范围；with_vlm=false 时不调用外部模型。"""
        t0 = time.perf_counter()
        validate_text(text)
        if region is not None:
            validate_region(region)
        from core import ocr, screen, uia, windows

        matches: list[dict] = []
        sources: list[str] = []
        target = _norm_text(text)

        # 第 1 层：本地 OCR（快、免费、支持中文）
        if region is not None:
            img = screen.grab_region(tuple(region))
            ox, oy = region[0], region[1]
        else:
            vx, vy, _vw, _vh = virtual_screen()
            img = screen.grab()
            ox, oy = vx, vy
        ocr_res = ocr.ocr_image(img, offset_x=ox, offset_y=oy)
        if ocr_res.get("ok"):
            for item in ocr_res.get("texts", []):
                if target and target in _norm_text(item.get("text", "")):
                    matches.append(
                        {
                            "text": item.get("text"),
                            "rect": item.get("rect"),
                            "score": item.get("score"),
                            "source": "ocr",
                        }
                    )
            if matches:
                sources.append("ocr")

        # 第 2 层：UIA 元素名称（仅当 OCR 未命中时）
        if not matches:
            wins = windows.list_windows(with_title_only=True, visible_only=True)
            for w in wins:
                if region is not None and not _rect_overlap(w.get("rect"), region):
                    continue
                uia_res = uia.get_elements(scope_hwnd=w.get("hwnd"), max_elements=200)
                for e in uia_res.get("elements", []):
                    if target and target in _norm_text(e.get("name", "")):
                        matches.append(
                            {
                                "text": e.get("name"),
                                "rect": e.get("rect"),
                                "type": e.get("type"),
                                "source": "uia",
                            }
                        )
            if matches:
                sources.append("uia")

        # 第 3 层：外接 VLM 兜底（图标/自绘控件等本地识别不到时）
        if not matches and with_vlm:
            prompt = (
                f"请定位截图中的文本『{text}』，把它的完整外接矩形作为 box，"
                f'name 设为"{text}"。只输出该文本，不要其他元素。'
            )
            vlm_res = _vlm_cached(region, prompt, structured=True)
            if vlm_res.get("ok"):
                for obj in vlm_res.get("objects", []):
                    matches.append({"text": obj.get("name"), "rect": obj.get("box"), "source": "vlm"})
                if matches:
                    sources.append("vlm")

        matches = _dedupe_matches(matches)
        message = f"找到 {len(matches)} 处匹配" if matches else "未找到匹配文本"
        return make_result(
            True,
            message,
            {"text": text, "region": region, "sources": sources, "matches": matches},
            t0,
        )

    return mcp
