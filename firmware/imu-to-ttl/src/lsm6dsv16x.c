#include "lsm6dsv16x.h"
#include "half_float.h"
#include <string.h>
#include <math.h>

/* Register map (subset) — LSM6DSV16X datasheet / STdC */
#define REG_FUNC_CFG_ACCESS   0x01u
#define REG_FIFO_CTRL1        0x07u
#define REG_FIFO_CTRL3        0x09u
#define REG_FIFO_CTRL4        0x0Au
#define REG_WHO_AM_I          0x0Fu
#define REG_CTRL1_XL          0x10u
#define REG_CTRL2_G           0x11u
#define REG_CTRL3_C           0x12u
#define REG_OUTX_L_G          0x22u
#define REG_OUTX_L_A          0x28u
#define REG_FIFO_STATUS1      0x1Bu
#define REG_FIFO_STATUS2      0x1Cu
#define REG_FIFO_DATA_OUT_TAG 0x78u

#define WHO_AM_I_VAL          0x70u

#define EMB_FUNC_EN_A         0x04u
#define EMB_FUNC_FIFO_EN_A    0x44u
#define SFLP_ODR              0x5Eu
#define EMB_FUNC_INIT_A       0x66u

#define TAG_SFLP_GAME         0x13u
#define FUNC_CFG_ACCESS_EN    0x80u

/* Software 6-axis fusion (Mahony). This die batches the SFLP gravity vector
 * (tag 0x17, accel-only) but never produces the game rotation quaternion
 * (tag 0x13) - fusion core defective or a reduced-feature die (LSM6DSV16B
 * answers the same WHO_AM_I 0x70). Raw gyro + accel are healthy, so the
 * quaternion is computed on the M0+ instead. Hardware 0x13 samples take
 * priority when present. */
static float g_q[4] = {1.0f, 0.0f, 0.0f, 0.0f};
static float g_ibias[3];      /* gyro bias integral (rad/s) */

static void mahony_update(const int16_t *gyro_raw, const int16_t *accel_raw, float dt)
{
    const float KI = 0.10f, KP = 0.60f;
    /* ±500 dps / 32768 → rad/s */
    /* Integral term ADDS alongside KP*e (standard Mahony). Subtracting it
     * from the gyro makes the integrator fight the proportional term - a
     * positive-feedback oscillation that diverges (verified by host replay). */
    float gx = gyro_raw[0] * (500.0f / 32768.0f * 0.0174533f);
    float gy = gyro_raw[1] * (500.0f / 32768.0f * 0.0174533f);
    float gz = gyro_raw[2] * (500.0f / 32768.0f * 0.0174533f);
    float ax = (float)accel_raw[0], ay = (float)accel_raw[1], az = (float)accel_raw[2];
    float an = ax * ax + ay * ay + az * az;

    if (an > 1.0f && an < 1.0e9f) {  /* gravity reference valid (not free-fall) */
        float inv = 1.0f / sqrtf(an);
        ax *= inv; ay *= inv; az *= inv;
        /* error = measured gravity x estimated gravity (from q) */
        float q0 = g_q[0], q1 = g_q[1], q2 = g_q[2], q3 = g_q[3];
        float hx = 2.0f * (q1 * q3 - q0 * q2);
        float hy = 2.0f * (q0 * q1 + q2 * q3);
        float hz = q0 * q0 - q1 * q1 - q2 * q2 + q3 * q3;
        float ex = ay * hz - az * hy;
        float ey = az * hx - ax * hz;
        float ez = ax * hy - ay * hx;
        g_ibias[0] += KI * ex * dt;
        g_ibias[1] += KI * ey * dt;
        g_ibias[2] += KI * ez * dt;
        gx += KP * ex + g_ibias[0];
        gy += KP * ey + g_ibias[1];
        gz += KP * ez + g_ibias[2];
    }
    /* integrate quaternion */
    float q0 = g_q[0], q1 = g_q[1], q2 = g_q[2], q3 = g_q[3];
    float half = 0.5f * dt;
    g_q[0] = q0 + (-q1 * gx - q2 * gy - q3 * gz) * half;
    g_q[1] = q1 + (q0 * gx + q2 * gz - q3 * gy) * half;
    g_q[2] = q2 + (q0 * gy - q1 * gz + q3 * gx) * half;
    g_q[3] = q3 + (q0 * gz + q1 * gy - q2 * gx) * half;
    float n = sqrtf(g_q[0]*g_q[0] + g_q[1]*g_q[1] + g_q[2]*g_q[2] + g_q[3]*g_q[3]);
    if (n > 0.5f) {
        float inv = 1.0f / n;
        g_q[0] *= inv; g_q[1] *= inv; g_q[2] *= inv; g_q[3] *= inv;
    } else {
        g_q[0] = 1.0f; g_q[1] = g_q[2] = g_q[3] = 0.0f;
    }
}

