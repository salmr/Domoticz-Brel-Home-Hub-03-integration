#!/usr/bin/env python3
# -*- coding: ascii -*-
"""brel_lib - UDP client library for the Brel Home Hub (HUB-03/2).

The hub speaks a JSON-over-UDP protocol:

* Unicast, port 32100: request/response (GetDeviceList, ReadDevice, WriteDevice).
* Multicast 238.0.0.18, port 32101: unsolicited Report / Heartbeat messages.

Write commands need an AccessToken. It is the hub token (returned by
GetDeviceList) encrypted with AES-128-ECB (no padding) using the 16-byte
hub KEY, hex encoded in upper case. The KEY is shown in the Brel app by
tapping 5 times on "Version 1.x.x(x)".

Example:
    hub = BrelHub("192.168.1.50", "0123456789ABCDEF")
    devices = hub.connect()          # find hub, list shutters, build token
    mac = sorted(devices)[0]
    hub.set_value(mac, P=50)         # move to 50 percent
    print(hub.get_status(mac))

Command line test tool:
    python brel_lib.py --key 0123456789ABCDEF list
    python brel_lib.py --key 0123456789ABCDEF --host 192.168.1.50 pos MAC 40

Requirements: Python 3.5+, and PyCryptodome (python3-pycryptodome) unless a
pre-generated AccessToken is supplied.

The block between the SHARED markers is copied verbatim into the Domoticz
plugin (plugin.py) so that both files always use identical hub logic.
"""

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


def _main(argv=None):
    """Small command line tool to test a hub without Domoticz."""
    import argparse
    ap = argparse.ArgumentParser(description="Brel Home Hub test tool")
    ap.add_argument("--host", help="hub IP (omit to discover)")
    ap.add_argument("--key", help="16 ASCII or 32 HEX char hub key")
    ap.add_argument("--token", help="pre-generated AccessToken")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("action", choices=["list", "status", "open", "close", "stop", "pos", "angle"])
    ap.add_argument("mac", nargs="?")
    ap.add_argument("value", nargs="?", type=int)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format="%(name)s: %(message)s")
    hub = BrelHub(args.host, args.key, args.token, args.debug)
    devices = hub.connect()
    if devices is None:
        print("Hub not reachable")
        return 2
    if args.action == "list":
        for mac in sorted(devices):
            print(mac, devices[mac].get("deviceType"))
        return 0
    if not args.mac:
        print("MAC required for this action")
        return 2
    if args.action == "status":
        print(json.dumps(hub.get_status(args.mac), indent=2))
        return 0
    if args.action in ("pos", "angle") and args.value is None:
        print("VALUE required for this action")
        return 2
    if args.action == "open":
        resp = hub.set_value(args.mac, P=0, O=OP_OPEN)
    elif args.action == "close":
        resp = hub.set_value(args.mac, P=100, O=OP_CLOSE)
    elif args.action == "stop":
        resp = hub.set_value(args.mac, O=OP_STOP)
    elif args.action == "pos":
        resp = hub.set_value(args.mac, P=args.value, O=OP_POSITION)
    else:
        resp = hub.set_value(args.mac, A=args.value)
    print(json.dumps(resp, indent=2))
    return 0 if resp else 1


if __name__ == "__main__":
    raise SystemExit(_main())
