#include "board.h"
#include "control_table.h"
#include "ft_slave.h"
#include "lsm6dsv16x.h"
#include "md_config.h"

static md_control_table_t g_table;
static md_ft_slave_t g_slave;
static md_lsm6_t g_imu;

static void bus_tx(void *user, const uint8_t *data, size_t len)
{
    (void)user;
    /* Guard the parser against the local echo (AUTO bus sees its own TX). */
    md_ft_slave_tx_begin(&g_slave);
    board_bus_write(data, len);
    md_ft_slave_tx_end(&g_slave);
}

int main(void)
{
    board_init();
    md_control_table_init(&g_table);

    md_ft_slave_init(&g_slave, MD_IMU_ID, g_table.bytes, MD_CTRL_TABLE_SIZE, bus_tx, 0);

    g_imu.user = 0;
    g_imu.write = board_imu_spi_write;
    g_imu.read = board_imu_spi_read;
    g_imu.delay_ms = board_delay_ms;
    (void)md_lsm6_init(&g_imu);

    /* boot beacon: one frame right after init so the TX physical path can be
     * verified without any host query. 0xFF 0x07 'D' 'U' 'C' 'K' + chk. */
    {
        static const uint8_t beacon[] = {0xFF, 0x07, 'D', 'U', 'C', 'K'};
        bus_tx(0, beacon, sizeof(beacon));
    }

    uint32_t last_sample = 0;
    for (;;) {
        uint8_t b;
        while (board_bus_read_byte(&b)) {
            md_ft_slave_on_byte(&g_slave, b);
        }

        uint32_t now = board_millis();
        if ((now - last_sample) >= 10u) { /* 100 Hz sampling; host reads at 50 Hz */
            last_sample = now;
            if (md_lsm6_poll(&g_imu)) {
                md_control_table_update_imu(&g_table, &g_imu.last);
            }
        }

        board_idle(); /* WFI on target; wakes on RXNE / SysTick */
    }
}
