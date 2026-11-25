#!/usr/bin/env python
"""stage-ckpt: 让被中断的夜间阶段从失败那一步续跑，而不是整天一刀切。

现成的 stage 幂等只有整天粒度：`already_done` 见到 done 就跳过一整天，
中途断了则连已完成的子步一起重跑，或者干脆整晚作废。这里引入两样东西：

1. 落盘计划（plan）：阶段开工前把步骤写进 `state/ckpt/<stage>.json`，
   后面每一步的完成都记进同一份账。
2. 证据哈希：`done` 必须交产出文件，落盘时算 sha256。下次 `next` 先重算，
   文件被改过或删掉就把该步打回 stale，逼你重做。

写盘一律 tmp + os.replace；账本解析失败自动从 `.bak` 回滚。

用法:
    python ckpt.py plan collect --step fetch --step merge --step dedupe
    python ckpt.py next collect                 # 打印下一个 pending/stale 步，全完输出 DONE
    python ckpt.py done collect merge --evidence radar/x/merged.json
    python ckpt.py verify collect               # 只重算证据，把失真的步打回 stale
    python ckpt.py status collect [--json]
    python ckpt.py reset collect [--force]

退出码: 0 正常 / 1 已无待办或校验发现失真 / 2 用法错
"""
import argparse
import hashlib
import json
import os
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.join(HERE, "..", "..", "..", "state")
VALID = ("pending", "done", "stale")


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class Ledger:
    def __init__(self, root, stage):
        self.dir = os.path.abspath(os.path.join(root, "ckpt"))
        self.path = os.path.join(self.dir, "%s.json" % stage)
        self.bak = self.path + ".bak"
        self.stage = stage

    # ---------------------------------------------------------------- 落盘
    def _write(self, data):
        os.makedirs(self.dir, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        if os.path.exists(self.path):
            with open(self.path, "rb") as f:
                raw = f.read()
            with open(self.bak, "wb") as f:
                f.write(raw)
        os.replace(tmp, self.path)

    def load(self):
        for cand in (self.path, self.bak):
            if not os.path.exists(cand):
                continue
            try:
                with open(cand, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data.get("steps"), list):
                    return data
            except (OSError, ValueError):
                continue
        return None

    def require(self):
        data = self.load()
        if data is None:
            raise SystemExit("阶段 %s 没有计划，先跑 plan" % self.stage)
        return data

    # ---------------------------------------------------------------- 计划
    def plan(self, steps, force=False):
        if not steps:
            raise SystemExit("至少要一个 --step")
        if os.path.exists(self.path) and not force:
            raise SystemExit("计划已存在，重排要加 --force")
        data = {"stage": self.stage, "created": _now(),
                "steps": [{"name": s, "status": "pending", "evidence": {}, "at": None}
                          for s in _uniq(steps)]}
        self._write(data)
        return data

    # ---------------------------------------------------------------- 推进
    def verify(self):
        """重算 done 步的证据；失真或缺失的步降级为 stale。返回被打回的步名。"""
        data = self.require()
        reopened = []
        for st in data["steps"]:
            if st["status"] != "done":
                continue
            bad = None
            for rel, want in st["evidence"].items():
                path = os.path.join(self.dir, rel) if os.path.isabs(rel) else rel
                if not os.path.exists(path):
                    bad = "缺证据 %s" % rel
                    break
                if sha(path) != want:
                    bad = "证据被改 %s" % rel
                    break
            if bad:
                st["status"] = "stale"
                st["reason"] = bad
                reopened.append(st["name"])
        if reopened:
            self._write(data)
        return reopened

    def next_step(self):
        data = self.require()
        self.verify()
        data = self.load()
        for st in data["steps"]:
            if st["status"] != "done":
                return st
        return None

    def done(self, name, evidence):
        data = self.require()
        st = next((s for s in data["steps"] if s["name"] == name), None)
        if st is None:
            raise SystemExit("计划里没有这步: %s" % name)
        if not evidence:
            raise SystemExit("done 必须给 --evidence，没证据等于没做")
        hashes = {}
        for e in evidence:
            if not os.path.exists(e):
                raise SystemExit("证据不存在: %s" % e)
            hashes[os.path.abspath(e)] = sha(e)
        st["status"] = "done"
        st["evidence"] = hashes
        st["at"] = _now()
        st.pop("reason", None)
        self._write(data)
        return st

    def reset(self, force=False):
        if not os.path.exists(self.path):
            raise SystemExit("没有可重置的计划")
        if not force:
            os.remove(self.path)
        else:
            for p in (self.path, self.bak):
                if os.path.exists(p):
                    os.remove(p)


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _uniq(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="阶段步骤账本")
    ap.add_argument("cmd", choices=("plan", "next", "done", "verify", "status", "reset"))
    ap.add_argument("stage")
    ap.add_argument("step", nargs="?")
    ap.add_argument("--step", action="append", default=[], dest="steps")
    ap.add_argument("--evidence", action="append", default=[])
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)

    led = Ledger(a.root, a.stage)

    if a.cmd == "plan":
        data = led.plan(a.steps, a.force)
        print("plan %s: %d 步" % (a.stage, len(data["steps"])))
        return 0
    if a.cmd == "next":
        st = led.next_step()
        if st is None:
            print("DONE")
            return 1
        print(st["name"])
        return 0
    if a.cmd == "done":
        if not a.step:
            raise SystemExit("done 要指定步骤名")
        led.done(a.step, a.evidence)
        print("done %s/%s" % (a.stage, a.step))
        return 0
    if a.cmd == "verify":
        reopened = led.verify()
        if reopened:
            print("reopened: " + " ".join(reopened))
            return 1
        print("证据一致")
        return 0
    if a.cmd == "status":
        data = led.require()
        led.verify()
        data = led.load()
        if a.json:
            print(json.dumps(data, ensure_ascii=False, indent=1))
        else:
            for st in data["steps"]:
                print("%-8s %s" % (st["status"], st["name"]))
        return 0
    if a.cmd == "reset":
        led.reset(a.force)
        print("reset %s" % a.stage)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
