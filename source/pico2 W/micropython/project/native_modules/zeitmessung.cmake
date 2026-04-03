# Zeitmessung Project Configuration
# This CMakeLists.txt is included by the main build to add project-specific customizations

# Add custom REPL banner to identify this as Zeitmessung firmware
# This will be included in the build after user modules are processed
if(TARGET firmware)
    target_compile_definitions(firmware PRIVATE
        MICROPY_BANNER_MACHINE="Raspberry Pi Pico 2 W [Zeitmessung FW] with RP2350"
    )
endif()
