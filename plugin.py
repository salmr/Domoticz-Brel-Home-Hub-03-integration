#!/usr/bin/env python3
# -*- coding: ascii -*-
"""
<plugin key="Brel-Home-Hub-03" name="Brel Home Hub 03" author="Salmr199" version="2.0.1">
    <description>
        <![CDATA[
        <div style="font-family: Arial, sans-serif; line-height: 1.5;">
            <h2>Brel Home Hub 03 Version 2.0.1</h2>

            <p>Domoticz plugin with dynamic hub discovery via broadcast and multicast and stable UDP control.
            Tested for
            <a href="https://www.brel-home.nl/nl/pro/producten/smart-home/716/hub-032" target="_blank" rel="noopener noreferrer">Brel HUB-03/2</a>.</p>

            <h3>Acknowledgements</h3>
            <p>Thanks to Superjunky for all the hard work that was used as a starting point for this project.</p>

            <h3>Features</h3>
            <ul>
                <li>Creates a Position, Angle, Battery, Charging and RSSI device for every shutter in your Brel Home Hub.</li>
                <li>Devices are tied to the shutter MAC, so renaming them is safe and restarts never create duplicates.</li>
                <li>Live updates from the hub multicast reports plus a periodic poll.</li>
                <li>Never blocks Domoticz: all network traffic runs in background threads.</li>
            </ul>

            <h3>Configuration</h3>
            <ul>
                <li>You can enter the IP of your Brel Home Hub, but the plugin scans the network when the hub does not answer there.</li>
                <li>Enter the KEY of your Brel Home Hub. Get the KEY by quickly tapping 5 times on "Version 1.x.x(x)" in your Brel smartphone app. You get the 16-byte KEY in a popup. On Android, tap next to the profile picture instead of the version number.</li>
                <li>Do not forget to let Domoticz accept new devices before you activate this plugin.</li>
                <li>If PyCryptodome is not available, enter a pre-generated AccessToken in the field below. Generate it with an
                    <a href="https://www.devglan.com/online-tools/aes-encryption-decryption" target="_blank" rel="noopener noreferrer">AES Encryption/Decryption Tool</a>
                    (AES-128, ECB, no padding, hex output, upper case). A fixed token stops working when the hub restarts.</li>
                <li>Upgrading from an older version: your existing devices (also renamed ones) are adopted automatically. They are matched on the name tail (MAC) or, for renamed devices, on the old unit layout (5 units per shutter, MACs in sorted order). Set "Legacy devices" to "Remove unused duplicates" for ONE start to delete the extra copies the old version created, then set it back to Keep.</li>
            </ul>
        </div>
        ]]>
    </description>
    <params>
        <param field="Address" label="Last known Hub IP (optional)" width="200px"/>
        <param field="Password" label="Key (16-byte)" width="200px" required="true"/>
        <param field="Mode1" label="Pre-generated AccessToken (optional)" width="200px"/>
        <param field="Mode2" label="Debug" width="75px">
            <options>
                <option label="Off" value="0" default="true"/>
                <option label="On" value="1"/>
            </options>
        </param>
        <param field="Mode3" label="Poll interval (s)" width="75px" default="600"/>
        <param field="Mode4" label="Legacy devices" width="250px">
            <options>
                <option label="Keep (adopt)" value="0" default="true"/>
                <option label="Remove unused duplicates (one start)" value="2"/>
                <option label="Remove ALL legacy devices (one start)" value="1"/>
            </options>
        </param>
    </params>
</plugin>
"""

# Domoticz injects the globals Parameters and Devices into this module.
try:
    import Domoticz
except Exception as _exc:
    raise ImportError("[Brel plugin] Cannot import Domoticz host module: {}".format(_exc))

import queue

# ---- BEGIN SHARED ----
import binascii
import json
import logging
import re
import socket
import threading
import time

try:
    from Crypto.Cipher import AES
except Exception:
    try:
        from Cryptodome.Cipher import AES
    except Exception:
        AES = None

MULTICAST_IP = "238.0.0.18"
UNICAST_PORT = 32100
MULTICAST_PORT = 32101

# Operation codes used in WriteDevice messages (unchanged from the old plugin).
OP_OPEN = 1
OP_STOP = 2
OP_CLOSE = 3
OP_POSITION = 2     # operation sent together with a targetPosition level

# Full scale of targetAngle / currentAngle on the hub (degrees).
ANGLE_MAX = 180

_LOG = logging.getLogger("brel")
_LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "error": logging.ERROR}
_HEX = re.compile(r"[0-9A-Fa-f]+")


