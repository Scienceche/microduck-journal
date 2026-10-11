#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Feetech STS/SCS slave protocol (half duplex TTL).
 * Request : FF FF ID LEN INST PARAM... CHKSUM     (LEN = N + 2)
 * Response: FF FF ID LEN ERR  DATA...  CHKSUM     (LEN = 1 + len(DATA))
 * CHKSUM  = ~(ID + LEN + INST/ERR + bytes...) & 0xFF
 */

#define MD_FT_INST_PING        0x01u
#define MD_FT_INST_READ        0x02u
#define MD_FT_INST_WRITE       0x03u
#define MD_FT_INST_SYNC_READ   0x82u
#define MD_FT_BROADCAST_ID     0xFEu

typedef void (*md_ft_write_fn)(void *user, const uint8_t *data, size_t len);

typedef struct {
    uint8_t id;
    uint8_t *table;
    uint16_t table_size;
    md_ft_write_fn write;
    void *write_user;

    /* parser state */
    uint8_t state;
    uint8_t buf[40];
    uint8_t n;

    /* transmit guard: input during our own transmission is ignored */
    bool tx_active;
} md_ft_slave_t;

void md_ft_slave_init(md_ft_slave_t *s, uint8_t id, uint8_t *table, uint16_t table_size,
                      md_ft_write_fn write, void *user);

/* Feed one received byte from the bus. */
void md_ft_slave_on_byte(md_ft_slave_t *s, uint8_t b);

/* Call around board_bus_write() so the local echo can never be parsed. */
void md_ft_slave_tx_begin(md_ft_slave_t *s);
void md_ft_slave_tx_end(md_ft_slave_t *s);

/* Exposed for tests: compute the protocol checksum. */
uint8_t md_ft_checksum(const uint8_t *bytes, size_t len);

#ifdef __cplusplus
}
#endif
