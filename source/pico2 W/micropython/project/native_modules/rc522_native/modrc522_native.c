#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "pico/stdlib.h"
#include "hardware/spi.h"

#include "py/runtime.h"
#include "py/obj.h"

#define MI_OK (0)
#define MI_ERR (2)

#define PCD_IDLE (0x00)
#define PCD_TRANSCEIVE (0x0C)
#define PCD_SOFTRESET (0x0F)

#define REG_COMMAND (0x01)
#define REG_COMIEN (0x02)
#define REG_COMIRQ (0x04)
#define REG_DIVIRQ (0x05)
#define REG_ERROR (0x06)
#define REG_FIFODATA (0x09)
#define REG_FIFOLEVEL (0x0A)
#define REG_CONTROL (0x0C)
#define REG_BITFRAMING (0x0D)
#define REG_COLL (0x0E)
#define REG_MODE (0x11)
#define REG_TXMODE (0x12)
#define REG_RXMODE (0x13)
#define REG_TXCONTROL (0x14)
#define REG_TXASK (0x15)
#define REG_RFCFG (0x26)
#define REG_TMODE (0x2A)
#define REG_TPRESCALER (0x2B)
#define REG_TRELOAD_H (0x2C)
#define REG_TRELOAD_L (0x2D)
#define REG_VERSION (0x37)

#define PICC_CMD_REQA (0x26)
#define PICC_SEL_CL1 (0x93)

#define RC522_MAX_RX (64)

typedef struct _rc522_state_t {
    bool initialized;
    bool configured;
    uint8_t spi_id;
    uint32_t baudrate;
    uint8_t pin_sck;
    uint8_t pin_mosi;
    uint8_t pin_miso;
    uint8_t pin_cs;
    uint8_t pin_rst;
    spi_inst_t *spi;
    uint32_t scans;
    uint32_t errors;
    uint8_t last_error;
} rc522_state_t;

static rc522_state_t rc522 = {
    .initialized = false,
    .configured = false,
    .spi_id = 1,
    .baudrate = 50000,
    .pin_sck = 10,
    .pin_mosi = 11,
    .pin_miso = 12,
    .pin_cs = 13,
    .pin_rst = 22,
    .spi = NULL,
    .scans = 0,
    .errors = 0,
    .last_error = 0,
};

static inline bool rc522_ready(void) {
    return rc522.initialized && rc522.spi != NULL;
}

static inline void rc522_cs(bool level) {
    gpio_put(rc522.pin_cs, level ? 1 : 0);
}

static inline uint8_t reg_addr_write(uint8_t reg) {
    return (uint8_t)((reg << 1) & 0x7E);
}

static inline uint8_t reg_addr_read(uint8_t reg) {
    return (uint8_t)(((reg << 1) & 0x7E) | 0x80);
}

static inline spi_inst_t *spi_from_id(uint8_t spi_id) {
    if (spi_id == 0) {
        return spi0;
    }
    if (spi_id == 1) {
        return spi1;
    }
    return NULL;
}

static bool rc522_reg_write(uint8_t reg, uint8_t value) {
    if (!rc522_ready()) {
        return false;
    }
    uint8_t tx[2] = {reg_addr_write(reg), value};
    rc522_cs(false);
    int written = spi_write_blocking(rc522.spi, tx, 2);
    rc522_cs(true);
    return written == 2;
}

static bool rc522_reg_read(uint8_t reg, uint8_t *value_out) {
    if (!rc522_ready() || value_out == NULL) {
        return false;
    }
    uint8_t addr = reg_addr_read(reg);
    uint8_t rx = 0;
    rc522_cs(false);
    int written = spi_write_blocking(rc522.spi, &addr, 1);
    int read = spi_read_blocking(rc522.spi, 0, &rx, 1);
    rc522_cs(true);
    if (written != 1 || read != 1) {
        return false;
    }
    *value_out = rx;
    return true;
}

static bool rc522_set_bits(uint8_t reg, uint8_t mask) {
    uint8_t v = 0;
    if (!rc522_reg_read(reg, &v)) {
        return false;
    }
    return rc522_reg_write(reg, (uint8_t)(v | mask));
}

static bool rc522_clear_bits(uint8_t reg, uint8_t mask) {
    uint8_t v = 0;
    if (!rc522_reg_read(reg, &v)) {
        return false;
    }
    return rc522_reg_write(reg, (uint8_t)(v & (uint8_t)(~mask)));
}

