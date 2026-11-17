"""COM side (Windows only): talks IRtdServer the way Excel does.

The two interfaces below are transcribed from Excel's type library so that
Excel itself does not have to be installed to load an in-process RTD
server.  (Out-of-process servers still need the interfaces to be registered
for marshalling, which installing Excel does.)

    IRtdServer       {EC0E6191-DB51-11D3-8F3E-00C04F3651B8}
    IRTDUpdateEvent  {A43788C1-D91B-11D3-8F39-00C04F3651B8}

Everything here must run on one thread: a single-threaded apartment whose
message loop is pumped by ``pump()``.  Call ``enter_apartment()`` on that
thread first and ``leave_apartment()`` when done.
"""

from __future__ import annotations

import ctypes
import logging
import struct
import sys
from ctypes import HRESULT, POINTER, byref, c_long, wintypes

if sys.platform != "win32":  # pragma: no cover
    raise ImportError("COM is only available on Windows")

import comtypes  # noqa: E402  (initialises COM as STA on this thread)
import comtypes.client  # noqa: E402
from comtypes import COMMETHOD, GUID, STDMETHOD, COMError, COMObject, dispid  # noqa: E402
from comtypes.automation import IDispatch, VARIANT, VARIANT_BOOL  # noqa: E402
from comtypes.safearray import _midlSAFEARRAY  # noqa: E402

from .host import pairs_from_refresh  # noqa: E402

log = logging.getLogger("calc_rtd.com")

S_OK = 0
DISP_E_MEMBERNOTFOUND = -2147352573
DISP_E_UNKNOWNNAME = -2147352570
REGDB_E_CLASSNOTREG = -2147221164
CO_E_CLASSSTRING = -2147221005
VT_ERROR = 10

_VariantArray = _midlSAFEARRAY(VARIANT)


def enter_apartment():
    """Make the calling thread a COM single-threaded apartment."""
    comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)


def leave_apartment():
    comtypes.CoUninitialize()


class IRTDUpdateEvent(IDispatch):
    _iid_ = GUID("{A43788C1-D91B-11D3-8F39-00C04F3651B8}")
    _idlflags_ = ["dual", "oleautomation"]
    _methods_ = [
        COMMETHOD([dispid(10)], HRESULT, "UpdateNotify"),
        COMMETHOD([dispid(11), "propget"], HRESULT, "HeartbeatInterval",
                  (["out", "retval"], POINTER(c_long), "plRetVal")),
        COMMETHOD([dispid(11), "propput"], HRESULT, "HeartbeatInterval",
                  (["in"], c_long, "plRetVal")),
        COMMETHOD([dispid(12)], HRESULT, "Disconnect"),
    ]


class IRtdServer(IDispatch):
    _iid_ = GUID("{EC0E6191-DB51-11D3-8F3E-00C04F3651B8}")
    _idlflags_ = ["dual", "oleautomation"]
    # Raw vtable signatures: every out parameter is passed explicitly.
    _methods_ = [
        STDMETHOD(HRESULT, "ServerStart", [POINTER(IRTDUpdateEvent), POINTER(c_long)]),
        STDMETHOD(HRESULT, "ConnectData",
                  [c_long, POINTER(_VariantArray), POINTER(VARIANT_BOOL), POINTER(VARIANT)]),
        STDMETHOD(HRESULT, "RefreshData", [POINTER(c_long), POINTER(_VariantArray)]),
        STDMETHOD(HRESULT, "DisconnectData", [c_long]),
        STDMETHOD(HRESULT, "Heartbeat", [POINTER(c_long)]),
        STDMETHOD(HRESULT, "ServerTerminate", []),
    ]


class UpdateEvent(COMObject):
    """The callback object Excel hands to ServerStart."""

    _com_interfaces_ = [IRTDUpdateEvent]
    _names = {"updatenotify": 10, "heartbeatinterval": 11, "disconnect": 12}

    def __init__(self):
        super().__init__()
        self.notified = False
        self.disconnected = False
        self.heartbeat_ms = 15000

    # vtable (early-bound) entry points
    def UpdateNotify(self):
        self.notified = True

    def _get_HeartbeatInterval(self):
        return self.heartbeat_ms

    def _set_HeartbeatInterval(self, value):
        self.heartbeat_ms = int(value)

    def Disconnect(self):
        self.disconnected = True

    # IDispatch (late-bound) entry points; comtypes cannot derive these
    # without a registered type library, so they are spelled out.
    def IDispatch_GetIDsOfNames(self, this, riid, rgszNames, cNames, lcid, rgDispId):
        for index in range(cNames):
            found = self._names.get((rgszNames[index] or "").lower())
            if found is None:
                return DISP_E_UNKNOWNNAME
            rgDispId[index] = found
        return S_OK

    def IDispatch_Invoke(self, this, dispIdMember, riid, lcid, wFlags,
                         pDispParams, pVarResult, pExcepInfo, puArgErr):
        if dispIdMember == 10:
            self.notified = True
        elif dispIdMember == 12:
            self.disconnected = True
        elif dispIdMember == 11:
            if wFlags & (4 | 8):
                self.heartbeat_ms = int(pDispParams[0].rgvarg[0].value)
            elif pVarResult:
                pVarResult[0].value = self.heartbeat_ms
        else:
            return DISP_E_MEMBERNOTFOUND
        return S_OK