class BrelHub(object):
    """Client for one Brel Home Hub.

    All network methods are blocking, never raise (errors are logged and
    None is returned) and are safe to call from several threads: unicast
    requests are serialised with a lock.

    Attributes:
        host: Hub IP address or None until known.
        key: 16-byte AES key (bytes) or None.
        secret: Pre-generated AccessToken supplied by the user, or None.
        devices: Dict mac -> device dict for real shutters.
        gateway: Last GetDeviceList reply (dict) or None.
        gateway_mac: MAC string of the hub itself, or None.
        access_token: Current AccessToken (str) or None.
        position_inverted: Dict mac -> bool; True flips 0..100 positions.
    """

    def __init__(self, host=None, key=None, secret=None, debug=False, log=None):
        """Create a hub client.

        Args:
            host: Hub IP, or None/"" to discover it.
            key: 16 ASCII characters or 32 hex characters (str), or bytes.
            secret: Optional pre-generated AccessToken (skips AES).
            debug: True or "1" to emit debug messages.
            log: Optional callable log(level, message) with level in
                "debug", "info", "error". Defaults to the logging module.
        """
        self.host = host.strip() if isinstance(host, str) and host.strip() else None
        self.debug = (str(debug) == "1" or debug is True)
        self._log_fn = log
        self.devices = {}
        self.gateway = None
        self.gateway_mac = None
        self.access_token = None
        self.position_inverted = {}
        self._io_lock = threading.Lock()
        self._token_lock = threading.RLock()
        self._err_times = {}
        self.secret = secret.strip() if isinstance(secret, str) and secret.strip() else None
        self.key = self._parse_key(key)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _log(self, level, msg):
        """Log a message through the injected logger; never raises."""
        if level == "debug" and not self.debug:
            return
        try:
            if self._log_fn:
                self._log_fn(level, msg)
            else:
                _LOG.log(_LEVELS.get(level, logging.INFO), msg)
        except Exception:
            pass

    def _log_limited(self, key, msg, every=30.0):
        """Log an error at most once per `every` seconds for a given key."""
        now = time.time()
        if now - self._err_times.get(key, 0.0) >= every:
            self._err_times[key] = now
            self._log("error", msg)

    @staticmethod
    def _timestamp(mac=None):
        """Return a msgID: last digit group of the MAC (or 101) + epoch ms."""
        base = "101"
        if mac:
            nums = re.findall(r"\d+", mac)
            if nums:
                base = nums[-1]
        return "{}{}".format(base, int(time.time() * 1000))

    @staticmethod
    def _looks_hex(text):
        """True if text is a non-empty string of hex digits."""
        return isinstance(text, str) and _HEX.fullmatch(text) is not None

    @staticmethod
    def is_real_mac(mac):
        """True for a shutter MAC (hex, 16+ chars). The hub itself has 12."""
        return isinstance(mac, str) and len(mac) >= 16 and _HEX.fullmatch(mac) is not None

    def _parse_key(self, key):
        """Convert the user key to 16 bytes. Returns None if unusable."""
        if isinstance(key, bytes):
            if len(key) == 16:
                return key
            key = key.decode("utf-8", "ignore")
        key = str(key).strip() if key else ""
        if not key:
            if not self.secret:
                self._log("error", "no key provided. Set the hub key (16 ASCII or 32 HEX chars).")
            return None
        try:
            if len(key) == 32 and self._looks_hex(key):
                return binascii.unhexlify(key)
            if len(key) == 16:
                return key.encode("utf-8")
        except Exception as exc:
            self._log("error", "failed to parse key: {}".format(exc))
            return None
        self._log("error", "invalid key length {}. Use 16 ASCII or 32 HEX chars.".format(len(key)))
        return None

    def _parse_token_to_16bytes(self, token):
        """Convert the hub token (16 ASCII or 32 HEX chars) to 16 bytes."""
        try:
            if isinstance(token, bytes):
                return token if len(token) == 16 else None
            if isinstance(token, str):
                tok = token.strip()
                if len(tok) == 16:
                    return tok.encode("utf-8")
                if len(tok) == 32 and self._looks_hex(tok):
                    return binascii.unhexlify(tok)
        except Exception:
            pass
        return None

    def _resolve_mac(self, mac):
        """Return the hub's own spelling of a MAC (case-insensitive) or None."""
        if mac in self.devices:
            return mac
        low = str(mac).lower()
        for known in self.devices:
            if known.lower() == low:
                return known
        return None

    @staticmethod
    def _check_reply(resp):
        """Classify a hub reply. Returns (ok, reason)."""
        if not isinstance(resp, dict):
            return False, "no reply from hub"
        if resp.get("msgType") == "GenericResponse" or "actionResult" in resp:
            return False, str(resp.get("actionResult") or resp.get("data") or "hub reported an error")
        return True, ""

    # ------------------------------------------------------------------
    # network IO
    # ------------------------------------------------------------------
    def _send(self, payload, timeout=3.0, attempts=2):
        """Send one unicast request and return the decoded JSON reply.

        UDP can drop packets, so a timeout is retried once. Returns None on
        any failure (the reason is logged, rate limited).
        """
        if not self.host:
            self._log_limited("nohost", "no hub host set; cannot send.")
            return None
        mtype = payload.get("msgType")
        reason = "unknown error"
        try:
            raw = json.dumps(payload).encode("utf-8")
            with self._io_lock:
                for _ in range(attempts):
                    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    try:
                        sock.settimeout(timeout)
                        sock.sendto(raw, (self.host, UNICAST_PORT))
                        data, _addr = sock.recvfrom(8192)
                    except socket.timeout:
                        reason = "timeout"
                        continue
                    except OSError as exc:
                        reason = str(exc)
                        break
                    finally:
                        sock.close()
                    try:
                        resp = json.loads(data.decode("utf-8", "ignore"))
                    except ValueError:
                        self._log("debug", "hub replied with invalid JSON.")
                        return None
                    self._log("debug", "{} -> {} ok".format(mtype, payload.get("mac", "hub")))
                    return resp if isinstance(resp, dict) else None
        except Exception as exc:
            reason = str(exc)
        self._log_limited("udp", "UDP error ({}): {}".format(mtype, reason))
        return None

    def discover_hub(self, timeout=5.0):
        """Find the hub by broadcast probe, then by listening to multicast.

        Returns:
            The hub IP (str) and stores it in self.host, or None.
        """
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.settimeout(timeout)
                probe = {"msgType": "GetDeviceList", "msgID": self._timestamp()}
                sock.sendto(json.dumps(probe).encode("utf-8"), ("255.255.255.255", UNICAST_PORT))
                _data, addr = sock.recvfrom(8192)
                self.host = addr[0]
                self._log("info", "hub discovered via broadcast at {}".format(self.host))
                return self.host
            finally:
                sock.close()
        except Exception as exc:
            self._log("debug", "broadcast discovery failed: {}".format(exc))

        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", MULTICAST_PORT))
            mreq = socket.inet_aton(MULTICAST_IP) + socket.inet_aton("0.0.0.0")
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            sock.settimeout(1.0)
            end = time.time() + timeout
            while time.time() < end:
                try:
                    data, addr = sock.recvfrom(8192)
                except socket.timeout:
                    continue
                try:
                    msg = json.loads(data.decode("utf-8", "ignore"))
                except ValueError:
                    continue
                if isinstance(msg, dict) and msg.get("msgType") in ("Gateway", "Report", "Heartbeat"):
                    self.host = addr[0]
                    self._log("info", "hub discovered via multicast at {}".format(self.host))
                    return self.host
        except Exception as exc:
            self._log("debug", "multicast discovery failed: {}".format(exc))
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
        return None

    # ------------------------------------------------------------------
    # protocol
    # ------------------------------------------------------------------
    def get_device_list(self):
        """Ask the hub for its device list.

        Returns:
            Dict mac -> device (real shutters only, may be empty), or None
            if the hub did not answer.
        """
        try:
            resp = self._send({"msgType": "GetDeviceList", "msgID": self._timestamp()})
            ok, why = self._check_reply(resp)
            if not ok:
                if resp is not None:
                    self._log("error", "GetDeviceList refused: {}".format(why))
                return None
            raw = resp.get("data")
            if not isinstance(raw, list):
                raw = []
            shutters = {}
            for dev in raw:
                if isinstance(dev, dict) and self.is_real_mac(dev.get("mac")):
                    shutters[dev["mac"]] = dev
            self.gateway = resp
            mac = resp.get("mac")
            self.gateway_mac = mac if isinstance(mac, str) else self.gateway_mac
            self.devices = shutters
            self._log("debug", "{} devices reported, {} real shutters".format(len(raw), len(shutters)))
            return shutters
        except Exception as exc:
            self._log("error", "get_device_list failed: {}".format(exc))
            return None

    def generate_access_token(self):
        """Build the AccessToken (or use the pre-generated one).

        Returns:
            The AccessToken string, or None if it could not be derived.
        """
        with self._token_lock:
            if self.secret:
                self.access_token = self.secret
                return self.access_token
            try:
                if AES is None:
                    self._log_limited("aes", "PyCryptodome not available. Install python3-pycryptodome "
                                      "or enter a pre-generated AccessToken.")
                    return None
                if not self.key:
                    self._log_limited("nokey", "no valid key; cannot derive AccessToken.")
                    return None
                raw = self.gateway.get("token") if self.gateway else None
                token16 = self._parse_token_to_16bytes(raw)
                if token16 is None:
                    self._log("error", "hub token is not 16 ASCII or 32 HEX chars; cannot derive AccessToken.")
                    return None
                encrypted = AES.new(self.key, AES.MODE_ECB).encrypt(token16)
                self.access_token = binascii.hexlify(encrypted).decode().upper()
                return self.access_token
            except Exception as exc:
                self._log("error", "failed to create AccessToken: {}".format(exc))
                return None

    def refresh_token(self):
        """Re-read the hub token and rebuild the AccessToken.

        Returns:
            True on success. False if a fixed AccessToken is in use or the
            hub could not be reached.
        """
        if self.secret:
            return False
        with self._token_lock:
            if self.get_device_list() is None:
                return False
            return self.generate_access_token() is not None

    def connect(self):
        """Locate the hub, read its device list and derive the AccessToken.

        Tries the configured host first, then falls back to discovery.

        Returns:
            Dict of shutters (may be empty) or None if the hub was not found.
        """
        try:
            devices = self.get_device_list() if self.host else None
            if devices is None:
                if self.host:
                    self._log("info", "no answer from {}; scanning the network".format(self.host))
                if not self.discover_hub():
                    return None
                devices = self.get_device_list()
            if devices is None:
                return None
            if self.generate_access_token() is None:
                self._log("error", "no AccessToken; the hub can be read but not controlled.")
            return devices
        except Exception as exc:
            self._log("error", "connect failed: {}".format(exc))
            return None

    def get_status(self, mac):
        """Read one device. Returns the reply dict (see its "data") or None."""
        try:
            real = self._resolve_mac(mac)
            if real is None:
                self._log("error", "unknown device {}".format(mac))
                return None
            resp = self._send({
                "msgType": "ReadDevice",
                "mac": real,
                "deviceType": self.devices[real].get("deviceType"),
                "msgID": self._timestamp(real),
            })
            ok, why = self._check_reply(resp)
            if not ok:
                if resp is not None:
                    self._log("error", "ReadDevice {} refused: {}".format(real, why))
                return None
            return resp
        except Exception as exc:
            self._log("error", "get_status failed: {}".format(exc))
            return None

    def set_value(self, mac, P=None, A=None, O=None):
        """Send a WriteDevice command.

        Args:
            mac: Device MAC.
            P: targetPosition 0..100 (inversion applied if configured).
            A: targetAngle (0..ANGLE_MAX).
            O: operation (OP_OPEN, OP_STOP, OP_CLOSE).

        Returns:
            The hub reply dict, or None on failure. If the hub rejects the
            AccessToken it is rebuilt and the command is retried once.
        """
        try:
            real = self._resolve_mac(mac)
            if real is None:
                self._log("error", "unknown device {}".format(mac))
                return None
            payload = {}
            if O is not None:
                payload["operation"] = int(O)
            if P is not None:
                level = max(0, min(100, int(P)))
                if self.position_inverted.get(real, False):
                    level = 100 - level
                payload["targetPosition"] = level
            if A is not None:
                payload["targetAngle"] = max(0, min(ANGLE_MAX, int(A)))
            if not payload:
                return None
            why = ""
            for attempt in (0, 1):
                resp = self._send({
                    "msgType": "WriteDevice",
                    "mac": real,
                    "deviceType": self.devices[real].get("deviceType"),
                    "AccessToken": self.access_token,
                    "data": payload,
                    "msgID": self._timestamp(real),
                })
                ok, why = self._check_reply(resp)
                if ok:
                    self._log("debug", "{} <- {} reply {}".format(real, payload, resp))
                    return resp
                if attempt == 0 and resp is not None and "token" in why.lower() and self.refresh_token():
                    self._log("info", "AccessToken rebuilt after hub rejected it; retrying.")
                    continue
                break
            self._log("error", "WriteDevice {} failed: {}".format(real, why))
            return None
        except Exception as exc:
            self._log("error", "set_value failed: {}".format(exc))
            return None

    def denormalize_position(self, mac, value):
        """Map a UI slider value to a hub position: clamp 1..99, apply inversion."""
        try:
            value = int(value)
        except Exception:
            value = 1
        value = 99 if value >= 100 else (1 if value <= 0 else value)
        if self.position_inverted.get(mac, False):
            value = 100 - value
        return value

    # ------------------------------------------------------------------
    # multicast
    # ------------------------------------------------------------------
    def _note_gateway_message(self, msg, addr):
        """Follow hub IP changes and token rotation seen on multicast."""
        try:
            mac = msg.get("mac")
            if not self.gateway_mac or not isinstance(mac, str) or mac.lower() != self.gateway_mac.lower():
                return
            if msg.get("msgType") not in ("Heartbeat", "Gateway"):
                return
            if addr and addr[0] != self.host:
                self._log("info", "hub IP changed {} -> {}".format(self.host, addr[0]))
                self.host = addr[0]
            tok = msg.get("token")
            if tok and not self.secret and self.gateway is not None and tok != self.gateway.get("token"):
                self.gateway["token"] = tok
                self.generate_access_token()
                self._log("debug", "hub token changed; AccessToken rebuilt.")
        except Exception:
            pass

    def listen_multicast(self, on_message, stop_event):
        """Receive hub multicast messages until stop_event is set.

        Re-opens the socket after errors. Runs in the calling thread.

        Args:
            on_message: Callable on_message(msg_dict, (ip, port)). Exceptions
                raised by it are trapped and logged.
            stop_event: threading.Event; set it to end the loop.
        """
        while not stop_event.is_set():
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("", MULTICAST_PORT))
                mreq = socket.inet_aton(MULTICAST_IP) + socket.inet_aton("0.0.0.0")
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
                sock.settimeout(1.0)
                while not stop_event.is_set():
                    try:
                        data, addr = sock.recvfrom(8192)
                    except socket.timeout:
                        continue
                    try:
                        msg = json.loads(data.decode("utf-8", "ignore"))
                    except ValueError:
                        continue
                    if not isinstance(msg, dict):
                        continue
                    self._note_gateway_message(msg, addr)
                    try:
                        on_message(msg, addr)
                    except Exception as exc:
                        self._log("error", "multicast handler error: {}".format(exc))
            except Exception as exc:
                if not stop_event.is_set():
                    self._log_limited("mcast", "multicast listener error: {}".format(exc))
            finally:
                if sock is not None:
                    try:
                        sock.close()
                    except Exception:
                        pass
            stop_event.wait(5.0)
