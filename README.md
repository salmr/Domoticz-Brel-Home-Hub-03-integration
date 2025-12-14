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
- Automatic Brel Home Hub discovery
- Real-time updates via multicast
- Standalone script included to test network connectivity and integration outside Domoticz

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
4. `pycryptodome` Python module

---

## Installation

### 1. Clone the plugin into the Domoticz plugins directory
```shell
cd domoticz/plugins/### 4. Restart domoticz and enable Brel Home Hub from the hardware page
Don't forget to enable "Allow new Hardware" in the Domoticz settings page.

## Configuration
- Optional: Enter the IP of your Brel Home Hub. If empty, teh program will scan your network and will use the first found Brel HUB.
- Enter the KEY of your Brel Home Hub. Get the KEY by quickly tapping 5 times on "Version 1.x.x(x)" in your Brel SmartPhone app. You'll get the 16-byte KEY in a popup, which you can then copy/paste. On Android you'll have to tap next to your profile picture instead of the version-number.
- Don't forget to let Domoticz allow new devices before you activate this plugin!
- If you have trouble loading your pycrypto module, you can manually add an accessToken in your settings. Get it by generating it at https://www.devglan.com/online-tools/aes-encryption-decryption

## Known issues
None so far.

## Usage
Devices have to be added to the gateway as per Brel's instructions, using the official Brel app.

### Blinds and curtains Position
Domoticz sets the position of a blind as a percentage between 0 (fully open) to 100 (fully closed). You need to set the minimum/maximum posistions of the blind before using Domoticz. Please refer to the instructions from Brel on how to set the maximum positions of a blind.

### Venetian blinds Tilt
Besides the position, Domoticz can set the angle of a venetian blind in degrees. An additional device is created, where the name will end with "Tilt". For this device you can set a percentage between 0 and 100, and is converted by the plugin into degrees between 0 and 180. To open your blinds, set the angle to 50% (which translates to 90 degrees).

By default this Tilt-device in Domoticz will send a 90-degrees-command when switched on, and a 0-degrees-command when switched of. Use the slider to choose a custom position.
git clone https://github.com/salmr/Domoticz-Brel-Home-Hub-03-integration.git Domoticz-Brel-Plugin
```

### 2. Update pip:
```shell
  $ pip3 install -U pip
```

### 3. Install pycrypto
The plugin uses the pycrypto python module ([`https://pypi.org/project/pycrypto/`](https://pypi.org/project/pycrypto/))

```shell
  $ pip3 install pycryptodome
```

#### 3.1 Let Domoticz know about the pycrypto module
Domoticz may not have the path to the pycrypto library in its python environment.
In this case you will observe something starting like that in the log:
* failed to load 'plugin.py', Python Path used was
* Module Import failed, exception: 'ImportError'

To find where pycrypto is installed, in a shell:
```shell
  $ pip3 show pycrypto
```
The Crypto directory should be present in the directory indicated with Location.

When you have it installed, just add a symbolic link to it in Domoticz-Brel-Plugin directory with ```ln -s```.
Example:
```shell
  $ cd ~/domoticz/plugins/Domoticz-Brel-Plugin
  $ ln -s /home/pi/.local/lib/python3.5/site-packages/Crypto Crypto
```

### 4. Restart domoticz and enable Brel Home Hub from the hardware page
Don't forget to enable "Allow new Hardware" in the Domoticz settings page.

## Configuration
- Optional: Enter the IP of your Brel Home Hub. If empty, teh program will scan your network and will use the first found Brel HUB.
- Enter the KEY of your Brel Home Hub. Get the KEY by quickly tapping 5 times on "Version 1.x.x(x)" in your Brel SmartPhone app. You'll get the 16-byte KEY in a popup, which you can then copy/paste. On Android you'll have to tap next to your profile picture instead of the version-number.
- Don't forget to let Domoticz allow new devices before you activate this plugin!
- If you have trouble loading your pycrypto module, you can manually add an accessToken in your settings. Get it by generating it at https://www.devglan.com/online-tools/aes-encryption-decryption

## Known issues
None so far.

## Usage
Devices have to be added to the gateway as per Brel's instructions, using the official Brel app.

### Blinds and curtains Position
Domoticz sets the position of a blind as a percentage between 0 (fully open) to 100 (fully closed). You need to set the minimum/maximum posistions of the blind before using Domoticz. Please refer to the instructions from Brel on how to set the maximum positions of a blind.

### Venetian blinds Tilt
Besides the position, Domoticz can set the angle of a venetian blind in degrees. An additional device is created, where the name will end with "Tilt". For this device you can set a percentage between 0 and 100, and is converted by the plugin into degrees between 0 and 180. To open your blinds, set the angle to 50% (which translates to 90 degrees).

By default this Tilt-device in Domoticz will send a 90-degrees-command when switched on, and a 0-degrees-command when switched of. Use the slider to choose a custom position.
