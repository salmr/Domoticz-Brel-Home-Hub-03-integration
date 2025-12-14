#!/usr/bin/env python3
# Stand-alone Brel Home Hub 03/2 monitor & controller with network discovery
# https://www.brel-home.nl/nl/pro/producten/smart-home/716/hub-032
# To debug for Domoticz made by Salmr199
# No warranty, it comes as it comes
# Requires: pip install pycryptodome

# To retreive the KEY from your Brel Hub:
# Get the KEY by quickly tapping 5 times on "Version 1.x.x(x)" in your Brel SmartPhone app. 
# You'll get the 16-byte KEY in a popup, which you can then copy/paste. 
# On Android tap next to the profile picture instead of the version-number.

import socket
import json
import time
import threading
import binascii
from datetime import datetime
from Crypto.Cipher import AES
import re
import ipaddress

MULTICAST_IP = "238.0.0.18"
UNICAST_PORT = 32100
MULTICAST_PORT = 32101
SUBNET_IP = "192.168.1"
KEY  = "XXXXXXXXXXXXXXXX" #Key must be exactly 16 bytes

# ---------------- Network scan for Brel Hubs ----------------
def scan_for_brel_hub(subnet=SUBNET_IP + ".0/24", timeout=0.5):
    """Scan local network for a Brel Hub, return first IP found"""
    print(f"🔍 Scanning network {subnet} for Brel Hubs...")
    for ip in ipaddress.IPv4Network(subnet):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        try:
            msg = json.dumps({"msgType": "GetDeviceList", "msgID": "1010"}).encode()
            sock.sendto(msg, (str(ip), UNICAST_PORT))
            data, addr = sock.recvfrom(2048)
            response = json.loads(data.decode())
            if "data" in response:
                print(f"Found Brel Hub at {ip}")
                return str(ip)
        except:
            pass
        finally:
            sock.close()
    print("No Brel Hubs found on the network")
    return None
      
# ---------------- BrelHub class ----------------
class BrelHub:
    def __init__(self, host, key, secret=None):
        self.host = host
        self.key = key.encode()
        self.secret = secret
        self.devices = {}
        self.access_token = None

    # ---------- Communication helpers ----------
    def _timestamp(self, mac=None):
        base = '101'
        if mac:
            base = re.findall(r'\d+', mac)[-1]
        ms = int((datetime.utcnow() - datetime(1970,1,1)).total_seconds()*1000)
        return f"{base}{ms}"

    def _send_request(self, payload):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(5)
        sock.sendto(bytes(json.dumps(payload),'utf8'), (self.host, UNICAST_PORT))
        try:
            reply, addr = sock.recvfrom(2048)
            return json.loads(reply.decode())
        except socket.timeout:
            print("Timeout: No reply from Brel hub")
            return None
        finally:
            sock.close()
            
    def mac_from_selection(self, selection):
        try:
            sel = int(selection)
            if sel == 1:
                print("Device 1 is the hub and cannot be controlled")
                return None
            idx = sel - 2
            return self.device_index[idx]
        except (ValueError, IndexError):
            print("Invalid device number")
            return None


    # ---------- API methods ----------
    def get_device_list(self):
        msg = {"msgType": "GetDeviceList", "msgID": self._timestamp()}
        data = self._send_request(msg)
        if not data:
            return None

        self.devices = {}
        self.device_index = []

        print("\n📡 Devices discovered:")
        for i, dev in enumerate(data.get("data", []), start=1):
            mac = dev["mac"]
            self.devices[mac] = dev

            name = dev.get("name") or f"Device {i}"
            dtype = dev.get("deviceType")

            if i == 1:
                print(f"  {i}) {name} [{mac}] (HUB)")
                continue  #Do not add hub to selectable list

            self.device_index.append(mac)
            print(f"  {i}) {name} [{mac}] (Type: {dtype})")

        self.gateway = data
        return self.devices

    def generate_access_token(self):
        if self.secret:
            self.access_token = self.secret
            print("Using pre-generated secret token")
            return self.access_token
        try:
            token = self.gateway["token"].encode()
            cipher = AES.new(self.key, AES.MODE_ECB)
            encrypted = cipher.encrypt(token)
            self.access_token = binascii.hexlify(encrypted).decode().upper()
            print(f"AccessToken generated: {self.access_token}")
            return self.access_token
        except Exception as e:
            print("Failed to generate AccessToken:", e)
            return None

    def get_status(self, mac):
        if mac not in self.devices:
            print("Unknown device")
            return None
        msg = {
            "msgType": "ReadDevice",
            "mac": mac,
            "deviceType": self.devices[mac]["deviceType"],
            "msgID": self._timestamp(mac)
        }
        data = self._send_request(msg)
        if not data:
            return None
        pos = data["data"]["currentPosition"]
        angle = data["data"]["currentAngle"]
        print(f"\nStatus {mac}")
        print(f"  Position: {pos}%")
        print(f"  Angle:    {angle}°")
        return data

    def set_value(self, mac, P=None, A=None):
        if mac not in self.devices:
            print("Unknown device")
            return None
        payload = {}
        if P is not None: payload["targetPosition"] = int(P)
        if A is not None: payload["targetAngle"] = int(A)
        msg = {
            "msgType": "WriteDevice",
            "mac": mac,
            "deviceType": self.devices[mac]["deviceType"],
            "AccessToken": self.access_token,
            "data": payload,
            "msgID": self._timestamp(mac)
        }
        print(f"➡ Sending command: {payload}")
        ack = self._send_request(msg)
        print("✔ Command acknowledged:", ack)
        return ack
        
    def poll_all_devices(self):
        print("\nPolling all devices...")
        results = {}
        for mac in self.device_index:
            msg = {
                "msgType": "ReadDevice",
                "mac": mac,
                "deviceType": self.devices[mac]["deviceType"],
                "msgID": self._timestamp(mac)
            }
            data = self._send_request(msg)
            if data:
                results[mac] = data
                print(f"Polled {mac}")
            else:
                print(f"Failed to poll {mac}")
            time.sleep(0.1)  # be nice to the hub
        return results
        
    def print_device_table(self, results):
        if not results:
            print("No data to display")
            return

        # Resolve friendly names
        names = []
        for mac in results:
            dev = self.devices.get(mac, {})
            names.append(dev.get("name") or mac[-5:])

        # Collect all possible fields
        fields = set()
        for data in results.values():
            fields.update(data.get("data", {}).keys())

        fields = sorted(fields)

        # Column widths
        col_widths = [max(15, len(name)) for name in names]
        field_width = 15

        def sep(left, mid, right):
            print(left + mid.join("─" * w for w in [field_width] + col_widths) + right)

        def row(values):
            print(
                "│"
                + f"{values[0]:<{field_width}}│"
                + "│".join(f"{v:<{w}}" for v, w in zip(values[1:], col_widths))
                + "│"
            )

        # Header
        sep("┌", "┬", "┐")
        row(["Field"] + names)
        sep("├", "┼", "┤")

        # Rows
        for field in fields:
            values = []
            for mac in results:
                values.append(str(results[mac]["data"].get(field, "-")))
            row([field] + values)

        sep("└", "┴", "┘")

    def poll_device_raw(self, mac):
        if mac not in self.devices:
            print("Unknown device")
            return None

        msg = {
            "msgType": "ReadDevice",
            "mac": mac,
            "deviceType": self.devices[mac]["deviceType"],
            "msgID": self._timestamp(mac)
        }

        data = self._send_request(msg)
        if not data:
            print("Failed to poll device")
            return None

        return data