static void mahony_update(const int16_t *gyro_raw, const int16_t *accel_raw, float dt);

static uint16_t f32_to_f16_bits(float v)
{
    return md_float_to_half(v);
}

/* SWD debug probes: tag histogram + EMB register readbacks after init. */
volatile uint16_t g_tag_hist[8];      /* tag & 0x07 buckets, 0x13->bucket 3 */
volatile uint16_t g_fifo_nonempty;    /* polls that saw level > 0 */
volatile uint8_t  g_dbg_rd[0x70];     /* EMB readbacks: [0x04],[0x44],[0x5E],[0x66] */
volatile uint8_t  g_dbg_live[16];     /* st1,st2 + runtime EMB: EN_A,FIFO_EN,ODR,INIT,gbias[6] */

static int32_t wr(md_lsm6_t *d, uint8_t reg, uint8_t v)
{
    return d->write(d->user, reg, &v, 1);
}

static int32_t rd(md_lsm6_t *d, uint8_t reg, uint8_t *v, uint16_t n)
{
    return d->read(d->user, reg, v, n);
}

static void emb_enter(md_lsm6_t *d)
{
    wr(d, REG_FUNC_CFG_ACCESS, FUNC_CFG_ACCESS_EN);
}

static void emb_exit(md_lsm6_t *d)
{
    wr(d, REG_FUNC_CFG_ACCESS, 0x00u);
}

