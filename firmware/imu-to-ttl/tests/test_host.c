/* Host-side protocol tests for the Feetech STS slave (no hardware needed). */
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "board.h"
#include "control_table.h"
#include "ft_slave.h"
#include "imu_pack.h"
#include "md_config.h"
#include "host_test_helpers.h"

static int g_fail;

#define CHECK(cond) do { \
    if (!(cond)) { printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); g_fail++; } \
} while (0)

typedef struct {
    md_control_table_t table;
    md_ft_slave_t slave;
    uint8_t tx[128];
    size_t tx_len;
} dut_t;

static void capture_tx(void *user, const uint8_t *data, size_t len)
{
    dut_t *d = (dut_t *)user;
    if (len <= sizeof(d->tx)) {
        memcpy(d->tx, data, len);
        d->tx_len = len;
    }
}

static void dut_init(dut_t *d)
{
    board_init();
    md_control_table_init(&d->table);
    md_ft_slave_init(&d->slave, MD_IMU_ID, d->table.bytes, MD_CTRL_TABLE_SIZE, capture_tx, d);
    d->tx_len = 0;
}

static void feed(dut_t *d, const uint8_t *bytes, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        md_ft_slave_on_byte(&d->slave, bytes[i]);
    }
}

/* run one request through the whole path (feed + return captured response) */
static size_t run(dut_t *d, const uint8_t *req, size_t n)
{
    d->tx_len = 0;
    feed(d, req, n);
    return d->tx_len;
}

static size_t build(uint8_t *out, uint8_t id, uint8_t inst, const uint8_t *params, uint8_t n)
{
    uint8_t len = (uint8_t)(n + 2u);
    out[0] = 0xFF; out[1] = 0xFF; out[2] = id; out[3] = len; out[4] = inst;
    if (n) memcpy(&out[5], params, n);
    out[5 + n] = md_ft_checksum(&out[2], (size_t)(3 + n));
    return (size_t)(6 + n);
}

/* ---- 1. checksum matches the official manual examples ---- */
static void test_checksum_examples(void)
{
    const uint8_t ping_req[] = {0x01, 0x02, 0x01};
    const uint8_t read_req[] = {0x01, 0x04, 0x02, 0x38, 0x02};
    const uint8_t write_req[] = {0x01, 0x09, 0x03, 0x2A, 0x00, 0x08, 0x00, 0x00, 0xE8, 0x03};
    const uint8_t sync_req[] = {0xFE, 0x06, 0x82, 0x38, 0x08, 0x01, 0x02};
    const uint8_t ping_rsp[] = {0x01, 0x02, 0x00};
    const uint8_t read_rsp[] = {0x01, 0x04, 0x00, 0x18, 0x05};
    CHECK(md_ft_checksum(ping_req, sizeof(ping_req)) == 0xFB);
    CHECK(md_ft_checksum(read_req, sizeof(read_req)) == 0xBE);
    CHECK(md_ft_checksum(write_req, sizeof(write_req)) == 0xD5);
    CHECK(md_ft_checksum(sync_req, sizeof(sync_req)) == 0x36);
    CHECK(md_ft_checksum(ping_rsp, sizeof(ping_rsp)) == 0xFC);
    CHECK(md_ft_checksum(read_rsp, sizeof(read_rsp)) == 0xDD);
    printf("ok: checksum vs manual examples\n");
}

/* ---- 2. ping ---- */
static void test_ping(void)
{
    dut_t d; dut_init(&d);
    uint8_t req[16]; size_t n = build(req, MD_IMU_ID, MD_FT_INST_PING, NULL, 0);
    size_t r = run(&d, req, n);
    CHECK(r == 6);
    CHECK(d.tx[0] == 0xFF && d.tx[1] == 0xFF);
    CHECK(d.tx[2] == MD_IMU_ID);
    CHECK(d.tx[3] == 2 && d.tx[4] == 0);
    CHECK(d.tx[5] == md_ft_checksum(&d.tx[2], 3));
    printf("ok: ping response\n");
}

/* ---- 3. read back the IMU block ---- */
static void test_read_imu_block(void)
{
    dut_t d; dut_init(&d);
    md_imu_sample_t s;
    memset(&s, 0, sizeof(s));
    s.gyro_raw[0] = 1234; s.gyro_raw[1] = -567; s.gyro_raw[2] = 89;
    s.quat_half[0] = 0x3800; s.quat_half[1] = 0xB800; s.quat_half[2] = 0x0000;
    s.status = 3; s.sample_count = 42;
    md_control_table_update_imu(&d.table, &s);

    uint8_t params[2] = {MD_IMU_READ_ADDR, MD_IMU_BLOCK_LEN};
    uint8_t req[16]; size_t n = build(req, MD_IMU_ID, MD_FT_INST_READ, params, 2);
    size_t r = run(&d, req, n);
    CHECK(r == 6 + MD_IMU_BLOCK_LEN); /* FF FF ID LEN ERR 12B CHK */
    if (r >= 6 + MD_IMU_BLOCK_LEN) {
        CHECK(d.tx[3] == (uint8_t)(MD_IMU_BLOCK_LEN + 2));
        CHECK(d.tx[4] == 0);
        for (unsigned i = 0; i < MD_IMU_BLOCK_LEN; i++) {
            CHECK(d.tx[5 + i] == d.table.bytes[MD_IMU_READ_ADDR + i]);
        }
        uint8_t pkt_len = 2u + d.tx[3];
        CHECK(d.tx[2 + pkt_len - 1] == md_ft_checksum(&d.tx[2], (size_t)(pkt_len - 1)));
    }
    printf("ok: read imu block\n");
}

