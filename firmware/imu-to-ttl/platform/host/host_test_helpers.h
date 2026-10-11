#pragma once
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Host board model test helpers (see board_host.c). */
void host_feed(const uint8_t *data, size_t len);
size_t host_last_tx(uint8_t *out, size_t max);
void host_clear_tx(void);
size_t host_echo_dropped(void);
void host_echo_sim_enable(int on);

#ifdef __cplusplus
}
#endif
