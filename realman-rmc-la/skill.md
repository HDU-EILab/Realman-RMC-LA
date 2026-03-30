---
name: openclaw-realman-rmc-la
version: 1.3.2
description: Realman RMC-LA isolated-network operation skill. SSH jump-host access + JSON API control for arm and dexterous hand, plus base/lift probing workflow.
---

# OpenClaw · Realman RMC-LA 系统控制 Skill

**版本**：`1.3.2`（与 `skill.json` 中 `version` 一致）。

## 能力范围（本 skill 做什么）

在 **Realman 端侧网络** 中，统一处理以下链路：

| 能力 | 说明 |
|------|------|
| **SSH 跳转主控** | 先登录端侧主控，再访问隔离网段设备 |
| **机械臂 JSON 控制** | 通过 `192.168.10.18:8080` 下发 RMC-LA JSON 指令 |
| **灵巧手最小控制** | 通过机械臂末端 Modbus 转发，执行开/合验证 |
| **底盘/升降台探活模板** | 未知协议时先做网络层/协议层/执行层分步验证 |

> 本 skill 是 OpenClaw 的多个 skill 之一。  
> **仅负责连通、探活、最小风险控制**，不替代其它 skill 的完整业务流程编排。

## 部署意图（OpenClaw）

本 skill 将部署到 **OpenClaw agent**，目标是让 OpenClaw 能够：

1. 通过 SSH 进入端侧主控；
2. 访问并控制该独立系统中的关键设备（机械臂、灵巧手、移动底盘、升降台）；
3. 以“先探活、再最小动作、再回读确认”的方式稳定执行工作。

> 即：这是 OpenClaw 控制这套独立 Realman 系统的操作基线文档。

---

## 已知现场拓扑（按实测）

| 角色 | 地址 | 备注 |
|------|------|------|
| 跳板主控 | 读取 `runtime_config.json` 中 `jump_host_ip` | SSH 入口 |
| SSH 账号 | `rm / rm` | 受控网络调试账号 |
| 机械臂控制器 | `192.168.10.18:8080` | JSON/TCP 接口 |
| 灵巧手型号 | `ROH-LiteS001` | 用户已确认（按现场确认为准） |

实测结论（已验证）：

- 从本地可连 `192.168.10.18:8080`；
- 从跳板主控（`runtime_config.json` 中 `jump_host_ip`）可连 `192.168.10.18:8080`；
- `get_joint_degree` 可正常返回；
- `movej`（关节1到90度）返回 `receive_state=true` 且轨迹完成反馈；
- 灵巧手 Modbus 开合指令返回 `write_state=true`。

---

## 何时调用本 skill（触发条件）

当用户意图属于以下类型时应优先使用本 skill：

1. “先 SSH 到主控，再控隔离网机械/末端设备”；
2. “验证 RMC-LA 控制链路是否可用（端口、协议、回包）”；
3. “灵巧手是否可开合、寄存器控制是否生效”；
4. “底盘/升降台接入前的探活流程与最小动作验证”。

不应由本 skill 直接处理：

- 复杂任务编排（完整抓取工艺、导航任务树、产线状态机）；
- 与本协议无关的视觉模型训练或离线算法开发。

---

## OpenClaw 调用判定（明确规则）

### 应调用本 skill（满足任一即可）

1. 用户明确提到 **Realman / 睿尔曼 / RMC-LA / RM65**，且目标是控制现场设备。  
2. 任务包含 **SSH 跳板主控**（读取 `runtime_config.json` 的 `jump_host_ip`）后再访问隔离网设备。  
3. 任务属于以下之一：  
   - 机械臂 JSON 接口连通性/状态验证；  
   - 机械臂最小动作验证（如 `movej` 单轴小幅测试）；  
   - 灵巧手 Modbus 开合/寄存器写入验证；  
   - 底盘/升降台的网络探活与最小风险动作验证。  
4. 用户要求“先探活、再动作、回读确认”的安全执行方式。

### 不应调用本 skill

1. 任务主体不是 Realman 系统（例如 UR5+RealSense 视觉跟随）。  
2. 需求是纯视觉算法开发/训练，与 Realman 控制链路无关。  
3. 需求是完整业务编排（多系统任务图、生产节拍策略），而非设备接入与最小控制验证。

