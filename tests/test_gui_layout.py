import subprocess
import sys
import textwrap

import pytest

tk = pytest.importorskip("tkinter")

import grok_register_ttk as app


def test_module_imports_when_tkinter_is_unavailable():
    script = textwrap.dedent(
        """
        import builtins

        original_import = builtins.__import__

        def blocked_import(name, *args, **kwargs):
            if name == "tkinter" or name.startswith("tkinter."):
                raise ImportError("tkinter intentionally blocked")
            return original_import(name, *args, **kwargs)

        builtins.__import__ = blocked_import
        import grok_register_ttk as app
        assert app.TK_AVAILABLE is False
        assert app.tk is None
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=".",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_calculate_gui_window_size_respects_large_and_small_screens():
    width, height, min_width, min_height = app.calculate_gui_window_size(1920, 1080)
    assert (width, height) == (1120, 900)
    assert min_width <= width
    assert min_height <= height

    width, height, min_width, min_height = app.calculate_gui_window_size(1366, 768)
    assert width <= 1366
    assert height < 900
    assert min_width <= width
    assert min_height <= height

    width, height, min_width, min_height = app.calculate_gui_window_size(800, 600)
    assert width <= 800
    assert height <= 600
    assert min_width <= width
    assert min_height <= height


@pytest.fixture
def root():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    app.setup_light_theme(root)
    yield root
    try:
        root.update_idletasks()
    except tk.TclError:
        pass
    root.destroy()


@pytest.mark.parametrize("scaling", [1.0, 1.25, 1.5, 2.0])
def test_small_window_keeps_actions_status_and_log_visible(root, scaling):
    root.tk.call("tk", "scaling", scaling)
    gui = app.GrokRegisterGUI(root)
    root.geometry("960x700")
    root.update_idletasks()

    root_top = root.winfo_rooty()
    root_bottom = root_top + root.winfo_height()

    for widget in (
        gui.start_btn,
        gui.stop_btn,
        gui.status_label,
        gui.log_text,
    ):
        assert widget.winfo_ismapped()
        widget_bottom = widget.winfo_rooty() + widget.winfo_height()
        assert widget_bottom <= root_bottom + 2

    assert gui.log_text.winfo_height() >= 40


@pytest.mark.parametrize("scaling", [1.0, 1.5, 2.0])
def test_configuration_area_scrolls_and_reaches_outlook_controls(root, scaling):
    root.tk.call("tk", "scaling", scaling)
    gui = app.GrokRegisterGUI(root)
    root.geometry("960x700")
    root.update_idletasks()

    canvas = gui.config_canvas
    bbox = canvas.bbox("all")
    assert bbox is not None
    assert bbox[3] - bbox[1] > canvas.winfo_height()
    assert gui.config_scrollbar.winfo_ismapped()

    canvas.yview_moveto(1.0)
    root.update_idletasks()

    canvas_top = canvas.winfo_rooty()
    canvas_bottom = canvas_top + canvas.winfo_height()
    outlook_top = gui.outlook_pool_btn.winfo_rooty()
    outlook_bottom = outlook_top + gui.outlook_pool_btn.winfo_height()
    assert outlook_top >= canvas_top - 2
    assert outlook_bottom <= canvas_bottom + 2


def test_mousewheel_handler_does_not_scroll_when_pointer_is_outside(root, monkeypatch):
    gui = app.GrokRegisterGUI(root)
    root.geometry("960x700")
    root.update_idletasks()

    scroll = gui.config_scroll
    before = gui.config_canvas.yview()
    monkeypatch.setattr(scroll, "_pointer_is_inside", lambda: False)

    class Event:
        delta = -120
        num = None

    assert scroll._on_mousewheel(Event()) is None
    assert gui.config_canvas.yview() == before


def test_mousewheel_handler_scrolls_configuration_when_pointer_is_inside(root, monkeypatch):
    gui = app.GrokRegisterGUI(root)
    root.geometry("960x700")
    root.update_idletasks()

    scroll = gui.config_scroll
    gui.config_canvas.yview_moveto(0.0)
    before = gui.config_canvas.yview()
    monkeypatch.setattr(scroll, "_pointer_is_inside", lambda: True)

    class Event:
        delta = -120
        num = None

    assert scroll._on_mousewheel(Event()) == "break"
    root.update_idletasks()
    after = gui.config_canvas.yview()
    assert after[0] > before[0]
