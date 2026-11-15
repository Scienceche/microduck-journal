#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P2.5 双专家 Actor — MLPModel 兼容版(2026-11-12 run10 事故后重写).

run10 事故根因(教训全部内化到本文件):
  1. gate 曾在【归一化后】latent 上用【原始重力】公式 tilt=|gz+1|
     → 直立时归一化 gz≈-0.3 → gate≈0.98 → 行走专家从未参与 → 行走全废。
     修复: gate 改在 get_latent 之前的【原始 obs】上切片计算。
  2. obs_normalizer 训练中持续更新(var 漂移 30%) → 冻结的专家权重吃到漂移的
     输入分布, 等于白冻。修复: override update_normalization 为 no-op。
  3. stand 专家无锚被 PPO 重写(相对漂移 121%), gate_offset 漂到 1.087
     (倒地时 gate≈0.45, 起身能力被稀释)。修复: P2.5 只训 gate 两个标量,
     两个专家全部冻结, 且 gate 参数每次 forward 钳制在安全范围。

用法 (microduck_velstand_env_cfg.py):
    actor=RslRlModelCfg(
        class_name="mjlab_microduck.tasks.dual_expert_mlp.DualExpertMLPModel",
        hidden_dims=(512, 256, 128), ...
    )
"""
from __future__ import annotations

import copy

import torch
import torch.nn as nn
from tensordict import TensorDict
from rsl_rl.models.mlp_model import MLPModel
from rsl_rl.modules.mlp import MLP


class _DualExpertExport(nn.Module):
    """导出包装(对抗审查 P0): 基类 as_onnx 只拷 self.mlp → 会静默丢掉
    stand_expert 和 gate, 导出一个纯 walk 模型。本包装导出完整路径:
    原始 flat obs → gate(原始重力切片) → 归一化 → 双专家 → 混合 → 确定性头。
    gate 参数导出为 buffer(=导出时刻常数)。"""

    is_recurrent: bool = False

    def __init__(self, model: "DualExpertMLPModel") -> None:
        super().__init__()
        self.obs_normalizer = copy.deepcopy(model.obs_normalizer)
        self.stand_normalizer = copy.deepcopy(model.stand_normalizer)  # P2.7
        self.mlp = copy.deepcopy(model.mlp)                      # walk 专家
        self.stand_expert = copy.deepcopy(model.stand_expert)    # 起身专家
        self.register_buffer("gate_k", model.gate_k.detach().clone())
        self.register_buffer("gate_offset", model.gate_offset.detach().clone())
        self.register_buffer("cmd_w", model.cmd_w.detach().clone())
        self.register_buffer("cmd_c0", model.cmd_c0.detach().clone())
        self.gravity_slice = int(model._gravity_slice)
        self.cmd_slice = int(model._cmd_slice) if model._cmd_slice is not None else -1
        if model.distribution is not None:
            self.deterministic_output = (
                model.distribution.as_deterministic_output_module())
        else:
            self.deterministic_output = nn.Identity()
        self.input_size = model.obs_dim

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """x = 已拼接的【原始】obs [B, obs_dim]。"""
        # 双分支 gate(与 _compute_gate 完全一致)
        gz = x[:, self.gravity_slice:self.gravity_slice + 1]
        tilt = (gz + 1.0).abs()
        g_g = torch.sigmoid(self.gate_k * (tilt - self.gate_offset))
        if 0 <= self.cmd_slice and self.cmd_slice + 3 <= x.shape[-1]:
            c = x[:, self.cmd_slice:self.cmd_slice + 3]
            cmd_mag = c.abs().sum(dim=-1, keepdim=True)
            g_c = torch.sigmoid(self.cmd_w * (self.cmd_c0 - cmd_mag))
        else:
            g_c = torch.zeros_like(g_g)
        gate = 1.0 - (1.0 - g_g) * (1.0 - g_c)
        walk_out = self.mlp(self.obs_normalizer(x))
        stand_out = self.stand_expert(self.stand_normalizer(x))
        return self.deterministic_output((1 - gate) * walk_out + gate * stand_out)

    def get_dummy_inputs(self) -> tuple[torch.Tensor]:
        return (torch.zeros(1, self.input_size),)

    @property
    def input_names(self) -> list[str]:
        return ["obs"]

    @property
    def output_names(self) -> list[str]:
        return ["actions"]

# gate 参数安全范围(P2.5 钳制, 对抗审查后收紧到已验证邻域):
# 最坏角 (k=8, offset=0.35): 直立 gate=σ(-8×0.35)=0.06;
# 最坏角 (k=8, offset=0.60): 倒地 gate=σ(8×0.40)=0.94 — 均在可用包络内
GATE_K_RANGE = (8.0, 16.0)
GATE_OFFSET_RANGE = (0.35, 0.6)
# P2.6 命令分支钳制: w 决定切换锐度, c0 是"算行走"的指令幅值门槛(m/s+rad/s 混合)
CMD_W_RANGE = (8.0, 60.0)
CMD_C0_RANGE = (0.10, 0.40)


class DualExpertMLPModel(MLPModel):
    """MLPModel with two expert branches and a soft gravity gate.

    - self.mlp = WALK expert (run8, frozen)
    - self.stand_expert = RECOVERY/STAND expert (run6, frozen)
    - output = (1-gate)*walk + gate*stand
    - P2.6 双输入 gate(平滑 OR): gate = 1-(1-σ_g)(1-σ_c)
      σ_g = σ(gate_k·(|gz_raw+1| − gate_offset))  — 倒地→起身专家
      σ_c = σ(cmd_w·(cmd_c0 − |vx|+|vy|+|wz|))    — 零指令→站立专家
      (P2.5 只看重力的缺口: 站着不动和行走中 tilt 都≈0, gate 无法区分,
       停住切换站立专家学不出来 — 命令分支补上这个维度)
    - 可训练参数仅 gate 四标量(每次 forward 钳制), 其余全部冻结
    """

    def __init__(self, obs, obs_groups, obs_set, output_dim,
                 hidden_dims=(512, 256, 128), activation="elu",
                 obs_normalization=False, distribution_cfg=None,
                 gravity_slice: int = 5, gate_k: float = 8.0,
                 gate_offset: float = 0.4, cmd_slice: int = 48,
                 cmd_w: float = 40.0, cmd_c0: float = 0.15, **kwargs):
        self._gravity_slice = gravity_slice
        self._cmd_slice = cmd_slice
        super().__init__(obs, obs_groups, obs_set, output_dim,
                         hidden_dims, activation, obs_normalization,
                         distribution_cfg, **kwargs)

        # Second expert branch (recovery) + 【它自己的归一化器】
        # (P2.7 实证: run6 专家吃 run8 归一化器 → 起身 100%→16%; 
        #  冻结专家必须配自己训练时的归一化统计, 2026-11-12 A/B 测试)
        self.stand_expert = MLP(self.obs_dim, output_dim, hidden_dims, activation)
        from rsl_rl.modules.normalization import EmpiricalNormalization
        self.stand_normalizer = EmpiricalNormalization(self.obs_dim)

        # Learnable gate parameters (P2.6: 重力分支 k/offset + 命令分支 w/c0)
        self.gate_k = nn.Parameter(torch.tensor(float(gate_k)))
        self.gate_offset = nn.Parameter(torch.tensor(float(gate_offset)))
        self.cmd_w = nn.Parameter(torch.tensor(float(cmd_w)))
        self.cmd_c0 = nn.Parameter(torch.tensor(float(cmd_c0)))

        # P2.5: 两个专家默认全冻结(walk + stand), 只训 gate
        self._freeze_walk = True
        self._freeze_stand = True
        self._apply_freeze()

    # ---------------- 冻结管理 ----------------
    def freeze_walk_expert(self, freeze: bool = True):
        self._freeze_walk = freeze
        self._apply_freeze()

    def freeze_stand_expert(self, freeze: bool = True):
        self._freeze_stand = freeze
        self._apply_freeze()

    def _apply_freeze(self):
        for p in self.mlp.parameters():
            p.requires_grad = not self._freeze_walk
        for p in self.stand_expert.parameters():
            p.requires_grad = not self._freeze_stand
        # 归一化器统计量冻结(权重本来也不是 Parameter, 但以防外层把整个
        # normalizer 注册进 optimizer, 这里显式关掉) — 两个归一化器都冻
        for p in self.obs_normalizer.parameters():
            p.requires_grad = False
        for p in self.stand_normalizer.parameters():
            p.requires_grad = False
        # 分布 std 冻结(对抗审查 P0): entropy 项对 Normal std 有恒定向上的梯度,
        # run10 的 std_param 漂移 3.88x 就是这条路; 部署走 deterministic,
        # std 无部署意义, 冻在专家原值(run8 的)
        if self.distribution is not None and hasattr(self.distribution, "std_param"):
            self.distribution.std_param.requires_grad_(False)

    def update_normalization(self, obs: TensorDict) -> None:
        """P2.5: 归一化器统计量冻结(保 run8 的, 漂移会破坏冻结专家的输入分布)。"""
        pass  # no-op: run10 事故修复 2

    # ---------------- 前向 ----------------
    def _get_flat_obs(self, obs: TensorDict) -> torch.Tensor:
        """原始(未归一化)flat obs — 与 get_latent 同款拼接, 但不过 normalizer。"""
        obs_list = [obs[obs_group] for obs_group in self.obs_groups]
        return torch.cat(obs_list, dim=-1)

    def _compute_gate(self, raw_flat: torch.Tensor) -> torch.Tensor:
        """双输入 gate(原始 obs, 归一化前): gate = 1-(1-σ_g)(1-σ_c) 平滑 OR。

        σ_g = σ(k·(tilt−offset))       重力分支: 倒地→起身专家
        σ_c = σ(w·(c0−cmd_mag))        命令分支: 零指令→站立专家
          cmd_mag = |vx|+|vy|+|wz|(twist 指令幅值, 原地转弯 |wz| 大也算"在动")
        仅重力输入时(旧 P2.5 兼容) 命令分支为 0。
        """
        # P2.5/2.6 钳制: forward 内就地夹回安全范围, PPO 无法把 gate 学到荒谬值
        with torch.no_grad():
            self.gate_k.clamp_(*GATE_K_RANGE)
            self.gate_offset.clamp_(*GATE_OFFSET_RANGE)
            self.cmd_w.clamp_(*CMD_W_RANGE)
            self.cmd_c0.clamp_(*CMD_C0_RANGE)
        # 重力分支
        gz = raw_flat[:, self._gravity_slice:self._gravity_slice + 1]
        tilt = (gz + 1.0).abs()
        g_g = torch.sigmoid(self.gate_k * (tilt - self.gate_offset))
        # 命令分支(切片越界则该分支失效, 兼容窄 obs)
        if self._cmd_slice is not None and self._cmd_slice + 3 <= raw_flat.shape[-1]:
            c = raw_flat[:, self._cmd_slice:self._cmd_slice + 3]
            cmd_mag = c.abs().sum(dim=-1, keepdim=True)
            g_c = torch.sigmoid(self.cmd_w * (self.cmd_c0 - cmd_mag))
        else:
            g_c = torch.zeros_like(g_g)
        return 1.0 - (1.0 - g_g) * (1.0 - g_c)

    def _dual_forward(self, latent_walk, latent_stand, gate):
        """各专家吃【自己的】归一化 latent, gate 来自原始 obs。"""
        with torch.no_grad():
            walk_out = self.mlp(latent_walk)
            stand_out = self.stand_expert(latent_stand)
        return (1 - gate) * walk_out + gate * stand_out

    def forward(self, obs: TensorDict, masks=None, hidden_state=None,
                stochastic_output: bool = False) -> torch.Tensor:
        """Override forward to use dual-expert path. Signature matches MLPModel."""
        from rsl_rl.utils import unpad_trajectories
        if masks is not None and not self.is_recurrent:
            obs = unpad_trajectories(obs, masks)
        # gate 用【原始】obs(归一化前) — run10 事故修复 1
        raw_flat = self._get_flat_obs(obs)
        gate = self._compute_gate(raw_flat)
        latent_walk = self.get_latent(obs, masks, hidden_state)      # run8 归一化
        latent_stand = self.stand_normalizer(raw_flat)               # run6 归一化
        raw_output = self._dual_forward(latent_walk, latent_stand, gate)
        if self.distribution is not None:
            if stochastic_output:
                self.distribution.update(raw_output)
                return self.distribution.sample()
            return self.distribution.deterministic_output(raw_output)
        return raw_output

    # ---------------- 权重装载 ----------------
    def load_walk_weights(self, checkpoint: dict):
        """Load walk expert from a standard MLPModel checkpoint."""
        sd = checkpoint.get("actor_state_dict", checkpoint)
        mapped = {}
        for k, v in sd.items():
            if k.startswith("mlp."):
                mapped[k] = v
        missing, unexpected = self.load_state_dict(mapped, strict=False)
        assert not any("mlp" in m for m in missing), f"walk expert missing: {missing}"

    def load_stand_weights(self, checkpoint: dict):
        """Load stand expert from a standard MLPModel checkpoint."""
        sd = checkpoint.get("actor_state_dict", checkpoint)
        for name, module in self.stand_expert.named_modules():
            if not isinstance(module, nn.Linear):
                continue
            layer_idx = name.split(".")[0]
            w_key = f"mlp.{layer_idx}.weight"
            b_key = f"mlp.{layer_idx}.bias"
            if w_key in sd:
                module.weight.data.copy_(sd[w_key])
                if hasattr(module, "bias") and module.bias is not None and b_key in sd:
                    module.bias.data.copy_(sd[b_key])

    # ---------------- 导出(对抗审查 P0 修复) ----------------
    def as_jit(self) -> nn.Module:
        return _DualExpertExport(self)

    def as_onnx(self, verbose: bool = False) -> nn.Module:
        m = _DualExpertExport(self)
        m.verbose = verbose
        return m