static bool rc522_chip_init(void) {
    gpio_put(rc522.pin_rst, 0);
    sleep_ms(20);
    gpio_put(rc522.pin_rst, 1);
    sleep_ms(20);

    if (!rc522_reg_write(REG_COMMAND, PCD_SOFTRESET)) {
        return false;
    }
    sleep_ms(100);

    for (size_t i = 0; i < 100; ++i) {
        uint8_t cmd = 0;
        if (!rc522_reg_read(REG_COMMAND, &cmd)) {
            return false;
        }
        if ((cmd & 0x10) == 0) {
            break;
        }
        sleep_ms(1);
    }

    if (!rc522_reg_write(REG_TMODE, 0x8D)) return false;
    if (!rc522_reg_write(REG_TPRESCALER, 0x3E)) return false;
    if (!rc522_reg_write(REG_TRELOAD_L, 30)) return false;
    if (!rc522_reg_write(REG_TRELOAD_H, 0)) return false;

    if (!rc522_reg_write(REG_TXASK, 0x40)) return false;
    if (!rc522_reg_write(REG_MODE, 0x3D)) return false;
    if (!rc522_reg_write(REG_RFCFG, 0x70)) return false;
    if (!rc522_reg_write(REG_TXMODE, 0x00)) return false;
    if (!rc522_reg_write(REG_RXMODE, 0x00)) return false;

    if (!rc522_set_bits(REG_TXCONTROL, 0x03)) return false;
    if (!rc522_reg_write(REG_COLL, 0x80)) return false;
    if (!rc522_clear_bits(REG_COMIRQ, 0x80)) return false;

    return true;
}

static bool rc522_chip_init_with_retry(size_t attempts) {
    if (!rc522_ready()) {
        return false;
    }

    if (attempts == 0) {
        attempts = 1;
    }

    for (size_t i = 0; i < attempts; ++i) {
        spi_init(rc522.spi, rc522.baudrate);
        spi_set_format(rc522.spi, 8, SPI_CPOL_0, SPI_CPHA_0, SPI_MSB_FIRST);
        sleep_ms(2);

        if (rc522_chip_init()) {
            uint8_t ver = 0;
            if (rc522_reg_read(REG_VERSION, &ver) && ver != 0x00 && ver != 0xFF) {
                return true;
            }
            rc522.last_error = 4;
        } else {
            rc522.last_error = 3;
        }

        sleep_ms(25);
    }

    return false;
}

static uint8_t rc522_transceive_internal(const uint8_t *tx, size_t tx_len, uint8_t tx_last_bits, uint32_t timeout_ms, uint32_t settle_us, uint8_t *rx, size_t *rx_len, uint16_t *bitlen_out) {
    if (!rc522_ready() || tx == NULL || tx_len == 0 || rx == NULL || rx_len == NULL || bitlen_out == NULL) {
        return MI_ERR;
    }

    if (!rc522_reg_write(REG_COMIEN, (uint8_t)(0x77 | 0x80))) return MI_ERR;
    if (!rc522_clear_bits(REG_COMIRQ, 0x80)) return MI_ERR;
    if (!rc522_set_bits(REG_FIFOLEVEL, 0x80)) return MI_ERR;
    if (!rc522_reg_write(REG_COMMAND, PCD_IDLE)) return MI_ERR;

    for (size_t i = 0; i < tx_len; ++i) {
        if (!rc522_reg_write(REG_FIFODATA, tx[i])) return MI_ERR;
    }

    if (!rc522_reg_write(REG_BITFRAMING, (uint8_t)(tx_last_bits & 0x07))) return MI_ERR;

    if (settle_us > 0) {
        sleep_us(settle_us);
    }

    if (!rc522_reg_write(REG_COMMAND, PCD_TRANSCEIVE)) return MI_ERR;
    if (!rc522_set_bits(REG_BITFRAMING, 0x80)) return MI_ERR;

    absolute_time_t deadline = make_timeout_time_ms(timeout_ms);
    while (!time_reached(deadline)) {
        uint8_t irq = 0;
        if (!rc522_reg_read(REG_COMIRQ, &irq)) return MI_ERR;
        if ((irq & 0x01) || (irq & 0x30)) {
            break;
        }
        sleep_us(100);
    }

    if (!rc522_clear_bits(REG_BITFRAMING, 0x80)) return MI_ERR;

    uint8_t error = 0;
    if (!rc522_reg_read(REG_ERROR, &error)) return MI_ERR;
    if ((error & 0x1B) != 0) {
        return MI_ERR;
    }

    uint8_t fifo_level = 0;
    if (!rc522_reg_read(REG_FIFOLEVEL, &fifo_level)) return MI_ERR;
    if (fifo_level == 0) {
        return MI_ERR;
    }

    size_t to_read = fifo_level;
    if (to_read > *rx_len) {
        to_read = *rx_len;
    }

    for (size_t i = 0; i < to_read; ++i) {
        if (!rc522_reg_read(REG_FIFODATA, &rx[i])) return MI_ERR;
    }

    uint8_t last = 0;
    if (!rc522_reg_read(REG_CONTROL, &last)) return MI_ERR;
    last &= 0x07;

    *rx_len = to_read;
    *bitlen_out = (uint16_t)((to_read == 0) ? 0 : ((to_read - 1u) * 8u + (last ? last : 8u)));
    return MI_OK;
}

