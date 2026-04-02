#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "pico/stdlib.h"
#include "pico/time.h"
#include "hardware/clocks.h"
#include "hardware/dma.h"
#include "hardware/pio.h"
#include "hardware/sync.h"

#include "py/runtime.h"
#include "py/obj.h"
#include "py/objarray.h"
#include "py/binary.h"

#ifndef NO_QSTR
#include "dmx_native_sdk.pio.h"
#endif

#define DMX_NATIVE_MAX_CHANNELS (512)
#define DMX_NATIVE_FRAME_SLOTS (DMX_NATIVE_MAX_CHANNELS + 1)
#define DMX_BREAK_US (92)
#define DMX_MAB_US (12)
#define DMX_SLOT_US (44)
#define SM_CTRL_CLOCK_HZ (6000000u)
#define SM_DATA_CLOCK_HZ (3000000u)
#define IRQ_FRAME_DONE (2u)
#define IRQ_FRAME_START (0u)
#define DMA_PRIME_TIMEOUT_US (500)

typedef struct _sm_pair_t {
    uint8_t ctrl;
    uint8_t data;
} sm_pair_t;

static const sm_pair_t dmx_sm_pairs[] = {
    {8, 9},
    {0, 1},
    {4, 5},
};

typedef struct _dmx_native_state_t {
    bool initialized;
    bool running;
    bool resources_allocated;
    uint8_t tx_pin;
    uint8_t trigger_pin;
    uint16_t channels;
    uint16_t refresh_rate;
    uint8_t requested_ctrl_sm;
    uint8_t requested_data_sm;
    uint8_t active_ctrl_sm;
    uint8_t active_data_sm;
    PIO pio;
    uint pio_index;
    uint ctrl_sm_local;
    uint data_sm_local;
    int ctrl_offset;
    int data_offset;
    int dma_channel;
    dma_channel_config dma_cfg;
    repeating_timer_t timer;
    bool timer_active;
    uint32_t frame_time_us;
    int64_t timer_period_us;
    bool frame_in_progress;
    absolute_time_t frame_deadline;
    uint32_t frame_count;
    uint32_t skipped_callbacks;
    uint32_t prime_timeouts;
    uint32_t frame_timeouts;
    uint32_t auto_resyncs;
    uint32_t last_sent_version;
    uint32_t data_version;
    uint8_t frame[DMX_NATIVE_FRAME_SLOTS];
    uint8_t tx_frame[DMX_NATIVE_FRAME_SLOTS];
    uint8_t dirty_mask[DMX_NATIVE_FRAME_SLOTS];
    uint16_t dirty_first;
    int16_t dirty_last;
} dmx_native_state_t;

static dmx_native_state_t dmx_state = {
    .initialized = false,
    .running = false,
    .resources_allocated = false,
    .tx_pin = 0,
    .trigger_pin = 1,
    .channels = DMX_NATIVE_MAX_CHANNELS,
    .refresh_rate = 43,
    .requested_ctrl_sm = 8,
    .requested_data_sm = 9,
    .active_ctrl_sm = 8,
    .active_data_sm = 9,
    .pio = NULL,
    .pio_index = 0,
    .ctrl_sm_local = 0,
    .data_sm_local = 1,
    .ctrl_offset = -1,
    .data_offset = -1,
    .dma_channel = -1,
    .timer_active = false,
    .frame_time_us = DMX_BREAK_US + DMX_MAB_US + (DMX_NATIVE_FRAME_SLOTS * DMX_SLOT_US),
    .timer_period_us = -22676,
    .frame_in_progress = false,
    .frame_count = 0,
    .skipped_callbacks = 0,
    .prime_timeouts = 0,
    .frame_timeouts = 0,
    .auto_resyncs = 0,
    .last_sent_version = 0,
    .data_version = 0,
    .dirty_first = DMX_NATIVE_FRAME_SLOTS,
    .dirty_last = -1,
};

static void dmx_native_mark_dirty(uint16_t idx) {
    dmx_state.dirty_mask[idx] = 1;
    if (idx < dmx_state.dirty_first) {
        dmx_state.dirty_first = idx;
    }
    if ((int16_t)idx > dmx_state.dirty_last) {
        dmx_state.dirty_last = (int16_t)idx;
    }
}