### 边界情况（先调用本 skill 再移交）

- 若用户最终目标是“复杂任务编排”，但当前阶段还未完成设备连通与最小控制验证，**先调用本 skill 完成接入验收**，再将结果移交给上层流程 skill。  
- 若出现故障（超时、409占用、回包 false、轨迹不动），优先按本 skill 的恢复流程处理，再决定是否移交。

### 给 OpenClaw 的路由提示（简版）

- 关键词命中：`睿尔曼`、`Realman`、`RMC-LA`、`RM65`、`jump host`、`192.168.10.18`、`灵巧手`、`Modbus`。  
- 命中后默认路由到本 skill，除非用户明确要求其它系统（如 UR/RealSense）或纯算法任务。

---

## 如何调用（执行契约）

### 1) 标准顺序

1. **先 SSH 到主控**  
2. **主控侧检查目标设备端口**  
3. **先读状态，再发轻量动作**  
4. **动作后复读状态并记录回包**  

### 2) SSH 跳转模板

先读取配置文件中的跳板 IP：

```bash
# runtime_config.json
{"jump_host_ip":"<your_jump_host_ip>"}
```

然后按该 IP 连接：

```bash
ssh rm@<jump_host_ip>
```

### 3) 主控侧端口探活模板

```bash
python3 - <<'PY'
import socket
host, port = "192.168.10.18", 8080
s = socket.socket(); s.settimeout(3)
try:
    s.connect((host, port))
    print("TCP connect ok", host, port)
except Exception as e:
    print("TCP connect failed:", e)
finally:
    s.close()
PY
```

---

## RMC-LA JSON 通信要点（来自协议文档）

- 所有命令为 JSON：`{"command":"..."}`
- 每条指令必须以 `\r\n` 结尾
- 常用反馈：
  - 设置结果：`{"command":"...","...":true/false}`
  - 状态结果：`{"state":"...","...":...}`
- 单位常见缩放：
  - 关节角：`0.001°`
  - 位置：`0.001 mm`
  - 姿态：`0.001 rad`

---

## 内置接口速查（迁移后不依赖 PDF）

> 本节为迁移到 OpenClaw 后的离线速查清单，优先覆盖现场高频控制链路。

### A) 通信与会话参数

| 项 | 值 |
|---|---|
| 机械臂控制地址 | `192.168.10.18:8080` |
| 传输协议 | TCP + JSON 文本 |
| 结束符 | **必须** `\r\n` |
| 串口默认（参考） | `460800 / 8N1` |
| 建议超时 | 连接 3s；读超时 0.2~1s；动作等待按任务设置 |

### B) 机械臂基础接口（建议先读后动）

| 用途 | 命令 | 关键参数 | 成功判定 |
|---|---|---|---|
| 读关节角 | `get_joint_degree` | 无 | 返回 `state=joint_degree` |
| 读整机状态 | `get_current_arm_state` | 无 | 返回 `state=current_arm_state` |
| 清系统错误 | `clear_system_err` | 无 | `clear_state=true` |
| 上/断电 | `set_arm_power` | `arm_power: 1/0` | 回包为 `true` |
| 读电源状态 | `get_arm_power_state` | 无 | `power_state=1/0` |

请求示例：

```json
{"command":"get_current_arm_state"}
```

典型状态返回字段：

```json
{"state":"current_arm_state","arm_state":{"joint":[...],"pose":[...],"arm_err":0,"sys_err":0}}
```

### C) 运动接口（最小必要参数）

| 运动类型 | 命令 | 必要参数 | 备注 |
|---|---|---|---|
| 关节运动 | `movej` | `joint[6]`,`v`,`r` | `joint` 单位 `0.001°` |
| 直线运动 | `movel` | `pose[6]`,`v`,`r` | `pose` 为 `[x,y,z,rx,ry,rz]` |
| 圆弧运动 | `movec` | `pose_via`,`pose_to`,`v`,`r`,`loop` | 高频场景一般先不用 |
| 关节步进 | `set_joint_step` | `joint_step:[idx,step]`,`v` | 小步验证推荐 |
| 位置步进 | `set_pos_step` | `step_type`,`step`,`v` | `step_type: x/y/z` |
| 姿态步进 | `set_ort_step` | `step_type`,`step`,`v` | `step_type: rx/ry/rz` |

