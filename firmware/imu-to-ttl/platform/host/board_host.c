/* Host (PC) board model for firmware tests. */
#include "board.h"
#include <string.h>
#include <stdio.h>

static uint8_t g_rx_q[512];
static unsigned g_rx_r, g_rx_w;
static uint8_t g_last_tx[128];
static size_t g_last_tx_len;
static uint32_t g_ms;
static size_t g_echo_dropped;
static int g_echo_sim = 1;

void board_init(void)
{
    g_rx_r = g_rx_w = 0;
    g_last_tx_len = 0;
    g_ms = 0;
    g_echo_dropped = 0;
}

void board_delay_ms(uint32_t ms) { g_ms += ms; }
uint32_t board_millis(void) { return g_ms; }
void board_idle(void) { }

void board_bus_write(const uint8_t *data, size_t len)
{
    if (len > sizeof(g_last_tx)) {
        len = sizeof(g_last_tx);
    }
    memcpy(g_last_tx, data, len);
    g_last_tx_len = len;
    /* AUTO bus: every transmitted byte is seen again on RX and discarded
     * by the board before it can reach the parser. Model that here. */
    if (g_echo_sim) {
        g_echo_dropped += len;
    }
}

bool board_bus_read_byte(uint8_t *out)
{
    if (g_rx_r == g_rx_w) {
        return false;
    }
    *out = g_rx_q[g_rx_r++ & 511u];
    return true;
}

/* ---- test helpers ---- */

void host_feed(const uint8_t *data, size_t len)
{
    for (size_t i = 0; i < len; i++) {
        g_rx_q[g_rx_w++ & 511u] = data[i];
    }
}

size_t host_last_tx(uint8_t *out, size_t max)
{
    size_t n = g_last_tx_len < max ? g_last_tx_len : max;
    memcpy(out, g_last_tx, n);
    return n;
}

void host_clear_tx(void) { g_last_tx_len = 0; }
size_t host_echo_dropped(void) { return g_echo_dropped; }
void host_echo_sim_enable(int on) { g_echo_sim = on; }

/* Fake IMU SPI */
int32_t board_imu_spi_write(void *user, uint8_t reg, const uint8_t *data, uint16_t len)
{
    (void)user; (void)reg; (void)data; (void)len;
    return 0;
}

int32_t board_imu_spi_read(void *user, uint8_t reg, uint8_t *data, uint16_t len)
{
    (void)user;
    memset(data, 0, len);
    if (reg == 0x0Fu && len >= 1) {
        data[0] = 0x70; /* WHO_AM_I = LSM6DSV16X */
    }
    return 0;
}
