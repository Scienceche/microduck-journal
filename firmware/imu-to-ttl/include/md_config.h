#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Host contract - unchanged from the DXL version so host decode stays valid */
#define MD_IMU_ID               200u      /* Feetech slave ID */
#define MD_IMU_BAUD             1000000u
#define MD_IMU_READ_ADDR        124u      /* 0x7C: 12-byte block */
#define MD_IMU_BLOCK_LEN        12u
#define MD_IMU_DIAG_LEN         8u
#define MD_IMU_MODEL_NUMBER     10200u
#define MD_IMU_FW_VERSION       1u

/* Control table covering addr 0 .. 143 */
#define MD_CTRL_TABLE_SIZE      152u

/* Feetech STS memory-map offsets used for identity */
#define MD_FT_ADDR_FW_MAJOR     0u
#define MD_FT_ADDR_FW_MINOR     1u
#define MD_FT_ADDR_END          2u
#define MD_FT_ADDR_ID           5u
#define MD_FT_ADDR_BAUD         6u
#define MD_FT_ADDR_RETURN_DELAY 7u
#define MD_FT_ADDR_STATUS_LEVEL 8u
#define MD_FT_BAUD_CODE_1M      0u

/*
 * Board pin map - imu_to-ttl v1.0.
 * AUTO bus (no DE / no RX_EN): PA2 TX drives 1G125 A and /OE; PA3 RX taps
 * the bus directly (FT pin, 150R + 5.1V zener + 100R PTC).
 *
 * Variant for the imu_to_dxl / OSHWhub "imu_to_servo" board: build with
 * -DMD_BUS_DE=1. That board drives the bus through an SN74LVC2G241 whose
 * TX buffer enable 1OE# (active low, "DE" net) sits on PA1. PA1 AF1 is
 * USART2_RTS_DE_CK (DS12992 Rev 4, Table 12), so USART2 hardware driver
 * enable does the direction switching: CR3 DEM=1, DEP=1 (DE low while
 * transmitting). R4 pulls 1OE# high, keeping the board silent on the bus
 * whenever it is not transmitting. The RX buffer (2OE) stays enabled via
 * R7, so the local-echo discard path below is unchanged.
 */
#define MD_PIN_IMU_INT          0u   /* PA0  <- LSM6 INT1 (optional) */
#define MD_PIN_USART2_TX        2u   /* PA2  AF1 -> 1G125 / 2G241 1A */
#define MD_PIN_USART2_RX        3u   /* PA3  AF1 <- bus */
#ifdef MD_BUS_DE
#define MD_PIN_USART2_DE        1u   /* PA1  AF1 = USART2_RTS_DE_CK -> 2G241 1OE# (low = TX on) */
#endif
#define MD_PIN_SPI_CS           4u   /* PA4 */
#define MD_PIN_SPI_SCK          5u   /* PA5  AF0 */
#define MD_PIN_SPI_MISO         6u   /* PA6  AF0 */
#define MD_PIN_SPI_MOSI         7u   /* PA7  AF0 */
#define MD_PIN_USART1_TX        11u  /* PA11 -> debug header pin 4 (SYSCFG remap to PA9) */
#define MD_PIN_USART1_RX        12u  /* PA12 <- debug header pin 5 (SYSCFG remap to PA10) */
#define MD_PIN_SWDIO            13u
#define MD_PIN_SWCLK            14u

/* Gyro full-scale for packing - match duck-control +/-500 dps */
#define MD_GYRO_FS_DPS          500

#ifdef __cplusplus
}
#endif
