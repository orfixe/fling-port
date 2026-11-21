#!/usr/bin/env python3
"""Fling Bluetooth remote: the laptop poses as a Bluetooth LE HID remote
(HID-over-GATT: keyboard + consumer control) that Google TV pairs with under
Settings → Remotes & accessories. Controlled over a local UNIX socket:
    key <name>      up down left right ok back home play_pause vol_up vol_down
                    mute power menu next prev search
    text <ascii>    type text (Latin letters, digits, basic punctuation)
    pair [seconds]  open the pairing window (default 180 s)
    status          → JSON {"running","connected","pairing","bonded","error"}
"""
import json
import os
import socket
import sys
import time

import dbus
import dbus.exceptions
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

NAME = "Fling Remote"
SOCK = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "fling-bt.sock")
STATE_FILE = os.path.expanduser("~/.local/share/fling/bt_state.json")

BLUEZ = "org.bluez"
GATT_MGR = "org.bluez.GattManager1"
ADV_MGR = "org.bluez.LEAdvertisingManager1"
SVC = "org.bluez.GattService1"
CHR = "org.bluez.GattCharacteristic1"
DSC = "org.bluez.GattDescriptor1"
PROPS = "org.freedesktop.DBus.Properties"
OM = "org.freedesktop.DBus.ObjectManager"

# ---------------------------------------------------------------- HID report map
REPORT_MAP = bytes([
    # Report 1: keyboard (8 modifier bits, reserved byte, 6 key slots)
    0x05, 0x01, 0x09, 0x06, 0xA1, 0x01, 0x85, 0x01,
    0x05, 0x07, 0x19, 0xE0, 0x29, 0xE7, 0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x08, 0x81, 0x02,
    0x95, 0x01, 0x75, 0x08, 0x81, 0x01,
    0x95, 0x06, 0x75, 0x08, 0x15, 0x00, 0x25, 0x65, 0x05, 0x07, 0x19, 0x00, 0x29, 0x65, 0x81, 0x00,
    0xC0,
    # Report 2: consumer control (one 16-bit usage)
    0x05, 0x0C, 0x09, 0x01, 0xA1, 0x01, 0x85, 0x02,
    0x15, 0x00, 0x26, 0xFF, 0x03, 0x19, 0x00, 0x2A, 0xFF, 0x03, 0x75, 0x10, 0x95, 0x01, 0x81, 0x00,
    0xC0,
])

KEYBOARD = {"up": 0x52, "down": 0x51, "left": 0x50, "right": 0x4F, "ok": 0x28, "enter": 0x28,
            "escape": 0x29, "backspace": 0x2A, "tab": 0x2B}
CONSUMER = {"home": 0x0223, "back": 0x0224, "play_pause": 0x00CD, "vol_up": 0x00E9,
            "vol_down": 0x00EA, "mute": 0x00E2, "power": 0x0030, "menu": 0x0040,
            "next": 0x00B5, "prev": 0x00B6, "search": 0x0221, "ffwd": 0x00B3, "rewind": 0x00B4}

SHIFT = 0x02


def char_usage(ch):
    """ASCII char → (modifier, usage) on a US layout, or None."""
    if "a" <= ch <= "z":
        return 0, 0x04 + ord(ch) - ord("a")
    if "A" <= ch <= "Z":
        return SHIFT, 0x04 + ord(ch) - ord("A")
    if "1" <= ch <= "9":
        return 0, 0x1E + ord(ch) - ord("1")
    table = {"0": (0, 0x27), " ": (0, 0x2C), "\n": (0, 0x28), "-": (0, 0x2D), "=": (0, 0x2E),
             ".": (0, 0x37), ",": (0, 0x36), "/": (0, 0x38), ";": (0, 0x33), "'": (0, 0x34),
             "!": (SHIFT, 0x1E), "@": (SHIFT, 0x1F), "#": (SHIFT, 0x20), "$": (SHIFT, 0x21),
             "%": (SHIFT, 0x22), "&": (SHIFT, 0x24), "*": (SHIFT, 0x25), "(": (SHIFT, 0x26),
             ")": (SHIFT, 0x27), "_": (SHIFT, 0x2D), "+": (SHIFT, 0x2E), "?": (SHIFT, 0x38),
             ":": (SHIFT, 0x33), '"': (SHIFT, 0x34)}
    return table.get(ch)

