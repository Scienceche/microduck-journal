#include "control_table.h"
#include "imu_pack.h"
#include <string.h>

void md_control_table_init(md_control_table_t *t)
{
    memset(t->bytes, 0, sizeof(t->bytes));
    t->bytes[MD_FT_ADDR_FW_MAJOR] = MD_IMU_FW_VERSION;
    t->bytes[MD_FT_ADDR_FW_MINOR] = MD_IMU_FW_MINOR;
    t->bytes[MD_FT_ADDR_END] = 0;
    t->bytes[MD_FT_ADDR_ID] = MD_IMU_ID;
    t->bytes[MD_FT_ADDR_BAUD] = MD_FT_BAUD_CODE_1M;
    t->bytes[MD_FT_ADDR_RETURN_DELAY] = 0;
    t->bytes[MD_FT_ADDR_STATUS_LEVEL] = 1; /* respond to all instructions */
}

void md_control_table_update_imu(md_control_table_t *t, const md_imu_sample_t *s)
{
    uint8_t block[MD_IMU_BLOCK_LEN];
    uint8_t diag[MD_IMU_DIAG_LEN];
    md_imu_pack12(s, block);
    md_imu_pack_diag(s, diag);
    memcpy(&t->bytes[MD_IMU_READ_ADDR], block, MD_IMU_BLOCK_LEN);
    memcpy(&t->bytes[MD_IMU_READ_ADDR + MD_IMU_BLOCK_LEN], diag, MD_IMU_DIAG_LEN);
}
