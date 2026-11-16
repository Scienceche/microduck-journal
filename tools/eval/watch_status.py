"""Bridge: mjlab tensorboard events -> rl_logs/status.json for the :8088 dashboard.

Maps the official microduck_rl training (mjlab/rsl_rl, tensorboard logger)
onto the status.json schema the existing dashboard already renders:

  timesteps   = iterations x num_envs x 24 (steps per env per iteration)
  avg_steps   = Train/mean_episode_length   (survival steps @50Hz, 1000 = full 20s)
  best_steps  = running max of mean_episode_length
  avg_reward  = Train/mean_reward
  scores      = last 10 (steps, reward, time_out_fraction)
  goal        = mean_episode_length >= 1000 (full episodes surviving)

Usage: python watch_status.py <num_envs> [log_root]
"""

import glob
import json
import os
import sys
import time

from tensorboard.backend.event_processing import event_accumulator

NUM_ENVS = int(sys.argv[1]) if len(sys.argv) > 1 else 4096
STEPS_PER_ENV = 24
LOG_ROOT = (
    sys.argv[2]
    if len(sys.argv) > 2
    else r"C:\duck_work\microduck_rl\logs\rsl_rl\velocity"
)
STATUS = r"C:\imu_build\rl_logs\status.json"
EPISODE_LIMIT = 1000  # 20 s at 50 Hz (base velocity env episode_length_s)


def newest_run() -> str | None:
    runs = [d for d in glob.glob(os.path.join(LOG_ROOT, "*", ""))]
    return max(runs, key=os.path.getmtime) if runs else None


def main() -> None:
    run_dir = None
    ea = None
    history = []
    best = 0
    last_hist_step = -10**9
    while True:
        newest = newest_run()
        if newest != run_dir:
            run_dir = newest
            ea = None
        if run_dir:
            if ea is None:
                ea = event_accumulator.EventAccumulator(run_dir)
            try:
                ea.Reload()
                scal = ea.Tags().get("scalars", [])

                def last(tag):
                    if tag not in scal:
                        return None
                    ev = ea.Scalars(tag)
                    return ev[-1].value if ev else None

                def series(tag):
                    return (
                        [(e.step, e.value) for e in ea.Scalars(tag)]
                        if tag in scal
                        else []
                    )

                ep_len = last("Train/mean_episode_length")
                reward = last("Train/mean_reward")
                timeout = last("Episode_Termination/time_out")
                fps = last("Perf/total_fps")
                iters = len(series("Train/mean_reward"))
                timesteps = iters * NUM_ENVS * STEPS_PER_ENV

                if ep_len is not None:
                    best = max(best, ep_len)
                    if timesteps - last_hist_step >= 250_000:
                        history.append(
                            {
                                "timesteps": timesteps,
                                "avg_steps": ep_len,
                                "avg_reward": reward,
                                "best": best,
                                "elapsed_s": time.time(),
                            }
                        )
                        history = history[-120:]
                        last_hist_step = timesteps
                    scores = [
                        {"steps": ep_len, "reward": reward, "timeout": timeout}
                    ]
                    goal = ep_len >= EPISODE_LIMIT
                    status = {
                        "running": True,
                        "goal_reached": goal,
                        "timesteps": timesteps,
                        "best_steps": best,
                        "avg_steps": ep_len,
                        "avg_reward": reward,
                        "elapsed_s": time.time(),
                        "fps": fps,
                        "iterations": iters,
                        "history": history,
                        "scores": scores,
                        "run_dir": run_dir,
                        "timestamp": time.time(),
                    }
                    tmp = STATUS + ".tmp"
                    with open(tmp, "w") as f:
                        json.dump(status, f, ensure_ascii=False)
                    os.replace(tmp, STATUS)
            except Exception as e:  # events file being written etc.
                print("reload skip:", e, flush=True)
        time.sleep(5)


if __name__ == "__main__":
    main()