# ---------------------------------------------------------------- GATT plumbing (BlueZ example-gatt-server style)


class Application(dbus.service.Object):
    def __init__(self, bus):
        self.path = "/app/fling"
        self.services = []
        super().__init__(bus, self.path)

    @dbus.service.method(OM, out_signature="a{oa{sa{sv}}}")
    def GetManagedObjects(self):
        out = {}
        for s in self.services:
            out[s.get_path()] = s.get_properties()
            for c in s.chars:
                out[c.get_path()] = c.get_properties()
                for d in c.descs:
                    out[d.get_path()] = d.get_properties()
        return out

    def get_path(self):
        return dbus.ObjectPath(self.path)


class Service(dbus.service.Object):
    def __init__(self, bus, index, uuid):
        self.path = f"/app/fling/service{index}"
        self.uuid, self.chars = uuid, []
        super().__init__(bus, self.path)

    def get_properties(self):
        return {SVC: {"UUID": self.uuid, "Primary": True,
                      "Characteristics": dbus.Array([c.get_path() for c in self.chars], signature="o")}}

    def get_path(self):
        return dbus.ObjectPath(self.path)


class Characteristic(dbus.service.Object):
    def __init__(self, bus, index, uuid, flags, service, value=b""):
        self.path = f"{service.path}/char{index}"
        self.uuid, self.flags, self.service = uuid, flags, service
        self.value = bytes(value)
        self.descs = []
        self.notifying = False
        super().__init__(bus, self.path)
        service.chars.append(self)

    def get_properties(self):
        return {CHR: {"Service": self.service.get_path(), "UUID": self.uuid,
                      "Flags": dbus.Array(self.flags, signature="s"),
                      "Descriptors": dbus.Array([d.get_path() for d in self.descs], signature="o")}}

    def get_path(self):
        return dbus.ObjectPath(self.path)

    @dbus.service.method(PROPS, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        return self.get_properties()[CHR]

    @dbus.service.method(CHR, in_signature="a{sv}", out_signature="ay")
    def ReadValue(self, options):
        return dbus.Array(self.value, signature="y")

    @dbus.service.method(CHR, in_signature="aya{sv}")
    def WriteValue(self, value, options):
        self.value = bytes(value)

    @dbus.service.method(CHR)
    def StartNotify(self):
        self.notifying = True

    @dbus.service.method(CHR)
    def StopNotify(self):
        self.notifying = False

    @dbus.service.signal(PROPS, signature="sa{sv}as")
    def PropertiesChanged(self, interface, changed, invalidated):
        pass

    def notify(self, value: bytes):
        self.value = bytes(value)
        if self.notifying:
            self.PropertiesChanged(CHR, {"Value": dbus.Array(self.value, signature="y")}, [])


class Descriptor(dbus.service.Object):
    def __init__(self, bus, index, uuid, flags, chrc, value):
        self.path = f"{chrc.path}/desc{index}"
        self.uuid, self.flags, self.chrc, self.value = uuid, flags, chrc, bytes(value)
        super().__init__(bus, self.path)
        chrc.descs.append(self)

    def get_properties(self):
        return {DSC: {"Characteristic": self.chrc.get_path(), "UUID": self.uuid,
                      "Flags": dbus.Array(self.flags, signature="s")}}

    def get_path(self):
        return dbus.ObjectPath(self.path)

    @dbus.service.method(DSC, in_signature="a{sv}", out_signature="ay")
    def ReadValue(self, options):
        return dbus.Array(self.value, signature="y")


class Advertisement(dbus.service.Object):
    def __init__(self, bus):
        self.path = "/app/fling/adv0"
        self.discoverable = False
        super().__init__(bus, self.path)

    def props(self):
        return {"Type": "peripheral",
                "ServiceUUIDs": dbus.Array(["1812", "180F"], signature="s"),
                "Appearance": dbus.UInt16(0x0180),  # generic remote control
                "LocalName": dbus.String(NAME),
                "Discoverable": dbus.Boolean(self.discoverable)}

    @dbus.service.method(PROPS, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        return self.props()

    @dbus.service.method("org.bluez.LEAdvertisement1")
    def Release(self):
        pass

    def get_path(self):
        return dbus.ObjectPath(self.path)


class Agent(dbus.service.Object):
    """Just-Works pairing, accepted only while the pairing window is open."""
    def __init__(self, bus, remote):
        self.remote = remote
        super().__init__(bus, "/app/fling/agent")

    def _check(self):
        if not self.remote.pairing_open():
            raise dbus.exceptions.DBusException("org.bluez.Error.Rejected", "pairing window closed")

    @dbus.service.method("org.bluez.Agent1")
    def Release(self):
        pass

    @dbus.service.method("org.bluez.Agent1", in_signature="os")
    def AuthorizeService(self, device, uuid):
        return

    @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="s")
    def RequestPinCode(self, device):
        self._check()
        return "0000"

    @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="u")
    def RequestPasskey(self, device):
        self._check()
        return dbus.UInt32(0)

    @dbus.service.method("org.bluez.Agent1", in_signature="ouq")
    def DisplayPasskey(self, device, passkey, entered):
        pass

    @dbus.service.method("org.bluez.Agent1", in_signature="os")
    def DisplayPinCode(self, device, pincode):
        pass

    @dbus.service.method("org.bluez.Agent1", in_signature="ou")
    def RequestConfirmation(self, device, passkey):
        self._check()

    @dbus.service.method("org.bluez.Agent1", in_signature="o")
    def RequestAuthorization(self, device):
        self._check()

    @dbus.service.method("org.bluez.Agent1")
    def Cancel(self):
        pass

