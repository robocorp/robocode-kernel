import logging
import time
from abc import ABC, abstractmethod
from ctypes import windll

try:
    windll.user32.SetProcessDPIAware()
except AttributeError:
    pass  # Might not be supported

import win32api
import win32con
import win32event
import win32gui
import win32ui


def lparam_to_point(lparam):
    """Parse coordinates from window message."""
    x = win32api.LOWORD(lparam)
    y = win32api.HIWORD(lparam)

    # Coordinates are signed, and can be negative
    # if on a second monitor
    if x & 32768:
        x |= -65536
    if y & 32768:
        y |= -65536

    return x, y


class SelectorToolBase(ABC):
    """Base class for selector tools.

    Displays a full-screen screenshot of the current desktop, and allows
    drawing on top of it and capturing the mouse position/clicks.
    """

    CURSOR = win32con.IDC_CROSS
    STOP_EVENT = win32event.CreateEvent(None, 0, 0, None)

    def __init__(self, timeout=30):
        self.logger = logging.getLogger(__name__)
        self.timeout = float(timeout)
        self._screenshot = None
        self._background = None
        self._window = None

    @property
    @abstractmethod
    def message_map(self):
        """Map of window message types to handler functions."""
        raise NotImplementedError

    @property
    @abstractmethod
    def result(self):
        """Result of the operation. Should be set before ``stop()`` is called."""
        raise NotImplementedError

    def run(self):
        """Create window and start handling messages.
        Blocks until ``stop()``` is called or timeout is reached.
        """
        self._create_window()
        try:
            timeout = time.time() + self.timeout
            while time.time() < timeout:
                if not self._pump_messages():
                    break
            else:
                self.logger.warning("Timeout reached")
        finally:
            self._remove_window()

        return self.result

    def stop(self):
        """Signal message loop to stop processing."""
        win32event.SetEvent(self.STOP_EVENT)

    def _pump_messages(self):
        """Handle messages from the Windows message loop.
        Returns `False` when process should stop.

        Notes:
            - WAIT_OBJECT_0 refers to the first item in given waitables
            - WAIT_OBJECT_0 + len(waitables) refers to a message defined by
              the mask, i.e. QS_ALLEVENTS
        """
        waitables = [self.STOP_EVENT]
        event = win32event.MsgWaitForMultipleObjects(
            waitables, False, 100, win32event.QS_ALLEVENTS,
        )

        if event == win32event.WAIT_OBJECT_0:
            return False
        if event == win32event.WAIT_OBJECT_0 + len(waitables):
            win32gui.PumpWaitingMessages()
            return True
        if event == win32event.WAIT_TIMEOUT:
            return True

        raise RuntimeError(f"Unexpected event: {event}")

    def _create_window(self):
        """Create full-screen window with screenshot as background."""
        # NB: For some reason a reference to the brush needs to be kept in Python,
        #     or otherwise there will only be a white background
        self._screenshot = self._take_screenshot()
        self._background = win32gui.CreatePatternBrush(self._screenshot.GetHandle())

        instance = win32api.GetModuleHandle()
        wndclass = win32gui.WNDCLASS()

        # Combine default message handlers with child class
        message_map = {win32con.WM_DESTROY: self._on_destroy}
        message_map.update(self.message_map)

        wndclass.style = win32con.CS_HREDRAW | win32con.CS_VREDRAW
        wndclass.lpfnWndProc = message_map
        wndclass.hInstance = instance
        wndclass.hbrBackground = self._background
        wndclass.hCursor = win32gui.LoadCursor(None, self.CURSOR)
        wndclass.lpszClassName = self.__class__.__name__

        wndclass_atom = win32gui.RegisterClass(wndclass)

        # WS_POPUP:         The window is a pop-up window
        # WS_VISIBLE:       The window is initially visible
        style = win32con.WS_POPUP | win32con.WS_VISIBLE

        # WS_EX_COMPOSITED: Paints all descendants in bottom-to-top order
        # WS_EX_LAYERED:    Painting is buffered to bitmap (faster)
        # WS_EX_TOOLWINDOW: Tool window, i.e. does not appear in the taskbar
        # WS_EX_TOPMOST:    Stay on top of all non-topmost windows
        ex_style = (
            win32con.WS_EX_COMPOSITED
            | win32con.WS_EX_LAYERED
            | win32con.WS_EX_TOOLWINDOW
            | win32con.WS_EX_TOPMOST
        )

        window = win32gui.CreateWindowEx(
            ex_style,  # extended window style
            wndclass_atom,  # class name
            None,  # window title
            style,  # window style
            0,  # x
            0,  # y
            win32api.GetSystemMetrics(win32con.SM_CXSCREEN),  # width
            win32api.GetSystemMetrics(win32con.SM_CYSCREEN),  # height
            None,  # parent
            None,  # menu
            instance,  # instance
            None,  # reserved
        )

        win32gui.ShowWindow(window, win32con.SW_SHOW)
        self._window = window

    def _remove_window(self):
        """Destroy window and free reserved resources."""
        if self._window:
            win32gui.DestroyWindow(self._window)
            win32gui.UnregisterClass(self.__class__.__name__, None)
            self._window = None

        if self._screenshot:
            win32gui.DeleteObject(self._screenshot.GetHandle())
            self._screenshot = None

    def _on_destroy(self, hwnd, message, wparam, lparam):
        """Default handler for WM_DESTROY."""
        win32gui.PostQuitMessage(0)
        return 0

    @staticmethod
    def _take_screenshot():
        """Bit-block copy current desktop pixel data into a bitmap."""
        src = win32gui.GetDesktopWindow()
        src_hdc = win32gui.GetWindowDC(src)
        src_dc = win32ui.CreateDCFromHandle(src_hdc)

        src_width = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
        src_height = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)

        dst_bitmap = win32ui.CreateBitmap()
        dst_bitmap.CreateCompatibleBitmap(src_dc, src_width, src_height)

        dst_dc = src_dc.CreateCompatibleDC()
        dst_default = dst_dc.SelectObject(dst_bitmap)
        assert dst_default is not None

        dst_dc.BitBlt((0, 0), (src_width, src_height), src_dc, (0, 0), win32con.SRCCOPY)

        # Default bitmap should be restored to DC after use,
        # otherwise some memory/performance issues might occur
        dst_dc.SelectObject(dst_default)

        dst_dc.DeleteDC()
        src_dc.DeleteDC()
        win32gui.ReleaseDC(src, src_hdc)

        return dst_bitmap


