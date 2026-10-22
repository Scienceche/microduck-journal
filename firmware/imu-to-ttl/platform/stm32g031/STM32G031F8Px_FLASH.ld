/* STM32G031F8Px — 64K FLASH, 8K RAM */
ENTRY(Reset_Handler)

_estack = ORIGIN(RAM) + LENGTH(RAM);

MEMORY
{
  FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K
  RAM (xrw)  : ORIGIN = 0x20000000, LENGTH = 8K
}

SECTIONS
{
  .isr_vector :
  {
    KEEP(*(.isr_vector))
  } >FLASH

  .text :
  {
    *(.text*)
    *(.rodata*)
    KEEP(*(.init))
    KEEP(*(.fini))
    _etext = .;
  } >FLASH

  _sidata = LOADADDR(.data);

  .data :
  {
    _sdata = .;
    *(.data*)
    _edata = .;
  } >RAM AT>FLASH

  .bss :
  {
    _sbss = .;
    *(.bss*)
    *(COMMON)
    _ebss = .;
  } >RAM

  /* 看门狗复位不清 SRAM：复位计数放在 noinit 段跨复位存活（POR 仍清零） */
  .noinit (NOLOAD) :
  {
    . = ALIGN(4);
    __noinit_start__ = .;
    *(.noinit*)
    __noinit_end__ = .;
  } >RAM

  ._user_heap (NOLOAD) :
  {
    . = ALIGN(8);
    PROVIDE(_end = .);
    . = . + 0x400;
  } >RAM
}
