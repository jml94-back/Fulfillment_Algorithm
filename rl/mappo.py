"""MAPPO (공유 파라미터 actor + 중앙 critic) 학습기.  python -m rl.mappo --steps 3e6 --out rl/out/run_mappo

- actor: 각 AMR 의 지역 관측(171)만 본다 → 실행 시 AMR 마다 독립적으로 돌린다(분산 실행).
- critic: 지역 관측 + 전역 상태(4대 위치·속도·목표, 작업자) + 슬롯 원-핫 → 학습 때만 쓴다(중앙 학습).
- --critic local 로 주면 critic 도 지역 관측만 보는 IPPO 가 된다 (비교 실험용).
- 커리큘럼: 단계별 성공률(도착 / (도착 + 충돌 + 시간 초과))이 기준을 넘으면 다음 단계로.
"""
from __future__ import annotations

import argparse
import csv
import json
import multiprocessing as mp
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

from .amr_env import OBS_DIM, STAGES, STATE_DIM, AMRFleetEnv

N_AG = AMRFleetEnv.n_agents


# ---------------------------------------------------------------------- 병렬 환경 (프로세스당 여러 개)
def _worker(conn, seeds, stage):
    envs = [AMRFleetEnv(stage, s) for s in seeds]
    obs = [e.reset() for e in envs]
    stats_acc = [dict(e.stats) for e in envs]
    while True:
        cmd, data = conn.recv()
        if cmd == "step":
            out = []
            for k, e in enumerate(envs):
                o, r, d, info = e.step(data[k])
                trunc = info["truncated"]
                final_cobs = e.critic_obs(o) if trunc else None
                if trunc:
                    for key, v in e.stats.items():
                        stats_acc[k][key] = stats_acc[k].get(key, 0) + v
                    o = e.reset()
                out.append((o, e.critic_obs(o), r, d, trunc, final_cobs, e.active_mask()))
            conn.send(out)
        elif cmd == "init":
            conn.send([(o, e.critic_obs(o), e.active_mask()) for e, o in zip(envs, obs)])
        elif cmd == "stage":
            for e in envs:
                e.set_stage(data)        # 다음 reset 부터 적용
        elif cmd == "stats":
            # 진행 중 에피소드까지 합쳐서 돌려주고 누적값 초기화
            tot = {}
            for k, e in enumerate(envs):
                for key in e.stats:
                    tot[key] = tot.get(key, 0) + stats_acc[k].get(key, 0) + e.stats[key]
                stats_acc[k] = {key: -e.stats[key] for key in e.stats}
            conn.send(tot)
        elif cmd == "close":
            conn.close()
            return


class VecEnv:
    def __init__(self, n_envs: int, n_procs: int, stage: int, seed: int) -> None:
        per = np.array_split(np.arange(n_envs), n_procs)
        self.pipes, self.procs, self.sizes = [], [], []
        ctx = mp.get_context("fork")
        for p, idx in enumerate(per):
            a, b = ctx.Pipe()
            pr = ctx.Process(target=_worker, args=(b, [seed * 1000 + int(i) for i in idx], stage), daemon=True)
            pr.start()
            self.pipes.append(a)
            self.procs.append(pr)
            self.sizes.append(len(idx))

    def init(self):
        for p in self.pipes:
            p.send(("init", None))
        res = [x for p in self.pipes for x in p.recv()]
        return (np.stack([r[0] for r in res]), np.stack([r[1] for r in res]), np.stack([r[2] for r in res]))

    def step(self, actions: np.ndarray):
        k = 0
        for p, n in zip(self.pipes, self.sizes):
            p.send(("step", actions[k:k + n]))
            k += n
        return [x for p in self.pipes for x in p.recv()]

    def set_stage(self, s: int):
        for p in self.pipes:
            p.send(("stage", s))

    def stats(self) -> dict:
        tot = {}
        for p in self.pipes:
            p.send(("stats", None))
            for k, v in p.recv().items():
                tot[k] = tot.get(k, 0) + v
        return tot

    def close(self):
        for p in self.pipes:
            p.send(("close", None))


# ---------------------------------------------------------------------- 신경망
def mlp(i, o, h=256):
    return nn.Sequential(nn.Linear(i, h), nn.Tanh(), nn.Linear(h, h), nn.Tanh(), nn.Linear(h, o))


class Actor(nn.Module):
    def __init__(self):
        super().__init__()
        self.mu = mlp(OBS_DIM, 2)
        self.log_std = nn.Parameter(torch.full((2,), -0.5))

    def dist(self, obs):
        return torch.distributions.Normal(self.mu(obs), self.log_std.exp())


CRITIC_DIM = {"central": OBS_DIM + STATE_DIM, "local": OBS_DIM}


class RunningStd:
    def __init__(self):
        self.n, self.mean, self.m2 = 1e-4, 0.0, 1.0

    def update(self, x):
        for v in np.asarray(x).ravel():
            self.n += 1
            d = v - self.mean
            self.mean += d / self.n
            self.m2 += d * (v - self.mean)

    @property
    def std(self):
        return max(np.sqrt(self.m2 / self.n), 1e-3)


