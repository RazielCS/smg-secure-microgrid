#!/bin/bash
set -e
SYSTEM_PATH=$PATH
while IFS='=' read -r key value; do
    export "$key=$value"
done < <(/home/raz/.espressif/tools/activate_idf_v5.4.2.sh -e)
export PATH=/home/raz/micropython/mpy-cross/build:$PATH:$SYSTEM_PATH
export USER_C_MODULES=/home/raz/aesgcm_module/micropython.cmake
export MICROPY_MPYCROSS=/home/raz/micropython/mpy-cross/build/mpy-cross
cd /home/raz/micropython/ports/esp32
rm -rf build
python3 $IDF_PATH/tools/idf.py -DUSER_C_MODULES=/home/raz/aesgcm_module/micropython.cmake build