static mp_obj_t rc522_native_init(size_t n_args, const mp_obj_t *args, mp_map_t *kw_args) {
    enum {
        ARG_spi_id,
        ARG_sck,
        ARG_mosi,
        ARG_miso,
        ARG_cs,
        ARG_rst,
        ARG_baud,
    };
    static const mp_arg_t allowed_args[] = {
        { MP_QSTR_spi_id, MP_ARG_INT, {.u_int = 1} },
        { MP_QSTR_sck, MP_ARG_INT, {.u_int = 10} },
        { MP_QSTR_mosi, MP_ARG_INT, {.u_int = 11} },
        { MP_QSTR_miso, MP_ARG_INT, {.u_int = 12} },
        { MP_QSTR_cs, MP_ARG_INT, {.u_int = 13} },
        { MP_QSTR_rst, MP_ARG_INT, {.u_int = 22} },
        { MP_QSTR_baud, MP_ARG_INT, {.u_int = 50000} },
    };

    mp_arg_val_t vals[MP_ARRAY_SIZE(allowed_args)];
    mp_arg_parse_all(n_args, args, kw_args, MP_ARRAY_SIZE(allowed_args), allowed_args, vals);

    uint8_t spi_id = (uint8_t)vals[ARG_spi_id].u_int;
    spi_inst_t *spi = spi_from_id(spi_id);
    if (spi == NULL) {
        mp_raise_ValueError(MP_ERROR_TEXT("spi_id must be 0 or 1"));
    }

    if (rc522.initialized && rc522.spi != NULL) {
        spi_deinit(rc522.spi);
    }

    rc522.spi_id = spi_id;
    rc522.pin_sck = (uint8_t)vals[ARG_sck].u_int;
    rc522.pin_mosi = (uint8_t)vals[ARG_mosi].u_int;
    rc522.pin_miso = (uint8_t)vals[ARG_miso].u_int;
    rc522.pin_cs = (uint8_t)vals[ARG_cs].u_int;
    rc522.pin_rst = (uint8_t)vals[ARG_rst].u_int;
    rc522.baudrate = (uint32_t)vals[ARG_baud].u_int;
    rc522.spi = spi;

    gpio_init(rc522.pin_cs);
    gpio_set_dir(rc522.pin_cs, GPIO_OUT);
    gpio_put(rc522.pin_cs, 1);

    gpio_init(rc522.pin_rst);
    gpio_set_dir(rc522.pin_rst, GPIO_OUT);
    gpio_put(rc522.pin_rst, 1);

    gpio_set_function(rc522.pin_sck, GPIO_FUNC_SPI);
    gpio_set_function(rc522.pin_mosi, GPIO_FUNC_SPI);
    gpio_set_function(rc522.pin_miso, GPIO_FUNC_SPI);

    rc522.initialized = true;
    rc522.configured = false;
    rc522.last_error = 0;

    rc522.configured = rc522_chip_init_with_retry(3);
    if (!rc522.configured) {
        rc522.errors += 1;
        rc522.last_error = 1;
        if (rc522.spi != NULL) {
            spi_deinit(rc522.spi);
        }
        rc522.initialized = false;
        rc522.spi = NULL;
        mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("RC522 init failed"));
    }

    return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_KW(rc522_native_init_obj, 0, rc522_native_init);

static mp_obj_t rc522_native_deinit(void) {
    if (rc522.initialized && rc522.spi != NULL) {
        spi_deinit(rc522.spi);
    }
    rc522.initialized = false;
    rc522.configured = false;
    rc522.spi = NULL;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(rc522_native_deinit_obj, rc522_native_deinit);

static mp_obj_t rc522_native_reset(void) {
    if (!rc522_ready()) {
        mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("RC522 not initialized"));
    }
    bool ok = rc522_chip_init();
    if (!ok) {
        rc522.errors += 1;
        rc522.last_error = 2;
    }
    return mp_obj_new_bool(ok);
}
static MP_DEFINE_CONST_FUN_OBJ_0(rc522_native_reset_obj, rc522_native_reset);