# ---------------------------------------------------------------------- 학습
def train(args):
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    torch.set_num_threads(args.torch_threads)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "config.json").write_text(json.dumps(vars(args), indent=1))
    stage = args.stage_start
    venv = VecEnv(args.envs, args.procs, stage, args.seed)
    obs, cobs, mask = venv.init()                        # (E, 4, D)
    E, T = args.envs, args.horizon
    cdim = CRITIC_DIM[args.critic]
    actor, critic = Actor(), mlp(cdim, 1)
    opt = torch.optim.Adam(list(actor.parameters()) + list(critic.parameters()), lr=args.lr, eps=1e-5)
    ret_rms = RunningStd()
    ret_acc = np.zeros((E, N_AG))

    def cin(o, c):
        return c if args.critic == "central" else o

    log = open(out / "log.csv", "w", newline="")
    wr = csv.writer(log)
    wr.writerow(["update", "agent_steps", "stage", "mean_reward", "arrivals", "collisions", "coll_robot",
                 "coll_worker", "coll_static", "timeouts", "success", "arrivals_per_agent_hour",
                 "collisions_per_100_arrivals", "worker_fast_frac", "std", "wall_s"])
    steps, upd, t0 = 0, 0, time.time()
    stage_steps = 0
    stage_hist: list = []
    best_success = 0.0
    while steps < args.steps:
        B_obs = np.zeros((T, E, N_AG, OBS_DIM), np.float32)
        B_cob = np.zeros((T, E, N_AG, cdim), np.float32)
        B_act = np.zeros((T, E, N_AG, 2), np.float32)
        B_logp = np.zeros((T, E, N_AG), np.float32)
        B_val = np.zeros((T + 1, E, N_AG), np.float32)
        B_rew = np.zeros((T, E, N_AG), np.float32)
        B_term = np.zeros((T, E, N_AG), np.float32)
        B_trunc = np.zeros((T, E), np.float32)
        B_boot = np.zeros((T, E, N_AG), np.float32)
        B_mask = np.zeros((T, E, N_AG), np.float32)
        for t in range(T):
            with torch.no_grad():
                o = torch.as_tensor(obs)
                d = actor.dist(o)
                a = d.sample()
                lp = d.log_prob(a).sum(-1)
                v = critic(torch.as_tensor(cin(obs, cobs))).squeeze(-1)
            B_obs[t], B_cob[t], B_act[t], B_logp[t], B_val[t], B_mask[t] = obs, cin(obs, cobs), a, lp, v, mask
            res = venv.step(a.numpy())
            nobs, ncobs, nmask = [], [], []
            for e, (o2, c2, r, dn, tr, fc, m2) in enumerate(res):
                B_rew[t, e], B_term[t, e], B_trunc[t, e] = r, dn, float(tr)
                if tr:
                    with torch.no_grad():
                        B_boot[t, e] = critic(torch.as_tensor(cin(fc[:, :OBS_DIM], fc))).squeeze(-1).numpy()
                nobs.append(o2)
                ncobs.append(c2)
                nmask.append(m2)
            obs, cobs, mask = np.stack(nobs), np.stack(ncobs), np.stack(nmask)
            # 보상 크기 정규화용: 할인 누적 보상의 표준편차
            ret_acc = ret_acc * args.gamma * (1 - B_term[t]) + B_rew[t]
            ret_rms.update(ret_acc[B_mask[t] > 0])
            ret_acc[B_trunc[t] > 0] = 0
        with torch.no_grad():
            B_val[T] = critic(torch.as_tensor(cin(obs, cobs))).squeeze(-1).numpy()
        # GAE (보상은 표준편차로 나눠서 사용; critic 은 정규화된 값 공간에서 학습)
        rew = B_rew / ret_rms.std
        adv = np.zeros_like(rew)
        last = np.zeros((E, N_AG), np.float32)
        for t in reversed(range(T)):
            tr = B_trunc[t][:, None]
            nv = np.where(tr > 0, B_boot[t], B_val[t + 1])
            nonterm = 1.0 - B_term[t]
            delta = rew[t] + args.gamma * nv * nonterm - B_val[t]
            last = delta + args.gamma * args.lam * nonterm * (1 - tr) * last
            adv[t] = last
        ret = adv + B_val[:T]
        m = B_mask.reshape(-1) > 0
        f_obs = torch.as_tensor(B_obs.reshape(-1, OBS_DIM)[m])
        f_cob = torch.as_tensor(B_cob.reshape(-1, cdim)[m])
        f_act = torch.as_tensor(B_act.reshape(-1, 2)[m])
        f_lp = torch.as_tensor(B_logp.reshape(-1)[m])
        f_adv = torch.as_tensor(adv.reshape(-1)[m])
        f_ret = torch.as_tensor(ret.reshape(-1)[m])
        f_adv = (f_adv - f_adv.mean()) / (f_adv.std() + 1e-8)
        n = len(f_obs)
        for _ in range(args.epochs):
            perm = torch.randperm(n)
            for s in range(0, n, args.minibatch):
                idx = perm[s:s + args.minibatch]
                d = actor.dist(f_obs[idx])
                lp = d.log_prob(f_act[idx]).sum(-1)
                ratio = (lp - f_lp[idx]).exp()
                pg = -torch.min(ratio * f_adv[idx], ratio.clamp(1 - args.clip, 1 + args.clip) * f_adv[idx]).mean()
                vl = 0.5 * (critic(f_cob[idx]).squeeze(-1) - f_ret[idx]).pow(2).mean()
                ent = d.entropy().sum(-1).mean()
                loss = pg + args.vf * vl - args.ent * ent
                opt.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(list(actor.parameters()) + list(critic.parameters()), 0.5)
                opt.step()
        steps += n
        upd += 1
        st = venv.stats()
        ev = st["arrivals"] + st["collisions"] + st["timeouts"]
        success = st["arrivals"] / max(ev, 1)
        hours = st["steps"] * 0.1 / 3600
        row = [upd, steps, stage, float(B_rew.reshape(-1)[m].mean()), st["arrivals"], st["collisions"],
               st["coll_robot"], st["coll_worker"], st["coll_static"], st["timeouts"], round(success, 4),
               round(st["arrivals"] / max(hours, 1e-9), 2),
               round(100 * st["collisions"] / max(st["arrivals"], 1), 2),
               round(st["worker_fast"] / max(st["steps"], 1), 4),
               float(actor.log_std.detach().exp().mean()), round(time.time() - t0, 1)]
        wr.writerow(row)
        log.flush()
        if upd % 5 == 0 or upd == 1:
            print(f"[{upd:4d}] steps {steps:>9,} stage {stage} rew {row[3]:+.3f} arr {st['arrivals']:4d} "
                  f"coll {st['collisions']:3d} to {st['timeouts']:3d} succ {success:.3f} "
                  f"arr/h {row[11]:6.1f} std {row[14]:.2f} {row[15]:.0f}s", flush=True)
        # 커리큘럼
        stage_hist.append((st["arrivals"], ev))
        # 최근 업데이트를 거꾸로 모아 사건 수가 cur_min_events 이상이 되는 구간의 성공률
        a_sum = e_sum = n_win = 0
        for a_, e_ in reversed(stage_hist):
            a_sum, e_sum, n_win = a_sum + a_, e_sum + e_, n_win + 1
            if e_sum >= args.cur_min_events and n_win >= args.cur_window:
                break
        win = stage_hist[-n_win:] if e_sum >= args.cur_min_events and n_win >= args.cur_window else []
        stage_steps += n
        if stage < args.stage_max and ((win and a_sum / e_sum >= args.cur_success)
                                       or stage_steps >= args.stage_max_steps):
            stage += 1
            venv.set_stage(stage)
            stage_hist = []
            stage_steps = 0
            torch.save({"actor": actor.state_dict(), "critic": critic.state_dict()}, out / f"stage{stage - 1}.pt")
            print(f"  → 커리큘럼 단계 {stage} 로 진행", flush=True)
        if stage == args.stage_max and win and a_sum / e_sum > best_success:
            best_success = a_sum / e_sum
            torch.save({"actor": actor.state_dict(), "critic": critic.state_dict(), "stage": stage,
                        "success": best_success, "steps": steps}, out / "best.pt")
        torch.save({"actor": actor.state_dict(), "critic": critic.state_dict(), "stage": stage, "steps": steps},
                   out / "last.pt")
    venv.close()
    log.close()


