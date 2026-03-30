# Realman-RMC-LA

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

杭州电子科技大学 **HDU-EILab** 实验室 **Realman 复合机器人（机械臂 + 灵巧手 + 升降 + 云迹底盘）** 相关调试代码、接口说明与 OpenClaw Skill。**仓库意图**：便于组织内成员查看、修改与分享，支持课题交接。

---

## 仓库范围说明

| 包含 | 不包含（有意排除） |
|------|-------------------|
| 机械臂 JSON 调试 GUI（直连 / SSH 跳板） | `Vision2DexterousHand/`（视觉灵巧手子项目，独立维护） |
| 底盘 ROS 侧示例/驱动片段 `remote_agv_driver.py` | 以 `_` 开头的本地一次性调试脚本（见 `.gitignore`） |
| 接口文档、`realman-rmc-la` OpenClaw Skill | 主控 `catkin_ws` 内大型厂商 PDF（请在现场机器查阅） |

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
   - **`opencv-python`、`mediapipe`**：仅在使用 **手势控制灵巧手** 时需要；不用手势可暂不安装。  
   - **`tkinter`**：通常随 Python 官方安装包提供；Linux 若缺失请安装系统包（如 `python3-tk`）。

4. **运行**  

```bash
# 本机 TCP 直连机械臂控制器 8080（需路由可达）
python hand_debug_gui.py

# 经 SSH 跳板访问内网 192.168.10.18:8080
python hand_debug_gui_ssh_jump.py
```

### 主控（Ubuntu + ROS1，运行底盘/驱动）

- 安装 **ROS Noetic**（或现场实际版本）、配置 **`catkin_ws`**。  
- 底盘：`roslaunch agv_ros agv_start.launch`（详见 `INTERFACE_REFERENCE.md` §3）。  
- `remote_agv_driver.py` 需放入 catkin 包内按 ROS 节点方式编译运行，依赖 `agv_ros` 等消息包。

---

## WEB 示教器操作（概要）

详细步骤与 JSON 单位对照见 **[INTERFACE_REFERENCE.md §6](./INTERFACE_REFERENCE.md#6-web-示教器操作概要睿尔曼-rm-系列)**。要点：

1. 浏览器访问控制器 **WEB 示教**（IP/端口以现场为准）。  
2. **示教** 分页下 **关节 1～6** 一般以 **度(°)** 显示；**TCP JSON** 中 `joint` 多为 **0.001° 整数**（÷1000 与界面对齐）。  
3. 使用 **`+` / `-`** 微调关节；运动前确认空间安全。  
4. 急停/轨迹控制可在示教器完成，也可通过 JSON（`set_arm_stop`、`set_arm_pause` 等）完成。

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

## 推送到 GitHub（组织 HDU-EILab）

1. 在 GitHub 上由组织所有者创建仓库 **`HDU-EILab/Realman-RMC-LA`**（建议 **Public**，便于成员 clone；若敏感可 Private 并只加组织成员）。  
2. 本地首次提交（在 **`Realman` 目录** 下执行）：

```bash
cd Realman
git init
git add -A
git status   # 确认无 Vision2DexterousHand、无 _*.py
git commit -m "Initial commit: Realman RMC-LA tools and interface docs"
git branch -M main
git remote add origin https://github.com/HDU-EILab/Realman-RMC-LA.git
git push -u origin main
```

3. **协作建议**  
   - 为 `main` 开启 **分支保护**，通过 **Pull Request** 合并。  
   - Issue / Discussion 中勿贴密码与内网拓扑细节。  
   - 修改现场 IP、寄存器地址后，可在 PR 说明中记录变更原因。

4. 若已安装 [GitHub CLI](https://cli.github.com/) 且已登录：

```bash
cd Realman
gh repo create HDU-EILab/Realman-RMC-LA --public --source=. --remote=origin --push
```

（若组织策略要求私有仓库，将 `--public` 改为 `--private`。）

---

## 安全声明

本仓库工具会直接发送运动与 IO 指令，**仅限实验室授权网络与设备上使用**。使用人需遵守实验室安全规范；因误操作或配置错误导致的设备或人身损害，**责任由操作者自负**。

---

## 许可证

[MIT License](LICENSE) — 允许组织内自由使用与修改，但**不**免除安全操作责任。
