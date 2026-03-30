# Realman-RMC-LA

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**GitHub**：<https://github.com/HDU-EILab/Realman-RMC-LA>

杭州电子科技大学 **HDU-EILab** 实验室 **Realman 复合机器人（机械臂 + 灵巧手 + 升降 + 云迹底盘）** 相关调试代码、接口说明与 OpenClaw Skill。**仓库意图**：便于组织内成员查看、修改与分享，支持课题交接。

> 仓库内附带 **`lift_platform_manual_V1.3.pdf`**（升降平台手册）、**睿尔曼 6 自由度机械臂 JSON 通信协议 v3.5.3.pdf**（TCP 指令细节）；更系统的表格与 ROS 说明仍以 **[INTERFACE_REFERENCE.md](./INTERFACE_REFERENCE.md)** 为准。

---

## 仓库范围说明

| 包含 |
|------|
| 机械臂 JSON 调试 GUI（直连 / SSH 跳板）|
| 底盘 ROS 侧示例/驱动片段 `remote_agv_driver.py`|
| 接口文档、`realman-rmc-la` OpenClaw Skill|

---

## 文档导航（请先读）

| 文档 | 内容 |
|------|------|
| **[INTERFACE_REFERENCE.md](./INTERFACE_REFERENCE.md)** | **主接口文档**：网络端点、TCP/JSON 全表、Modbus、ROS 话题、示教器操作概要、本仓库各脚本说明、安全提示 |
| [SYSTEM_CONTROL_REFERENCE.md](./SYSTEM_CONTROL_REFERENCE.md) | 与上表核心一致的速查版（界面 ToolTip 仍引用） |
| [realman-rmc-la/skill.md](./realman-rmc-la/skill.md) | OpenClaw 技能说明与触发条件 |
| [命令.md](./命令.md) | 现场常用命令速记（如 `roslaunch`、conda 环境名） |

---

## 必要环境搭建

### Windows / macOS / Linux（运行调试 GUI）

1. **Python**：建议 **3.10+**（64 位）。  
2. **创建虚拟环境（推荐）**：

```bash
cd Realman
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate
pip install -r requirements.txt
```

3. **依赖说明**  
   - **`paramiko`**：SSH 跳板版 GUI、以及「底盘」分页通过 SSH 在主控执行 ROS 相关脚本时 **必需**。  
   - **`opencv-python`、`mediapipe`**：仅在使用 **手势控制灵巧手** 时需要；不用手势可暂不安装（这个可以不用考虑）。  
   - **`tkinter`**：通常随 Python 官方安装包提供；Linux 若缺失请安装系统包（如 `python3-tk`）。

4. **运行**  

```bash
# 本机 TCP 直连机械臂控制器 8080（需路由可达）
python hand_debug_gui.py

# 经 SSH 跳板访问内网 192.168.10.18:8080
python hand_debug_gui_ssh_jump.py
```

### 主控（Ubuntu + ROS1，运行底盘/驱动）

- Nomachine连接Jetson主控板，IP应该固定为.0.115，如果有变可以登录路由器网关（密码同wifi密码）查看IP
- 底盘：需要提前运行`roslaunch agv_ros agv_start.launch`（详见 `INTERFACE_REFERENCE.md` §3）。  
- `remote_agv_driver.py` 需放入 catkin 包内按 ROS 节点方式编译运行，依赖 `agv_ros` 等消息包（这块我没去搞，不清楚具体是啥情况）。

---

## 示教器操作（概要）

详细步骤与 JSON 单位对照见 **[INTERFACE_REFERENCE.md §6](./INTERFACE_REFERENCE.md#6-web-示教器操作概要睿尔曼-rm-系列)**。要点：

1. 如果要访问realmna自己的示教器，需要网线直连，修改网段到10，机械臂IP固定为.10.18，底盘IP固定为.10.18:9001。
2. **底盘AGV**分页下急停以**软急停触发**和**软急停解除**为准，软急停触发时可以任意移动底盘。
3. 大部分按钮功能已经弃用，尤其是**灵巧手手势控制**板块，该板块是我之前做着玩的，效果不太行，所以我没有上传相应代码。 
4. **底盘AGV**中**导航到标记**按钮对应的标记点名需要在底盘web示教器中查看和添加，可以nomachine连接主控板后再登录web示教器查看。
5. **升降平台**接口可能弃用，这块控制属于所谓*扩展*，我没有找到相应控制接口，需要访问机械臂示教器查看（jetson板端无法打开web示教器是正常情况）。
6. 升降平台目前是零位，如果需要下降，需要在web示教器端先**设零位**，然后可以下降10mm（这个设定死了，我改不了）。

---

## 控制系统的几类接口（索引）

| 接口类型 | 典型用途 | 文档位置 |
|----------|----------|----------|
| 机械臂 TCP/JSON | 状态、movej/movel、轨迹、电源 | [INTERFACE_REFERENCE.md §2](./INTERFACE_REFERENCE.md#2-机械臂控制器-tcpjson) |
| 末端 Modbus（经 8080 转发） | 灵巧手 `set_modbus_mode` + `write_registers` | [INTERFACE_REFERENCE.md §2.6](./INTERFACE_REFERENCE.md#26-灵巧手modbus-写寄存器) |
| 升降 Modbus（经 8080 转发） | 另一 RS485 口参数 + `write_registers` | [INTERFACE_REFERENCE.md §2.7](./INTERFACE_REFERENCE.md#27-升降机经控制器-modbus-转发) |
| 云迹底盘 ROS1 | Joy、导航、取消、状态查询 | [INTERFACE_REFERENCE.md §3](./INTERFACE_REFERENCE.md#3-底盘云迹-agvros1) |
| 机械臂 ROS 升降话题 | `Lift_SetHeight` 等 | [INTERFACE_REFERENCE.md §4](./INTERFACE_REFERENCE.md#4-机械臂-ros-驱动中的升降相关话题节选) |
| 本仓库 Python 工具 | GUI、跳板、ROS 示例节点 | [INTERFACE_REFERENCE.md §7](./INTERFACE_REFERENCE.md#7-本仓库-python-工具说明) |

---

## 目录结构

```
Realman/
├── README.md                      # 本文件
├── LICENSE                        # MIT
├── requirements.txt
├── INTERFACE_REFERENCE.md         # 对外主接口文档
├── SYSTEM_CONTROL_REFERENCE.md    # 速查
├── 命令.md
├── hand_debug_gui.py              # 本机 TCP 调试 GUI
├── hand_debug_gui_ssh_jump.py     # SSH 跳板版 GUI + 机械臂分页/序列录制
├── remote_agv_driver.py           # 主控 ROS 侧底盘相关示例（需在 Linux+ROS 使用）
└── realman-rmc-la/                # OpenClaw Skill（skill.md / skill.json / runtime_config.json）
```

---

## 克隆与协作

```bash
git clone https://github.com/HDU-EILab/Realman-RMC-LA.git
cd Realman-RMC-LA
```

**协作建议**

- 为 `main` 开启 **分支保护**，通过 **Pull Request** 合并。  
- Issue / Discussion 中勿贴密码与内网拓扑细节。  

---

## 许可证

[MIT License](LICENSE) — 允许组织内自由使用与修改
