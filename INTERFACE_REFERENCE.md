# Realman 复合系统 — 接口与工具参考（交接版）

> **用途**：汇总机械臂 TCP/JSON、末端 Modbus、升降 Modbus、云迹底盘 ROS1、机械臂 ROS 升降话题，以及本仓库调试工具的行为说明。  
> **配套**：`hand_debug_gui.py`（本机 TCP）、`hand_debug_gui_ssh_jump.py`（SSH 跳板）、`remote_agv_driver.py`（主控 ROS 节点示例）。  
> **精简对照**：根目录 `SYSTEM_CONTROL_REFERENCE.md` 与本文核心表一致；**以本文为对外交接主文档**。

---

## 1. 网络与端点

| 角色 | 典型地址 | 协议 | 说明 |
|------|----------|------|------|
| 机械臂控制器 | `192.168.10.18:8080` | TCP + JSON，行尾 `\r\n` | 灵巧手/升降经 `set_modbus_mode`、`write_registers` 转发 RS485 |
| 主控（跳板、ROS） | `192.168.0.115`（现场以实际为准） | SSH + ROS1 | 用户常为 `rm`；工作空间默认 `~/catkin_ws` |
| 底盘驱动栈 | 主控上 ROS | `agv_ros` / `agv_driver` | 需先 `roslaunch agv_ros agv_start.launch` |
| 水滴底盘 API（部分现场） | 如 `192.168.10.10:31001` | TCP | 与 `agv_driver` 内 `chassis_host/chassis_port` 一致；软急停等 |

---

## 2. 机械臂控制器 TCP/JSON

### 2.1 行协议

- 每帧一条 JSON 对象，**必须以 `\r\n` 结束**。  
- 读响应时按行解析 JSON；控制器可能一次返回多行。

### 2.2 单位与反馈（常见约定）

- 关节角指令/回包中常用 **`0.001°`** 整数（**示教器界面常以度(°)显示**，关系：**协议整数 ÷ 1000 ≈ 度**）。  
- 位姿常用 **`0.001 mm` / `0.001 rad`** 等缩放（以控制器回包与厂商文档为准）。  
- 设置类回包常含 `command` 与 `..._state: true/false`；`write_registers` 常见 `write_state`。  
- **`get_joint_degree` 与 `get_current_arm_state` 内 `arm_state.joint` 可能相差数个计数（约 0.001° 级）**：采样时刻或内部状态源不同，属常见现象。

### 2.3 状态与诊断

| 用途 | `command` | 主要参数 | 说明 |
|------|-----------|----------|------|
| 读关节角 | `get_joint_degree` | 无 | 回包 `state=joint_degree` |
| 读整机状态 | `get_current_arm_state` | 无 | 回包 `state=current_arm_state` |
| 清系统错误 | `clear_system_err` | 无 | 成功常见 `clear_state=true` |
| 上电/断电 | `set_arm_power` | `arm_power`: `1`/`0` | |
| 读电源状态 | `get_arm_power_state` | 无 | |

### 2.4 运动（节选）

| 类型 | `command` | 必要字段 |
|------|-----------|----------|
| 关节运动 | `movej` | `joint`（6）、`v`、`r` |
| 直线运动 | `movel` | `pose`（6）、`v`、`r` |
| 圆弧运动 | `movec` | `pose_via`、`pose_to`、`v`、`r`、`loop` |
| 关节步进 | `set_joint_step` | `joint_step`、`v` |
| 位置步进 | `set_pos_step` | `step_type`、`step`、`v` |
| 姿态步进 | `set_ort_step` | `step_type`、`step`、`v` |

### 2.5 轨迹控制

| 用途 | `command` |
|------|-----------|
| 急停 | `set_arm_stop` |
| 暂停 | `set_arm_pause` |
| 继续 | `set_arm_continue` |
| 删当前轨迹 | `set_delete_current_trajectory` |
| 删全部轨迹 | `set_arm_delete_trajectory` |
| 查当前轨迹类型 | `get_arm_current_trajectory` |

### 2.6 灵巧手（Modbus 写寄存器）

1. **配置串口模式**

```json
{"command":"set_modbus_mode","port":1,"baudrate":115200,"timeout":2}
```

- `port`：末端 RS485 口（现场多为 `1`）。  
- `timeout`：多为 **百毫秒** 量级（与示教器一致）。

2. **写寄存器（6×uint16 → 12 字节）**

```json
{"command":"write_registers","port":1,"address":1135,"num":6,"data":[...12字节...],"device":2}
```

- 现场常用 **`address=1135`**, **`num=6`**, **`device=2`**（以集成商配置为准）。  
- 每通道 **低字节** 为关节量 **0~255**，高字节常为 `0`。  
- **开手（示例）**：`data` 末尾为 `...,255,0`（拇指根与其它指极性按现场定义）。  
- **闭手（示例）**：前 10 字节为 `255`，末两字节 `0,0`。

