// dualbeam_native.c - Native dual-beam PIO measurement module for MicroPython (RP2040)
#include "py/obj.h"
#include "py/runtime.h"
#include "hardware/pio.h"
#include "hardware/irq.h"
#include "pico/stdlib.h"
#include <string.h>

// --- PIO program (replace with your actual instructions) ---
#include "hardware/clocks.h"

// Example: dual-beam measure PIO program (replace with your actual)
static const uint16_t dual_beam_program[] = {
    // PIO assembler output here (example, not functional):
    0xa0a1, // wait 0, gpio, 2
    0xa0a3, // wait 0, gpio, 3
    // ...
};
#define DUAL_BEAM_PROGRAM_SIZE (sizeof(dual_beam_program)/2)

// --- State ---
typedef struct {
    PIO pio;
    uint sm;
    int pin1;
    int pin2;
    int program_offset;
    volatile uint32_t result;
    volatile bool result_ready;
} dualbeam_state_t;

static dualbeam_state_t dualbeam_state = {0};

// --- IRQ handler ---
void dualbeam_irq_handler(void) {
    if (pio_sm_is_rx_fifo_empty(dualbeam_state.pio, dualbeam_state.sm)) return;
    dualbeam_state.result = pio_sm_get(dualbeam_state.pio, dualbeam_state.sm);
    dualbeam_state.result_ready = true;
}

// --- Python: init(pin1, pin2) ---
STATIC mp_obj_t dualbeam_init(mp_obj_t pin1_obj, mp_obj_t pin2_obj) {
    int pin1 = mp_obj_get_int(pin1_obj);
    int pin2 = mp_obj_get_int(pin2_obj);
    dualbeam_state.pio = pio0;
    dualbeam_state.sm = 0;
    dualbeam_state.pin1 = pin1;
    dualbeam_state.pin2 = pin2;
    dualbeam_state.result_ready = false;
    // Load program
    dualbeam_state.program_offset = pio_add_program_at_offset(
        dualbeam_state.pio, (const pio_program_t *)dual_beam_program, 0);
    // Set up pins
    pio_sm_set_consecutive_pindirs(dualbeam_state.pio, dualbeam_state.sm, pin1, 1, false);
    pio_sm_set_consecutive_pindirs(dualbeam_state.pio, dualbeam_state.sm, pin2, 1, false);
    // Set up IRQ
    irq_set_exclusive_handler(PIO0_IRQ_0, dualbeam_irq_handler);
    irq_set_enabled(PIO0_IRQ_0, true);
    pio_set_irq0_source_enabled(dualbeam_state.pio, PIO_INTR_SM0_RXNEMPTY_LSB, true);
    return mp_const_none;
}
STATIC MP_DEFINE_CONST_FUN_OBJ_2(dualbeam_init_obj, dualbeam_init);

// --- Python: arm(debounce) ---
STATIC mp_obj_t dualbeam_arm(mp_obj_t debounce_obj) {
    uint32_t debounce = mp_obj_get_int(debounce_obj);
    dualbeam_state.result_ready = false;
    pio_sm_restart(dualbeam_state.pio, dualbeam_state.sm);
    pio_sm_put(dualbeam_state.pio, dualbeam_state.sm, debounce);
    pio_sm_set_enabled(dualbeam_state.pio, dualbeam_state.sm, true);
    return mp_const_none;
}
STATIC MP_DEFINE_CONST_FUN_OBJ_1(dualbeam_arm_obj, dualbeam_arm);

// --- Python: read() ---
STATIC mp_obj_t dualbeam_read(void) {
    if (!dualbeam_state.result_ready) return mp_const_none;
    dualbeam_state.result_ready = false;
    return mp_obj_new_int_from_uint(dualbeam_state.result);
}
STATIC MP_DEFINE_CONST_FUN_OBJ_0(dualbeam_read_obj, dualbeam_read);

// --- Module globals ---
STATIC const mp_rom_map_elem_t dualbeam_module_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_init), MP_ROM_PTR(&dualbeam_init_obj) },
    { MP_ROM_QSTR(MP_QSTR_arm), MP_ROM_PTR(&dualbeam_arm_obj) },
    { MP_ROM_QSTR(MP_QSTR_read), MP_ROM_PTR(&dualbeam_read_obj) },
};
STATIC MP_DEFINE_CONST_DICT(dualbeam_module_globals, dualbeam_module_globals_table);

const mp_obj_module_t dualbeam_native_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t*)&dualbeam_module_globals,
};

MP_REGISTER_MODULE(MP_QSTR_dualbeam_native, dualbeam_native_module);
