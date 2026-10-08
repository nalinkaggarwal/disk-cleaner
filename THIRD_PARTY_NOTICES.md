# Third-party notices

TickClean's own code is MIT licensed (see `LICENSE`) and has no runtime dependencies. The standalone
`TickClean.exe` is built with PyInstaller and bundles the following, unmodified:

| Component | Used for | License |
|---|---|---|
| CPython runtime and standard library (including `tkinter`) | running the program | Python Software Foundation License |
| Tcl/Tk | the window toolkit behind `tkinter` | Tcl/Tk (BSD-style) license |
| PyInstaller bootloader | starting the bundled program | GPL-2.0-or-later with a special exception that permits distributing the bootloader in executables built with PyInstaller regardless of the program's license |

The licenses of the build tools and bundled runtime are available from their projects:
<https://docs.python.org/3/license.html>, <https://www.tcl-lang.org/software/tcltk/license.html>,
<https://github.com/pyinstaller/pyinstaller/blob/develop/COPYING.txt>.