static void dmx_native_apply_dirty(void) {
    if (dmx_state.dirty_last < (int16_t)dmx_state.dirty_first) {
        return;
    }
    for (uint16_t idx = dmx_state.dirty_first; idx <= (uint16_t)dmx_state.dirty_last; ++idx) {
        if (dmx_state.dirty_mask[idx]) {
            dmx_state.tx_frame[idx] = dmx_state.frame[idx];
            dmx_state.dirty_mask[idx] = 0;
        }
    }
    dmx_state.dirty_first = DMX_NATIVE_FRAME_SLOTS;
    dmx_state.dirty_last = -1;
}

static bool dmx_native_resolve_pair(uint8_t ctrl_sm, uint8_t data_sm, PIO *pio_out, uint *ctrl_local_out, uint *data_local_out, uint *pio_index_out) {
    if ((ctrl_sm / 4) != (data_sm / 4)) {
        return false;
    }
    uint block = ctrl_sm / 4;
    if (block >= NUM_PIOS) {
        return false;
    }
    static PIO pio_instances[] = {
        pio0,
        pio1,
#if NUM_PIOS > 2
        pio2,
#endif
    };
    *pio_out = pio_instances[block];
    *ctrl_local_out = ctrl_sm & 0x3u;
    *data_local_out = data_sm & 0x3u;
    *pio_index_out = block;
    return true;
}

static void dmx_native_force_irq0(void) {
    dmx_state.pio->irq_force = 1u << IRQ_FRAME_START;
}

static void dmx_native_clear_pio_irqs(void) {
    for (uint i = 0; i < 8; ++i) {
        pio_interrupt_clear(dmx_state.pio, i);
    }
}

static bool dmx_native_wait_dma_fifo_prime(uint32_t timeout_us) {
    absolute_time_t deadline = make_timeout_time_us(timeout_us);
    while (!time_reached(deadline)) {
        if (pio_sm_get_tx_fifo_level(dmx_state.pio, dmx_state.data_sm_local) > 0) {
            return true;
        }
        tight_loop_contents();
    }
    return false;
}

static void dmx_native_stop_hw(void) {
    if (dmx_state.timer_active) {
        cancel_repeating_timer(&dmx_state.timer);
        dmx_state.timer_active = false;
    }
    if (dmx_state.dma_channel >= 0) {
        dma_channel_abort((uint)dmx_state.dma_channel);
    }
    if (dmx_state.resources_allocated) {
        pio_sm_set_enabled(dmx_state.pio, dmx_state.ctrl_sm_local, false);
        pio_sm_set_enabled(dmx_state.pio, dmx_state.data_sm_local, false);
        dmx_native_clear_pio_irqs();
    }
    gpio_init(dmx_state.tx_pin);
    gpio_set_dir(dmx_state.tx_pin, GPIO_OUT);
    gpio_put(dmx_state.tx_pin, 1);
    dmx_state.frame_in_progress = false;
}

static void dmx_native_release_resources(void) {
    dmx_native_stop_hw();
    if (dmx_state.resources_allocated) {
        if (dmx_state.data_offset >= 0) {
            pio_remove_program(dmx_state.pio, &sm_dmx_data_program, (uint)dmx_state.data_offset);
        }
        if (dmx_state.ctrl_offset >= 0) {
            pio_remove_program(dmx_state.pio, &sm_dmx_control_program, (uint)dmx_state.ctrl_offset);
        }
        pio_sm_unclaim(dmx_state.pio, dmx_state.ctrl_sm_local);
        pio_sm_unclaim(dmx_state.pio, dmx_state.data_sm_local);
    }
    if (dmx_state.dma_channel >= 0) {
        dma_channel_unclaim((uint)dmx_state.dma_channel);
    }
    dmx_state.resources_allocated = false;
    dmx_state.ctrl_offset = -1;
    dmx_state.data_offset = -1;
    dmx_state.dma_channel = -1;
    dmx_state.pio = NULL;
}

