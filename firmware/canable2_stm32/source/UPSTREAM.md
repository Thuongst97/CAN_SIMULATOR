# Provenance of this source copy

* Repository: <https://github.com/normaldotcom/canable2-fw> (Openlight Labs, GPL-3.0)
* Commit: `b158aa7ab106bc018715cd4d5f02f544f9de1830` (2022-07-07, "Add printf library") – the commit of the official
  `canable2-b158aa7.bin` in `../bin/`
* Downloaded: 2026-10-18 from GitHub's archive of that commit (codeload.github.com)

Trimmed to what the Makefile builds (the full archive is 108 MB, mostly ST documentation and prebuilt libraries):

| Kept | Removed |
|---|---|
| `Makefile`, `STM32G431CBTx_FLASH.ld`, `LICENSE.md`, `README.md`, `inc/`, `src/` | – |
| `Middlewares/` (ST USB device library: core + CDC) | – |
| `Drivers/STM32G4xx_HAL_Driver/` (Inc, Src) | – |
| `Drivers/CMSIS/Include/`, `Drivers/CMSIS/Device/ST/STM32G4xx/` | `Drivers/CMSIS/{Core, Core_A, DSP, Lib}` (not referenced by the Makefile) |

No source file was modified. Build with
`make GIT_VERSION=b158aa7 GIT_REMOTE=github.com/normaldotcom/canable2-fw.git` (this copy is not a git checkout).