bool md_lsm6_init(md_lsm6_t *dev)
{
    uint8_t id = 0;
    memset(&dev->last, 0, sizeof(dev->last));
    dev->present = false;
    dev->sflp_live = false;
    dev->sample_count = 0;

    if (!dev->write || !dev->read || !dev->delay_ms) {
        return false;
    }

    dev->delay_ms(15);
    if (rd(dev, REG_WHO_AM_I, &id, 1) != 0 || id != WHO_AM_I_VAL) {
        return false;
    }
    dev->present = true;
    dev->last.status = 0x01u;
    g_q[0] = 1.0f; g_q[1] = g_q[2] = g_q[3] = 0.0f;
    g_ibias[0] = g_ibias[1] = g_ibias[2] = 0.0f;

    /* Init order mirrors the community-kit driver exactly (measured working
     * on a genuine part): CTRL3 SW_RESET, then FS/BDU, then the whole EMB
     * configuration while BOTH sensors are still in power-down, FIFO flush,
     * and only then the sensor ODRs. The previous order enabled SFLP_GAME
     * (EMB_FUNC_EN_A) and wrote the gbias registers AFTER the sensors were
     * already running; on the suspect die that sequence left the engine
     * output tumbling. */
    wr(dev, REG_CTRL3_C, 0x01u); /* SW_RESET, self-clearing */
    dev->delay_ms(10);
    for (uint8_t guard = 0; guard < 20u; guard++) {
        uint8_t c3 = 0;
        (void)rd(dev, REG_CTRL3_C, &c3, 1);
        if ((c3 & 0x01u) == 0u) {
            break;
        }
        dev->delay_ms(1);
    }
    if (rd(dev, REG_WHO_AM_I, &id, 1) != 0 || id != WHO_AM_I_VAL) {
        return false; /* vanished across the reset */
    }

    wr(dev, 0x03u, 0x01u);       /* IF_CFG (03h): I2C/I3C disable, SPI only (kit) */
    wr(dev, REG_CTRL3_C, 0x44u); /* BDU=1, IF_INC=1 */
    wr(dev, 0x15u, 0x02u);       /* CTRL6: FS_G = ±500 dps */
    wr(dev, 0x17u, 0x01u);       /* CTRL8: FS_XL = ±4 g */
    wr(dev, REG_FIFO_CTRL1, 0x20u); /* watermark 32 (official) */
    wr(dev, REG_FIFO_CTRL4, 0x00u); /* bypass = flush before arming */

    /* Whole EMB configuration in one session, sensors still off:
     * FIFO_EN_A bit1 = game rotation → FIFO; EN_A bit1 = SFLP_GAME_EN;
     * SFLP_ODR (5Eh) = [0,1,ODR[2:0],0,1,1]; 011 = 120 Hz. */
    emb_enter(dev);
    wr(dev, EMB_FUNC_FIFO_EN_A, 0x02u);
    wr(dev, EMB_FUNC_EN_A, 0x02u);
    wr(dev, SFLP_ODR, 0x5Bu);
    g_dbg_rd[0x44] = 0; rd(dev, EMB_FUNC_FIFO_EN_A, &g_dbg_rd[0x44], 1);
    g_dbg_rd[0x5E] = 0; rd(dev, SFLP_ODR, &g_dbg_rd[0x5E], 1);
    g_dbg_rd[0x04] = 0; rd(dev, EMB_FUNC_EN_A, &g_dbg_rd[0x04], 1);
    emb_exit(dev);

    wr(dev, REG_FIFO_CTRL4, 0x06u); /* continuous (community-kit value) */

    /* Sensors last: they are what starts producing words. */
    wr(dev, REG_CTRL1_XL, 0x06u);   /* 120 Hz high-performance */
    wr(dev, REG_CTRL2_G, 0x06u);

    /* main-page register readbacks through the driver's own (proven) path */
    g_dbg_rd[0x0A] = 0; rd(dev, REG_FIFO_CTRL4, &g_dbg_rd[0x0A], 1);
    g_dbg_rd[0x09] = 0; rd(dev, 0x09u, &g_dbg_rd[0x09], 1);      /* FIFO_CTRL3 */
    g_dbg_rd[0x10] = 0; rd(dev, REG_CTRL1_XL, &g_dbg_rd[0x10], 1);
    g_dbg_rd[0x11] = 0; rd(dev, REG_CTRL2_G, &g_dbg_rd[0x11], 1);
    g_dbg_rd[0x12] = 0; rd(dev, REG_CTRL3_C, &g_dbg_rd[0x12], 1);
    g_dbg_rd[0x15] = 0; rd(dev, 0x15u, &g_dbg_rd[0x15], 1);      /* CTRL6 */

    return true;
}

static void read_gyro_accel(md_lsm6_t *dev)
{
    uint8_t raw[12];
    if (rd(dev, REG_OUTX_L_G, raw, 6) != 0) {
        return;
    }
    if (rd(dev, REG_OUTX_L_A, &raw[6], 6) != 0) {
        return;
    }
    dev->last.gyro_raw[0] = (int16_t)(raw[0] | ((uint16_t)raw[1] << 8));
    dev->last.gyro_raw[1] = (int16_t)(raw[2] | ((uint16_t)raw[3] << 8));
    dev->last.gyro_raw[2] = (int16_t)(raw[4] | ((uint16_t)raw[5] << 8));
    dev->last.accel_raw[0] = (int16_t)(raw[6] | ((uint16_t)raw[7] << 8));
    dev->last.accel_raw[1] = (int16_t)(raw[8] | ((uint16_t)raw[9] << 8));
    dev->last.accel_raw[2] = (int16_t)(raw[10] | ((uint16_t)raw[11] << 8));
}

