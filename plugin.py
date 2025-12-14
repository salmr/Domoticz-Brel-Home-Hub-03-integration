# plugin.py - Brel Home Hub Domoticz Plugin (Dynamic Discovery, Broadcast + Multicast)
# start: https://github.com/superjunky/Domoticz-Brel-Plugin.git 

"""
<plugin key="Brel-Home-Hub-03" name="Brel Home Hub 03" author="Salmr199" version="1.2.0">
    <description>
        <![CDATA[
        <div style="font-family: Arial, sans-serif; line-height: 1.5;">
            <h2>Brel Home Hub 03 Version 1.2.0</h2>

            <p>Domoticz plugin with dynamic hub discovery via broadcast and multicast, stable UDP control. Tested for 
            <a href="https://www.brel-home.nl/nl/pro/producten/smart-home/716/hub-032" target="_blank" rel="noopener noreferrer">Brel HUB-03/2</a>.</p>

            <h3>Acknowledgements</h3>
            <p>Thanks to Superjunky for all the hard work that was used as a starting point for this project.</p>

            <h3>Features</h3>
            <ul>
                <li>Creates devices for every device configured in your Brel Home Hub.</li>
                <li>Creates separate devices for Position and Angle.</li>            
                <li>Polls each device to obatin latest information.</li>
            </ul>

            <h3>Configuration</h3>
            <ul>
                <li>You can enter the IP of your Brel Home Hub, but the system will scan the network for an active Brel HUB.</li>
                <li>Enter the KEY of your Brel Home Hub. Get the KEY by quickly tapping 5 times on "Version 1.x.x(x)" in your Brel SmartPhone app. You'll get the 16-byte KEY in a popup, which you can then copy/paste. On Android, tap next to the profile picture instead of the version number.</li>
                <li>Don't forget to let Domoticz allow new devices before you activate this plugin!</li>                
                <li>If you have trouble loading your pycrypto module, you can manually add an AccessToken in your settings. Get it by generating it at 
                    <a href="https://www.devglan.com/online-tools/aes-encryption-decryption" target="_blank" rel="noopener noreferrer">AES Encryption/Decryption Tool</a>.
                </li>
            </ul>
        </div>
        ]]>
    </description>

    <params>
        <param field="Address" label="Last known Hub IP (optional)" width="200px"/>
        <param field="Password" label="Key (16-byte)" width="200px" required="true"/>
        <param field="Mode1" label="Pre-generated AccessToken (optional)" width="200px"/>
        <param field="Mode2" label="Debug (0/1)" width="50px" default="0"/>
    </params>
</plugin>

"""
import Domoticz
import socket
import json
import threading
import time
import binascii
import re
from datetime import datetime
from Crypto.Cipher import AES

UNICAST_PORT = 32100
MULTICAST_PORT = 32101
MULTICAST_IP = "238.0.0.18"