static bool dmx_native_try_allocate_pair(uint8_t ctrl_sm, uint8_t data_sm) {
    PIO pio = NULL;
    uint ctrl_local = 0;
    uint data_local = 0;
    uint pio_index = 0;
    if (!dmx_native_resolve_pair(ctrl_sm, data_sm, &pio, &ctrl_local, &data_local, &pio_index)) {
        return false;
    }
    if (pio_sm_is_claimed(pio, ctrl_local) || pio_sm_is_claimed(pio, data_local)) {
        return false;
    }
    if (!pio_can_add_program(pio, &sm_dmx_control_program)) {
        return false;
    }
    int ctrl_offset = pio_add_program(pio, &sm_dmx_control_program);
    if (!pio_can_add_program(pio, &sm_dmx_data_program)) {
        pio_remove_program(pio, &sm_dmx_control_program, (uint)ctrl_offset);
        return false;
    }
    int data_offset = pio_add_program(pio, &sm_dmx_data_program);
    pio_claim_sm_mask(pio, (1u << ctrl_local) | (1u << data_local));

    dmx_state.pio = pio;
    dmx_state.pio_index = pio_index;
    dmx_state.ctrl_sm_local = ctrl_local;
    dmx_state.data_sm_local = data_local;
    dmx_state.ctrl_offset = ctrl_offset;
    dmx_state.data_offset = data_offset;
    dmx_state.active_ctrl_sm = ctrl_sm;
    dmx_state.active_data_sm = data_sm;
    dmx_state.dma_channel = dma_claim_unused_channel(true);
    dmx_state.resources_allocated = true;

    dmx_state.dma_cfg = dma_channel_get_default_config((uint)dmx_state.dma_channel);
    channel_config_set_transfer_data_size(&dmx_state.dma_cfg, DMA_SIZE_8);
    channel_config_set_read_increment(&dmx_state.dma_cfg, true);
    channel_config_set_write_increment(&dmx_state.dma_cfg, false);
    channel_config_set_dreq(&dmx_state.dma_cfg, pio_get_dreq(dmx_state.pio, dmx_state.data_sm_local, true));
    return true;
}

static void dmx_native_allocate_resources(void) {
    dmx_native_release_resources();

    sm_pair_t ordered[3];
    size_t ordered_count = 0;
    ordered[ordered_count++] = (sm_pair_t){dmx_state.requested_ctrl_sm, dmx_state.requested_data_sm};
    for (size_t i = 0; i < MP_ARRAY_SIZE(dmx_sm_pairs); ++i) {
        if (dmx_sm_pairs[i].ctrl == ordered[0].ctrl && dmx_sm_pairs[i].data == ordered[0].data) {
            continue;
        }
        ordered[ordered_count++] = dmx_sm_pairs[i];
    }

    for (size_t i = 0; i < ordered_count; ++i) {
        if (dmx_native_try_allocate_pair(ordered[i].ctrl, ordered[i].data)) {
            return;
        }
    }
    mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("unable to allocate DMX PIO/DMA resources"));
}