轨迹反馈判定（动作成功）：

```json
{"state":"current_trajectory_state","trajectory_state":true,"device":0}
```

### D) 轨迹控制接口

| 用途 | 命令 |
|---|---|
| 急停（不可恢复） | `set_arm_stop` |
| 暂停（可恢复） | `set_arm_pause` |
| 继续 | `set_arm_continue` |
| 清当前轨迹（需暂停后） | `set_delete_current_trajectory` |
| 清全部轨迹（需暂停后） | `set_arm_delete_trajectory` |
| 查当前轨迹类型 | `get_arm_current_trajectory` |

### E) 灵巧手 / 末端 Modbus（当前现场实测路径）

1) 先开 Modbus 模式：

```json
{"command":"set_modbus_mode","port":1,"baudrate":115200,"timeout":2}
```

参数说明：

- `port=1`：末端接口板 RS485  
- `baudrate`：常用 `115200`  
- `timeout`：单位百毫秒，建议 `1~3`

2) 再写寄存器控制手势（现场使用 `address=1135,num=6,device=2`）：

- 开手：`data=[0,0,0,0,0,0,0,0,0,0,255,0]`
- 闭手：`data=[255,255,255,255,255,255,255,255,255,255,0,0]`

`num=6` 表示连续 6 个 16 位寄存器，载荷为 12 字节（每通道低字节为关节量 **0~255**，高字节常为 `0`）。通道语义（与现场约定一致）：

| 通道 | 含义 |
|------|------|
| 1 | 拇指（主驱动） |
| 2~5 | 食指、中指、无名指、小指 |
| 6 | 拇指根部 |

开/闭手预设中：**通道 1~5** 与 **通道 6** 的“开/闭”极性相反（开手时 ch6 为 `255`、闭手时 ch6 为 `0`），与上表两例 `data` 一致。

成功判定：

```json
{"command":"write_registers","write_state":true}
```

### F) 控制器/工具端 IO 常用接口

| 范围 | 用途 | 命令 |
|---|---|---|
| 控制器 IO | 设置数字 IO 模式（I 系列） | `set_IO_mode` |
| 控制器 IO | 设置数字输出 | `set_DO_state` |
| 控制器 IO | 查询 IO 状态 | `get_IO_state` / `get_DO_state` / `get_DI_state` |
| 工具端 IO | 设置输出 | `set_tool_DO_state` |
| 工具端 IO | 设置模式 | `set_tool_IO_mode` |
| 工具端 IO | 查询状态 | `get_tool_IO_state` |
| 工具端供电 | 设置电压 | `set_tool_voltage` |
| 工具端供电 | 查询电压 | `get_tool_voltage` |

### G) 推荐最小调用序列（机械臂+灵巧手）

1. `get_arm_power_state`（确认上电）  
2. `get_current_arm_state`（确认错误状态）  
3. 必要时 `clear_system_err`  
4. `get_joint_degree`（记录基线）  
5. 小动作 `movej` / `set_joint_step`（低速）  
6. 等待 `current_trajectory_state=true`  
7. `set_modbus_mode(port=1)`  
8. `write_registers` 开手/闭手验证  
9. 再次 `get_current_arm_state` 与 `get_joint_degree` 做闭环

### H) 返回字段速记（便于编排器解析）

| 字段 | 含义 |
|---|---|
| `receive_state=true` | 指令接收成功（不等于动作完成） |
| `trajectory_state=true` | 轨迹完成到位 |
| `set_state=true` | 设置类命令成功 |
| `write_state=true` | Modbus 写入成功 |
| `arm_err/sys_err` | 机械臂/系统错误码（0 通常为正常） |

---

## 机械臂最小验证指令集

### 读关节角

```json
{"command":"get_joint_degree"}
```

### 关节1到 +90°

```json
{"command":"movej","joint":[90000,0,0,0,0,0],"v":20,"r":0}
```

### 轨迹完成反馈（示例）

```json
{"state":"current_trajectory_state","trajectory_state":true,"device":0}
```

---

## 灵巧手最小验证指令集（经末端 Modbus）

### 1) 配置末端 Modbus

