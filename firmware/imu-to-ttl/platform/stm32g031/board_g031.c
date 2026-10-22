/**
 * STM32G031F8P6 board layer - IMU-to-TTL v1.0.
 * AUTO half-duplex bus: PA2 (USART2_TX) drives 1G125 A + /OE; PA3 is RX
 * tapped directly on the bus (FT pin, 150R + 5.1V zener + 100R PTC).
 * Local echo of our own transmission is discarded at the ISR level.
 */
#include "board.h"
#include "md_config.h"
#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

#if defined(MD_PLATFORM_STM32G031)

#define PERIPH_BASE        0x40000000u
#define AHBPERIPH_BASE     (PERIPH_BASE + 0x00020000u)
#define RCC_BASE           (AHBPERIPH_BASE + 0x00001000u)
#define GPIOA_BASE         0x50000000u  /* IOPORT region on STM32G0 */

#define USART2_BASE        (PERIPH_BASE + 0x00004400u)
#define SPI1_BASE          (PERIPH_BASE + 0x00013000u)

#define RCC_IOPENR         (*(volatile uint32_t *)(RCC_BASE + 0x34u))
#define RCC_APBENR1        (*(volatile uint32_t *)(RCC_BASE + 0x3Cu))
#define RCC_APBENR2        (*(volatile uint32_t *)(RCC_BASE + 0x40u))

#define GPIOA_MODER        (*(volatile uint32_t *)(GPIOA_BASE + 0x00u))
#define GPIOA_OSPEEDR      (*(volatile uint32_t *)(GPIOA_BASE + 0x08u))
#define GPIOA_AFRL         (*(volatile uint32_t *)(GPIOA_BASE + 0x20u))
#define GPIOA_BSRR         (*(volatile uint32_t *)(GPIOA_BASE + 0x18u))

#define USART_CR1          (*(volatile uint32_t *)(USART2_BASE + 0x00u))
#define USART_CR3          (*(volatile uint32_t *)(USART2_BASE + 0x08u))
#define USART_BRR          (*(volatile uint32_t *)(USART2_BASE + 0x0Cu))
#define USART_ISR          (*(volatile uint32_t *)(USART2_BASE + 0x1Cu))
#define USART_RDR          (*(volatile uint32_t *)(USART2_BASE + 0x24u))
#define USART_TDR          (*(volatile uint32_t *)(USART2_BASE + 0x28u))

#define NVIC_ISER0         (*(volatile uint32_t *)0xE000E100u)

#define SPI_CR1            (*(volatile uint32_t *)(SPI1_BASE + 0x00u))
#define SPI_CR2            (*(volatile uint32_t *)(SPI1_BASE + 0x04u))
#define SPI_SR             (*(volatile uint32_t *)(SPI1_BASE + 0x08u))
#define SPI_DR             (*(volatile uint32_t *)(SPI1_BASE + 0x0Cu))

#ifndef MD_PLATFORM_IRQ_USART2
#define MD_PLATFORM_IRQ_USART2 28u
#endif

#define PIN_MODER(pin, mode) \
    do { \
        GPIOA_MODER = (GPIOA_MODER & ~(3u << ((pin) * 2))) | ((uint32_t)(mode) << ((pin) * 2)); \
    } while (0)

static volatile uint32_t g_ms;
static volatile uint8_t g_rx_q[64];
static volatile uint8_t g_rx_r, g_rx_w;
static volatile bool g_tx_active;
static volatile uint32_t g_echo_dropped;

void SysTick_Handler(void)
{
    g_ms++;
}

/* USART2 IRQ: enqueue received bytes, drop the local echo while transmitting. */
void USART2_IRQHandler(void)
{
    uint32_t isr = USART_ISR;
    /* 错误标志清刷:ORE/FE/NE 滞留会堵死接收路径(上电总线毛刺的典型后遗症)。
     * G0 USART_ISR: FE=bit1 NE=bit2 ORE=bit3;ICR 写 1 清除(已对 CMSIS 头核对)。 */
    if (isr & ((1u << 3) | (1u << 2) | (1u << 1))) {
        (*(volatile uint32_t *)(USART2_BASE + 0x20u)) = (1u << 3) | (1u << 2) | (1u << 1); /* ICR */
        if (isr & (1u << 5)) {
            (void)USART_RDR; /* 清残留数据 */
        }
        return;
    }
    if (isr & (1u << 5)) { /* RXNE */
        uint8_t b = (uint8_t)USART_RDR;
        if (g_tx_active) {
            g_echo_dropped++;
        } else {
            uint8_t next = (uint8_t)((g_rx_w + 1u) & 63u);
            if (next != g_rx_r) {
                g_rx_q[g_rx_w] = b;
                g_rx_w = next;
            }
        }
    }
}

