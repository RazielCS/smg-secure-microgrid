. C:\Espressif\tools\Microsoft.v5.4.2.PowerShell_profile.ps1
$toolchainBin = "C:\Espressif\tools\xtensa-esp-elf\esp-14.2.0_20241119\xtensa-esp-elf\bin"
$toolchainLibexec = "C:\Espressif\tools\xtensa-esp-elf\esp-14.2.0_20241119\xtensa-esp-elf\libexec\gcc\xtensa-esp-elf\14.2.0"
$mpyCross = "C:\micropython\mpy-cross\build"
$env:PATH = "$mpyCross;$toolchainBin;$toolchainLibexec;$env:PATH"
$env:USER_C_MODULES = "C:/Users/racas/Latex/protocolo_hashchain_smg/ports/aesgcm/micropython.cmake"
$env:IDF_PATH = "C:\esp\v5.4.2\esp-idf"
Set-Location C:\micropython\ports\esp32
idf.py build 2>&1