/* ---- 4. sync read ---- */
static void test_sync_read(void)
{
    dut_t d; dut_init(&d);
    uint8_t params_with[] = {MD_IMU_READ_ADDR, 4, 10, MD_IMU_ID, 11};
    uint8_t req[24]; size_t n = build(req, MD_FT_BROADCAST_ID, MD_FT_INST_SYNC_READ, params_with, 5);
    size_t r = run(&d, req, n);
    CHECK(r > 0);
    CHECK(d.tx[2] == MD_IMU_ID && d.tx[4] == 0);

    uint8_t params_without[] = {MD_IMU_READ_ADDR, 4, 10, 11};
    n = build(req, MD_FT_BROADCAST_ID, MD_FT_INST_SYNC_READ, params_without, 4);
    r = run(&d, req, n);
    CHECK(r == 0);
    printf("ok: sync read (present/absent)\n");
}

/* ---- 5. bad checksum ignored ---- */
static void test_bad_checksum(void)
{
    dut_t d; dut_init(&d);
    uint8_t req[16]; size_t n = build(req, MD_IMU_ID, MD_FT_INST_PING, NULL, 0);
    req[n - 1] ^= 0x5A;
    CHECK(run(&d, req, n) == 0);
    printf("ok: bad checksum ignored\n");
}

/* ---- 6. write whitelist ---- */
static void test_write(void)
{
    dut_t d; dut_init(&d);
    uint8_t params[2] = {MD_FT_ADDR_RETURN_DELAY, 10};
    uint8_t req[16]; size_t n = build(req, MD_IMU_ID, MD_FT_INST_WRITE, params, 2);
    CHECK(run(&d, req, n) == 6);
    CHECK(d.table.bytes[MD_FT_ADDR_RETURN_DELAY] == 10);

    uint8_t bad[2] = {0x20, 0x99};
    n = build(req, MD_IMU_ID, MD_FT_INST_WRITE, bad, 2);
    (void)run(&d, req, n);
    CHECK(d.table.bytes[0x20] == 0);
    printf("ok: write whitelist\n");
}

/* ---- 7. foreign ID silence, broadcast ping ---- */
static void test_ids(void)
{
    dut_t d; dut_init(&d);
    uint8_t req[16]; size_t n = build(req, 123, MD_FT_INST_PING, NULL, 0);
    CHECK(run(&d, req, n) == 0);

    n = build(req, MD_FT_BROADCAST_ID, MD_FT_INST_PING, NULL, 0);
    CHECK(run(&d, req, n) == 6);
    printf("ok: foreign id silence / broadcast ping\n");
}

/* ---- 8. resync after garbage ---- */
static void test_resync(void)
{
    dut_t d; dut_init(&d);
    const uint8_t junk[] = {0x00, 0x12, 0xFF, 0x33, 0xFF, 0xFF, 0xFF};
    feed(&d, junk, sizeof(junk));
    uint8_t req[16]; size_t n = build(req, MD_IMU_ID, MD_FT_INST_PING, NULL, 0);
    d.tx_len = 0;
    feed(&d, req, n);
    CHECK(d.tx_len == 6);
    printf("ok: resync after garbage\n");
}

/* ---- 9. echo guard during transmission ---- */
static void test_echo_guard(void)
{
    dut_t d; dut_init(&d);
    uint8_t echo[] = {0xFF, 0xFF, MD_IMU_ID, 0x02, 0x00, 0x00};
    md_ft_slave_tx_begin(&d.slave);
    feed(&d, echo, sizeof(echo));
    md_ft_slave_tx_end(&d.slave);
    CHECK(d.tx_len == 0);

    uint8_t req[16]; size_t n = build(req, MD_IMU_ID, MD_FT_INST_PING, NULL, 0);
    CHECK(run(&d, req, n) == 6);
    printf("ok: echo guard\n");
}

/* ---- 10. end-to-end through the host board model (echo simulation on) ---- */
static void test_end_to_end(void)
{
    dut_t d; dut_init(&d);
    size_t dropped0 = host_echo_dropped();
    uint8_t b;

    uint8_t params[2] = {MD_IMU_READ_ADDR, MD_IMU_BLOCK_LEN};
    uint8_t req[16];
    size_t n = build(req, MD_IMU_ID, MD_FT_INST_READ, params, 2);

    host_feed(req, n);
    while (board_bus_read_byte(&b)) {
        md_ft_slave_on_byte(&d.slave, b);
    }
    CHECK(d.tx_len == 6u + MD_IMU_BLOCK_LEN);
    CHECK(host_echo_dropped() >= dropped0);
    printf("ok: end-to-end through board model\n");
}

int main(void)
{
    test_checksum_examples();
    test_ping();
    test_read_imu_block();
    test_sync_read();
    test_bad_checksum();
    test_write();
    test_ids();
    test_resync();
    test_echo_guard();
    test_end_to_end();
    if (g_fail) {
        printf("\n%d CHECK(S) FAILED\n", g_fail);
        return 1;
    }
    printf("\nall feetech slave tests passed\n");
    return 0;
}
