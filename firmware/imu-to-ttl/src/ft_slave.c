#include "ft_slave.h"
#include "md_config.h"
#include <string.h>

enum {
    ST_FF1 = 0,
    ST_FF2,
    ST_ID,
    ST_LEN,
    ST_DATA,   /* INST + params + checksum, length = LEN */
};

/* --- helpers ------------------------------------------------------------ */

uint8_t md_ft_checksum(const uint8_t *bytes, size_t len)
{
    uint8_t sum = 0;
    for (size_t i = 0; i < len; i++) {
        sum = (uint8_t)(sum + bytes[i]);
    }
    return (uint8_t)~sum;
}

static void respond(md_ft_slave_t *s, uint8_t err, const uint8_t *data, uint8_t n)
{
    uint8_t pkt[3 + 1 + 1 + 32 + 1];
    if (n > 32u) {
        n = 32u;
    }
    /* LEN counts ERR + data + checksum (manual: PING reply FF FF 01 02 00 FC) */
    uint8_t len = (uint8_t)(n + 2u);
    pkt[0] = 0xFF;
    pkt[1] = 0xFF;
    pkt[2] = s->id;
    pkt[3] = len;
    pkt[4] = err;
    if (n) {
        memcpy(&pkt[5], data, n);
    }
    pkt[5 + n] = md_ft_checksum(&pkt[2], (size_t)(3 + n));
    s->write(s->write_user, pkt, (size_t)(6 + n));
}

static uint16_t clamp_read(const md_ft_slave_t *s, uint16_t addr, uint16_t len)
{
    if (addr >= s->table_size) {
        return 0;
    }
    uint16_t avail = (uint16_t)(s->table_size - addr);
    return len > avail ? avail : len;
}

static void handle_read(md_ft_slave_t *s, const uint8_t *p, uint8_t n)
{
    if (n < 2u) {
        return;
    }
    uint16_t addr = p[0];
    uint16_t len = clamp_read(s, addr, p[1]);
    if (len > 32u) {
        len = 32u;
    }
    respond(s, 0, len ? &s->table[addr] : NULL, (uint8_t)len);
}

static void handle_write(md_ft_slave_t *s, const uint8_t *p, uint8_t n)
{
    if (n < 1u) {
        return;
    }
    uint16_t addr = p[0];
    uint16_t len = (uint16_t)(n - 1u);
    /* Only safe fields are writable; anything else is accepted and ignored. */
    if (addr == MD_FT_ADDR_RETURN_DELAY && len >= 1u) {
        s->table[addr] = p[1];
    }
}

static void handle_packet(md_ft_slave_t *s)
{
    /* buf: [0]=ID [1]=LEN [2]=INST [3..]=params */
    uint8_t id = s->buf[0];
    uint8_t inst = s->buf[2];
    const uint8_t *params = &s->buf[3];
    uint8_t nparams = (uint8_t)(s->n - 3u);

    bool for_us = (id == s->id) || (id == MD_FT_BROADCAST_ID);

    switch (inst) {
    case MD_FT_INST_PING:
        if (for_us) {
            respond(s, 0, NULL, 0);
        }
        break;
    case MD_FT_INST_READ:
        if (id == s->id) {
            handle_read(s, params, nparams);
        }
        break;
    case MD_FT_INST_WRITE:
        if (for_us) {
            handle_write(s, params, nparams);
            if (id == s->id) {
                respond(s, 0, NULL, 0);
            }
        }
        break;
    case MD_FT_INST_SYNC_READ: {
        /* params: ADDR LEN id1 id2 ... idN */
        if (nparams >= 3u) {
            for (uint8_t i = 2u; i < nparams; i++) {
                if (params[i] == s->id) {
                    handle_read(s, params, 2u);
                    break;
                }
            }
        }
        break;
    }
    default:
        break;
    }
}

/* --- public API --------------------------------------------------------- */

void md_ft_slave_init(md_ft_slave_t *s, uint8_t id, uint8_t *table, uint16_t table_size,
                      md_ft_write_fn write, void *user)
{
    memset(s, 0, sizeof(*s));
    s->id = id;
    s->table = table;
    s->table_size = table_size;
    s->write = write;
    s->write_user = user;
    s->state = ST_FF1;
}

void md_ft_slave_tx_begin(md_ft_slave_t *s)
{
    s->tx_active = true;
    s->state = ST_FF1;
    s->n = 0;
}

void md_ft_slave_tx_end(md_ft_slave_t *s)
{
    s->tx_active = false;
    s->state = ST_FF1;
    s->n = 0;
}

void md_ft_slave_on_byte(md_ft_slave_t *s, uint8_t b)
{
    if (s->tx_active) {
        return; /* local echo during our own transmission */
    }
    switch (s->state) {
    case ST_FF1:
        if (b == 0xFF) {
            s->state = ST_FF2;
        }
        break;
    case ST_FF2:
        if (b == 0xFF) {
            /* stay: FF FF FF ... resync on the last two */
        } else {
            s->buf[0] = b; /* ID */
            s->n = 1;
            s->state = ST_LEN;
        }
        break;
    case ST_LEN:
        s->buf[1] = b; /* LEN (N+2) */
        if (b < 2u) {
            s->state = ST_FF1; /* invalid length */
        } else {
            s->n = 2;
            s->state = ST_DATA;
        }
        break;
    case ST_DATA: {
        s->buf[s->n++] = b;
        /* buf holds ID + LEN + (LEN bytes: INST/ERR + params + checksum) */
        if (s->n >= (uint8_t)(2u + s->buf[1])) {
            uint8_t total = (uint8_t)(2u + s->buf[1]);
            uint8_t chk = s->buf[total - 1];
            if (md_ft_checksum(s->buf, (size_t)(total - 1)) == chk && total >= 4u) {
                handle_packet(s);
            }
            s->state = ST_FF1;
            s->n = 0;
        }
        break;
    }
    default:
        s->state = ST_FF1;
        s->n = 0;
        break;
    }
}