static mp_obj_t rc522_native_read_reg(mp_obj_t reg_in) {
    if (!rc522_ready()) {
        mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("RC522 not initialized"));
    }
    uint8_t reg = (uint8_t)mp_obj_get_int(reg_in);
    uint8_t val = 0;
    if (!rc522_reg_read(reg, &val)) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("SPI read failed"));
    }
    return mp_obj_new_int_from_uint(val);
}
static MP_DEFINE_CONST_FUN_OBJ_1(rc522_native_read_reg_obj, rc522_native_read_reg);

static mp_obj_t rc522_native_write_reg(mp_obj_t reg_in, mp_obj_t val_in) {
    if (!rc522_ready()) {
        mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("RC522 not initialized"));
    }
    uint8_t reg = (uint8_t)mp_obj_get_int(reg_in);
    uint8_t val = (uint8_t)mp_obj_get_int(val_in);
    if (!rc522_reg_write(reg, val)) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("SPI write failed"));
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(rc522_native_write_reg_obj, rc522_native_write_reg);

static mp_obj_t rc522_native_version(void) {
    if (!rc522_ready()) {
        mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("RC522 not initialized"));
    }
    uint8_t val = 0;
    if (!rc522_reg_read(REG_VERSION, &val)) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("Version read failed"));
    }
    return mp_obj_new_int_from_uint(val);
}
static MP_DEFINE_CONST_FUN_OBJ_0(rc522_native_version_obj, rc522_native_version);

static mp_obj_t rc522_native_reqa(void) {
    if (!rc522_ready()) {
        mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("RC522 not initialized"));
    }
    uint8_t tx = PICC_CMD_REQA;
    uint8_t rx[RC522_MAX_RX] = {0};
    size_t rx_len = sizeof(rx);
    uint16_t bitlen = 0;

    if (!rc522_reg_write(REG_BITFRAMING, 0x07) || !rc522_reg_write(REG_COLL, 0x80)) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("REQA prewrite failed"));
    }

    uint8_t status = rc522_transceive_internal(&tx, 1, 0x07, 100, 0, rx, &rx_len, &bitlen);
    return mp_obj_new_tuple(2, (mp_obj_t[]){
        mp_obj_new_int(status),
        mp_obj_new_int(bitlen),
    });
}
static MP_DEFINE_CONST_FUN_OBJ_0(rc522_native_reqa_obj, rc522_native_reqa);

static mp_obj_t rc522_native_anticoll_level(mp_obj_t sel_code_in) {
    if (!rc522_ready()) {
        mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("RC522 not initialized"));
    }
    uint8_t sel_code = (uint8_t)mp_obj_get_int(sel_code_in);
    uint8_t tx[2] = {sel_code, 0x20};
    uint8_t rx[RC522_MAX_RX] = {0};
    size_t rx_len = sizeof(rx);
    uint16_t bitlen = 0;

    if (!rc522_reg_write(REG_BITFRAMING, 0x00) || !rc522_reg_write(REG_COLL, 0x80)) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("anticoll prewrite failed"));
    }

    uint8_t status = rc522_transceive_internal(tx, 2, 0x00, 100, 20, rx, &rx_len, &bitlen);

    mp_obj_t payload = mp_const_none;
    if (status == MI_OK && rx_len >= 5) {
        payload = mp_obj_new_bytes(rx, 5);
    }

    return mp_obj_new_tuple(3, (mp_obj_t[]){
        mp_obj_new_int(status),
        payload,
        mp_obj_new_int(bitlen),
    });
}
static MP_DEFINE_CONST_FUN_OBJ_1(rc522_native_anticoll_level_obj, rc522_native_anticoll_level);

static mp_obj_t rc522_native_get_uid4(void) {
    if (!rc522_ready()) {
        mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("RC522 not initialized"));
    }

    rc522.scans += 1;

    uint8_t tx_reqa = PICC_CMD_REQA;
    uint8_t rx[RC522_MAX_RX] = {0};
    size_t rx_len = sizeof(rx);
    uint16_t bitlen = 0;

    if (!rc522_reg_write(REG_BITFRAMING, 0x07) || !rc522_reg_write(REG_COLL, 0x80)) {
        rc522.errors += 1;
        rc522.last_error = 10;
        return mp_const_none;
    }
    uint8_t status = rc522_transceive_internal(&tx_reqa, 1, 0x07, 100, 0, rx, &rx_len, &bitlen);
    if (status != MI_OK || bitlen != 0x10) {
        return mp_const_none;
    }

    uint8_t tx_anti[2] = {PICC_SEL_CL1, 0x20};
    rx_len = sizeof(rx);
    bitlen = 0;
    if (!rc522_reg_write(REG_BITFRAMING, 0x00) || !rc522_reg_write(REG_COLL, 0x80)) {
        rc522.errors += 1;
        rc522.last_error = 11;
        return mp_const_none;
    }
    status = rc522_transceive_internal(tx_anti, 2, 0x00, 100, 20, rx, &rx_len, &bitlen);
    if (status != MI_OK || rx_len < 5) {
        return mp_const_none;
    }

    uint8_t bcc = (uint8_t)(rx[0] ^ rx[1] ^ rx[2] ^ rx[3]);
    if (bcc != rx[4]) {
        rc522.errors += 1;
        rc522.last_error = 12;
        return mp_const_none;
    }

    return mp_obj_new_bytes(rx, 4);
}
static MP_DEFINE_CONST_FUN_OBJ_0(rc522_native_get_uid4_obj, rc522_native_get_uid4);