# ---------------------------------------------------------------- the remote


class Remote:
    def __init__(self):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SystemBus()
        self.adapter_path = self._adapter()
        self.pair_until = 0.0
        self.error = None
        self.queue = []          # pending (report_char, bytes, delay_ms)
        self.sending = False

        self.app = Application(self.bus)
        hid = Service(self.bus, 0, "1812")
        Characteristic(self.bus, 0, "2a4a", ["read"], hid, [0x11, 0x01, 0x00, 0x02])       # HID Information
        Characteristic(self.bus, 1, "2a4b", ["read", "encrypt-read"], hid, REPORT_MAP)      # Report Map
        Characteristic(self.bus, 2, "2a4c", ["write-without-response"], hid, [0])           # Control Point
        Characteristic(self.bus, 3, "2a4e", ["read", "write-without-response"], hid, [1])   # Protocol Mode
        self.kbd = Characteristic(self.bus, 4, "2a4d", ["read", "notify", "encrypt-read"], hid, bytes(8))
        Descriptor(self.bus, 0, "2908", ["read"], self.kbd, [0x01, 0x01])
        self.cons = Characteristic(self.bus, 5, "2a4d", ["read", "notify", "encrypt-read"], hid, bytes(2))
        Descriptor(self.bus, 0, "2908", ["read"], self.cons, [0x02, 0x01])
        bat = Service(self.bus, 1, "180f")
        self.battery = Characteristic(self.bus, 0, "2a19", ["read", "notify"], bat, [100])
        self.app.services = [hid, bat]

        self.adv = Advertisement(self.bus)

        adapter = self.bus.get_object(BLUEZ, self.adapter_path)
        dbus.Interface(adapter, PROPS).Set("org.bluez.Adapter1", "Powered", dbus.Boolean(True))
        # Pairing confirmation stays with the desktop's own agent (KDE shows a
        # prompt): replacing the default agent would break pairing headphones etc.
        dbus.Interface(adapter, GATT_MGR).RegisterApplication(
            self.app.get_path(), {}, reply_handler=lambda: None,
            error_handler=lambda e: self._fail(f"GATT: {e}"))
        self._advertise()

    def _adapter(self):
        objs = dbus.Interface(self.bus.get_object(BLUEZ, "/"), OM).GetManagedObjects()
        for path, ifaces in objs.items():
            if ADV_MGR in ifaces and GATT_MGR in ifaces:
                return path
        raise RuntimeError("Bluetooth adapter with LE peripheral support not found")

    def _fail(self, msg):
        self.error = str(msg)
        print("bt:", msg, flush=True)

    def _advertise(self):
        mgr = dbus.Interface(self.bus.get_object(BLUEZ, self.adapter_path), ADV_MGR)
        try:
            mgr.UnregisterAdvertisement(self.adv.get_path())
        except dbus.exceptions.DBusException:
            pass
        mgr.RegisterAdvertisement(self.adv.get_path(), {}, reply_handler=lambda: None,
                                  error_handler=lambda e: self._fail(f"advertising: {e}"))

    # pairing window
    def pairing_open(self):
        return time.time() < self.pair_until

    def open_pairing(self, seconds=180):
        self.pair_until = time.time() + seconds
        self.adv.discoverable = True
        self._advertise()
        GLib.timeout_add_seconds(seconds + 1, self._close_pairing)

    def _close_pairing(self):
        if not self.pairing_open() and self.adv.discoverable:
            self.adv.discoverable = False
            self._advertise()
        return False

    # connection info
    def peers(self):
        objs = dbus.Interface(self.bus.get_object(BLUEZ, "/"), OM).GetManagedObjects()
        out = []
        for path, ifaces in objs.items():
            d = ifaces.get("org.bluez.Device1")
            if d and str(path).startswith(self.adapter_path):
                out.append({"name": str(d.get("Alias", d.get("Name", ""))),
                            "connected": bool(d.get("Connected")), "bonded": bool(d.get("Bonded", d.get("Paired")))})
        return out

    def status(self):
        peers = self.peers()
        subscribed = self.kbd.notifying or self.cons.notifying
        return {"running": True, "connected": subscribed,
                "peer": next((p["name"] for p in peers if p["connected"]), None),
                "pairing": self.pairing_open(), "error": self.error}

    # sending
    def _enqueue(self, chrc, data, gap=25):
        self.queue.append((chrc, bytes(data), gap))
        if not self.sending:
            self.sending = True
            GLib.idle_add(self._pump)

    def _pump(self):
        if not self.queue:
            self.sending = False
            return False
        chrc, data, gap = self.queue.pop(0)
        chrc.notify(data)
        GLib.timeout_add(gap, self._pump)
        return False

    def key(self, name):
        if name in KEYBOARD:
            u = KEYBOARD[name]
            self._enqueue(self.kbd, [0, 0, u, 0, 0, 0, 0, 0], 40)
            self._enqueue(self.kbd, bytes(8))
        elif name in CONSUMER:
            u = CONSUMER[name]
            self._enqueue(self.cons, [u & 0xFF, u >> 8], 40)
            self._enqueue(self.cons, [0, 0])
        else:
            raise ValueError(f"unknown key {name}")

    def text(self, s):
        skipped = 0
        for ch in s:
            m = char_usage(ch)
            if not m:
                skipped += 1
                continue
            mod, u = m
            self._enqueue(self.kbd, [mod, 0, u, 0, 0, 0, 0, 0], 20)
            self._enqueue(self.kbd, bytes(8), 20)
        return skipped

    # control socket
    def serve(self):
        try:
            os.unlink(SOCK)
        except FileNotFoundError:
            pass
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(SOCK)
        os.chmod(SOCK, 0o600)
        srv.listen(8)
        srv.setblocking(False)

        def on_conn(fd, cond):
            try:
                conn, _ = srv.accept()
            except BlockingIOError:
                return True
            conn.settimeout(2)
            try:
                line = conn.recv(4096).decode(errors="ignore").strip()
                cmd, _, arg = line.partition(" ")
                if cmd == "key":
                    self.key(arg.strip())
                    reply = {"ok": True}
                elif cmd == "text":
                    reply = {"ok": True, "skipped": self.text(arg)}
                elif cmd == "pair":
                    self.open_pairing(int(arg or 180))
                    reply = {"ok": True}
                elif cmd == "status":
                    reply = self.status()
                elif cmd == "quit":
                    conn.sendall(b'{"ok": true}\n')
                    conn.close()
                    loop.quit()
                    return False
                else:
                    reply = {"ok": False, "error": "unknown command"}
            except Exception as e:
                reply = {"ok": False, "error": str(e)}
            try:
                conn.sendall((json.dumps(reply) + "\n").encode())
            finally:
                conn.close()
            return True

        GLib.io_add_watch(srv.fileno(), GLib.IO_IN, on_conn)
        loop = GLib.MainLoop()
        loop.run()


def client(cmd: str, timeout=3.0):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(SOCK)
    s.sendall(cmd.encode())
    data = s.recv(65536)
    s.close()
    return json.loads(data.decode() or "{}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] != "serve":
        print(json.dumps(client(" ".join(sys.argv[1:]))))
        sys.exit(0)
    try:
        client("status", 0.5)
        print("already running")
        sys.exit(0)
    except OSError:
        pass
    Remote().serve()
