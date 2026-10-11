/**
 * Minimal Cortex-M0+ startup for STM32G031F8 (64K flash / 8K RAM).
 */
  .syntax unified
  .cpu cortex-m0plus
  .fpu softvfp
  .thumb

.global g_pfnVectors
.global Default_Handler
.global Reset_Handler

.word _sidata
.word _sdata
.word _edata
.word _sbss
.word _ebss

  .section .text.Reset_Handler
  .weak Reset_Handler
  .type Reset_Handler, %function
Reset_Handler:
  ldr r0, =_estack
  mov sp, r0
  ldr r0, =_sdata
  ldr r1, =_edata
  ldr r2, =_sidata
  movs r3, #0
  b LoopCopyDataInit
CopyDataInit:
  ldr r4, [r2, r3]
  str r4, [r0, r3]
  adds r3, r3, #4
LoopCopyDataInit:
  adds r4, r0, r3
  cmp r4, r1
  bcc CopyDataInit
  ldr r2, =_sbss
  ldr r4, =_ebss
  movs r3, #0
  b LoopFillZerobss
FillZerobss:
  str r3, [r2]
  adds r2, r2, #4
LoopFillZerobss:
  cmp r2, r4
  bcc FillZerobss
  bl main
  b .

  .section .text.Default_Handler,"ax",%progbits
Default_Handler:
  b Default_Handler

  .weak SysTick_Handler
  .thumb_set SysTick_Handler, Default_Handler

  .weak USART2_IRQHandler
  .thumb_set USART2_IRQHandler, Default_Handler

  .section .isr_vector,"a",%progbits
  .type g_pfnVectors, %object
g_pfnVectors:
  .word _estack
  .word Reset_Handler
  .word Default_Handler /* NMI */
  .word Default_Handler /* HardFault */
  .word 0
  .word 0
  .word 0
  .word 0
  .word 0
  .word 0
  .word 0
  .word Default_Handler /* SVCall */
  .word 0
  .word 0
  .word Default_Handler /* PendSV */
  .word SysTick_Handler
  /* IRQ 0..31 (STM32G031 has 32 peripheral IRQs) */
  .word Default_Handler /* 0  WWDG */
  .word Default_Handler /* 1  PVD */
  .word Default_Handler /* 2  RTC_TAMP */
  .word Default_Handler /* 3  FLASH */
  .word Default_Handler /* 4  RCC */
  .word Default_Handler /* 5  EXTI0_1 */
  .word Default_Handler /* 6  EXTI2_3 */
  .word Default_Handler /* 7  EXTI4_15 */
  .word Default_Handler /* 8  reserved */
  .word Default_Handler /* 9  DMA1_Ch1 */
  .word Default_Handler /* 10 DMA1_Ch2_3_DMA2_Ch1_2 */
  .word Default_Handler /* 11 DMA1_Ch4_7_DMAMUX */
  .word Default_Handler /* 12 ADC1 */
  .word Default_Handler /* 13 TIM1_BRK_UP_TRG_COM */
  .word Default_Handler /* 14 TIM1_CC */
  .word Default_Handler /* 15 TIM2 */
  .word Default_Handler /* 16 TIM3 */
  .word Default_Handler /* 17 reserved */
  .word Default_Handler /* 18 reserved */
  .word Default_Handler /* 19 TIM14 */
  .word Default_Handler /* 20 reserved */
  .word Default_Handler /* 21 TIM16 */
  .word Default_Handler /* 22 TIM17 */
  .word Default_Handler /* 23 I2C1 */
  .word Default_Handler /* 24 I2C2 */
  .word Default_Handler /* 25 SPI1 */
  .word Default_Handler /* 26 SPI2 */
  .word Default_Handler /* 27 USART1 */
  .word USART2_IRQHandler /* 28 USART2 (TTL bus) */
  .word Default_Handler /* 29 LPUART1 */
  .word Default_Handler /* 30 reserved */
  .word Default_Handler /* 31 reserved */
  .size g_pfnVectors, .-g_pfnVectors