```json
{"command":"set_modbus_mode","port":1,"baudrate":115200,"timeout":2}
```

### 2) 开手（示例）

```json
{"command":"write_registers","port":1,"address":1135,"num":6,"data":[0,0,0,0,0,0,0,0,0,0,255,0],"device":2}
```

### 3) 闭手（示例）

```json
{"command":"write_registers","port":1,"address":1135,"num":6,"data":[255,255,255,255,255,255,255,255,255,255,0,0],"device":2}
```

判定标准：返回 `set_state=true` / `write_state=true` 即链路成功。

### 4) 升降机（本地调试 GUI，非厂商 Web 示教器）

厂商 **Web 示教器**（浏览器访问控制器页面）一般**无法**由用户二次开发嵌入自定义按钮。若要在案头侧与灵巧手同一套流程里试升降，可使用仓库内 **`Realman/hand_debug_gui.py`**：仍通过 **`192.168.10.18:8080` JSON**，发送 **`set_modbus_mode`**（可选、按口配置波特率）+ **`write_registers`**。其中 `port` / `device` / `address` / `num` / `data` 须按**升降机说明书**及**实际接在控制器的 RS485 口**填写（可与灵巧手 `port=1` 不同，例如另一路 `port=2`）。

---

## 底盘 / 升降台接入流程（协议未知时）

按三层推进，避免误动作：

1. **网络层**：主控到目标设备 `IP:PORT` 连通  
2. **协议层**：只发查询/探活命令  
3. **执行层**：最小位移/低速动作 + 状态回读

检索路径（主控）：

- `~/catkin_ws/src/`
- `~/Documents/*/src/`

关键词：

- `base`, `chassis`, `lift`, `elevator`, `modbus`, `udp`, `tcp`

---

## 返回编排器的结构化结果（建议）

```json
{
  "ok": true,
  "skill": "openclaw-realman-rmc-la",
  "sub_capability": "ssh-isolated-network-access",
  "jump_host": "<jump_host_ip_from_runtime_config>",
  "targets": [
    {
      "name": "robot_controller",
      "endpoint": "192.168.10.18:8080",
      "probe": "tcp_connect",
      "result": "ok"
    }
  ],
  "notes": "Minimal-risk verification only. No full workflow orchestration executed."
}
```

---

## 安全要求

1. 先状态查询，后动作命令。  
2. 首次动作使用低速（如 `v<=20`）。  
3. 灵巧手先开手再闭手，避免意外夹持。  
4. 未知底盘/升降台协议时，禁止直接发连续运动命令。  

---

## 常见故障与恢复流程

本节聚焦四类高频问题：**超时、409占用、回包 false、轨迹不动**。

### 1) 超时（连接超时 / 读超时）

典型现象：

- TCP 连接 `192.168.10.18:8080` 失败；
- 指令已发送但迟迟无返回；
- SSH 可连，但主控到目标设备不通。

排查顺序：

1. 先确认 SSH 跳板在线：`ssh rm@<jump_host_ip>`  
2. 在主控侧测目标端口连通（socket connect）  
3. 检查命令是否以 `\r\n` 结尾（协议硬要求）  
4. 降低并发：同一时刻仅保留一个控制通道  
5. 重发“只读命令”验证链路（如 `get_joint_degree`）

恢复动作：

- 若端口不通：先修网络，再恢复控制；
- 若偶发读超时：保持连接，间隔重试（建议 1~2s）；
- 若持续超时：关闭连接后重建 socket，再执行只读探活。

### 2) 409 占用（资源被占用）

典型现象：

- 上层服务返回 409 或“busy/occupied”；
- 相机/设备/控制口被其他进程占用（虽本 skill 不以视觉为主，但整体系统可能有并行进程）。

排查顺序：

1. 确认是否有并行控制进程（示教器、其他 agent、后台脚本）；
2. 确认是否存在“上次残留任务”未退出；
3. 优先停止占用方，再进行当前任务。

恢复动作：

- 严格单控制源：同一设备只允许一个主动控制会话；
- 清理残留进程后，先执行只读命令再恢复动作命令；
- 恢复后第一条命令建议使用 `get_current_arm_state`。

### 3) 回包 `false`（设置失败/写失败）

典型现象：

