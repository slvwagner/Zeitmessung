# dualbeam_native user C module for MicroPython
set(DUALBEAM_NATIVE_PATH ${CMAKE_CURRENT_LIST_DIR})
include_directories(${DUALBEAM_NATIVE_PATH})
add_subdirectory(${DUALBEAM_NATIVE_PATH} ${CMAKE_BINARY_DIR}/dualbeam_native)
