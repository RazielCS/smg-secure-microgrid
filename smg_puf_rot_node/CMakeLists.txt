cmake_minimum_required(VERSION 3.16)

# esp32_puflib is a plain ESP-IDF component (idf_component_register), referenced in place
# per its own README rather than vendored/copied. Clone it as a sibling of this repo's
# root (i.e. one level above this directory):
#   git clone https://github.com/RazielCS/smg-secure-microgrid
#   git clone https://github.com/staniond/esp32_puflib smg-secure-microgrid/esp32_puflib
# its parent directory is added as an extra component search path.
set(EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/..")

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(smg_puf_rot_node)