# -- retiring the callback object --------------------------------------------
#
# After ServerTerminate a server should forget the callback object, but
# garbage-collected servers (.NET ones, i.e. most of them) keep their reference
# until their runtime shuts down - which happens at process exit, after the
# Python interpreter is gone.  A Release() arriving then would run Python code
# in a dead interpreter and crash the process.  Excel does not have the problem
# because its callback object is native code.
#
# So when a server is terminated, the callback object is "retired": the memory
# the server still points to is kept alive forever, and its vtable pointer is
# switched to a table of tiny native functions that just return.  Late calls
# become harmless no-ops that never enter Python.

_E_NOINTERFACE = 0x80004002
_E_NOTIMPL = 0x80004001
_E_FAIL = 0x80004005
_DISP_E_UNKNOWNNAME = 0x80020006

# (return value, number of arguments including `this`) per vtable slot of
# IRTDUpdateEvent: IUnknown, IDispatch, then the interface's own methods.
_RETIRED_SLOTS = (
    (_E_NOINTERFACE, 3),       # QueryInterface
    (1, 1),                    # AddRef
    (1, 1),                    # Release
    (_E_NOTIMPL, 2),           # GetTypeInfoCount
    (_E_NOTIMPL, 4),           # GetTypeInfo
    (_DISP_E_UNKNOWNNAME, 6),  # GetIDsOfNames
    (S_OK, 9),                 # Invoke
    (S_OK, 1),                 # UpdateNotify
    (_E_FAIL, 2),              # get_HeartbeatInterval
    (S_OK, 2),                 # put_HeartbeatInterval
    (S_OK, 1),                 # Disconnect
)

_retired_vtable = None  # address of the native table; False if unsupported here


def _build_retired_vtable():
    """Allocate the native no-op vtable once per process. Returns its address or False."""
    import platform

    pointer_size = ctypes.sizeof(ctypes.c_void_p)
    if platform.machine().upper() not in ("AMD64", "X86_64", "X86", "I386", "I686"):
        return False  # e.g. ARM64: no stub code for it
    stubs = []
    for value, argc in _RETIRED_SLOTS:
        if pointer_size == 8:
            code = b"\xB8" + struct.pack("<I", value) + b"\xC3"                  # mov eax, v ; ret
        else:
            code = b"\xB8" + struct.pack("<I", value) + b"\xC2" + struct.pack("<H", 4 * argc)  # ret n
        stubs.append(code.ljust(8, b"\xCC"))
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.VirtualAlloc.restype = ctypes.c_void_p
    kernel32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
    kernel32.VirtualProtect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD,
                                        POINTER(wintypes.DWORD)]
    size = 4096
    base = kernel32.VirtualAlloc(None, size, 0x3000, 0x04)  # commit+reserve, read/write
    if not base:
        return False
    ctypes.memmove(base, b"".join(stubs), 8 * len(stubs))
    table = base + 2048
    packed = struct.pack("<%d%s" % (len(stubs), "Q" if pointer_size == 8 else "I"),
                         *[base + 8 * index for index in range(len(stubs))])
    ctypes.memmove(table, packed, len(packed))
    old = wintypes.DWORD(0)
    if not kernel32.VirtualProtect(base, size, 0x20, byref(old)):  # execute/read; never freed
        return False
    return table


