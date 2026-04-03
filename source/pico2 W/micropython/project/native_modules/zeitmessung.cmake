# Zeitmessung Project Configuration
# This CMakeLists.txt is included by the main build to add project-specific customizations

# Add custom REPL banner to identify this as Zeitmessung firmware
# This will be included in the build after user modules are processed
string(TIMESTAMP ZEITMESSUNG_BUILD_TIMESTAMP "%Y-%m-%d %H:%M:%S")
set(ZEITMESSUNG_BANNER "Raspberry Pi Pico 2 W [Zeitmessung FW] with RP2350 (built ${ZEITMESSUNG_BUILD_TIMESTAMP})")

if(TARGET firmware)
    target_compile_definitions(firmware PRIVATE
        MICROPY_BANNER_MACHINE="${ZEITMESSUNG_BANNER}"
    )
endif()