- `{"command":"...","set_state":false}`
- `{"command":"write_registers","write_state":false}`
- `{"command":"...","...":false}`

常见原因：

1. 参数非法（范围/字段名/数据长度错误）；  
2. 设备模式未切换（例如灵巧手未先 `set_modbus_mode`）；  
3. 设备状态不允许（关节错误未清、使能状态不对）；  
4. 命令冲突（并发下发）。

恢复动作（推荐固定流程）：

1. 执行只读状态查询；
2. 机械臂侧先清错：`clear_system_err` / 关节清错；
3. 灵巧手侧重新配置 Modbus，再发写寄存器；
4. 降低动作复杂度（先开手/小角度，再执行主动作）；
5. 若仍失败，保留失败请求与回包用于定位。

### 4) 轨迹不动（命令接收但机械臂未运动）

典型现象：

- 收到 `receive_state=true`，但机械臂不动；
- 无 `current_trajectory_state=true` 到位反馈；
- 指令下发后停在原位。

排查顺序：

1. 电源与使能：确认上电状态、关节使能状态；  
2. 错误状态：查询 `arm_err/sys_err`、关节错误码；  
3. 目标合理性：目标点是否可达、是否越限、速度是否过低；  
4. 坐标系一致性：当前工具/工作坐标系是否与目标定义一致；  
5. 运动链冲突：是否存在暂停/急停/轨迹残留。

恢复动作：

1. 读取 `get_current_arm_state` + `get_joint_degree`；
2. 执行 `clear_system_err`（必要时）；
3. 发送小步动作验证（如关节1小角度）；
4. 验证通过后再执行目标轨迹；
5. 若仍不动，切回示教器核对机器人本体状态。

### 故障处理输出模板（建议）

```json
{
  "ok": false,
  "skill": "openclaw-realman-rmc-la",
  "step_failed": "timeout|busy_409|response_false|trajectory_not_moving",
  "endpoint": "192.168.10.18:8080",
  "last_command": {"command":"movej","joint":[90000,0,0,0,0,0],"v":20,"r":0},
  "last_response": {"state":"current_trajectory_state","trajectory_state":false},
  "recovery_action": "clear_system_err_then_probe_readonly_then_small_motion_test"
}
```

---

## 维护约定（后续必须持续更新）

从现在开始，对该项目的后续调整（网络、设备地址、命令参数、控制流程、恢复策略）都应同步更新本文件，至少包含：

1. **改动内容**（改了什么）  
2. **实测结果**（如何验证）  
3. **影响范围**（机械臂/灵巧手/底盘/升降台）  
4. **回滚方式**（失败如何恢复）

建议每次改动在文末追加“变更记录”条目。

---

## 文档来源与依据

- `睿尔曼6自由度机械臂JSON通信协议v3.5.3.pdf`
- `睿尔曼RM65I系列机器人WEB示教器用户手册V1.1.pdf`
- 现场联机实测记录（SSH 跳转、机械臂/灵巧手指令回包验证）

---

## 实验记录（2026-03-23）

> 以下仅保留对后续接入与故障恢复有直接价值的实验结论。

### 记录 A：OHand Python SDK 可用性验证（主控机）

- 目标：验证 `ohand_serial_sdk_python` 是否可在跳板主控环境运行。  
- 环境：跳板主控 `runtime_config.json` → `jump_host_ip`（当前为 `192.168.0.115`），`Python 3.8.10`，`aarch64`。  
- 操作：克隆仓库、安装 SDK、导入 `ohand` 模块。  
- 结果：安装与导入成功，说明 SDK 在该主控环境可用（至少到导入层）。  
- 影响：可作为“主控直连串口方案”的备选能力。  
- 备注：后续发现现场并非主控直连灵巧手，故该方案不是当前主链路。

### 记录 B：主控本地串口路径排查（否定性结论）

- 目标：确认是否可从主控本地串口直接控制灵巧手。  
- 观测：仅见 `/dev/ttyS0~ttyS3`，且全部在开口配置时返回 `Input/output error`。  
- 额外事实：未见 `ttyUSB*`/`ttyACM*`，串口开口测试全部失败。  
- 结论：当前现场不应走“主控本地串口直控灵巧手”路径。  
- 影响：控制链路应切换为“机械臂控制器透传末端 Modbus”。

