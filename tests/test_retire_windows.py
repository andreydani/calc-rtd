"""Late calls into a retired callback object must never reach Python (Windows only)."""

import ctypes
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="COM is Windows only")


def test_retired_object_answers_from_native_code():
    from calc_rtd import com_driver

    event = com_driver.UpdateEvent()
    pointers = list(event._com_pointers_.values())
    held_by_server = event.QueryInterface(com_driver.IRTDUpdateEvent)  # noqa: F841 (keeps a reference)
    assert event._refcnt.value == 1
    assert com_driver.retire(pointers)

    width = ctypes.sizeof(ctypes.c_void_p)
    for pointer_to_this in pointers:
        this = ctypes.addressof(pointer_to_this.contents)
        vtable = ctypes.c_void_p.from_address(this).value

        def slot(index, restype, *argtypes):
            address = ctypes.c_void_p.from_address(vtable + index * width).value
            return ctypes.WINFUNCTYPE(restype, *argtypes)(address)

        pointer = ctypes.c_void_p
        assert slot(1, ctypes.c_ulong, pointer)(this) == 1                       # AddRef
        assert slot(2, ctypes.c_ulong, pointer)(this) == 1                       # Release
        assert slot(2, ctypes.c_ulong, pointer)(this) == 1                       # ... as often as it likes
        assert slot(0, ctypes.c_ulong, pointer, pointer, pointer)(this, None, None) == 0x80004002
        assert slot(7, ctypes.c_ulong, pointer)(this) == 0                       # UpdateNotify
        assert slot(8, ctypes.c_ulong, pointer, pointer)(this, None) == 0x80004005
        assert slot(9, ctypes.c_ulong, pointer, ctypes.c_long)(this, 5000) == 0
        assert slot(10, ctypes.c_ulong, pointer)(this) == 0                      # Disconnect
        invoke = slot(6, ctypes.c_ulong, pointer, ctypes.c_long, pointer, ctypes.c_ulong,
                      ctypes.c_ushort, pointer, pointer, pointer, pointer)
        assert invoke(this, 10, None, 0, 1, None, None, None, None) == 0

    # None of that reached the Python object.
    assert event.notified is False and event.disconnected is False and event.heartbeat_ms == 15000
    assert event._refcnt.value == 1