static void dmx_native_configure_sms(void) {
    gpio_init(dmx_state.tx_pin);
    gpio_set_dir(dmx_state.tx_pin, GPIO_OUT);
    gpio_put(dmx_state.tx_pin, 1);
    gpio_init(dmx_state.trigger_pin);
    gpio_set_dir(dmx_state.trigger_pin, GPIO_OUT);
    gpio_put(dmx_state.trigger_pin, 0);

    pio_gpio_init(dmx_state.pio, dmx_state.tx_pin);
    pio_gpio_init(dmx_state.pio, dmx_state.trigger_pin);
    pio_sm_set_consecutive_pindirs(dmx_state.pio, dmx_state.ctrl_sm_local, dmx_state.tx_pin, 1, true);
    pio_sm_set_consecutive_pindirs(dmx_state.pio, dmx_state.ctrl_sm_local, dmx_state.trigger_pin, 1, true);
    pio_sm_set_consecutive_pindirs(dmx_state.pio, dmx_state.data_sm_local, dmx_state.tx_pin, 1, true);

    pio_sm_config ctrl_cfg = sm_dmx_control_program_get_default_config((uint)dmx_state.ctrl_offset);
    sm_config_set_clkdiv(&ctrl_cfg, (float)clock_get_hz(clk_sys) / (float)SM_CTRL_CLOCK_HZ);
    sm_config_set_set_pins(&ctrl_cfg, dmx_state.tx_pin, 1);
    sm_config_set_sideset_pins(&ctrl_cfg, dmx_state.trigger_pin);

    pio_sm_config data_cfg = sm_dmx_data_program_get_default_config((uint)dmx_state.data_offset);
    sm_config_set_clkdiv(&data_cfg, (float)clock_get_hz(clk_sys) / (float)SM_DATA_CLOCK_HZ);
    sm_config_set_set_pins(&data_cfg, dmx_state.tx_pin, 1);
    sm_config_set_out_pins(&data_cfg, dmx_state.tx_pin, 1);
    sm_config_set_sideset_pins(&data_cfg, dmx_state.tx_pin);
    sm_config_set_out_shift(&data_cfg, true, true, 8);
    sm_config_set_fifo_join(&data_cfg, PIO_FIFO_JOIN_TX);

    pio_sm_init(dmx_state.pio, dmx_state.ctrl_sm_local, (uint)dmx_state.ctrl_offset, &ctrl_cfg);
    pio_sm_init(dmx_state.pio, dmx_state.data_sm_local, (uint)dmx_state.data_offset, &data_cfg);
    pio_sm_clear_fifos(dmx_state.pio, dmx_state.ctrl_sm_local);
    pio_sm_clear_fifos(dmx_state.pio, dmx_state.data_sm_local);
    dmx_native_clear_pio_irqs();
}

static void dmx_native_resync(const char *reason) {
    (void)reason;
    dmx_state.auto_resyncs += 1;
    dma_channel_abort((uint)dmx_state.dma_channel);
    dmx_native_configure_sms();
    pio_sm_set_enabled(dmx_state.pio, dmx_state.data_sm_local, true);
    pio_sm_set_enabled(dmx_state.pio, dmx_state.ctrl_sm_local, true);
    sleep_us(200);
    pio_sm_put_blocking(dmx_state.pio, dmx_state.ctrl_sm_local, dmx_state.channels);
    dmx_native_force_irq0();
    dmx_state.frame_in_progress = false;
}

static bool dmx_native_update_frame(void) {
    if (dmx_state.frame_in_progress) {
        if (pio_interrupt_get(dmx_state.pio, IRQ_FRAME_DONE)) {
            pio_interrupt_clear(dmx_state.pio, IRQ_FRAME_DONE);
            dmx_state.frame_in_progress = false;
            dmx_state.last_sent_version = dmx_state.data_version;
        } else if (time_reached(dmx_state.frame_deadline)) {
            dmx_state.frame_timeouts += 1;
            dmx_state.frame_in_progress = false;
            dmx_native_resync("frame timeout");
        } else {
            dmx_state.skipped_callbacks += 1;
            return true;
        }
    }

    dmx_native_apply_dirty();
    dma_channel_abort((uint)dmx_state.dma_channel);
    dma_channel_configure(
        (uint)dmx_state.dma_channel,
        &dmx_state.dma_cfg,
        &dmx_state.pio->txf[dmx_state.data_sm_local],
        dmx_state.tx_frame,
        dmx_state.channels + 1,
        true
    );
    if (!dmx_native_wait_dma_fifo_prime(DMA_PRIME_TIMEOUT_US)) {
        dmx_state.prime_timeouts += 1;
        dma_channel_abort((uint)dmx_state.dma_channel);
        dmx_native_resync("prime timeout");
        return true;
    }

    pio_interrupt_clear(dmx_state.pio, IRQ_FRAME_DONE);
    dmx_native_force_irq0();
    dmx_state.frame_in_progress = true;
    dmx_state.frame_deadline = make_timeout_time_us(dmx_state.frame_time_us + 3000u);
    dmx_state.frame_count += 1;
    return true;
}

static bool dmx_native_timer_cb(repeating_timer_t *timer) {
    (void)timer;
    if (!dmx_state.running) {
        return false;
    }
    return dmx_native_update_frame();
}

