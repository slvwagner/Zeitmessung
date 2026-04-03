add_library(usermod_rc522_native INTERFACE)

target_sources(usermod_rc522_native INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/modrc522_native.c
)

target_include_directories(usermod_rc522_native INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}
)

target_link_libraries(usermod_rc522_native INTERFACE
    hardware_spi
    hardware_gpio
    pico_stdlib
)

target_link_libraries(usermod INTERFACE usermod_rc522_native)