### 记录 C：经机械臂控制器透传灵巧手（主链路确认）

- 目标：验证真实可用链路。  
- 链路：上位机/Agent -> `192.168.10.18:8080` JSON -> 机械臂末端 `port=1` Modbus -> 灵巧手。  
- 实测命令与回包：  
  - `get_arm_power_state` -> `power_state=1`  
  - `get_current_arm_state` -> 正常返回 `arm_state`  
  - `set_modbus_mode(port=1, baudrate=115200, timeout=2)` -> `set_state=true`  
  - `write_registers(...开手...)` -> `write_state=true`  
  - `write_registers(...闭手...)` -> `write_state=true`  
- 结论：该路径已联机验证可稳定执行开/闭手。

### 记录 D：异常“异响+闭合保持”处置

- 现象：灵巧手间断异响，且保持闭合。  
- 处置：先 `set_modbus_mode`，再连续下发开手寄存器命令。  
- 回包：开手过程中出现过一次 `write_state=false`，其余为 `true`。  
- 现场反馈：最终“已开手，且无异响”。  
- 经验：  
  1. 异响通常与闭合保持/持续施力有关；  
  2. 出现 `false` 时应增加命令间隔并重发，而不是高频连发；  
  3. “开手作为默认安全态”可降低卡滞与噪声风险。

### 记录 E：主控上 RealSense D435 类设备连通性（2026-03-28）

- 目标：在跳板主控上探测 Intel RealSense（D435/D435i 等）是否被 USB 与 SDK 识别。  
- 环境：`jump_host_ip` → `192.168.0.115`，SSH `rm/rm`，`aarch64` Tegra。  
- 操作：`lsusb`、`rs-enumerate-devices`、`python3 pyrealsense2` 枚举、`/dev/video*`。  
- 结果：  
  - `lsusb` 未见典型 RealSense 的 **8086:0b3a / 0b07** 等 UVC 设备；仅见 **8087:0a2b**（常见为 **Intel 无线/蓝牙**，非深度相机）。  
  - `rs-enumerate-devices`：**No device detected**。  
  - `pyrealsense2`：`devices 0`。  
  - 未见 `/dev/video*` 节点（可能无视频类设备或未生成设备节点）。  
- 结论：**当前该主控上未检测到已连接的 D435 类 RealSense**；需在硬件侧确认 **USB 线材、USB3 口、独立供电、相机是否接在本板** 或是否接在其它计算机。  
- 后续：相机识别后，再验证 `udev`、用户权限、`realsense-viewer` 或最小抓流脚本。  
- **复核（2026-03-28）**：自开发机 SSH `rm@192.168.0.115` 再次执行 `lsusb`、`/dev/video*`、`rs-enumerate-devices -c`：仍 **无 RealSense USB PID**，**无 video 节点**，SDK 仍报 **No device detected**（与上条结论一致）。

## 版本记录

- `1.0.0`：首版，建立 SSH 跳转、机械臂/灵巧手最小验证、底盘/升降台探活模板。  
- `1.1.0`：新增部署意图（OpenClaw 独立控制系统）、常见故障与恢复流程（超时、409占用、回包 false、轨迹不动）、维护约定。  
- `1.2.0`：新增“发布与同步策略（强约束）”：本仓库中的相对路径目录为唯一源，部署端仅手动复制发布副本，彻底脱钩自动联动。  
- `1.2.1`：将 SSH 跳板主控 IP 外置到 `runtime_config.json`（`jump_host_ip`），文档中的 SSH 与回报示例改为配置读取，便于现场随时变更。  
- `1.2.2`：记录用户确认的灵巧手型号为 `ROH-LiteS001`，作为当前系统默认型号标识。  
- `1.3.0`：补充 2026-03-23 关键实验记录（SDK可用性、主控串口否定结论、机械臂透传主链路确认、异响恢复案例与经验）。  
- `1.3.1`：跳板主控 `jump_host_ip` 更新为 `192.168.0.115`；补充主控 RealSense 探测记录（当前未识别到 D435 设备）。  
- `1.3.2`：复核 `192.168.0.115` 上 RealSense 连通性（记录 E 追加复核结论，仍无设备枚举）。  