# ---------------- Multicast listener ----------------
def listen_multicast(callback):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.bind(("", MULTICAST_PORT))
    mreq = socket.inet_aton(MULTICAST_IP) + socket.inet_aton("0.0.0.0")
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    print("\nListening for Brel Report messages...\n")
    while True:
        data, addr = sock.recvfrom(2048)
        try:
            msg = json.loads(data.decode())
            if msg.get("msgType") == "Report":
                callback(msg)
        except:
            pass

# ---------------- Main Program ----------------
if __name__ == "__main__":
    print("=== Brel Hub Network Scanner & Monitor ===")
    
    # Scan local network and return first found hub
    HOST = scan_for_brel_hub(SUBNET_IP + ".0/24")
    if not HOST:
        exit(0)

    hub = BrelHub(HOST, KEY)

    # Start multicast listener in background
    threading.Thread(
        target=listen_multicast,
        args=(lambda m: print(
            f"REPORT {m['mac']}: Pos={m['data']['currentPosition']}%, "
            f"Angle={m['data']['currentAngle']}°"
        ),),
        daemon=True
    ).start()

    # Original interactive program
    hub.get_device_list()
    hub.generate_access_token()

    while True:
        print("\nCommands:")
        print(" 1 = List devices")
        print(" 2 = Read status")
        print(" 3 = Set value")
        print(" 4 = Poll all devices")
        print(" 5 = Show JSON of device")
        print(" 0 = Quit")
        choice = input("> ")

        if choice == "1":
            hub.get_device_list()
        elif choice == "2":
            sel = input("Device number: ")
            mac = hub.mac_from_selection(sel)
            if mac:
                hub.get_status(mac)
        elif choice == "3":
            sel = input("Device number: ")
            mac = hub.mac_from_selection(sel)
            if not mac:
                continue
            P = input("Position (0–100 or blank): ")
            A = input("Angle (0–180 or blank): ")
            hub.set_value(
                mac,
                P if P.strip() != '' else None,
                A if A.strip() != '' else None
            )
        elif choice == "4":
            results = hub.poll_all_devices()
            hub.print_device_table(results)
        elif choice == "5":
            sel = input("Device number: ")
            mac = hub.mac_from_selection(sel)
            if not mac:
                continue
            data = hub.poll_device_raw(mac)
            if data:
                print(json.dumps(data, indent=2))

        elif choice == "0":
            break
        else:
            print("Unknown command")
