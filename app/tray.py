"""Значок оверлея в трее Windows (возле часов) — вместо чёрного окна консоли.

Средствами самой Windows (Shell_NotifyIcon через ctypes), без лишних библиотек.
Правой (или левой) кнопкой по значку — меню; пункты вызывают функции программы.
Уведомления («вышла новая версия», «база обновлена») — всплывающим сообщением у значка.
Окно трея живёт в своём потоке со своим циклом сообщений; всё, что трогает окна tkinter,
программа выполняет у себя в главном потоке (сюда приходят только «нажали пункт N»).
"""
import ctypes
import logging
import threading
from ctypes import wintypes

log = logging.getLogger("wardogs")

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32
kernel32 = ctypes.windll.kernel32

WM_DESTROY, WM_COMMAND, WM_CLOSE, WM_USER = 0x0002, 0x0111, 0x0010, 0x0400
WM_TRAY = WM_USER + 20
WM_LBUTTONUP, WM_RBUTTONUP, WM_LBUTTONDBLCLK = 0x0202, 0x0205, 0x0203
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
NIIF_INFO = 0x1
MF_STRING, MF_SEPARATOR, MF_CHECKED, MF_GRAYED = 0x0, 0x800, 0x8, 0x1
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x2, 0x100, 0x80
IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x10, 0x40
IDI_APPLICATION = 32512

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
user32.RegisterClassW.argtypes = [ctypes.c_void_p]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
user32.DestroyMenu.argtypes = [wintypes.HMENU]
user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                              wintypes.UINT]
user32.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.c_void_p]
user32.LoadImageW.restype = wintypes.HANDLE
user32.LoadIconW.restype = wintypes.HICON
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  wintypes.HWND, ctypes.c_void_p]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                ("szTip", ctypes.c_wchar * 128), ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                ("szInfo", ctypes.c_wchar * 256), ("uVersion", wintypes.UINT), ("szInfoTitle", ctypes.c_wchar * 64),
                ("dwInfoFlags", wintypes.DWORD), ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wintypes.HICON)]


class Tray:
    """items() → [(текст, id, отмечен, доступен) | None (разделитель)]; on_command(id) — нажали пункт.
    double_click — id пункта, который срабатывает от двойного щелчка по значку (None — ничего)."""

    def __init__(self, icon_path, tip, items, on_command, double_click=None):
        self.icon_path, self.tip, self.items, self.on_command = icon_path, tip, items, on_command
        self.double_click = double_click
        self.hwnd = None
        self._nid = None
        self._ready = threading.Event()
        threading.Thread(target=self._run, daemon=True, name="трей").start()
        self._ready.wait(5)

    # --- снаружи (из любого потока)
    def notify(self, title, text):
        """Всплывающее сообщение у значка."""
        if not self._nid:
            return
        n = self._nid
        n.uFlags = NIF_INFO
        n.szInfoTitle, n.szInfo, n.dwInfoFlags = title[:63], text[:255], NIIF_INFO
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(n))

    def set_tip(self, tip):
        if not self._nid:
            return
        self._nid.uFlags = NIF_TIP
        self._nid.szTip = tip[:127]
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._nid))

    def close(self):
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)

    # --- поток трея
    def _icon(self):
        if self.icon_path:
            h = user32.LoadImageW(None, self.icon_path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
            if h:
                return h
            log.warning("Трей: не загрузился значок %s — беру стандартный", self.icon_path)
        return user32.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))

    def _menu(self):
        m = user32.CreatePopupMenu()
        for it in self.items():
            if it is None:
                user32.AppendMenuW(m, MF_SEPARATOR, 0, None)
                continue
            text, cid, checked, enabled = it
            flags = MF_STRING | (MF_CHECKED if checked else 0) | (0 if enabled else MF_GRAYED)
            user32.AppendMenuW(m, flags, cid, text)
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(self.hwnd)    # иначе меню не закрывается щелчком мимо
        cmd = user32.TrackPopupMenu(m, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY, pt.x, pt.y, 0, self.hwnd, None)
        user32.DestroyMenu(m)
        if cmd:
            try:
                self.on_command(cmd)
            except Exception:
                log.exception("Трей: ошибка в пункте меню %s", cmd)

    def _proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAY and lparam == WM_RBUTTONUP:       # правая кнопка — меню
            self._menu()
            return 0
        if msg == WM_TRAY and lparam == WM_LBUTTONDBLCLK and self.double_click:   # двойной щелчок — свой пункт
            log.info("Трей: двойной щелчок по значку")
            try:
                self.on_command(self.double_click)
            except Exception:
                log.exception("Трей: ошибка при двойном щелчке")
            return 0
        if msg == WM_CLOSE:
            if self._nid:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
                self._nid = None
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _run(self):
        try:
            hinst = kernel32.GetModuleHandleW(None)
            self._wndproc = WNDPROC(self._proc)        # держим ссылку, иначе сборщик мусора уберёт
            wc = WNDCLASSW()
            wc.lpfnWndProc = self._wndproc
            wc.hInstance = hinst
            wc.lpszClassName = "WardogsOverlayTray"
            user32.RegisterClassW(ctypes.byref(wc))
            self.hwnd = user32.CreateWindowExW(0, wc.lpszClassName, "Wardogs overlay tray", 0, 0, 0, 0, 0,
                                               None, None, hinst, None)
            n = NOTIFYICONDATAW()
            n.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            n.hWnd, n.uID = self.hwnd, 1
            n.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            n.uCallbackMessage = WM_TRAY
            n.hIcon = self._icon()
            n.szTip = self.tip[:127]
            if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(n)):
                log.error("Трей: Windows не дала добавить значок")
            self._nid = n
            log.info("Трей: значок добавлен")
        except Exception:
            log.exception("Трей: не получилось создать значок")
        finally:
            self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