static void gpio_init(void)
{
    RCC_IOPENR |= (1u << 0); /* GPIOA */

    /* PA0 IMU_INT input (reserved) */
    PIN_MODER(MD_PIN_IMU_INT, 0);

    /* PA4 SPI CS output high */
    PIN_MODER(MD_PIN_SPI_CS, 1);
    GPIOA_BSRR = (1u << MD_PIN_SPI_CS);

    /* PB7/PB8 (U1.1, RX_EN net): drive HIGH after boot. The board straps this
     * pin to GND through 10k so the (option-byte-less clone) MCU reliably
     * boots from flash; without this drive the strap would hold the bus
     * transceiver's 2OE low and leave the board deaf. */
    {
        #define GPIOB_BASE_ 0x50000400u
        #define GPIOB_MODER_ (*(volatile uint32_t *)(GPIOB_BASE_ + 0x00u))
        #define GPIOB_BSRR_  (*(volatile uint32_t *)(GPIOB_BASE_ + 0x18u))
        RCC_IOPENR |= (1u << 1);              /* GPIOB clock */
        GPIOB_MODER_ = (GPIOB_MODER_ & ~(3u << 7 * 2)) | (1u << 7 * 2); /* PB7 out */
        GPIOB_BSRR_ = (1u << 7);              /* high = receive enable */
    }

    /* PA2/PA3 AF1 USART2 */
    PIN_MODER(MD_PIN_USART2_TX, 2);
    PIN_MODER(MD_PIN_USART2_RX, 2);
    GPIOA_AFRL = (GPIOA_AFRL & ~(0xFFu << (MD_PIN_USART2_TX * 4))) |
                 (0x11u << (MD_PIN_USART2_TX * 4));
#ifdef MD_BUS_DE
    /* PA1 -> U3.1 1OE# (low = transmit enable), software-driven: the clone
     * MCU's USART2 has no working DEM (DE output never asserts, and DEAT/DEDT
     * refuse to write), so the hardware DE mode cannot be used. */
    PIN_MODER(MD_PIN_USART2_DE, 1);
    GPIOA_BSRR = (1u << MD_PIN_USART2_DE); /* high = TX disabled at idle */
#endif

    /* PA5/6/7 AF0 SPI1 */
    PIN_MODER(MD_PIN_SPI_SCK, 2);
    PIN_MODER(MD_PIN_SPI_MISO, 2);
    PIN_MODER(MD_PIN_SPI_MOSI, 2);
    GPIOA_AFRL &= ~((0xFul << (MD_PIN_SPI_SCK * 4)) |
                    (0xFul << (MD_PIN_SPI_MISO * 4)) |
                    (0xFul << (MD_PIN_SPI_MOSI * 4)));
    GPIOA_OSPEEDR |= (3u << (MD_PIN_SPI_SCK * 2)) | (3u << (MD_PIN_SPI_MOSI * 2));
}

static void usart2_init(void)
{
    RCC_APBENR1 |= (1u << 17); /* USART2EN */
    USART_CR1 = 0;
    USART_CR3 = 0; /* no DEM: direction pin is software-driven (clone MCU) */
    USART_BRR = 16; /* 16 MHz HSI / 1 Mbps */
    USART_CR1 = (1u << 5) | (1u << 3) | (1u << 2) | (1u << 0); /* RXNEIE TE RE UE */
    NVIC_ISER0 = (1u << MD_PLATFORM_IRQ_USART2);
}