def retire(com_pointers):
    """Make a comtypes COMObject safe to be called after the interpreter is gone.

    ``com_pointers`` are the values of its ``_com_pointers_`` (taken while it
    was alive). Returns True if the object was retired.
    """
    global _retired_vtable
    if _retired_vtable is None:
        try:
            _retired_vtable = _build_retired_vtable()
        except Exception:
            log.debug("could not build the retired vtable", exc_info=True)
            _retired_vtable = False
    if not _retired_vtable:
        return False
    for pointer_to_this in com_pointers:
        # The server still holds `this`; that memory must outlive the interpreter.
        ctypes.pythonapi.Py_IncRef(ctypes.py_object(pointer_to_this))
        this = ctypes.addressof(pointer_to_this.contents)
        ctypes.c_void_p.from_address(this).value = _retired_vtable
    return True


def _plain(variant):
    return None if variant.vt == VT_ERROR else variant.value


class ComDriver:
    """One live RTD server instance (see host.py for the contract)."""

    def __init__(self, progid, server=""):
        self.progid = progid
        self._event = UpdateEvent()
        self._event_pointers = list(self._event._com_pointers_.values())
        try:
            self._server = comtypes.client.CreateObject(
                progid, machine=server or None, interface=IRtdServer)
        except (COMError, OSError) as exc:
            raise RuntimeError(_explain(progid, exc)) from exc

    # the server flips these through the callback object
    @property
    def notified(self):
        return self._event.notified

    @notified.setter
    def notified(self, value):
        self._event.notified = value

    @property
    def heartbeat_interval(self):
        return self._event.heartbeat_ms / 1000.0

    def start(self):
        result = c_long(0)
        self._server.ServerStart(self._event.QueryInterface(IRTDUpdateEvent), byref(result))
        return result.value

    def connect_data(self, topic_id, strings):
        array = _VariantArray.create([str(item) for item in strings])
        array._needsfree = True
        get_new_values = VARIANT_BOOL(True)
        out = VARIANT()
        # comtypes takes the address of a SAFEARRAY pointer itself (no byref here).
        self._server.ConnectData(topic_id, array, byref(get_new_values), byref(out))
        return _plain(out)

    def refresh_data(self):
        count = c_long(0)
        array = _VariantArray()
        self._server.RefreshData(byref(count), array)
        if not array:
            return []
        array._needsfree = True
        return pairs_from_refresh(array.unpack(), count.value)

    def disconnect_data(self, topic_id):
        self._server.DisconnectData(topic_id)

    def heartbeat(self):
        result = c_long(0)
        self._server.Heartbeat(byref(result))
        return result.value

    def terminate(self):
        server, self._server = self._server, None
        try:
            if server is not None:
                server.ServerTerminate()
        finally:
            del server
            pointers, self._event_pointers = self._event_pointers, []
            if pointers and self._event._refcnt.value > 0:
                # The server kept a reference to the callback object (see retire()).
                retire(pointers)


def _is_elevated():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _explain(progid, exc):
    code = getattr(exc, "winerror", None)
    if code is None:
        code = getattr(exc, "hresult", None)
    if code is None and exc.args:
        code = exc.args[0]
    bits = 64 if sys.maxsize > 2 ** 32 else 32
    if code in (REGDB_E_CLASSNOTREG, CO_E_CLASSSTRING):
        hint = ("RTD server %r (or a component it depends on) is not registered for this process. "
                "Check the ProgID, and that this %d-bit Python matches the bitness the server "
                "was installed for." % (progid, bits))
        if _is_elevated():
            hint += (" This bridge is running as administrator: start it WITHOUT elevation, "
                     "at the same level as the application that feeds the RTD server.")
        return hint
    return "could not create RTD server %r: %s" % (progid, exc)


# -- message pump ---------------------------------------------------------
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.MsgWaitForMultipleObjects.argtypes = [
    wintypes.DWORD, ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD, wintypes.DWORD]
_user32.MsgWaitForMultipleObjects.restype = wintypes.DWORD
_user32.PeekMessageW.argtypes = [
    POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
_user32.PeekMessageW.restype = wintypes.BOOL
_user32.TranslateMessage.argtypes = [POINTER(wintypes.MSG)]
_user32.DispatchMessageW.argtypes = [POINTER(wintypes.MSG)]
_user32.DispatchMessageW.restype = ctypes.c_ssize_t

_QS_ALLINPUT = 0x04FF
_PM_REMOVE = 0x0001


def pump(timeout):
    """Wait up to ``timeout`` seconds while dispatching COM/window messages."""
    _user32.MsgWaitForMultipleObjects(0, None, False, int(timeout * 1000), _QS_ALLINPUT)
    message = wintypes.MSG()
    while _user32.PeekMessageW(byref(message), None, 0, 0, _PM_REMOVE):
        _user32.TranslateMessage(byref(message))
        _user32.DispatchMessageW(byref(message))
