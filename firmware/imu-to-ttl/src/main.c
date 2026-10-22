#include "board.h"
#include "control_table.h"
#include "ft_slave.h"
#include "lsm6dsv16x.h"
#include "md_config.h"

/* IWDG:克隆 STM32 在总线风暴/异常路径下会挂死(bus_tx 的 TC 死等会把 DE 拉住
 * 卡死整条总线,断电池才能恢复)。独立看门狗挂死后 ~1.5s 自动复位。
 * IWDG 基址 0x40003000 与官方 stm32g031xx.h 一致。 */
#define IWDG_KR   (*(volatile uint16_t *)0x40003000u)
#define IWDG_PR   (*(volatile uint32_t *)0x40003004u)
#define IWDG_RLR  (*(volatile uint32_t *)0x40003008u)

/* 复位诊断(RM0444 / stm32g031xx.h):RCC 基址 0x40021000,G0 的 CSR 偏移是
 * 0x60(不是 F4 的 0x94)。复位标志位: */
#define RCC_CSR_ADDR   0x40021060u
#define CSR_RMVF       (1u << 23)
#define CSR_F_OBL      (1u << 25)
#define CSR_F_PIN      (1u << 26)
#define CSR_F_PWR      (1u << 27)  /* POR/BOR */
#define CSR_F_SFT      (1u << 28)
#define CSR_F_IWDG     (1u << 29)
#define CSR_F_WWDG     (1u << 30)

/* noinit RAM:看门狗/软件复位不清 SRAM,计数跨复位存活(断电清零) */
#define RST_MAGIC 0x4455434Bu  /* "KUCD" */
__attribute__((section(".noinit"))) static struct {
    uint32_t magic;
    uint8_t count;
} g_rst;

static void iwdg_init(void)
{
    IWDG_KR = 0xCCCCu;   /* start */
    IWDG_KR = 0x5555u;   /* unlock PR/RLR */
    IWDG_PR = 3u;        /* LSI/32 (PR 是档位:3=÷32;4=÷64) */
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

/* 上电采集复位原因进控制表 144,并维护 noinit 复位计数 145。 */
static void reset_diag_init(void)
{
    volatile uint32_t csr = *(volatile uint32_t *)RCC_CSR_ADDR;
    uint8_t flags = 0;
    if (csr & CSR_F_IWDG) flags |= 1u << 0;
    if (csr & CSR_F_WWDG) flags |= 1u << 1;
    if (csr & CSR_F_SFT)  flags |= 1u << 2;
    if (csr & CSR_F_PWR)  flags |= 1u << 3;
    if (csr & CSR_F_PIN)  flags |= 1u << 4;
    if (csr & CSR_F_OBL)  flags |= 1u << 5;

    if (g_rst.magic != RST_MAGIC) {  /* POR:SRAM 未初始化(或首次) */
        g_rst.magic = RST_MAGIC;
        g_rst.count = 0;
    } else if (flags & (1u << 0)) {  /* 仅 IWDG 复位计数(看门狗自证用) */
        if (g_rst.count < 255u) g_rst.count++;
    }

    g_table.bytes[MD_REG_RESET_FLAGS] = flags;
    g_table.bytes[MD_REG_RESET_COUNT] = g_rst.count;
    *(volatile uint32_t *)RCC_CSR_ADDR = CSR_RMVF;  /* 清标志,下次只反映新原因 */

    /* 构建日期戳：__DATE__ = "Mmm dd yyyy"，写 yy mm dd 三字节到 147-149 */
    {
        static const char months[] = "JanFebMarAprMayJunJulAugSepOctNovDec";
        const char *d = __DATE__;
        uint8_t mo = 0;
        for (uint8_t i = 0; i < 12u; i++) {
            if (d[0] == months[i * 3] && d[1] == months[i * 3 + 1] && d[2] == months[i * 3 + 2]) {
                mo = (uint8_t)(i + 1u);
                break;
            }
        }
        int day = (d[4] == ' ') ? d[5] - '0' : (d[4] - '0') * 10 + (d[5] - '0');
        int yr = (d[9] - '0') * 10 + (d[10] - '0');
        g_table.bytes[MD_REG_BUILD_DATE] = (uint8_t)yr;
        g_table.bytes[MD_REG_BUILD_DATE + 1u] = mo;
        g_table.bytes[MD_REG_BUILD_DATE + 2u] = (uint8_t)day;
    }
}

int main(void)
{
    board_init();
    iwdg_init();
    md_control_table_init(&g_table);
    reset_diag_init();

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
    uint32_t last_heartbeat = 0;
    for (;;) {
        /* 看门狗在线自证:主机写 0xA5 到 146 → 故意停喂 → ~1.5s 后应复位。
         * 复位后 flags(144) 的 IWDG 位置 1、count(145) +1 即为证明。 */
        if (g_table.bytes[MD_REG_WDT_TEST] != 0xA5u) {
            iwdg_feed();
        }
        uint8_t b;
        uint32_t drained = 0;
        while (board_bus_read_byte(&b)) {
            md_ft_slave_on_byte(&g_slave, b);
            if (g_table.bytes[MD_REG_WDT_TEST] != 0xA5u && ((++drained & 0x3Fu) == 0u)) {
                iwdg_feed(); /* 总线洪水也不饿死看门狗 */
            }
        }

        uint32_t now = board_millis();
        if ((now - last_sample) >= 10u) { /* 100 Hz sampling; host reads at 50 Hz */
            last_sample = now;
            if (md_lsm6_poll(&g_imu)) {
                md_control_table_update_imu(&g_table, &g_imu.last);
            }
        }

        /* 心跳信标:每 5s 发一帧。若 TX 路径卡死(board_bus_write 死等 TC),
         * 主循环停在这里 → 停喂 → IWDG 1.5s 复位 → 通信自愈。
         * 这是"内核活着但通信死"故障(2026-10-22 早晨复现)的对症防护。 */
        if ((now - last_heartbeat) >= 5000u) {
            last_heartbeat = now;
            static const uint8_t hb[] = {0xFF, 0x07, 'D', 'U', 'C', 'K'};
            bus_tx(0, hb, sizeof(hb));
        }

        board_idle(); /* WFI on target; wakes on RXNE / SysTick */
    }
}