# ---- END SHARED ----

PLUGIN_VERSION = "2.0.1"
HEARTBEAT_SECS = 2          # main-thread tick: drains events, schedules polls
RECONNECT_SECS = 60         # wait between failed connection attempts
THREAD_RESTART_SECS = 10    # minimum time between worker restarts
MAX_EVENTS_PER_TICK = 500

# slot, label, Domoticz.Device keyword arguments
SLOT_SPECS = (
    ("Pos", "Pos", {"Type": 244, "Subtype": 73, "Switchtype": 13}),
    ("Ang", "Angle", {"Type": 244, "Subtype": 73, "Switchtype": 13}),
    ("Bat", "Battery", {"TypeName": "Custom", "Options": {"Custom": "1;raw"}}),
    ("Cha", "Charging", {"TypeName": "Text"}),
    ("RSS", "RSSI", {"TypeName": "Custom", "Options": {"Custom": "1;dBm"}}),
)
SLOT_NAMES = tuple(s[0] for s in SLOT_SPECS)

# DeviceID is "<MAC>_<slot>" and must fit the 25 character Domoticz limit.
DEVICE_ID_RE = re.compile(r"^([0-9A-Fa-f]{16,21})_(Pos|Ang|Bat|Cha|RSS)$")

# Devices made by versions before 2.0 were named "<Label> <MAC>" and sat on
# units 5*n+1 .. 5*n+5 (n = index of the MAC in sorted order). Renamed ones
# can only be recognised by that unit layout.
LEGACY_NAME_RE = re.compile(r"(Pos|Angle|Battery|Charging|RSSI)\s+([0-9A-Fa-f]{16,})\s*$")
LEGACY_SLOT = {"Pos": "Pos", "Angle": "Ang", "Battery": "Bat", "Charging": "Cha", "RSSI": "RSS"}