static mp_obj_t rc522_native_status(void) {
    mp_obj_t d = mp_obj_new_dict(0);
    mp_obj_dict_store(d, MP_OBJ_NEW_QSTR(MP_QSTR_initialized), mp_obj_new_bool(rc522.initialized));
    mp_obj_dict_store(d, MP_OBJ_NEW_QSTR(MP_QSTR_configured), mp_obj_new_bool(rc522.configured));
    mp_obj_dict_store(d, MP_OBJ_NEW_QSTR(MP_QSTR_spi_id), mp_obj_new_int_from_uint(rc522.spi_id));
    mp_obj_dict_store(d, MP_OBJ_NEW_QSTR(MP_QSTR_baudrate), mp_obj_new_int_from_uint(rc522.baudrate));
    mp_obj_dict_store(d, MP_OBJ_NEW_QSTR(MP_QSTR_scans), mp_obj_new_int_from_uint(rc522.scans));
    mp_obj_dict_store(d, MP_OBJ_NEW_QSTR(MP_QSTR_errors), mp_obj_new_int_from_uint(rc522.errors));
    mp_obj_dict_store(d, MP_OBJ_NEW_QSTR(MP_QSTR_last_error), mp_obj_new_int_from_uint(rc522.last_error));
    mp_obj_dict_store(d, MP_OBJ_NEW_QSTR(MP_QSTR_version), rc522.initialized ? rc522_native_version() : mp_obj_new_int(0));
    return d;
}
static MP_DEFINE_CONST_FUN_OBJ_0(rc522_native_status_obj, rc522_native_status);

static const mp_rom_map_elem_t rc522_native_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_rc522_native) },

    { MP_ROM_QSTR(MP_QSTR_MI_OK), MP_ROM_INT(MI_OK) },
    { MP_ROM_QSTR(MP_QSTR_MI_ERR), MP_ROM_INT(MI_ERR) },
    { MP_ROM_QSTR(MP_QSTR_PICC_SEL_CL1), MP_ROM_INT(PICC_SEL_CL1) },

    { MP_ROM_QSTR(MP_QSTR_init), MP_ROM_PTR(&rc522_native_init_obj) },
    { MP_ROM_QSTR(MP_QSTR_deinit), MP_ROM_PTR(&rc522_native_deinit_obj) },
    { MP_ROM_QSTR(MP_QSTR_reset), MP_ROM_PTR(&rc522_native_reset_obj) },
    { MP_ROM_QSTR(MP_QSTR_read_reg), MP_ROM_PTR(&rc522_native_read_reg_obj) },
    { MP_ROM_QSTR(MP_QSTR_write_reg), MP_ROM_PTR(&rc522_native_write_reg_obj) },
    { MP_ROM_QSTR(MP_QSTR_version), MP_ROM_PTR(&rc522_native_version_obj) },
    { MP_ROM_QSTR(MP_QSTR_reqa), MP_ROM_PTR(&rc522_native_reqa_obj) },
    { MP_ROM_QSTR(MP_QSTR_anticoll_level), MP_ROM_PTR(&rc522_native_anticoll_level_obj) },
    { MP_ROM_QSTR(MP_QSTR_get_uid4), MP_ROM_PTR(&rc522_native_get_uid4_obj) },
    { MP_ROM_QSTR(MP_QSTR_status), MP_ROM_PTR(&rc522_native_status_obj) },
};

static MP_DEFINE_CONST_DICT(rc522_native_globals, rc522_native_globals_table);

const mp_obj_module_t rc522_native_user_cmodule = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&rc522_native_globals,
};

MP_REGISTER_MODULE(MP_QSTR_rc522_native, rc522_native_user_cmodule);
