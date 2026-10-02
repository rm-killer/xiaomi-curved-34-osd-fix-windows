import ctypes
import time
import winsound
import os
import sys

# NVAPI Constants and IDs
NVAPI_INITIALIZE = 0x0150E828
NVAPI_ENUM_NVIDIA_DISPLAY_HANDLE = 0x9ABDD40D
NVAPI_GET_ASSOCIATED_DISPLAY_OUTPUT_ID = 0xD995937E
NVAPI_GET_PHYSICAL_GPUS_FROM_DISPLAY = 0x34EF9506
NVAPI_I2C_READ = 0x2FDE12C5
NVAPI_I2C_WRITE = 0xE812EB07

BASE = 0xAA4
BLOCK_ADDR = 0x40
FLAGS_OFFSET_IN_BLOCK = 1 # In 0x3445 revision: 0x41 is byte 1 of block 0x40
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

    # Init
    ctypes.CFUNCTYPE(ctypes.c_int)(q(NVAPI_INITIALIZE))()

    # Display handle
    disp_fn = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(q(NVAPI_ENUM_NVIDIA_DISPLAY_HANDLE))
    h_disp = ctypes.c_void_p()
    if disp_fn(0, ctypes.byref(h_disp)) != 0:
        raise RuntimeError("No Nvidia display found.")

    # Output ID
    get_out_id_fn = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32))(q(NVAPI_GET_ASSOCIATED_DISPLAY_OUTPUT_ID))
    output_id = ctypes.c_uint32(0)
    get_out_id_fn(h_disp, ctypes.byref(output_id))

    # GPU handle
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
    for b in frame: chk ^= b
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
    if i2c_write_fn(gpu_handle, ctypes.byref(w_info)) != 0: return None
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
    if i2c_read_fn(gpu_handle, ctypes.byref(r_info)) != 0: return None
    return bytes(read_buf[2:18])

def write16(gpu_handle, disp_mask, i2c_write_fn, addr, data16):
    hi, lo = hi_lo(addr)
    # Vendor command 7B 99 55 write: 51 96 03 7B 99 55 HH LL <16 bytes> <chk>
    # 0x80 | 22 (len of [0x03, 0x7B, 0x99, 0x55, hi, lo, *data16]) = 0x80 | 22 = 0x96
    frame = [0x51, 0x96, 0x03, 0x7B, 0x99, 0x55, hi, lo, *data16]
    chk = 0x6E
    for b in frame: chk ^= b
    reg = ctypes.c_uint8(0x51)
    # Data buffer: 0x96 + 0x03 + 0x7B + 0x99 + 0x55 + hi + lo + 16 bytes + chk = 24 bytes
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
    print("   XIAOMI MI CURVED 34 (0x3445) OSD REPAIR SCRIPT")
    print("=" * 60)
    print("Initializing NVAPI hardware interface...")
    gpu_handle, disp_mask, i2c_read_fn, i2c_write_fn = init_nvapi()
    print("Hardware link OK (Display Mask: " + hex(disp_mask) + ")")

    # Step 1: Backup EEPROM
    backup_path = "D:/Hermes Stuff/mi_monitor_backup_" + time.strftime("%Y%m%d_%H%M%S") + ".bin"
    print("Reading and backing up EEPROM to: " + backup_path)
    full_backup = bytearray()
    for a in range(0x00, 0x100, 16):
        c = read16(gpu_handle, disp_mask, i2c_read_fn, i2c_write_fn, a)
        if not c:
            print("ERROR: Failed reading address " + hex(a) + ". Aborting safely.")
            return
        full_backup.extend(c)
    with open(backup_path, "wb") as f:
        f.write(full_backup)
    print("Backup complete (" + str(len(full_backup)) + " bytes).")

    # Step 2: Read current block 0x40
    cur_block = read16(gpu_handle, disp_mask, i2c_read_fn, i2c_write_fn, BLOCK_ADDR)
    print("Current Block 0x40: " + cur_block.hex(" "))
    flag_val = cur_block[FLAGS_OFFSET_IN_BLOCK]
    print("Target byte 0x41 is: " + hex(flag_val) + " (bit 0x10 = " + str(bool(flag_val & BIT_TO_CLEAR)) + ")")

    if not (flag_val & BIT_TO_CLEAR):
        print("Bit 0x10 is NOT set in byte 0x41. Nothing to do. Aborting safely.")
        return

    # Prepare patched block
    new_block = bytearray(cur_block)
    new_block[FLAGS_OFFSET_IN_BLOCK] = flag_val & ~BIT_TO_CLEAR
    print("New Block 0x40:     " + new_block.hex(" "))

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
        print("ERROR: Write failed with NVAPI status " + str(w_res))
        return
    time.sleep(0.1)

    readback = read16(gpu_handle, disp_mask, i2c_read_fn, i2c_write_fn, BLOCK_ADDR)
    if readback != bytes(new_block):
        print("ERROR: Readback mismatch! Restoring from backup...")
        write16(gpu_handle, disp_mask, i2c_write_fn, BLOCK_ADDR, cur_block)
        return

    print("\nSUCCESS! WRITE VERIFIED!")
    print(">>> PULL THE MONITOR POWER CABLE RIGHT NOW! <<<")
    print(">>> PULL THE MONITOR POWER CABLE RIGHT NOW! <<<\n")

    # Alarm beeps
    for _ in range(10):
        winsound.Beep(1800, 200)
        time.sleep(0.05)

    print("Wait 10 seconds, plug it back in, and test your joystick button.")

if __name__ == "__main__":
    main()