### 2.7 升降机（经控制器 Modbus 转发）

与灵巧手相同命令字：`set_modbus_mode` + `write_registers`。  
**`port` / `baudrate` / `device` / `address` / `num` / `data`** 须按 **升降机 Modbus 说明书** 与 **实际接线口** 填写（常与灵巧手不同口，例如 `port=2`、`9600`）。

---

## 3. 底盘（云迹 AGV，ROS1）

### 3.1 启动

```bash
cd ~/catkin_ws
source devel/setup.bash
roslaunch agv_ros agv_start.launch
```

### 3.2 速度摇杆式控制

- **话题**：`/navigation_joy_control`  
- **类型**：`agv_ros/NavigationJoyControl`  
- **字段**：  
  - `linear_velocity`（float32）：**`-0.5 ~ 0.5` m/s**（正为前进）  
  - `angular_velocity`（float32）：**`-1.0 ~ 1.0` rad/s**（偏航）  

### 3.3 导航与点位

| 话题 | 类型 | 说明 |
|------|------|------|
| `/navigation_marker` | `std_msgs/String` | 去已建图标记点，如 `dianA` |
| `/navigation_location` | `agv_ros/NavigationLocation` | 地图坐标 `(x, y, theta)` |
| `/navigation_multipoint` | `std_msgs/String` | 多点，逗号分隔，至少 2 点 |
| `navigation_move_cancel` | `std_msgs/String` | 取消当前移动规划 |

### 3.4 限速与参数

| 话题 | 类型 | 说明 |
|------|------|------|
| `/navigation_max_speed` | `std_msgs/Float64` | 最大行进速度比例，约 `[0.3, 0.7]` |
| `navigation_max_speed_ratio` | `std_msgs/Float64` | 速度比，约 `[0.3, 1.4]` |
| `/navigation_max_speed_linear` | `std_msgs/Float64` | 最大线速度 m/s |
| `/navigation_max_speed_angular` | `std_msgs/Float64` | 最大角速度 rad/s |
| `/navigation_get_params` | `std_msgs/String` | 查询参数（负载常为 `""`） |

### 3.5 状态、电量、灯带

| 话题 | 类型 | 说明 |
|------|------|------|
| `/navigation_get_robot_status` | `std_msgs/String` | 查询底盘全局状态 |
| `/navigation_get_power_status` | `std_msgs/String` | 电量 |
| `/navigation_position_adjust_marker` | `std_msgs/String` | 用标记点矫正位姿 |
| `/navigation_led_set_color` | `agv_ros/NavigationLedSetColor` | RGB 各 **0~100** |

### 3.6 反馈

| 话题 | 类型 | 说明 |
|------|------|------|
| `/navigation_feedback` | `std_msgs/String` | API 执行结果文本 |

### 3.7 导航与手动速度互锁（现场常见）

云迹栈上 **导航任务** 与 **`/navigation_joy_control` 非零速度** 易互锁。新运动指令前常需：`navigation_move_cancel` + 连续若干帧 Joy **零速**；本仓库 GUI 与 `remote_agv_driver` 中已按此模式做预处理（以代码为准）。

---

## 4. 机械臂 ROS 驱动中的升降相关话题（节选）

> 需在运行 `rm_driver` 等节点时使用；消息体见 `dual_arm_msgs` 定义。

| 方向 | 话题 | 类型（表内描述） | 功能 |
|------|------|------------------|------|
| 订阅 | `/rm_driver/Lift_SetHeight` | `dual_arm_msgs/Lift_Height` | 升降位置闭环 |
| 订阅 | `/rm_driver/Lift_SetSpeed` | `dual_arm_msgs/Lift_Speed` | 升降速度开环 |
| 订阅 | `/rm_driver/Lift_GetState` | `std_msgs/Empty` | 查询升降状态 |
| 发布 | `/rm_driver/LiftState` | `dual_arm_msgs/LiftState` | 状态反馈 |

完整话题/服务表见主控：`~/catkin_ws/src/单臂复合升降机器人ROS1话题服务列表.md`。

---

## 5. 主控常见离线文档路径（参考）

| 路径 | 内容 |
|------|------|
| `~/catkin_ws/src/README_CN.md` | 复合升降 + 底盘 + 相机总说明 |
| `~/catkin_ws/src/agv_driver/README_agv.md` | 底盘包说明 |
| `~/catkin_ws/src/rm_lifter_robot_demo/README_demo.md` | 整体 demo、升降测试脚本说明 |
| `~/catkin_ws/src/开发文档/` | 底盘用户手册、水滴 API 手册等 PDF |

---

## 6. WEB 示教器操作概要（睿尔曼 RM 系列）

以下与常见 **WEB 示教器**（浏览器访问控制器）一致；**具体 IP、端口、账号以现场交付为准**。

