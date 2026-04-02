add_library(usermod_dmx_native INTERFACE)

target_sources(usermod_dmx_native INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/moddmx_native.c
)

pico_generate_pio_header(usermod_dmx_native
    ${CMAKE_CURRENT_LIST_DIR}/dmx_native_sdk.pio
)

target_include_directories(usermod_dmx_native INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}
)

target_link_libraries(usermod_dmx_native INTERFACE
    hardware_clocks
    hardware_dma
    hardware_pio
    hardware_sync
    pico_stdlib
)

target_link_libraries(usermod INTERFACE usermod_dmx_native)