class ImageRegionTool(SelectorToolBase):
    """Draw a selection rectangle and return the coordinates.
    Optionally save the region as an image file.
    """

    REGION_WIDTH = 3
    REGION_COLOR = win32api.RGB(255, 0, 0)

    def __init__(self, filename=None, timeout=30):
        super().__init__(timeout)
        self.filename = filename
        self._selection_start = None
        self._selection = None

    @property
    def message_map(self):
        return {
            win32con.WM_LBUTTONDOWN: self.on_lbuttondown,
            win32con.WM_MOUSEMOVE: self.on_mousemove,
            win32con.WM_LBUTTONUP: self.on_lbuttonup,
            win32con.WM_KEYDOWN: self.on_keydown,
            win32con.WM_PAINT: self.on_paint,
        }

    @property
    def result(self):
        return self._selection

    def _save_selection(self):
        """Save selection region from the original screenshot."""
        assert self.filename is not None
        assert self._selection is not None

        left, top, right, bottom = self._selection
        width = right - left
        height = bottom - top

        src_dch = win32gui.CreateCompatibleDC(None)
        src_dc = win32ui.CreateDCFromHandle(src_dch)

        src_default = src_dc.SelectObject(self._screenshot)
        assert src_default is not None

        dst_dch = win32gui.CreateCompatibleDC(None)
        dst_dc = win32ui.CreateDCFromHandle(dst_dch)

        dst_bitmap = win32ui.CreateBitmap()
        dst_bitmap.CreateCompatibleBitmap(src_dc, width, height)

        dst_default = dst_dc.SelectObject(dst_bitmap)
        assert dst_default is not None

        dst_dc.BitBlt((0, 0), (width, height), src_dc, (left, top), win32con.SRCCOPY)

        dst_bitmap.SaveBitmapFile(dst_dc, self.filename)
        self.logger.info("Saved selected region as '%s'", self.filename)

        dst_dc.SelectObject(dst_default)
        src_dc.SelectObject(src_default)

        win32gui.DeleteObject(dst_bitmap.GetHandle())
        dst_dc.DeleteDC()
        src_dc.DeleteDC()

    def on_lbuttondown(self, hwnd, message, wparam, lparam):
        """Left button being pressed starts new selection."""
        self._selection_start = lparam_to_point(lparam)
        self._selection = None

        # Force re-draw
        win32gui.InvalidateRect(hwnd, None, True)
        return 0

    def on_mousemove(self, hwnd, message, wparam, lparam):
        """Update selection region with mouse location."""
        # Only update region while left button is pressed
        if not wparam & win32con.MK_LBUTTON:
            return 0

        x1, y1 = self._selection_start
        x2, y2 = lparam_to_point(lparam)

        # Handle drawing region from bottom-right to top-left
        self._selection = [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)]

        # Force re-draw
        win32gui.InvalidateRect(hwnd, None, True)
        return 0

    def on_lbuttonup(self, hwnd, message, wparam, lparam):
        """Selection is finished when left button is no longer pressed."""
        if self.filename and self._selection:
            self._save_selection()

        self.stop()
        return 0

    def on_keydown(self, hwnd, message, wparam, lparam):
        """Cancel selection with escape key."""
        if wparam == win32con.VK_ESCAPE:
            self._selection = None
            self.stop()
        return 0

    def on_paint(self, hwnd, message, wparam, lparam):
        """Draw current selection rectangle."""
        hdc, paint = win32gui.BeginPaint(hwnd)

        # Selection not started yet
        if self._selection is None:
            win32gui.EndPaint(hwnd, paint)
            return 0

        brush = win32gui.GetStockObject(win32con.NULL_BRUSH)
        pen = win32gui.CreatePen(
            win32con.PS_SOLID, self.REGION_WIDTH, self.REGION_COLOR
        )

        brush_default = win32gui.SelectObject(hdc, brush)
        pen_default = win32gui.SelectObject(hdc, pen)

        win32gui.Rectangle(hdc, *self._selection)

        win32gui.SelectObject(hdc, pen_default)
        win32gui.SelectObject(hdc, brush_default)

        win32gui.EndPaint(hwnd, paint)
        return 0


class PointPickTool(SelectorToolBase):
    """Pick a point on the screen and return the coordinates."""

    def __init__(self, timeout=30):
        super().__init__(timeout)
        self._point = None

    @property
    def message_map(self):
        return {win32con.WM_LBUTTONDOWN: self.on_lbuttondown}

    @property
    def result(self):
        return self._point

    def on_lbuttondown(self, hwnd, message, wparam, lparam):
        """Store clicked point."""
        self._point = lparam_to_point(lparam)
        self.stop()
        return 0
