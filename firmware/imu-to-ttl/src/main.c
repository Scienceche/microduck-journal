#include "board.h"
#include "control_table.h"
#include "ft_slave.h"
#include "lsm6dsv16x.h"
#include "md_config.h"

/* IWDG:克隆 STM32 在总线风暴/异常路径下会整体挂死(需断电池才能恢复),
 * 独立看门狗挂死后 ~1.5s 自动复位。LSI 32k 名义:PR=÷32,RLR=1500。 */
#define IWDG_KR   (*(volatile uint16_t *)0x40003000u)
#define IWDG_PR   (*(volatile uint32_t *)0x40003004u)
#define IWDG_RLR  (*(volatile uint32_t *)0x40003008u)

static void iwdg_init(void)
{
    IWDG_KR = 0xCCCCu;   /* start */
    IWDG_KR = 0x5555u;   /* unlock PR/RLR */
    IWDG_PR = 4u;        /* LSI/32 */
    IWDG_RLR = 1500u;    /* ~1.5s @ 32kHz LSI */
    IWDG_KR = 0xAAAAu;   /* reload */
}

static inline void iwdg_feed(void) { IWDG_KR = 0xAAAAu; }

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
    iwdg_init();
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
        iwdg_feed();
        uint8_t b;
        uint32_t drained = 0;
        while (board_bus_read_byte(&b)) {
            md_ft_slave_on_byte(&g_slave, b);
            if ((++drained & 0x3Fu) == 0u) /* 总线洪水也不饿死看门狗 */
                iwdg_feed();
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