static mp_obj_t dmx_native_init(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    enum {
        ARG_tx_pin,
        ARG_trigger_pin,
        ARG_channels,
        ARG_refresh_rate,
        ARG_sm_ctrl_id,
        ARG_sm_data_id,
    };

    static const mp_arg_t allowed_args[] = {
        { MP_QSTR_tx_pin, MP_ARG_KW_ONLY | MP_ARG_INT, {.u_int = 0} },
        { MP_QSTR_trigger_pin, MP_ARG_KW_ONLY | MP_ARG_INT, {.u_int = 1} },
        { MP_QSTR_channels, MP_ARG_KW_ONLY | MP_ARG_INT, {.u_int = 512} },
        { MP_QSTR_refresh_rate, MP_ARG_KW_ONLY | MP_ARG_INT, {.u_int = 43} },
        { MP_QSTR_sm_ctrl_id, MP_ARG_KW_ONLY | MP_ARG_INT, {.u_int = 8} },
        { MP_QSTR_sm_data_id, MP_ARG_KW_ONLY | MP_ARG_INT, {.u_int = 9} },
    };

    mp_arg_val_t args[MP_ARRAY_SIZE(allowed_args)];
    mp_arg_parse_all(n_args, pos_args, kw_args, MP_ARRAY_SIZE(allowed_args), allowed_args, args);

    int channels = args[ARG_channels].u_int;
    int refresh_rate = args[ARG_refresh_rate].u_int;
    if (channels < 1 || channels > 512) {
        mp_raise_ValueError(MP_ERROR_TEXT("channels must be 1..512"));
    }
    if (refresh_rate < 1 || refresh_rate > 1000) {
        mp_raise_ValueError(MP_ERROR_TEXT("refresh_rate must be 1..1000"));
    }

    dmx_native_release_resources();

    dmx_state.tx_pin = (uint8_t)args[ARG_tx_pin].u_int;
    dmx_state.trigger_pin = (uint8_t)args[ARG_trigger_pin].u_int;
    dmx_state.channels = (uint16_t)channels;
    dmx_state.refresh_rate = (uint16_t)refresh_rate;
    dmx_state.requested_ctrl_sm = (uint8_t)args[ARG_sm_ctrl_id].u_int;
    dmx_state.requested_data_sm = (uint8_t)args[ARG_sm_data_id].u_int;
    dmx_state.frame_time_us = DMX_BREAK_US + DMX_MAB_US + ((uint32_t)(dmx_state.channels + 1) * DMX_SLOT_US);
    dmx_state.timer_period_us = -((int64_t)dmx_state.frame_time_us);
    memset(dmx_state.frame, 0, sizeof(dmx_state.frame));
    memset(dmx_state.tx_frame, 0, sizeof(dmx_state.tx_frame));
    memset(dmx_state.dirty_mask, 0, sizeof(dmx_state.dirty_mask));
    dmx_state.frame[0] = 0;
    dmx_state.tx_frame[0] = 0;
    dmx_state.dirty_first = DMX_NATIVE_FRAME_SLOTS;
    dmx_state.dirty_last = -1;
    dmx_state.initialized = true;
    dmx_state.running = false;
    dmx_state.frame_in_progress = false;
    dmx_state.frame_count = 0;
    dmx_state.skipped_callbacks = 0;
    dmx_state.prime_timeouts = 0;
    dmx_state.frame_timeouts = 0;
    dmx_state.auto_resyncs = 0;
    dmx_state.data_version = 0;
    dmx_state.last_sent_version = 0;

    dmx_native_allocate_resources();
    dmx_native_configure_sms();

    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_KW(dmx_native_init_obj, 0, dmx_native_init);

static void dmx_native_require_init(void) {
    if (!dmx_state.initialized) {
        mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("call init() first"));
    }
}

