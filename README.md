# Xiaomi Mi Curved Gaming Monitor 34" (2021 Revision - 0x3445): OSD Black Screen Fix for Windows 11

Fix for the broken On-Screen Display (OSD) menu on the **Xiaomi Mi Curved Gaming Monitor 34" (2021 revision, EDID `0x3445`)** caused by DDC/CI power commands (`VCP 0xD6`), running entirely on **Windows 11** via Python and NVIDIA NVAPI.

---

## The Symptom

- Pressing the monitor joystick button turns the screen **black for ~2 seconds**. The video signal returns, but **no menu ever appears**.
- Happens on all inputs (DisplayPort, HDMI).
- Triggered after using DDC/CI software (e.g., BetterDisplay, Twinkle Tray, Monitorian, ClickMonitorDDC, NirSoft ControlMyMonitor) that issued a power command or "turn display off" command (`VCP 0xD6`).
- Standard DDC factory reset (`VCP 0x04` or "Display Factory Reset") **does not fix it**.
- Normal power cycling doesn't help because the flag is written into the monitor's settings EEPROM.

---

## Root Cause & Why the Existing Linux Gist Fails on Revision `0x3445`

The original research and Linux script by [supermar1010](https://gist.github.com/supermar1010/3e81e3f23ce16c826e383edbf9442351) targeted the **2020 revision** (EDID Product Code `0x3444`):
- On the `0x3444` revision, the MStar MST9U scaler stores the stuck "power-down requested via DDC" flag at **byte `0x43`, bit `0x10`** in the EEPROM, where the user settings struct starts at `0x3F`.
- On the **2021 revision** (EDID Product Code `0x3445`), the EEPROM memory layout is shifted:
  - The power-on tick counter is at `0x9C–0x9F` (shifted 2 bytes earlier than `0x9E–0xA1`).
  - The user settings struct starts at `0x3D` (shifted 2 bytes earlier than `0x3F`).
  - Consequently, the stuck flag is at **byte `0x41`, bit `0x10`** (instead of `0x43`).
  - On `0x3445`, byte `0x43` is `0x00`, so running the original 2020 script fails its safety check with:
    ```text
    bit 0x10 is NOT set: this fix does not apply to your monitor.
    ```

Furthermore, standard Windows display APIs (`dxva2.dll`) block arbitrary vendor I2C packets, and DisplayPort / Thunderbolt-to-DP bridges filter out raw MStar EEPROM commands (`7B 99 22` / `7B 99 55`).

---

## How This Fix Works on Windows 11

1. **Direct HDMI Connection:** HDMI provides dedicated physical I2C pins (SCL/SDA) straight to the monitor scaler.
2. **NVIDIA Driver Interface (NVAPI):** Uses `nvapi64.dll` (`NvAPI_I2CRead` / `NvAPI_I2CWrite`) via Python `ctypes` to send raw MStar vendor DDC packets directly to the monitor without needing Linux or third-party kernel drivers.
3. **Safety First:** Automatically saves a complete 256-byte binary backup of your EEPROM (`mi_monitor_backup_<timestamp>.bin`) before touching anything.
4. **Audio Alert System:** The monitor's RAM background loop flushes stale in-RAM settings back to EEPROM every ~1–2 minutes. To prevent the monitor from overwriting your fix, the script writes the 1-bit patch, verifies it, and **immediately sounds loud system alarm beeps (`winsound.Beep`)**, cueing you to physically pull the monitor's power plug within 10–15 seconds.

---

## Requirements

1. **OS:** Windows 10 or Windows 11 (64-bit).
2. **GPU:** NVIDIA GeForce GPU (RTX 20, 30, 40 series, GTX 10 series) with standard NVIDIA drivers installed.
3. **Cable:** Standard **HDMI cable** connected directly from the monitor to your NVIDIA GPU (do not use DP or Type-C converters during this fix).
4. **Software:** Python 3 (standard installation from python.org, no extra pip packages required; stdlib `ctypes` is used).

---

## Step-by-Step Tutorial

### Step 1: Connect via HDMI
Temporarily unplug your DisplayPort cable. Connect an **HDMI cable** directly from the Xiaomi monitor into your NVIDIA graphics card HDMI port.

### Step 2: Download the Script
Save the Python script below as `fix_mi_monitor_3445.py` (e.g., on your Desktop).

### Step 3: Run the Script
Open PowerShell or Command Prompt and run:
```cmd
python fix_mi_monitor_3445.py
```

### Step 4: What You Will See
1. The script will initialize NVAPI and verify the hardware link to your monitor.
2. It will read addresses `0x00` through `0xFF` and save a full binary backup to disk.
3. It will read `Block 0x40` and confirm that **byte `0x41` has bit `0x10` set**:
   ```text
   Current Block 0x40: 02 10 00 00 00 00 00 00 00 00 00 00 00 01 00 00
   Target byte 0x41 is: 0x10 (bit 0x10 = True)
   New Block 0x40:     02 00 00 00 00 00 00 00 00 00 00 00 00 01 00 00
   ```
4. It will pause and prompt:
   ```text
   ############################################################
   READY FOR EXECUTION.
   Please put your hand on the physical power cable of your monitor.
   When you press ENTER, the fix will be written and loud BEEPS will sound.
   As soon as you hear the beeps, PULL THE MONITOR POWER CABLE!
   ############################################################

   Press ENTER to write the fix...
   ```

### Step 5: Execute & Pull the Power Cord
1. Place your hand on the monitor's physical power plug (at the wall or behind the monitor).
2. Press **ENTER**.
3. In under 0.2 seconds, the script writes the patch, verifies the readback, and **fires 10 loud alarm beeps**.
4. **Immediately pull the monitor's power cord.** (Do not use the joystick power button).
5. Leave it unplugged for **10 seconds**.
6. Plug the power cord back in.
7. Click the monitor joystick: **The OSD menu will pop right back up.**

---

## Python Fix Script (`fix_mi_monitor_3445.py`)

```python
#!/usr/bin/env python3
"""
Xiaomi Mi Curved 34" (Revision 0x3445) OSD Fix for Windows 11
Clears stuck power flag (bit 0x10 at EEPROM byte 0x41) over HDMI via NVIDIA NVAPI.
"""

import ctypes
import time
import winsound
import os
import sys

NVAPI_INITIALIZE = 0x0150E828
NVAPI_ENUM_NVIDIA_DISPLAY_HANDLE = 0x9ABDD40D
NVAPI_GET_ASSOCIATED_DISPLAY_OUTPUT_ID = 0xD995937E
NVAPI_GET_PHYSICAL_GPUS_FROM_DISPLAY = 0x34EF9506
NVAPI_I2C_READ = 0x2FDE12C5
NVAPI_I2C_WRITE = 0xE812EB07

BASE = 0xAA4
BLOCK_ADDR = 0x40
FLAGS_OFFSET_IN_BLOCK = 1  # In 0x3445 revision: 0x41 is byte index 1 of block 0x40
BIT_TO_CLEAR = 0x10

class NV_I2C_INFO_V3(ctypes.Structure):
    _fields_ = [
        ("version", ctypes.c_uint32),
        ("displayMask", ctypes.c_uint32),
        ("bIsDDCPort", ctypes.c_uint8),
        ("i2cDevAddress", ctypes.c_uint8),
        ("pbI2cRegAddress", ctypes.POINTER(ctypes.c_uint8)),
        ("regAddrSize", ctypes.c_uint32),
        ("pbData", ctypes.POINTER(ctypes.c_uint8)),
        ("cbSize", ctypes.c_uint32),
        ("i2cSpeed", ctypes.c_uint32),
        ("i2cSpeedKhz", ctypes.c_uint32),
        ("portId", ctypes.c_uint8),
        ("bIsPortIdSet", ctypes.c_uint32),
    ]

def init_nvapi():
    nv = ctypes.windll.LoadLibrary("nvapi64.dll")
    q = nv.nvapi_QueryInterface
    q.restype = ctypes.c_void_p
    q.argtypes = [ctypes.c_uint32]

    ctypes.CFUNCTYPE(ctypes.c_int)(q(NVAPI_INITIALIZE))()

    disp_fn = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(q(NVAPI_ENUM_NVIDIA_DISPLAY_HANDLE))
    h_disp = ctypes.c_void_p()
    if disp_fn(0, ctypes.byref(h_disp)) != 0:
        raise RuntimeError("No active NVIDIA display found.")

    get_out_id_fn = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32))(q(NVAPI_GET_ASSOCIATED_DISPLAY_OUTPUT_ID))
    output_id = ctypes.c_uint32(0)
    get_out_id_fn(h_disp, ctypes.byref(output_id))

    get_gpus_fn = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32))(q(NVAPI_GET_PHYSICAL_GPUS_FROM_DISPLAY))
    gpu_handles = (ctypes.c_void_p * 64)()
    count = ctypes.c_uint32(0)
    get_gpus_fn(h_disp, gpu_handles, ctypes.byref(count))
    gpu_handle = gpu_handles[0]

    i2c_write_fn = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)(q(NVAPI_I2C_WRITE))
    i2c_read_fn = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)(q(NVAPI_I2C_READ))

    return gpu_handle, output_id.value, i2c_read_fn, i2c_write_fn

def hi_lo(addr):
    want = (addr - BASE) & 0xFFFF
    return want >> 8, want & 0xFF

def read16(gpu_handle, disp_mask, i2c_read_fn, i2c_write_fn, addr):
    hi, lo = hi_lo(addr)
    frame = [0x51, 0x86, 0x03, 0x7B, 0x99, 0x22, hi, lo]
    chk = 0x6E
    for b in frame:
        chk ^= b
    reg = ctypes.c_uint8(0x51)
    data = (ctypes.c_uint8 * 8)(0x86, 0x03, 0x7B, 0x99, 0x22, hi, lo, chk)
    w_info = NV_I2C_INFO_V3()
    w_info.version = ctypes.sizeof(NV_I2C_INFO_V3) | (3 << 16)
    w_info.displayMask = disp_mask
    w_info.bIsDDCPort = 1
    w_info.i2cDevAddress = 0x37 << 1
    w_info.pbI2cRegAddress = ctypes.pointer(reg)
    w_info.regAddrSize = 1
    w_info.pbData = ctypes.cast(data, ctypes.POINTER(ctypes.c_uint8))
    w_info.cbSize = 8
    w_info.i2cSpeed = 0xFFFF
    w_info.i2cSpeedKhz = 0
    if i2c_write_fn(gpu_handle, ctypes.byref(w_info)) != 0:
        return None
    time.sleep(0.08)

    read_buf = (ctypes.c_uint8 * 20)()
    r_info = NV_I2C_INFO_V3()
    r_info.version = ctypes.sizeof(NV_I2C_INFO_V3) | (3 << 16)
    r_info.displayMask = disp_mask
    r_info.bIsDDCPort = 1
    r_info.i2cDevAddress = (0x37 << 1) | 1
    r_info.pbI2cRegAddress = None
    r_info.regAddrSize = 0
    r_info.pbData = ctypes.cast(read_buf, ctypes.POINTER(ctypes.c_uint8))
    r_info.cbSize = 20
    r_info.i2cSpeed = 0xFFFF
    r_info.i2cSpeedKhz = 0
    if i2c_read_fn(gpu_handle, ctypes.byref(r_info)) != 0:
        return None
    return bytes(read_buf[2:18])

def write16(gpu_handle, disp_mask, i2c_write_fn, addr, data16):
    hi, lo = hi_lo(addr)
    frame = [0x51, 0x96, 0x03, 0x7B, 0x99, 0x55, hi, lo, *data16]
    chk = 0x6E
    for b in frame:
        chk ^= b
    reg = ctypes.c_uint8(0x51)
    raw_payload = [0x96, 0x03, 0x7B, 0x99, 0x55, hi, lo, *data16, chk]
    data_arr = (ctypes.c_uint8 * len(raw_payload))(*raw_payload)
    w_info = NV_I2C_INFO_V3()
    w_info.version = ctypes.sizeof(NV_I2C_INFO_V3) | (3 << 16)
    w_info.displayMask = disp_mask
    w_info.bIsDDCPort = 1
    w_info.i2cDevAddress = 0x37 << 1
    w_info.pbI2cRegAddress = ctypes.pointer(reg)
    w_info.regAddrSize = 1
    w_info.pbData = ctypes.cast(data_arr, ctypes.POINTER(ctypes.c_uint8))
    w_info.cbSize = len(raw_payload)
    w_info.i2cSpeed = 0xFFFF
    w_info.i2cSpeedKhz = 0
    return i2c_write_fn(gpu_handle, ctypes.byref(w_info))

def main():
    print("=" * 60)
    print("   XIAOMI MI CURVED 34 (0x3445) OSD REPAIR (WINDOWS 11)")
    print("=" * 60)
    print("Initializing NVIDIA NVAPI interface...")
    gpu_handle, disp_mask, i2c_read_fn, i2c_write_fn = init_nvapi()
    print(f"Hardware link OK (Display Mask: {hex(disp_mask)})")

    # Step 1: Backup EEPROM
    backup_path = f"mi_monitor_backup_{time.strftime('%Y%m%d_%H%M%S')}.bin"
    print(f"Backing up EEPROM (0x00-0xFF) to: {backup_path}")
    full_backup = bytearray()
    for a in range(0x00, 0x100, 16):
        c = read16(gpu_handle, disp_mask, i2c_read_fn, i2c_write_fn, a)
        if not c:
            sys.exit(f"ERROR: Failed reading address {hex(a)}. Aborting safely.")
        full_backup.extend(c)
    with open(backup_path, "wb") as f:
        f.write(full_backup)
    print(f"Backup saved successfully ({len(full_backup)} bytes).")

    # Step 2: Read current block 0x40
    cur_block = read16(gpu_handle, disp_mask, i2c_read_fn, i2c_write_fn, BLOCK_ADDR)
    print(f"Current Block 0x40: {cur_block.hex(' ')}")
    flag_val = cur_block[FLAGS_OFFSET_IN_BLOCK]
    print(f"Target byte 0x41 is: {hex(flag_val)} (bit 0x10 = {bool(flag_val & BIT_TO_CLEAR)})")

    if not (flag_val & BIT_TO_CLEAR):
        print("Bit 0x10 is NOT set in byte 0x41. Nothing to fix. Exiting safely.")
        return

    # Prepare patched block
    new_block = bytearray(cur_block)
    new_block[FLAGS_OFFSET_IN_BLOCK] = flag_val & ~BIT_TO_CLEAR
    print(f"New Block 0x40:     {new_block.hex(' ')}")

    print("\n" + "#" * 60)
    print("READY FOR EXECUTION.")
    print("Please put your hand on the physical power cable of your monitor.")
    print("When you press ENTER, the fix will be written and loud BEEPS will sound.")
    print("As soon as you hear the beeps, PULL THE MONITOR POWER CABLE!")
    print("#" * 60 + "\n")

    input("Press ENTER to write the fix...")

    print("Writing fix to EEPROM...")
    w_res = write16(gpu_handle, disp_mask, i2c_write_fn, BLOCK_ADDR, new_block)
    if w_res != 0:
        sys.exit(f"ERROR: Write failed with NVAPI status {w_res}")
    time.sleep(0.1)

    readback = read16(gpu_handle, disp_mask, i2c_read_fn, i2c_write_fn, BLOCK_ADDR)
    if readback != bytes(new_block):
        print("ERROR: Readback mismatch! Restoring original block from backup...")
        write16(gpu_handle, disp_mask, i2c_write_fn, BLOCK_ADDR, cur_block)
        sys.exit(1)

    print("\nSUCCESS! WRITE VERIFIED!")
    print(">>> PULL THE MONITOR POWER CABLE RIGHT NOW! <<<")
    print(">>> PULL THE MONITOR POWER CABLE RIGHT NOW! <<<\n")

    # Sound rapid alarm beeps
    for _ in range(10):
        winsound.Beep(1800, 200)
        time.sleep(0.05)

    print("Wait 10 seconds, plug the power cable back in, and press the joystick.")

if __name__ == "__main__":
    main()
```

---

## How to Prevent This Bug in the Future

In whatever DDC/CI utility you use (BetterDisplay, Twinkle Tray, Monitorian, ClickMonitorDDC, etc.):
- **Disable any "Turn off display" or "Turn off backlight" features.**
- Standard brightness and contrast adjustments (VCP `0x10` and `0x12`) are 100% fine.
- Never allow any tool to send **VCP `0xD6` (Power Mode)** to this monitor.

---

## Credits & Acknowledgements
- **MStar DDC Protocol & Scaler Disassembly:** Credit to [supermar1010](https://gist.github.com/supermar1010/3e81e3f23ce16c826e383edbf9442351) and [shinyquagsire23](https://github.com/shinyquagsire23/mstar_extract_decompress).
- **Windows NVAPI Implementation & 0x3445 Revision Analysis:** Researched, mapped, and solved by **rmkiller** (RM the Boss).
