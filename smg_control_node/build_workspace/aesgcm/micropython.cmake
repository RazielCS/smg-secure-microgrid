add_library(usermod_aesgcm INTERFACE)

target_sources(usermod_aesgcm INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/aesgcm.c
)

target_include_directories(usermod_aesgcm INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}
)

target_link_libraries(usermod INTERFACE usermod_aesgcm)
