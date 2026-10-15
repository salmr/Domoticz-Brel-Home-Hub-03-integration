# A Domoticz plugin for Brel Home Hub

This plugin enables integration of the **Brel Home Hub 03/2** with **Domoticz**.
It automatically creates devices and allows you to control your blinds directly from Domoticz.

Many thanks to **Superjunky** for the original groundwork, which can still be found here:
https://github.com/superjunky/Domoticz-Brel-Plugin

This plugin has been tested with the **Brel Home Hub 03/2**.
Some work is still in progress, especially regarding device type assignments, so feedback and suggestions are welcome.

---

## Brel Home Hub

Brel offers a range of high-quality motors for blinds and screens, suitable for indoor and outdoor use.
The Brel Home Hub acts as a gateway that allows these motors to be controlled via a smartphone app.

This plugin allows Domoticz to communicate directly with the Brel Home Hub.

More information about the hub can be found here:
https://www.brel-home.nl/nl/pro/producten/smart-home/716/hub-032

---

## Key features
- Creates Domoticz devices for every device configured in the Brel Home Hub
- Creates separate devices for:
  - Position
  - Angle
  - Battery level
  - Charging state
  - RSSI signal strength
- Devices are tied to the shutter MAC address, so you can rename them freely and restarts never create duplicates
- Automatic Brel Home Hub discovery (configured IP first, then broadcast and multicast)
- Real-time updates via multicast, plus a periodic poll (default every 600 s)
- Never blocks Domoticz: all network traffic runs in background threads
- Command line test tool included (`brel_lib.py`) to test network connectivity and control outside Domoticz

---

## Supported Brel devices

The plugin supports:
- Bi-directional Brel devices used in combination with the **Brel Home Hub 03/2**

Not supported:
- Unknown or untested device types

---

## Compatible hardware

Most systems capable of running Domoticz with Python 3 support should work.

Tested on:
- Raspberry Pi 4 running Domoticz

---

## Software requirements
1. Python version **3.7 or higher**
2. Domoticz compiled with support for Python plugins
3. Up-to-date `pip`
4. `pycryptodome` Python module (only needed when you do not enter a pre-generated AccessToken)

---

## Installation

### 1. Clone the plugin into the Domoticz plugins directory
```shell
cd domoticz/plugins/
git clone https://github.com/salmr/Domoticz-Brel-Home-Hub-03-integration.git Domoticz-Brel-Plugin
```

### 2. Update pip
```shell
pip3 install -U pip
```