# ---------------------------
# BrelHub Implementation
# ---------------------------
class BrelHub:
    def __init__(self, host, key, secret=None, debug=False):
        self.host = host
        self.key = key.encode()
        self.secret = secret
        self.debug = debug
        self.devices = {}
        self.gateway = None
        self.access_token = None

    def _timestamp(self, mac=None):
        base = "101"
        if mac:
            nums = re.findall(r"\d+", mac)
            if nums:
                base = nums[-1]
        ms = int((datetime.utcnow() - datetime(1970, 1, 1)).total_seconds() * 1000)
        return f"{base}{ms}"

    def discover_hub(self, timeout=5):
        # Broadcast probe
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(("", 32102))
            sock.settimeout(timeout)
            probe = {"msgType": "GetDeviceList", "msgID": self._timestamp()}
            sock.sendto(json.dumps(probe).encode(), ("255.255.255.255", UNICAST_PORT))
            data, addr = sock.recvfrom(4096)
            self.host = addr[0]
            Domoticz.Log(f"Brel hub discovered via broadcast at {self.host}")
            sock.close()
            return self.host
        except:
            pass

        # Multicast listen
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", MULTICAST_PORT))
            mreq = socket.inet_aton(MULTICAST_IP) + socket.inet_aton("0.0.0.0")
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            sock.settimeout(timeout)
            start = time.time()
            while time.time() - start < timeout:
                try:
                    data, addr = sock.recvfrom(4096)
                    msg = json.loads(data.decode("utf-8", errors="ignore"))
                    if msg.get("msgType") in ("Gateway", "Report"):
                        self.host = addr[0]
                        Domoticz.Log(f"Brel hub discovered via multicast at {self.host}")
                        sock.close()
                        return self.host
                except socket.timeout:
                    pass
            sock.close()
        except:
            pass
        return None

    def _send(self, payload, timeout=5):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("", 32102))
        sock.settimeout(timeout)
        try:
            if self.debug:
                Domoticz.Log(f"Brel TX → {payload['msgType']} @ {self.host}")
            sock.sendto(json.dumps(payload).encode("utf-8"), (self.host, UNICAST_PORT))
            data, addr = sock.recvfrom(4096)
            if self.debug:
                Domoticz.Log(f"Brel RX ← {addr}")
            return json.loads(data.decode("utf-8", errors="ignore"))
        except Exception as e:
            Domoticz.Error(f"Brel UDP error ({payload['msgType']}): {e}")
            return None
        finally:
            sock.close()

    def get_device_list(self):
        msg = {"msgType": "GetDeviceList", "msgID": self._timestamp()}
        data = self._send(msg)
        if not data:
            return None
        self.gateway = data
        self.devices = {d["mac"]: d for d in data.get("data", [])}
        Domoticz.Log(f"Brel: {len(self.devices)} devices discovered")
        return self.devices

    def generate_access_token(self):
        if self.secret:
            self.access_token = self.secret
            return self.access_token
        token = self.gateway["token"].encode()
        cipher = AES.new(self.key, AES.MODE_ECB)
        encrypted = cipher.encrypt(token)
        self.access_token = binascii.hexlify(encrypted).decode().upper()
        return self.access_token

    def set_value(self, mac, P=None, A=None):
        payload = {}
        if P is not None:
            payload["targetPosition"] = int(P)
        if A is not None:
            payload["targetAngle"] = int(A)
        msg = {
            "msgType": "WriteDevice",
            "mac": mac,
            "deviceType": self.devices[mac]["deviceType"],
            "AccessToken": self.access_token,
            "data": payload,
            "msgID": self._timestamp(mac)
        }
        return self._send(msg)

    def get_status(self, mac):
        msg = {
            "msgType": "ReadDevice",
            "mac": mac,
            "deviceType": self.devices[mac]["deviceType"],
            "msgID": self._timestamp(mac)
        }
        return self._send(msg)