static mp_obj_t dmx_native_start(void) {
    dmx_native_require_init();
    if (!dmx_state.resources_allocated) {
        dmx_native_allocate_resources();
    }

    // Always reinitialize SMs on start so program counters/FIFOs are in a known state.
    dmx_native_configure_sms();

    if (dmx_state.running) {
        return mp_const_none;
    }
    dmx_state.frame_in_progress = false;
    dmx_native_clear_pio_irqs();
    pio_sm_set_enabled(dmx_state.pio, dmx_state.data_sm_local, true);
    pio_sm_set_enabled(dmx_state.pio, dmx_state.ctrl_sm_local, true);
    sleep_ms(1);

    // The control SM pulls slot-count once before entering wrap_target.
    // Prime it before the first forced IRQ0 so DMX can actually start.
    pio_sm_put_blocking(dmx_state.pio, dmx_state.ctrl_sm_local, dmx_state.channels);

    dmx_state.running = true;
    if (!dmx_native_update_frame()) {
        dmx_state.running = false;
        mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("failed to start initial DMX frame"));
    }
    if (!add_repeating_timer_us(dmx_state.timer_period_us, dmx_native_timer_cb, NULL, &dmx_state.timer)) {
        dmx_state.running = false;
        dmx_native_stop_hw();
        mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("failed to start DMX timer"));
    }
    dmx_state.timer_active = true;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(dmx_native_start_obj, dmx_native_start);