def _to_int(value):
    """Convert a hub value to int, or None if that is not possible."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _clamp(value, lo=0, hi=100):
    """Limit value to the range lo..hi."""
    return max(lo, min(hi, value))


def _blind_nvalue(level):
    """nValue for a Blinds Percentage device: 0 open, 1 closed, 2 partial."""
    if level <= 0:
        return 0
    return 1 if level >= 100 else 2


class BasePlugin(object):
    """Domoticz plugin object for the Brel Home Hub.

    Threading model: Domoticz calls (Devices, Domoticz.Log, ...) are only
    made from the Domoticz plugin thread. Background threads (connect, poll,
    command worker, multicast listener) hand their results over through
    self.events and are drained in onHeartbeat. Commands travel the other
    way through self.cmds.

    Event tuples on self.events:
        ("log", level, text)
        ("devices", devices_dict)   hub connected / device list refreshed
        ("conn_failed",)            hub could not be reached
        ("hub_lost",)               poll could not reach the hub
        ("report", mac, data)       state of one shutter
    """

    def __init__(self):
        """Initialise empty state; real setup happens in onStart."""
        self.debug = False
        self.hub = None
        self.events = queue.Queue()
        self.cmds = queue.Queue()
        self.stop_evt = threading.Event()
        self.threads = {}
        self.thread_started = {}
        self.units = {}              # mac.lower() -> {slot: unit}
        self.state = "idle"          # idle | connecting | running
        self.next_connect = 0.0
        self.last_poll = 0.0
        self.poll_interval = 600
        self.cleanup_mode = 0        # 0 keep, 1 remove all legacy, 2 remove unused duplicates

    # ------------------------------------------------------------------
    # logging
    # ------------------------------------------------------------------
    def _qlog(self, level, msg):
        """Thread-safe log: queue the message for the Domoticz thread."""
        if level == "debug" and not self.debug:
            return
        self.events.put(("log", level, msg))

    @staticmethod
    def _emit(level, msg):
        """Write a log line. Only call from the Domoticz plugin thread.

        Domoticz already prefixes plugin messages with the hardware name.
        """
        if level == "error":
            Domoticz.Error(msg)
        elif level == "debug":
            Domoticz.Log("DEBUG: " + msg)
        else:
            Domoticz.Log(msg)

    # ------------------------------------------------------------------
    # parameters
    # ------------------------------------------------------------------
    @staticmethod
    def _param(name, default=""):
        """Read a plugin parameter as a stripped string."""
        try:
            value = Parameters.get(name, default)
        except Exception:
            value = default
        return str(value).strip() if value is not None else default

    def _int_param(self, name, default, lo, hi):
        """Read an integer parameter, falling back to default if invalid."""
        value = _to_int(self._param(name, str(default)))
        if value is None:
            return default
        return _clamp(value, lo, hi)

    # ------------------------------------------------------------------
    # Domoticz lifecycle
    # ------------------------------------------------------------------
    def onStart(self):
        """Read settings, start the command worker and connect to the hub."""
        try:
            Domoticz.Log("v{} starting".format(PLUGIN_VERSION))
            self.debug = (self._param("Mode2") == "1")
            self.cleanup_mode = self._int_param("Mode4", 0, 0, 2)
            self.poll_interval = self._int_param("Mode3", 600, 30, 86400)
            Domoticz.Heartbeat(HEARTBEAT_SECS)

            self.stop_evt.clear()
            self.events = queue.Queue()
            self.cmds = queue.Queue()
            self.threads = {}
            self.thread_started = {}
            self.state = "idle"
            self.next_connect = 0.0
            self.last_poll = 0.0

            self.hub = BrelHub(self._param("Address"), self._param("Password"),
                               self._param("Mode1"), self.debug, self._qlog)

            self._rebuild_unit_index()
            self._start_thread("cmd", self._cmd_worker)
            self._start_connect()
        except Exception as exc:
            Domoticz.Error("onStart failed: {}".format(exc))

    def onStop(self):
        """Stop all background threads."""
        try:
            Domoticz.Log("stopping")
            self.stop_evt.set()
            for name in list(self.threads):
                thread = self.threads.get(name)
                if thread is not None and thread.is_alive() and name != "connect":
                    thread.join(timeout=2.0)
            Domoticz.Log("stopped")
        except Exception as exc:
            Domoticz.Error("onStop error: {}".format(exc))

    def onHeartbeat(self):
        """Drain worker events, reconnect when needed and schedule polls."""
        try:
            self._drain_events()
            if self.stop_evt.is_set():
                return
            now = time.time()
            if self.state == "idle" and now >= self.next_connect:
                self._start_connect()
            elif self.state == "running":
                self._watchdog(now)
                if now - self.last_poll >= self.poll_interval and not self._alive("poll"):
                    self.last_poll = now
                    self._start_thread("poll", self._poll_worker)
        except Exception as exc:
            Domoticz.Error("onHeartbeat error: {}".format(exc))

    def onCommand(self, Unit, Command, Level, Hue):
        """Translate a Domoticz command to a hub command and queue it."""
        try:
            dev = Devices[Unit]
            mac, slot = self._identify(Unit)
            if mac is None:
                if self.state != "running":
                    Domoticz.Error("'{}': hub not connected yet; command ignored.".format(dev.Name))
                else:
                    Domoticz.Error("cannot link '{}' to a shutter. Set 'Legacy devices' to 'Remove ALL legacy "
                                   "devices' for one start to recreate it.".format(dev.Name))
                return
            if slot not in ("Pos", "Ang"):
                return
            cmd = str(Command).strip().lower()
            cmd = {"on": "open", "off": "close"}.get(cmd, cmd)
            self.cmds.put((mac, slot, cmd, Level))
        except Exception as exc:
            Domoticz.Error("onCommand error: {}".format(exc))

    # ------------------------------------------------------------------
    # threads
    # ------------------------------------------------------------------
    def _alive(self, name):
        """True if the named worker thread is running."""
        thread = self.threads.get(name)
        return thread is not None and thread.is_alive()

    def _start_thread(self, name, target):
        """Start a daemon worker thread unless one with that name is alive."""
        if self._alive(name):
            return
        thread = threading.Thread(target=self._guard, args=(name, target), name="Brel-" + name)
        thread.daemon = True
        self.threads[name] = thread
        self.thread_started[name] = time.time()
        thread.start()

    def _guard(self, name, target):
        """Run a worker and trap any unexpected exception."""
        try:
            target()
        except Exception as exc:
            self._qlog("error", "worker '{}' crashed: {}".format(name, exc))
            if name == "connect":
                self.events.put(("conn_failed",))

    def _watchdog(self, now):
        """Restart the command worker or multicast listener if they died."""
        for name, target in (("cmd", self._cmd_worker), ("mcast", self._mcast_worker)):
            if not self._alive(name) and now - self.thread_started.get(name, 0.0) >= THREAD_RESTART_SECS:
                self._start_thread(name, target)

    def _start_connect(self):
        """Begin a background connection attempt."""
        if self._alive("connect"):
            return
        self.state = "connecting"
        self._start_thread("connect", self._connect_worker)

    def _connect_worker(self):
        """Background: find the hub, list devices, build the token."""
        devices = self.hub.connect()
        if devices is None:
            self._qlog("error", "hub not reachable; retrying in {} s".format(RECONNECT_SECS))
            self.events.put(("conn_failed",))
        else:
            self.events.put(("devices", devices))

    def _mcast_worker(self):
        """Background: listen for multicast reports."""
        self.hub.listen_multicast(self._on_multicast, self.stop_evt)

    def _on_multicast(self, msg, addr):
        """Background: queue shutter reports received via multicast."""
        if msg.get("msgType") != "Report":
            return
        mac = msg.get("mac")
        data = msg.get("data")
        if BrelHub.is_real_mac(mac) and isinstance(data, dict):
            self.events.put(("report", mac, data))

    def _poll_worker(self):
        """Background: refresh the device list and read every shutter."""
        hub = self.hub
        devices = hub.get_device_list()
        if devices is None:
            self.events.put(("hub_lost",))
            return
        if set(m.lower() for m in devices) - set(self.units):
            self.events.put(("devices", devices))     # new shutter paired
        fails = 0
        for mac in sorted(devices):
            if self.stop_evt.is_set():
                return
            resp = hub.get_status(mac)
            data = resp.get("data") if resp else None
            if isinstance(data, dict):
                fails = 0
                self.events.put(("report", mac, data))
            else:
                fails += 1
                if fails >= 3:
                    self._qlog("error", "poll aborted after 3 failed reads")
                    break

    def _cmd_worker(self):
        """Background: execute queued commands one at a time."""
        while not self.stop_evt.is_set():
            try:
                item = self.cmds.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                self._exec_command(*item)
            except Exception as exc:
                self._qlog("error", "command error: {}".format(exc))

    def _exec_command(self, mac, slot, cmd, level):
        """Background: send one command to the hub.

        Pos: open / close / stop / set level (position).
        Ang: set level (angle), stop; open and close map to 0 and ANGLE_MAX.
        """
        hub = self.hub
        inverted = hub.position_inverted.get(mac, False)
        open_pos = 100 if inverted else 0
        close_pos = 0 if inverted else 100

        if cmd == "stop":
            hub.set_value(mac, O=OP_STOP)
            return

        if slot == "Ang":
            if cmd == "set level":
                angle = int(round(_clamp(_to_int(level) or 0) * ANGLE_MAX / 100.0))
            elif cmd == "open":
                angle = 0
            elif cmd == "close":
                angle = ANGLE_MAX
            else:
                self._qlog("debug", "unhandled angle command '{}'".format(cmd))
                return
            hub.set_value(mac, A=angle)
            return

        if cmd == "open":
            hub.set_value(mac, P=open_pos, O=OP_OPEN)
        elif cmd == "close":
            hub.set_value(mac, P=close_pos, O=OP_CLOSE)
        elif cmd == "set level":
            lvl = _to_int(level)
            lvl = 1 if lvl is None else lvl
            if lvl <= 0:
                hub.set_value(mac, P=open_pos, O=OP_OPEN)
            elif lvl >= 100:
                hub.set_value(mac, P=close_pos, O=OP_CLOSE)
            else:
                hub.set_value(mac, P=hub.denormalize_position(mac, lvl), O=OP_POSITION)
        else:
            self._qlog("debug", "unhandled command '{}'".format(cmd))

    # ------------------------------------------------------------------
    # events (Domoticz thread)
    # ------------------------------------------------------------------
    def _drain_events(self):
        """Process queued worker events on the Domoticz thread."""
        for _ in range(MAX_EVENTS_PER_TICK):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                return
            try:
                kind = event[0]
                if kind == "log":
                    self._emit(event[1], event[2])
                elif kind == "report":
                    self._apply_report(event[1], event[2])
                elif kind == "devices":
                    self._on_devices(event[1])
                elif kind == "conn_failed":
                    self.state = "idle"
                    self.next_connect = time.time() + RECONNECT_SECS
                elif kind == "hub_lost":
                    Domoticz.Error("hub not answering; reconnecting")
                    self.state = "idle"
                    self.next_connect = time.time() + 5.0
            except Exception as exc:
                Domoticz.Error("event error: {}".format(exc))

    def _on_devices(self, devices):
        """Run the optional legacy cleanup, create missing devices, go live."""
        if self.cleanup_mode:
            self._cleanup_legacy_devices(self.cleanup_mode)
            self.cleanup_mode = 0
        self.create_devices(devices)
        self._rebuild_unit_index()
        if self.state != "running":
            Domoticz.Log("connected to hub {} with {} shutter(s)".format(self.hub.host, len(devices)))
            self.last_poll = 0.0       # read all states right away
        self.state = "running"
        self._start_thread("mcast", self._mcast_worker)

    # ------------------------------------------------------------------
    # device handling (Domoticz thread)
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_device_id(device_id):
        """Split "<MAC>_<slot>" into (mac, slot); (None, None) if no match."""
        match = DEVICE_ID_RE.match(device_id or "")
        if not match:
            return None, None
        return match.group(1), match.group(2)

    def _identify(self, unit):
        """Return (mac, slot) for a Domoticz unit, or (None, None).

        Order of recognition: DeviceID "<MAC>_<slot>" (new devices), legacy
        name "<Label> <MAC>", and finally the legacy unit layout for renamed
        devices (needs the hub device list, so it only works once connected).
        """
        dev = Devices.get(unit)
        if dev is None:
            return None, None
        mac, slot = self._parse_device_id(dev.DeviceID)
        if mac is not None:
            return mac, slot
        found = LEGACY_NAME_RE.search(dev.Name or "")
        if found:
            return found.group(2), LEGACY_SLOT[found.group(1)]
        macs = sorted(self.hub.devices) if self.hub and self.hub.devices else []
        idx, off = divmod(int(unit) - 1, len(SLOT_NAMES))
        if 0 <= idx < len(macs):
            return macs[idx], SLOT_NAMES[off]
        return None, None

    def _rebuild_unit_index(self):
        """Rebuild the mac -> {slot: unit} index from the Domoticz devices.

        Devices with a MAC DeviceID win over legacy ones; among legacy
        devices the lowest unit wins (the original, not a duplicate).
        """
        index = {}
        order = sorted(Devices.keys(), key=lambda u: (self._parse_device_id(Devices[u].DeviceID)[0] is None, u))
        for unit in order:
            mac, slot = self._identify(unit)
            if mac is not None:
                index.setdefault(mac.lower(), {}).setdefault(slot, unit)
        self.units = index
        if self.debug:
            self._emit("debug", "unit index holds {} shutter(s)".format(len(index)))

    def _cleanup_legacy_devices(self, mode):
        """Delete legacy devices (those without a '<MAC>_<slot>' DeviceID).

        mode 1: delete every legacy device (they are recreated afterwards).
        mode 2: delete only legacy devices that are not the adopted original
                of a shutter slot, i.e. the duplicates.
        """
        self._rebuild_unit_index()
        keep = set()
        if mode == 2:
            for slots in self.units.values():
                keep.update(slots.values())
        victims = [u for u in sorted(Devices.keys())
                   if DEVICE_ID_RE.match(Devices[u].DeviceID or "") is None and u not in keep]
        removed = 0
        for unit in victims:
            try:
                if unit in Devices:
                    Devices[unit].Delete()
                    removed += 1
            except Exception as exc:
                Domoticz.Error("cleanup: cannot delete unit {}: {}".format(unit, exc))
        Domoticz.Log("cleanup: removed {} legacy device(s), kept {}. Set 'Legacy devices' back to Keep."
                     .format(removed, len(keep)))
        self._rebuild_unit_index()

    def create_devices(self, devices):
        """Create the shutter devices that do not exist yet (also not as legacy).

        Existing devices are matched through the unit index (DeviceID, legacy
        name or legacy layout), so restarts and renames cannot create
        duplicates.
        """
        self._rebuild_unit_index()
        used = set(Devices.keys())
        created = 0
        for mac in sorted(devices):
            if len(mac) > 21:
                Domoticz.Error("MAC {} too long for a DeviceID; skipped".format(mac))
                continue
            for slot, label, kwargs in SLOT_SPECS:
                if slot in self.units.get(mac.lower(), {}):
                    continue
                unit = next((u for u in range(1, 256) if u not in used), None)
                if unit is None:
                    Domoticz.Error("no free Domoticz unit left (limit 255); {} shutters need {}. Set 'Legacy "
                                   "devices' to 'Remove unused duplicates' for one start."
                                   .format(len(devices), 5 * len(devices)))
                    return
                try:
                    Domoticz.Device(Name="{} {}".format(label, mac), Unit=unit,
                                    DeviceID="{}_{}".format(mac, slot), **kwargs).Create()
                    used.add(unit)
                    created += 1
                except Exception as exc:
                    Domoticz.Error("cannot create {} {}: {}".format(label, mac, exc))
        if created:
            Domoticz.Log("created {} device(s)".format(created))
            self._rebuild_unit_index()

    def _update(self, mac, slot, nvalue, svalue):
        """Update one Domoticz device addressed by MAC and slot."""
        unit = self.units.get(mac.lower(), {}).get(slot)
        if unit is None or unit not in Devices:
            return
        try:
            Devices[unit].Update(nValue=nvalue, sValue=svalue)
        except Exception as exc:
            Domoticz.Error("update of unit {} failed: {}".format(unit, exc))

    def _apply_report(self, mac, data):
        """Copy a hub status report into the Domoticz devices."""
        if mac.lower() not in self.units:
            if self.debug:
                self._emit("debug", "report for unknown shutter {} ignored".format(mac))
            return
        inverted = self.hub.position_inverted.get(mac, False) if self.hub else False

        pos = _to_int(data.get("currentPosition"))
        if pos is not None:
            level = _clamp(pos)
            level = 100 - level if inverted else level
            self._update(mac, "Pos", _blind_nvalue(level), str(level))

        angle = _to_int(data.get("currentAngle"))
        if angle is not None:
            level = _clamp(int(round(_clamp(angle, 0, ANGLE_MAX) * 100.0 / ANGLE_MAX)))
            self._update(mac, "Ang", _blind_nvalue(level), str(level))

        for slot, key in (("Bat", "batteryLevel"), ("Cha", "chargingState"), ("RSS", "RSSI")):
            if data.get(key) is not None:
                self._update(mac, slot, 0, str(data[key]))


_plugin = BasePlugin()


def onStart():
    """Domoticz hook."""
    _plugin.onStart()


def onStop():
    """Domoticz hook."""
    _plugin.onStop()


def onCommand(Unit, Command, Level, Hue):
    """Domoticz hook."""
    _plugin.onCommand(Unit, Command, Level, Hue)


def onHeartbeat():
    """Domoticz hook."""
    _plugin.onHeartbeat()
