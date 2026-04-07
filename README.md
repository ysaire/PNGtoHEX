# ch2hp - Binary Asset to C++ Header Converter
This Python script automatically converts all .png files within a directory into a single C++ header file (.hpp).

It is a lightweight tool designed for embedding icons, images, or assets directly into your application's binary—perfect for embedded systems (Arduino, ESP32, STM32) or C++ applications that need to run without an external file system.

# How to use
Place the convert.py script in the folder containing your images (or modify the path in the script).

Run the script: python convert.py

A file named icons_bin.hpp will be generated in the same directory.

# Output example
```c++
// File: logo.png - 1240 bytes
const unsigned char logo[] = {
    0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d,
    0x49, 0x48, 0x44, 0x52, 0x00, 0x00, 0x00, 0x20, 0x00, 0x00, 0x00, 0x20,
    // ... rest of the hex data
};
const unsigned int logo_len = 1240;
```
