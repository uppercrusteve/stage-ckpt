# stage-ckpt

给夜间流水线的阶段加「步级断点」：断在哪步，下个窗口从哪步续。

## 做什么

原来只有整天粒度的幂等：`already_done` 见到 `done` 就跳过一整天。代价是两头都难受——中途断了，已完成的子步跟着重跑；断两次，整晚作废。

这个模块把阶段拆成写进磁盘的步骤账本：

- `plan` 开工前把步骤钉进 `state/ckpt/<stage>.json`，全部 `pending`
- `next` 只吐下一个待办，全做完吐 `DONE`
- `done` 必须交 `--evidence`，落盘时对产出文件算 sha256
- `verify` 每次 `next` 前自动跑：文件被改过、被删掉，该步立刻打回 `stale`，逼重做

三条实现上的硬规矩：

1. **没证据等于没做**。`done` 不给 `--evidence` 直接报错，堵住「嘴上说完了」。
2. **写盘原子**。一律 tmp + `os.replace`；账本被写坏时自动从 `.bak` 回滚到上一次写盘前的版本——只丢最后一笔，不丢整份计划。
3. **时间戳不当文件名**。账本路径固定，重跑覆盖，能前后比对。

## 怎么跑

先看最小例子（三步假阶段，第二步故意失败一次，第二轮自动从第二步续）：

```bash
bash example/run-digest.sh
```

接进 `lib/gate.sh` 的写法：

```bash
source lib/gate.sh; source forge/<D>/stage-ckpt/ckpt.sh
window_ok "$S" || exit 0
gate_check "$S" || exit 0
ckpt_boot "$S" fetch merge dedupe    # 有账就沿用旧账，没账才新建
ckpt_run  "$S" run_step              # run_step <step> 干活，完事设 CKPT_EVIDENCE
```

`ckpt.sh` 认 `CKPT_ROOT` 环境变量，默认指向仓内 `state/`；测试和例子都用临时目录，不碰真账。

单独用 CLI：

```bash
python ckpt.py plan fetch --step a --step b
python ckpt.py next fetch
python ckpt.py done fetch a --evidence radar/x/a.json
python ckpt.py status fetch --json
python ckpt.py verify fetch
```

退出码：`0` 正常、`1` 已无待办或证据失真、`2` 用法错。

自测：

```bash
python -m unittest discover -s . -t .
```

已知边界：账本只保证「步骤被认真记录」，不保证步骤本身正确；证据哈希能发现篡改，挡不住一开始就交空文件——判据得自己写。

## 上游

`OthmanAdi/planning-with-files`（MIT）的「落盘计划 + 每轮重注入 + 确定性完成门禁」三件套，专治 compaction 之后断片。这里借了前两件，第三件换成可执行的证据校验。逐条见 `upstream.md`。本仓的 `lib/gate.sh` 是同项目内文件，不算上游。

## 许可

本目录原创部分 MIT，见 `LICENSE`。无第三方代码。

## 原创

上游把计划落盘只为人能重读，`gate.sh` 的幂等只到整天——**两边各缺一半，接起来才成立：从「断了要么全重跑要么整晚作废」到「断哪续哪，证据被改过就作废那一步」**，续跑因此从许愿变成了可验证的状态机。