### 3. Install pycryptodome
The plugin uses the [pycryptodome](https://pypi.org/project/pycryptodome/) Python module.
Do not install the old, unmaintained `pycrypto` package next to it, as both use the same `Crypto` module name.

```shell
pip3 install pycryptodome
```

#### 3.1 Let Domoticz know about the pycryptodome module
Domoticz may not have the path to the library in its Python environment. In this case you will see this in the log:
* failed to load 'plugin.py'
* PyCryptodome not available

To find where it is installed, in a shell:
```shell
pip3 show pycryptodome
```
The `Crypto` directory should be present in the directory shown after `Location`.

If Domoticz cannot find it, add a symbolic link to it in the plugin directory with `ln -s`.
Example (adjust the Python version to yours):

```shell
cd ~/domoticz/plugins/Domoticz-Brel-Plugin
ln -s /home/pi/.local/lib/python3.9/site-packages/Crypto Crypto
```

### 4. Restart Domoticz and enable Brel Home Hub from the hardware page
Do not forget to enable "Allow new Hardware" in the Domoticz settings page.

---

## Configuration in Domoticz

| Setting | Description |
|---|---|
| Last known Hub IP | Optional. The plugin tries this address first. If the hub does not answer there, it scans the network. Using a fixed IP (DHCP reservation) makes startup faster. |
| Key (16-byte) | The KEY of your Brel Home Hub. Tap 5 times quickly on "Version 1.x.x(x)" in the Brel smartphone app to get a popup with the 16-byte KEY. On Android, tap next to your profile picture instead of the version number. |
| Pre-generated AccessToken | Optional. Use this only if `pycryptodome` cannot be loaded. It is the hub token encrypted with AES-128 (ECB, no padding, hex output, upper case) using your KEY. A fixed token may stop working if the hub token changes. |
| Debug | Logs every hub request and reply. Switch it off again once everything works, and redact the log before posting it anywhere (see below). |
| Poll interval (s) | How often all shutters are read, 30 to 86400. Default 600. Live changes also arrive through multicast. |
| Legacy devices | Only used when upgrading from a version before 2.0. See [Upgrading](#upgrading-from-a-version-before-20). |

Do not forget to let Domoticz allow new devices before you activate this plugin.

---

## Upgrading from a version before 2.0

Versions before 2.0 created a new set of devices on every restart, until all 255 Domoticz units were used. Version 2.0.1 adopts your existing devices, including renamed ones, so room plans, names and history are kept.

Existing devices are recognised by the MAC at the end of the name, or, for renamed devices, by the old layout: 5 units per shutter, with the shutter MACs in sorted order (Pos, Angle, Battery, Charging, RSSI).

To remove the duplicates left by the old version:

1. Install the new version and restart the plugin. Check that your devices work.
2. Set **Legacy devices** to **Remove unused duplicates (one start)** and restart the plugin. This deletes only the extra copies and keeps the adopted originals.
3. Set **Legacy devices** back to **Keep (adopt)**.

If adoption cannot match your devices (for example because you deleted devices from the original unit range), use **Remove ALL legacy devices (one start)**. This deletes every device of this hardware without a MAC DeviceID and recreates them. You then have to rename them again.

---

## Usage
Devices have to be added to the gateway as per Brel's instructions, using the official Brel app.

### Blinds and curtains Position
Domoticz sets the position of a blind as a percentage between 0 (fully open) and 100 (fully closed). You need to set the minimum/maximum positions of the blind before using Domoticz. Please refer to the instructions from Brel on how to set the maximum positions of a blind.

### Venetian blinds Tilt
Besides the position, Domoticz can set the angle of a venetian blind in degrees. An additional device is created, where the name will end with "Angle". For this device you can set a percentage between 0 and 100, which the plugin converts into degrees between 0 and 180. To open your blinds, set the angle to 50% (which translates to 90 degrees).

### Sensors
- **Battery** shows the raw value reported by the hub. Its unit has not been confirmed.
- **Charging** shows the charging state reported by the hub.
- **RSSI** shows the signal strength in dBm. Values around -85 or lower indicate a weak connection to that shutter.

---

## Command line test tool

`brel_lib.py` can be run without Domoticz to check that your hub, key and network work:

```shell
python3 brel_lib.py --key YOUR_16_CHAR_KEY list
python3 brel_lib.py --key YOUR_16_CHAR_KEY --host HUB_IP status MAC
python3 brel_lib.py --key YOUR_16_CHAR_KEY --host HUB_IP pos MAC 40
```

Actions: `list`, `status`, `open`, `close`, `stop`, `pos`, `angle`. Add `--debug` for detailed output.

---

## Troubleshooting

| Log message | Meaning |
|---|---|
| `illegal Unit number (-1)` right after "Initialized" | Reported by Domoticz itself, before the plugin starts. It is usually caused by a leftover row in the database with a unit outside 1 to 255 (for example from a failed device creation in an old version). The plugin ignores it. Delete such a device in Setup > Devices, or remove the row from the database after making a backup. |
| `UDP error (GetDeviceList): timeout` | The hub did not answer on the configured IP. The plugin then scans the network. Enter the current hub IP to skip the scan. |
| `no free Domoticz unit left (limit 255)` | Old duplicate devices fill all units. See [Upgrading](#upgrading-from-a-version-before-20). |
| `cannot link '...' to a shutter` | A device could not be matched to a shutter. Use **Remove ALL legacy devices** for one start. |
| `PyCryptodome not available` | Install `pycryptodome` (step 3), or enter a pre-generated AccessToken. |
| `hub not reachable; retrying in 60 s` | The hub is off, on another network, or the key is wrong. The plugin retries every 60 seconds. |

---

## Known issues
- The unit of the battery value is not confirmed, so it is shown raw.
- The operation codes used for open (1), stop (2) and close (3) are kept from the original plugin and have not been checked against Brel's protocol documentation.
- Only the Brel Home Hub 03/2 has been tested.

---

## Changelog

### 2.0.1
- Existing devices from older versions are adopted automatically, including renamed ones.
- "Legacy devices" setting with three modes: keep, remove unused duplicates, remove all legacy devices.
- Removed the doubled `Brel: Brel:` log prefix.

### 2.0.0
- Devices are matched on the shutter MAC (DeviceID), so restarts and renames no longer create duplicates.
- The Angle device now sends the target angle instead of moving the position.
- Network traffic runs in background threads and results are applied from the Domoticz thread.
- Hub IP and token changes are followed. A rejected AccessToken is rebuilt and the command is retried once.
- The configured hub IP is tried first, with network discovery as fallback.
- New settings: poll interval and legacy device handling. The debug setting is now a drop-down.
- Position and Angle devices report open, closed and partial states.
- `brel_lib.py` can be used as a command line tool.

---

## Privacy when posting logs

Debug logs and hub replies contain your hub IP, shutter MAC addresses and device names. Redact these before posting a log in an issue or on the forum. Never post your hub KEY or AccessToken.