bool md_lsm6_poll(md_lsm6_t *dev)
{
    uint8_t st1 = 0, st2 = 0;
    uint16_t level;
    uint16_t drained = 0;
    bool hw_game_seen = false;

    if (!dev->present) {
        return false;
    }

    read_gyro_accel(dev);

    if (rd(dev, REG_FIFO_STATUS1, &st1, 1) != 0) {
        return false;
    }
    (void)rd(dev, REG_FIFO_STATUS2, &st2, 1);
    level = (uint16_t)(st1 | (((uint16_t)st2 & 0x01u) << 8));
    g_dbg_live[0] = st1;
    g_dbg_live[1] = st2;
    if ((dev->sample_count & 0x7Fu) == 0u) {
        /* runtime EMB snapshot: did the enables survive? is the algorithm
         * rewriting its gyro-bias estimate at 0x6E..0x73? */
        emb_enter(dev);
        dev->read(dev->user, 0x04u, (uint8_t *)&g_dbg_live[2], 1);
        dev->read(dev->user, 0x44u, (uint8_t *)&g_dbg_live[3], 1);
        dev->read(dev->user, 0x5Eu, (uint8_t *)&g_dbg_live[4], 1);
        dev->read(dev->user, 0x66u, (uint8_t *)&g_dbg_live[5], 1);
        dev->read(dev->user, 0x6Eu, (uint8_t *)&g_dbg_live[6], 6);
        emb_exit(dev);
    }
    if (level > 0u) {
        g_fifo_nonempty++;
    }

    while (level > 0u && drained < 48u) {
        uint8_t slot[7];
        uint8_t tag_lo;
        if (rd(dev, REG_FIFO_DATA_OUT_TAG, slot, 7) != 0) {
            break;
        }
        tag_lo = (uint8_t)(slot[0] & 0x1Fu);
        g_tag_hist[tag_lo & 0x07u]++;
        if (tag_lo == TAG_SFLP_GAME) {
            hw_game_seen = true;
            dev->last.quat_half[0] = (uint16_t)(slot[1] | ((uint16_t)slot[2] << 8));
            dev->last.quat_half[1] = (uint16_t)(slot[3] | ((uint16_t)slot[4] << 8));
            dev->last.quat_half[2] = (uint16_t)(slot[5] | ((uint16_t)slot[6] << 8));
            if (dev->last.quat_half[0] | dev->last.quat_half[1] | dev->last.quat_half[2]) {
                dev->sflp_live = true;
            }
        }
        level--;
        drained++;
    }

#ifdef MD_SOFT_FUSION
    /* A/B verdict build: ignore the SFLP engine output entirely and run the
     * software Mahony filter on the raw channels at the fixed 100 Hz cadence. */
    (void)hw_game_seen;
    mahony_update(dev->last.gyro_raw, dev->last.accel_raw, 0.01f);
    dev->last.quat_half[0] = f32_to_f16_bits(g_q[1]);
    dev->last.quat_half[1] = f32_to_f16_bits(g_q[2]);
    dev->last.quat_half[2] = f32_to_f16_bits(g_q[3]);
    dev->sflp_live = true; /* mark the attitude source as live for the host */
#else
    if (!hw_game_seen) {
        /* this die's fusion core never emits tag 0x13 - run the software
         * Mahony filter on the healthy raw channels instead (100 Hz cadence
         * from the main loop). */
        mahony_update(dev->last.gyro_raw, dev->last.accel_raw, 0.01f);
        dev->last.quat_half[0] = f32_to_f16_bits(g_q[1]);
        dev->last.quat_half[1] = f32_to_f16_bits(g_q[2]);
        dev->last.quat_half[2] = f32_to_f16_bits(g_q[3]);
        if (dev->last.quat_half[0] | dev->last.quat_half[1] | dev->last.quat_half[2]) {
            dev->sflp_live = true;
        }
    }
#endif

    if (!dev->sflp_live) {
        dev->last.quat_half[0] = 0;
        dev->last.quat_half[1] = 0;
        dev->last.quat_half[2] = 0;
    }

    /* Bump counter every poll so host stale-tracker sees life even before SFLP. */
    dev->sample_count = (uint16_t)(dev->sample_count + 1u);
    /* Fold a low nibble of counter into unused gyro LSBs? No — keep gyro clean.
       Host compares full 12B; gyro noise + changing quat is enough. For silent gyro,
       XOR sample into a reserved path: we rely on gyro raw changing or quat. */
    if (!dev->sflp_live && (dev->sample_count & 1u)) {
        /* Toggle unused high bit of gyro_z LSB area safely? Better: leave as-is.
           Stale detection before motion: sample_count is only in diag @136. */
    }

    dev->last.sample_count = dev->sample_count;
    dev->last.status = (uint8_t)(0x01u | (dev->sflp_live ? 0x02u : 0x00u));
    return true;
}
