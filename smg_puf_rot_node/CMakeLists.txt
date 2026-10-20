cmake_minimum_required(VERSION 3.16)

# esp32_puflib is vendored under components/esp32_puflib (see that directory's README for
# provenance and the fixes applied here relative to the staniond/esp32_puflib upstream).
# ESP-IDF auto-discovers components/ subdirectories of the project, so no
# EXTRA_COMPONENT_DIRS entry is needed.

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(smg_puf_rot_node)
