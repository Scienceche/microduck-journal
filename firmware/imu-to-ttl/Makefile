# IMU-to-TTL firmware
# host-test:  protocol tests on the PC (no toolchain needed beyond gcc)
# g031:       bare-metal image for STM32G031F8P6 (imu_to_ttl board, AUTO bus)
# g031-dxl:   same MCU, imu_to_dxl / OSHWhub "imu_to_servo" board
#             (SN74LVC2G241 with DE on PA1 -> -DMD_BUS_DE=1)
# gate:       runs build/imu_to_ttl.elf on the Renode G031 + LSM6DSV16X model
#             (bus protocol, IMU path, echo/resync robustness, 30k-cycle stress)

BUILD ?= build
HOST_CC ?= gcc
CROSS ?= arm-none-eabi-
RENODE ?= renode
GATE_STRESS ?= 30000

INCLUDES = -Iinclude -Iplatform/host

HOST_SRCS = \
	src/ft_slave.c \
	src/half_float.c \
	src/imu_pack.c \
	src/control_table.c \
	platform/host/board_host.c \
	tests/test_host.c

.PHONY: all host-test clean g031 g031-dxl gate

all: host-test

host-test: $(BUILD)/host_test
	$(BUILD)/host_test

gate: $(BUILD)/imu_to_ttl.elf
	cd .. && GATE_STRESS=$(GATE_STRESS) $(RENODE) --disable-xwt -P -1 \
		-e "include @sim/renode/run_gate.resc"
	@grep "^RESULT:" /tmp/imu_gate_report.txt
	@grep -c "^FAIL" /tmp/imu_gate_report.txt > /dev/null && \
		{ echo "gate failures:"; grep "^FAIL" /tmp/imu_gate_report.txt; exit 1; } || true

$(BUILD)/host_test: $(HOST_SRCS)
	mkdir -p $(BUILD)
	$(HOST_CC) -std=c11 -Wall -Wextra -O2 $(INCLUDES) $(HOST_SRCS) -lm -o $@

G031_SRCS = \
	src/main.c \
	src/ft_slave.c \
	src/half_float.c \
	src/imu_pack.c \
	src/control_table.c \
	src/lsm6dsv16x.c \
	platform/stm32g031/board_g031.c \
	platform/stm32g031/syscalls.c \
	platform/stm32g031/startup_stm32g031xx.s

g031: $(BUILD)/imu_to_ttl.elf

$(BUILD)/imu_to_ttl.elf: $(G031_SRCS)
	mkdir -p $(BUILD)
	$(CROSS)gcc -std=c11 -Wall -Wextra -O2 -mcpu=cortex-m0plus -mthumb \
		-DMD_PLATFORM_STM32G031=1 \
		-ffunction-sections -fdata-sections -nostartfiles \
		-specs=nano.specs \
		-Tplatform/stm32g031/STM32G031F8Px_FLASH.ld \
		-Iinclude \
		$(G031_SRCS) \
		-Wl,--gc-sections -Wl,-Map=$(BUILD)/imu_to_ttl.map \
		-lc -lm -lgcc \
		-o $@
	$(CROSS)size $@

g031-dxl: $(BUILD)/imu_to_dxl.elf

$(BUILD)/imu_to_dxl.elf: $(G031_SRCS)
	mkdir -p $(BUILD)
	$(CROSS)gcc -std=c11 -Wall -Wextra -O2 -mcpu=cortex-m0plus -mthumb \
		-DMD_PLATFORM_STM32G031=1 -DMD_BUS_DE=1 \
		-ffunction-sections -fdata-sections -nostartfiles \
		-specs=nano.specs \
		-Tplatform/stm32g031/STM32G031F8Px_FLASH.ld \
		-Iinclude \
		$(G031_SRCS) \
		-Wl,--gc-sections -Wl,-Map=$(BUILD)/imu_to_dxl.map \
		-lc -lm -lgcc \
		-o $@
	$(CROSS)size $@

clean:
	rm -rf $(BUILD)