1. **网络**  
   - 将调试电脑接入与机械臂控制器 **同一局域网网段**（或通过 VPN / SSH 隧道由主控转发，视现场拓扑而定）。  
   - 在浏览器地址栏输入示教器地址（例如 `http://<控制器IP>` 或文档指定端口）。

2. **登录**  
   - 使用实验室或厂商提供的用户名、密码登录。

3. **示教分页**  
   - 顶部常见 Tab：**示教**、**位姿编辑**、**拖动示教** 等。  
   - **示教 → 关节**：列出 **关节 1(°)～关节 6(°)**，数值为 **浮点角度（度）**。  
   - 使用 **`+` / `-`** 可对单关节微调；部分界面支持拖动或数值直接输入。

4. **与 JSON / 本仓库 GUI 的对应关系**  
   - 示教器显示 **度(°)**；TCP 回包 `joint` 多为 **×1000 的整数**（0.001°）。  
   - 例：示教器 **270.012°** ↔ 协议约 **270012**。  
   - 下发 `movej` 时 `joint` 数组应使用 **与控制器约定一致的整数单位**（通常为 0.001°）。

5. **安全**  
   - 运动前确认人员与障碍物；复合机器人注意 **底盘运动与机械臂工作空间** 干涉。  
   - 急停、暂停可在示教器操作，亦可通过 JSON：`set_arm_stop`、`set_arm_pause` 等（见 §2.5）。

6. **厂商手册**  
   - 更详细的示教流程、权限与安全联锁以 **《WEB 示教器用户手册》** 及现场版本为准。

---

## 7. 本仓库 Python 工具说明

### 7.1 `hand_debug_gui.py`（本机直连 `8080`）

- **用途**：TCP 直连机械臂控制器 JSON 端口；灵巧手 Modbus、升降、高级 JSON、底盘（经 SSH 在主控发 ROS 话题）。  
- **依赖**：`pip install -r requirements.txt`（`paramiko` 用于 SSH 分页；手势功能需 `opencv-python`、`mediapipe`）。  
- **运行**：`python hand_debug_gui.py`  
- **界面**：顶部连接区 + Notebook 分页（灵巧手 / 升降 / 底盘 / 高级 JSON 等）；日志区可开独立窗口。  
- **说明**：界面内 ToolTip 含各按钮对应 `command` 与关键参数；指令总表见本文 §2–§3。

### 7.2 `hand_debug_gui_ssh_jump.py`（SSH 跳板 → 内网 `192.168.10.18:8080`）

- **用途**：本机无法直连 `192.168.10.18` 时，先 SSH 登录主控（跳板），用 Paramiko **`direct-tcpip`** 通道转发到固定 **`ARM_JSON_VIA_JUMP_HOST` / `ARM_JSON_VIA_JUMP_PORT`**（源码内常量，默认 `192.168.10.18:8080`）。  
- **依赖**：同 `requirements.txt`，**必须** `paramiko`。  
- **运行**：`python hand_debug_gui_ssh_jump.py`  
- **额外分页**：**机械臂 TCP**（电源、轨迹、movej/movel、与整机状态读取等，且可 **录制/回放/保存 JSON 序列**）。  
- **修改跳板目标**：编辑文件顶部 `ARM_JSON_VIA_JUMP_HOST`、`ARM_JSON_VIA_JUMP_PORT`。

### 7.3 `remote_agv_driver.py`（主控 ROS 节点）

- **用途**：在主控 `catkin_ws` 内作为 ROS 节点运行，对接云迹导航话题，并通过 TCP 调用水滴 **`/api/estop`** 等（与现场 `agv_driver` 逻辑对齐；以文件内注释为准）。  
- **环境**：ROS1、`rospy`、`agv_ros` 消息包；需在主控 Linux 环境编译运行，**非 Windows 本机脚本**。  
- **部署**：拷贝至工作空间 `src` 包内，按标准 catkin 流程 `catkin_make` / `catkin build` 后 `rosrun`。

### 7.4 `realman-rmc-la/`（OpenClaw Skill）

- **用途**：OpenClaw Agent 用的技能描述与运行时配置（`skill.md`、`skill.json`、`runtime_config.json`）。  
- **说明**：与现场拓扑、`jump_host_ip` 等以 `runtime_config.json` 为准；详见该目录内 `skill.md`。

---

## 8. 安全与合规

- 底盘与机械臂运动前确认空间与人机安全。  
- `set_arm_stop` 等急停类指令后果严重，慎用。  
- **勿**在仓库、Issue、截图中提交 **SSH 密码、密钥、内网拓扑敏感信息**；GUI 密码框仅本会话使用。  
- 组织仓库建议启用 **分支保护** 与 **Code Review**，便于交接后协作。

---

## 9. 修订记录

- 与 `realman-rmc-la/skill.md`、主控 `~/catkin_ws` 内 README 及话题表保持一致维护。  
- 现场 IP、端口、寄存器地址以 **实际接线与集成商配置** 为准，本文仅为典型值。