def parse(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--steps", type=float, default=3e6, help="에이전트-스텝 수 (0.1초 x 로봇 수)")
    p.add_argument("--envs", type=int, default=16)
    p.add_argument("--procs", type=int, default=4)
    p.add_argument("--horizon", type=int, default=128)
    p.add_argument("--epochs", type=int, default=5)
    p.add_argument("--minibatch", type=int, default=2048)
    p.add_argument("--lr", type=float, default=3e-4)
    p.add_argument("--gamma", type=float, default=0.99)
    p.add_argument("--lam", type=float, default=0.95)
    p.add_argument("--clip", type=float, default=0.2)
    p.add_argument("--vf", type=float, default=0.5)
    p.add_argument("--ent", type=float, default=0.001)
    p.add_argument("--critic", choices=["central", "local"], default="central")
    p.add_argument("--stage-start", type=int, default=0)
    p.add_argument("--stage-max", type=int, default=len(STAGES) - 1)
    p.add_argument("--cur-success", type=float, default=0.85)
    p.add_argument("--cur-window", type=int, default=10)
    p.add_argument("--cur-min-events", type=int, default=150)
    p.add_argument("--torch-threads", type=int, default=2)
    p.add_argument("--stage-max-steps", type=float, default=8e6,
                   help="한 단계에서 이만큼 학습해도 기준을 못 넘으면 다음 단계로 (마지막 단계 학습 보장)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", default="rl/out/run_mappo")
    a = p.parse_args(argv)
    a.steps = int(a.steps)
    return a


if __name__ == "__main__":
    train(parse())
