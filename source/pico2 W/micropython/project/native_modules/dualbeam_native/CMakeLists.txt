set(MODULE_NAME "dualbeam_native")
add_library(${MODULE_NAME} INTERFACE)
target_sources(${MODULE_NAME} INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/dualbeam_native.c
)
target_include_directories(${MODULE_NAME} INTERFACE ${CMAKE_CURRENT_LIST_DIR})
