$env:PATH = "C:\msys64\ucrt64\bin;C:\msys64\usr\bin;" + $env:PATH
Set-Location C:\micropython\mpy-cross
& make.exe all 2>&1
