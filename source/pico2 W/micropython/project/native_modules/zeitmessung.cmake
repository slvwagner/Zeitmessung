# Zeitmessung Project Configuration
# This CMakeLists.txt is included by the main build to add project-specific customizations

# Add custom REPL banner to identify this as Zeitmessung firmware
# This will be included in the build after user modules are processed
string(TIMESTAMP ZEITMESSUNG_BUILD_TIMESTAMP "%Y-%m-%d %H:%M:%S")
set(ZEITMESSUNG_BANNER "Firmware for ZeitmessungRaspberry Pi Pico 2 W (built ${ZEITMESSUNG_BUILD_TIMESTAMP})")
message(STATUS "Zeitmessung CMake: Setting MICROPY_BANNER_MACHINE = ${ZEITMESSUNG_BANNER}")

# Use global add_compile_definitions since firmware target may not exist yet
add_compile_definitions(MICROPY_BANNER_MACHINE="${ZEITMESSUNG_BANNER}")
message(STATUS "Zeitmessung CMake: MICROPY_BANNER_MACHINE applied globally")