static void spi1_init(void)
{
    RCC_APBENR2 |= (1u << 12); /* SPI1EN */
    SPI_CR1 = 0;
    SPI_CR1 = (1u << 2) | (1u << 8) | (1u << 9) | (2u << 3); /* MSTR SSM SSI BR/8 */
    /* DS=8bit + FRXTH: RXNE at 1-byte FIFO level. Without FRXTH, RXNE only
     * asserts at the 16-bit level and a single 8-bit transfer never completes
     * - spi_xfer spins forever on real silicon (Renode's SPI model does not
     * reproduce the FIFO threshold, so the gate missed it). */
    SPI_CR2 = (7u << 8) | (1u << 12);
    SPI_CR1 |= (1u << 6); /* SPE */
}

static uint8_t spi_xfer(uint8_t v)
{
    while ((SPI_SR & (1u << 1)) == 0) {
    }
    *(volatile uint8_t *)&SPI_DR = v;
    while ((SPI_SR & (1u << 0)) == 0) {
    }
    return *(volatile uint8_t *)&SPI_DR;
}

void board_init(void)
{
    /* Vector table lives in main flash. On real silicon VTOR=0 works because
     * flash is aliased at 0x00000000, but set it explicitly: same code path
     * regardless of boot config, and required under emulation. */
    *(volatile uint32_t *)0xE000ED08u = 0x08000000u; /* SCB->VTOR */

    gpio_init();
    usart2_init();
    spi1_init();
    /* SysTick 1 ms at 16 MHz */
    *(volatile uint32_t *)0xE000E014u = 16000u - 1u; /* LOAD */
    *(volatile uint32_t *)0xE000E018u = 0;           /* clear current value */
    *(volatile uint32_t *)0xE000E010u = 7u;          /* enable, core clock, IRQ */
    g_ms = 0;
    g_rx_r = g_rx_w = 0;
}

void board_delay_ms(uint32_t ms)
{
    uint32_t start = g_ms;
    while ((g_ms - start) < ms) {
    }
}

uint32_t board_millis(void)
{
    return g_ms;
}

void board_idle(void)
{
    __asm__ volatile("wfi");
}

void board_bus_write(const uint8_t *data, size_t len)
{
    g_tx_active = true;
#ifdef MD_BUS_DE
    GPIOA_BSRR = (1u << (MD_PIN_USART2_DE + 16)); /* 1OE# low: drive the bus */
#endif
    for (size_t i = 0; i < len; i++) {
        while ((USART_ISR & (1u << 7)) == 0) {
        }
        USART_TDR = data[i];
    }
    while ((USART_ISR & (1u << 6)) == 0) {
    } /* TC */
    /* window for the trailing echo byte to be dropped by the ISR */
    for (volatile uint32_t i = 0; i < 40u; i++) {
    }
#ifdef MD_BUS_DE
    GPIOA_BSRR = (1u << MD_PIN_USART2_DE); /* 1OE# high: release the bus */
#endif
    /* flush anything that still made it into the queue */
    while (USART_ISR & (1u << 5)) {
        (void)USART_RDR;
    }
    g_tx_active = false;
}

bool board_bus_read_byte(uint8_t *out)
{
    if (g_rx_r == g_rx_w) {
        return false;
    }
    *out = g_rx_q[g_rx_r];
    g_rx_r = (uint8_t)((g_rx_r + 1u) & 63u);
    return true;
}

int32_t board_imu_spi_write(void *user, uint8_t reg, const uint8_t *data, uint16_t len)
{
    (void)user;
    GPIOA_BSRR = (1u << (MD_PIN_SPI_CS + 16));
    (void)spi_xfer((uint8_t)(reg & 0x7Fu));
    for (uint16_t i = 0; i < len; i++) {
        (void)spi_xfer(data[i]);
    }
    GPIOA_BSRR = (1u << MD_PIN_SPI_CS);
    return 0;
}

int32_t board_imu_spi_read(void *user, uint8_t reg, uint8_t *data, uint16_t len)
{
    (void)user;
    GPIOA_BSRR = (1u << (MD_PIN_SPI_CS + 16));
    (void)spi_xfer((uint8_t)(reg | 0x80u));
    for (uint16_t i = 0; i < len; i++) {
        data[i] = spi_xfer(0xFFu);
    }
    GPIOA_BSRR = (1u << MD_PIN_SPI_CS);
    return 0;
}

#else
/* Non-G031 translation unit placeholder - host platform supplies board_*. */
#endif
