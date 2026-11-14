// FeT Display on the CYD (ESP32-2432S028R): double-precision floats, so fetcore gives the same bits as on the Pi and
// deepTENS (tests/test_micropython.py, docs/PLAN-cyd.md), and no Bluetooth (not used; it saves memory).
#define MICROPY_HW_BOARD_NAME "CYD ESP32-2432S028R (double)"
#define MICROPY_HW_MCU_NAME "ESP32"
#define MICROPY_FLOAT_IMPL (MICROPY_FLOAT_IMPL_DOUBLE)
#define MICROPY_PY_BLUETOOTH (0)
