# Project-local MicroPython user C modules.

include(${CMAKE_CURRENT_LIST_DIR}/dmx_native/micropython.cmake)
include(${CMAKE_CURRENT_LIST_DIR}/rc522_native/micropython.cmake)
include(${CMAKE_CURRENT_LIST_DIR}/dualbeam_native/micropython.cmake)
include(${CMAKE_CURRENT_LIST_DIR}/zeitmessung.cmake)

# Post-build hook will be added by CMakeLists.txt after firmware target is created
# Store the firmware copy paths as variables that CMakeLists.txt will use
set(FIRMWARE_COPY_ENABLED TRUE CACHE INTERNAL "Enable firmware copy to project directory")
set(FIRMWARE_OUTPUT_DIR ${CMAKE_CURRENT_LIST_DIR}/../../firmware CACHE INTERNAL "Firmware output directory")