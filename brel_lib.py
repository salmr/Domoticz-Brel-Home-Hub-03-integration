# brel_lib.py - Library for Brel Home Hub plugin
# Handles UDP requests and AES token generation
import socket
import json
import binascii
from datetime import datetime
from Crypto.Cipher import AES
import re

MULTICAST_IP = "238.0.0.18"
UNICAST_PORT = 32100
MULTICAST_PORT = 32101

class BrelHub:
    def __init__(self, host, key, secret=None):
        self.host = host
        self.key = key.encode() if isinstance(key, str) else key
        self.secret = secret
        self.devices = {}
        self.gateway = None
        self.access_token = None

    def _timestamp(self, mac=None):
        base = '101'
        if mac:
            nums = re.findall(r'\d+', mac)
            if nums: base = nums[-1]
        ms = int((datetime.utcnow() - datetime(1970,1,1)).total_seconds() * 1000)
        return f"{base}{ms}"

    def _send_request(self, payload, timeout=3):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        try:
            sock.sendto(bytes(json.dumps(payload), 'utf8'), (self.host, UNICAST_PORT))
            reply, _ = sock.recvfrom(4096)
            return json.loads(reply.decode())
        except:
            return None
        finally:
            sock.close()

    def get_device_list(self):
        msg = {"msgType": "GetDeviceList", "msgID": self._timestamp()}
        data = self._send_request(msg)
        if not data:
            return None
        self.gateway = data
        self.devices = {d['mac']: d for d in data.get('data', [])}
        return self.devices

    def generate_access_token(self):
        if self.secret:
            self.access_token = self.secret
            return self.access_token

        try:
            token = self.gateway["token"]
            token_bytes = token.encode()

            key = self.key
            if len(key) != 16:
                raise ValueError("AES key must be exactly 16 bytes")

            cipher = AES.new(key, AES.MODE_ECB)

            # EXACT match to standalone (NO padding!)
            encrypted = cipher.encrypt(token_bytes)

            self.access_token = binascii.hexlify(encrypted).decode().upper()
            return self.access_token

        except Exception as e:
            return None


    def get_status(self, mac):
        msg = {
            "msgType": "ReadDevice",
            "mac": mac,
            "deviceType": self.devices[mac]["deviceType"],
            "msgID": self._timestamp(mac)
        }
        return self._send_request(msg)

    def set_value(self, mac, P=None, A=None):
        payload = {}
        if P is not None: payload['targetPosition'] = int(P)
        if A is not None: payload['targetAngle'] = int(A)
        msg = {
            "msgType": "WriteDevice",
            "mac": mac,
            "deviceType": self.devices[mac]["deviceType"],
            "AccessToken": self.access_token,
            "data": payload,
            "msgID": self._timestamp(mac)
        }
        return self._send_request(msg)
