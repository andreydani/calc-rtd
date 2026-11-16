"""COM side of the bridge (Windows only): talks IRtdServer like Excel does.

The two interfaces below are transcribed from Excel's type library so that
Excel itself does not have to be installed to load an in-process RTD
server.  (Out-of-process servers still need the interfaces to be registered
for marshalling, which installing Excel does.)

    IRtdServer       {EC0E6191-DB51-11D3-8F3E-00C04F3651B8}
    IRTDUpdateEvent  {A43788C1-D91B-11D3-8F39-00C04F3651B8}

Everything here must run on one thread: a single-threaded apartment whose
message loop is pumped by ``pump()``.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import HRESULT, POINTER, byref, c_long, wintypes

if sys.platform != "win32":  # pragma: no cover
    raise ImportError("COM is only available on Windows")

import comtypes  # noqa: E402  (initialises COM as STA on this thread)
import comtypes.client  # noqa: E402
from comtypes import COMMETHOD, GUID, STDMETHOD, COMError, COMObject, dispid  # noqa: E402
from comtypes.automation import IDispatch, VARIANT, VARIANT_BOOL  # noqa: E402
from comtypes.safearray import _midlSAFEARRAY  # noqa: E402

from .rtd_host import pairs_from_refresh  # noqa: E402

log = logging.getLogger("calc_rtd.com")

S_OK = 0
DISP_E_MEMBERNOTFOUND = -2147352573
DISP_E_UNKNOWNNAME = -2147352570
REGDB_E_CLASSNOTREG = -2147221164
CO_E_CLASSSTRING = -2147221005
VT_ERROR = 10

_VariantArray = _midlSAFEARRAY(VARIANT)


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


def _plain(variant):
    return None if variant.vt == VT_ERROR else variant.value


class ComDriver:
    """One live RTD server instance (see rtd_host for the contract)."""

    def __init__(self, progid, server=""):
        self.progid = progid
        self._event = UpdateEvent()
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
        if server is not None:
            server.ServerTerminate()


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