static mp_obj_t dmx_native_stop(void) {
    dmx_native_require_init();
    dmx_state.running = false;
    dmx_native_stop_hw();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(dmx_native_stop_obj, dmx_native_stop);

static mp_obj_t dmx_native_is_running(void) {
    return mp_obj_new_bool(dmx_state.running);
}
static MP_DEFINE_CONST_FUN_OBJ_0(dmx_native_is_running_obj, dmx_native_is_running);

static mp_obj_t dmx_native_clear(void) {
    dmx_native_require_init();
    uint32_t irq_state = save_and_disable_interrupts();
    memset(dmx_state.frame, 0, sizeof(dmx_state.frame));
    dmx_state.frame[0] = 0;
    memset(dmx_state.dirty_mask, 1, dmx_state.channels + 1);
    dmx_state.dirty_first = 0;
    dmx_state.dirty_last = dmx_state.channels;
    dmx_state.data_version += 1;
    restore_interrupts(irq_state);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(dmx_native_clear_obj, dmx_native_clear);

static mp_obj_t dmx_native_set_channel(mp_obj_t channel_obj, mp_obj_t value_obj) {
    dmx_native_require_init();

    int channel = mp_obj_get_int(channel_obj);
    int value = mp_obj_get_int(value_obj);
    if (channel < 1 || channel > dmx_state.channels) {
        mp_raise_ValueError(MP_ERROR_TEXT("channel out of range"));
    }
    if (value < 0 || value > 255) {
        mp_raise_ValueError(MP_ERROR_TEXT("value must be 0..255"));
    }

    uint32_t irq_state = save_and_disable_interrupts();
    if (dmx_state.frame[channel] != (uint8_t)value) {
        dmx_state.frame[channel] = (uint8_t)value;
        dmx_native_mark_dirty((uint16_t)channel);
        dmx_state.data_version += 1;
    }
    restore_interrupts(irq_state);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(dmx_native_set_channel_obj, dmx_native_set_channel);

static mp_obj_t dmx_native_set_channels(mp_obj_t data_obj) {
    dmx_native_require_init();

    mp_buffer_info_t bufinfo;
    mp_get_buffer_raise(data_obj, &bufinfo, MP_BUFFER_READ);
    size_t n = bufinfo.len;
    if (n > dmx_state.channels) {
        n = dmx_state.channels;
    }

    uint32_t irq_state = save_and_disable_interrupts();
    const uint8_t *src = (const uint8_t *)bufinfo.buf;
    for (size_t i = 0; i < n; ++i) {
        uint16_t idx = (uint16_t)(i + 1);
        if (dmx_state.frame[idx] != src[i]) {
            dmx_state.frame[idx] = src[i];
            dmx_native_mark_dirty(idx);
        }
    }
    if (n > 0) {
        dmx_state.data_version += 1;
    }
    restore_interrupts(irq_state);
    return mp_obj_new_int_from_uint(n);
}
static MP_DEFINE_CONST_FUN_OBJ_1(dmx_native_set_channels_obj, dmx_native_set_channels);

static mp_obj_t dmx_native_status(void) {
    mp_obj_t dict = mp_obj_new_dict(0);
    mp_obj_dict_store(dict, MP_OBJ_NEW_QSTR(MP_QSTR_initialized), mp_obj_new_bool(dmx_state.initialized));
    mp_obj_dict_store(dict, MP_OBJ_NEW_QSTR(MP_QSTR_running), mp_obj_new_bool(dmx_state.running));
    mp_obj_dict_store(dict, MP_OBJ_NEW_QSTR(MP_QSTR_tx_pin), mp_obj_new_int(dmx_state.tx_pin));
    mp_obj_dict_store(dict, MP_OBJ_NEW_QSTR(MP_QSTR_trigger_pin), mp_obj_new_int(dmx_state.trigger_pin));
    mp_obj_dict_store(dict, MP_OBJ_NEW_QSTR(MP_QSTR_channels), mp_obj_new_int(dmx_state.channels));
    mp_obj_dict_store(dict, MP_OBJ_NEW_QSTR(MP_QSTR_refresh_rate), mp_obj_new_int(dmx_state.refresh_rate));
    mp_obj_dict_store(dict, MP_OBJ_NEW_QSTR(MP_QSTR_sm_ctrl_id), mp_obj_new_int(dmx_state.active_ctrl_sm));
    mp_obj_dict_store(dict, MP_OBJ_NEW_QSTR(MP_QSTR_sm_data_id), mp_obj_new_int(dmx_state.active_data_sm));
    mp_obj_dict_store(dict, MP_OBJ_NEW_QSTR(MP_QSTR_pio_block), mp_obj_new_int(dmx_state.pio_index));
    mp_obj_dict_store(dict, MP_OBJ_NEW_QSTR(MP_QSTR_frame_count), mp_obj_new_int_from_uint(dmx_state.frame_count));
    mp_obj_dict_store(dict, MP_OBJ_NEW_QSTR(MP_QSTR_skipped_callbacks), mp_obj_new_int_from_uint(dmx_state.skipped_callbacks));
    mp_obj_dict_store(dict, MP_OBJ_NEW_QSTR(MP_QSTR_prime_timeouts), mp_obj_new_int_from_uint(dmx_state.prime_timeouts));
    mp_obj_dict_store(dict, MP_OBJ_NEW_QSTR(MP_QSTR_frame_timeouts), mp_obj_new_int_from_uint(dmx_state.frame_timeouts));
    mp_obj_dict_store(dict, MP_OBJ_NEW_QSTR(MP_QSTR_auto_resyncs), mp_obj_new_int_from_uint(dmx_state.auto_resyncs));
    return dict;
}
static MP_DEFINE_CONST_FUN_OBJ_0(dmx_native_status_obj, dmx_native_status);

static const mp_rom_map_elem_t dmx_native_module_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_dmx_native) },
    { MP_ROM_QSTR(MP_QSTR_init), MP_ROM_PTR(&dmx_native_init_obj) },
    { MP_ROM_QSTR(MP_QSTR_start), MP_ROM_PTR(&dmx_native_start_obj) },
    { MP_ROM_QSTR(MP_QSTR_stop), MP_ROM_PTR(&dmx_native_stop_obj) },
    { MP_ROM_QSTR(MP_QSTR_is_running), MP_ROM_PTR(&dmx_native_is_running_obj) },
    { MP_ROM_QSTR(MP_QSTR_clear), MP_ROM_PTR(&dmx_native_clear_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_channel), MP_ROM_PTR(&dmx_native_set_channel_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_channels), MP_ROM_PTR(&dmx_native_set_channels_obj) },
    { MP_ROM_QSTR(MP_QSTR_status), MP_ROM_PTR(&dmx_native_status_obj) },
};
static MP_DEFINE_CONST_DICT(dmx_native_module_globals, dmx_native_module_globals_table);

const mp_obj_module_t dmx_native_user_cmodule = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&dmx_native_module_globals,
};

MP_REGISTER_MODULE(MP_QSTR_dmx_native, dmx_native_user_cmodule);