# ---------------------------
# Domoticz Plugin
# ---------------------------
class BasePlugin:
    def onStart(self):
        Domoticz.Log("Brel Plugin starting")
        self.debug = Parameters["Mode2"] == "1"
        last_ip = Parameters["Address"]
        self.last_poll = 0  # last full poll timestamp

        self.hub = BrelHub(
            host=last_ip,
            key=Parameters["Password"],
            secret=Parameters.get("Mode1"),
            debug=self.debug
        )

        if not self.hub.discover_hub():
            if last_ip:
                Domoticz.Log(f"Using fallback Brel hub IP {last_ip}")
            else:
                Domoticz.Error("Brel hub not discovered and no fallback IP set")
                return

        devices = self.hub.get_device_list()
        if not devices:
            Domoticz.Error("Brel: Failed to get device list")
            return

        self.hub.generate_access_token()

        # Create Domoticz devices
        for idx, mac in enumerate(devices, start=1):
            base = (idx - 1) * 5 + 1
            if len(mac) < 15:
                Domoticz.Device(Name=f" {mac}", Unit=base, Type=243, Subtype=24).Create()
            else:
                Domoticz.Device(Name=f"Pos {mac}", Unit=base, Type=244, Subtype=73, Switchtype=13).Create()
                Domoticz.Device(Name=f"Angle {mac}", Unit=base + 1, Type=244, Subtype=73, Switchtype=13).Create()
                Domoticz.Device(Name=f"Battery {mac}", Unit=base + 2, Type=243, Subtype=0, Switchtype=0).Create()
                Domoticz.Device(Name=f"Charging {mac}", Unit=base + 3, Type=243, Subtype=0, Switchtype=0).Create()
                Domoticz.Device(Name=f"RSSI {mac}", Unit=base + 4, Type=243, Subtype=0, Switchtype=0).Create()

        # Start multicast listener
        self.mcast_thread = threading.Thread(target=self.listen_multicast, daemon=True)
        self.mcast_thread.start()

    def listen_multicast(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", MULTICAST_PORT))
            mreq = socket.inet_aton(MULTICAST_IP) + socket.inet_aton("0.0.0.0")
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

            while True:
                try:
                    data, _ = sock.recvfrom(4096)
                    msg = json.loads(data.decode("utf-8", errors="ignore"))
                    if msg.get("msgType") != "Report":
                        continue
                    mac = msg.get("mac")
                    d = msg.get("data", {})

                    pos = d.get("currentPosition")
                    angle = d.get("currentAngle")
                    battery = d.get("batteryLevel")
                    charging = d.get("chargingState")
                    rssi = d.get("RSSI")

                    for unit, dev in Devices.items():
                        if dev.Name.endswith(mac):
                            if "Pos" in dev.Name and pos is not None:
                                dev.Update(0, str(pos))
                            elif "Angle" in dev.Name and angle is not None:
                                dev.Update(0, str(angle))
                            elif "Battery" in dev.Name and battery is not None:
                                dev.Update(0, str(battery))
                            elif "Charging" in dev.Name and charging is not None:
                                dev.Update(0, str(charging))
                            elif "RSSI" in dev.Name and rssi is not None:
                                dev.Update(0, str(rssi))
                except Exception as e:
                    Domoticz.Error(f"Multicast listener error: {e}")
                    time.sleep(1)
        except Exception as e:
            Domoticz.Error(f"Multicast listener failed: {e}")

    def onCommand(self, Unit, Command, Level, Hue):
        threading.Thread(target=self._handle_command, args=(Unit, Command, Level), daemon=True).start()

    def _handle_command(self, Unit, Command, Level):
        try:
            dev = Devices[Unit]
            mac = dev.Name.split()[-1]
            if Command == "Set Level":
                if "Pos" in dev.Name:
                    self.hub.set_value(mac, P=Level)
                elif "Angle" in dev.Name:
                    self.hub.set_value(mac, A=Level)
        except Exception as e:
            Domoticz.Error(f"Command error: {e}")

    def onHeartbeat(self):
        now = time.time()
        # Poll all devices every 10 minutes (600 sec)
        if now - self.last_poll >= 600:
            self.last_poll = now
            threading.Thread(target=self.poll_all_devices, daemon=True).start()

    def poll_all_devices(self):
        try:
            for mac, dev_info in self.hub.devices.items():
                data = self.hub.get_status(mac)
                if not data:
                    continue
                pos = data["data"].get("currentPosition")
                angle = data["data"].get("currentAngle")
                battery = data["data"].get("batteryLevel")
                charging = data["data"].get("chargingState")
                rssi = data["data"].get("RSSI")

                for unit, dev in Devices.items():
                    if dev.Name.endswith(mac):
                        if "Pos" in dev.Name and pos is not None:
                            dev.Update(0, str(pos))
                        elif "Angle" in dev.Name and angle is not None:
                            dev.Update(0, str(angle))
                        elif "Battery" in dev.Name and battery is not None:
                            dev.Update(0, str(battery))
                        elif "Charging" in dev.Name and charging is not None:
                            dev.Update(0, str(charging))
                        elif "RSSI" in dev.Name and rssi is not None:
                            dev.Update(0, str(rssi))
        except Exception as e:
            Domoticz.Error(f"Error polling devices: {e}")

    def onStop(self):
        Domoticz.Log("Brel Plugin stopped")

# ---------------------------
# Domoticz hooks
# ---------------------------
_plugin = BasePlugin()
def onStart(): _plugin.onStart()
def onStop(): _plugin.onStop()
def onCommand(Unit, Command, Level, Hue): _plugin.onCommand(Unit, Command, Level, Hue)
def onHeartbeat(): _plugin.onHeartbeat()

