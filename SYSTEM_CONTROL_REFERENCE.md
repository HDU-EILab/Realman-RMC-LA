# Realman 复合系统控制指令参考

> **交接/对外说明**：更完整的示教器操作、环境搭建与工具说明见 **[INTERFACE_REFERENCE.md](./INTERFACE_REFERENCE.md)**（推荐新成员从此读起）。  
> **文档用途**：汇总现场常用的机械臂 TCP/JSON、末端 Modbus、升降 Modbus、云迹底盘 ROS1、以及机械臂 ROS 驱动中与升降相关的接口。  
> **配套工具**：`Realman/hand_debug_gui.py`（本机调试 GUI，已集成部分接口）。  
> **修订**：与 `realman-rmc-la/skill.md`、主控 `~/catkin_ws` 内 README 及话题表保持一致性维护。

---

## 1. 网络与端点

| 角色 | 典型地址 | 协议 | 说明 |
|------|----------|------|------|
| 机械臂控制器 | `192.168.10.18:8080` | TCP + JSON，行尾 `\r\n` | 灵巧手/升降经 `set_modbus_mode`、`write_registers` 转发 RS485 |
| 主控（跳板、ROS） | `192.168.0.115` | SSH + ROS1 | 用户常为 `rm`；工作空间默认 `~/catkin_ws` |
| 底盘驱动栈 | 主控上 ROS | `agv_ros` / `agv_driver` | 需先 `roslaunch agv_ros agv_start.launch` |

---

## 2. 机械臂控制器 TCP/JSON

### 2.1 行协议

- 每帧一条 JSON 对象，**必须以 `\r\n` 结束**。  
- 读响应时按行解析 JSON；控制器可能一次返回多行。

### 2.2 单位与反馈（常见约定）

- 关节角指令中常用 **`0.001°`** 整数。  
- 位姿常用 **`0.001 mm` / `0.001 rad`** 等缩放（以控制器回包与厂商文档为准）。  
- 设置类回包常含 `command` 与 `..._state: true/false`；`write_registers` 常见 `write_state`。

### 2.3 状态与诊断

| 用途 | `command` | 主要参数 | 说明 |
|------|-----------|----------|------|
| 读关节角 | `get_joint_degree` | 无 | 回包 `state=joint_degree` |
| 读整机状态 | `get_current_arm_state` | 无 | 回包 `state=current_arm_state` |
| 清系统错误 | `clear_system_err` | 无 | 成功常见 `clear_state=true` |
| 上电/断电 | `set_arm_power` | `arm_power`: `1`/`0` | |
| 读电源状态 | `get_arm_power_state` | 无 | |

**示例**

```json
{"command":"get_current_arm_state"}
```

```json
{"command":"get_joint_degree"}
```

```json
{"command":"clear_system_err"}
```

### 2.4 运动（节选）

| 类型 | `command` | 必要字段 |
|------|-----------|----------|
| 关节运动 | `movej` | `joint`（6）、`v`、`r` |
| 直线运动 | `movel` | `pose`（6）、`v`、`r` |
| 圆弧运动 | `movec` | `pose_via`、`pose_to`、`v`、`r`、`loop` |
| 关节步进 | `set_joint_step` | `joint_step`、`v` |
| 位置步进 | `set_pos_step` | `step_type`、`step`、`v` |
| 姿态步进 | `set_ort_step` | `step_type`、`step`、`v` |

轨迹完成可参考回包中的轨迹状态字段（如 `trajectory_state`）。

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

> 无原生「前进 1 cm」指令；短时速度脉冲为开环近似，需实车标定。

### 3.3 导航与点位

| 话题 | 类型 | 说明 |
|------|------|------|
| `/navigation_marker` | `std_msgs/String` | 去已建图标记点，如 `dianA` |
| `/navigation_location` | `agv_ros/NavigationLocation` | 地图坐标 `(x, y, theta)` |
| `/navigation_multipoint` | `std_msgs/String` | 多点，逗号分隔，至少 2 点，如 `m1,m2,m3` |
| `navigation_move_cancel` | `std_msgs/String` | 取消当前移动规划 |

### 3.4 限速与参数

| 话题 | 类型 | 说明 |
|------|------|------|
| `/navigation_max_speed` | `std_msgs/Float64` | 最大行进速度比例，约 `[0.3, 0.7]` |
| `navigation_max_speed_ratio` | `std_msgs/Float64` | 速度比，约 `[0.3, 1.4]` |
| `/navigation_max_speed_linear` | `std_msgs/Float64` | 最大线速度 m/s，约 `[0.1, 1.0]` |
| `/navigation_max_speed_angular` | `std_msgs/Float64` | 最大角速度 rad/s，约 `[0.5, 3.5]` |
| `/navigation_get_params` | `std_msgs/String` | 查询参数（负载常为 `""`） |

### 3.5 状态、电量、灯带

| 话题 | 类型 | 说明 |
|------|------|------|
| `/navigation_get_robot_status` | `std_msgs/String` | 查询底盘全局状态（负载常 `""`） |
| `/navigation_get_power_status` | `std_msgs/String` | 电量 |
| `/navigation_position_adjust_marker` | `std_msgs/String` | 用标记点矫正位姿 |
| `/navigation_led_set_color` | `agv_ros/NavigationLedSetColor` | RGB 各 **0~100** |

### 3.6 反馈

| 话题 | 类型 | 说明 |
|------|------|------|
| `/navigation_feedback` | `std_msgs/String` | API 执行结果文本 |

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
| `~/catkin_ws/src/开发文档/底盘户用使用手册V1.2.pdf` | 底盘用户手册（文件名以磁盘为准） |
| `~/catkin_ws/src/开发文档/WATER（水滴）软件API手册V1.1.pdf` | 水滴软件 API |
| `~/RM_API2/Demo/RMDemo_Python/RMDemo_Lift/readme.md` | 升降 Python 示例（API2） |

---

## 6. `hand_debug_gui.py` 功能对照

界面采用 **顶部常驻「连接配置」+ 中间 `ttk.Notebook` 分页 + 底部日志**，避免单页过高超出屏幕。

| 分页 / 区域 | 已实现 |
|-------------|--------|
| 连接区（常驻） | TCP 连接机械臂；读机械臂状态；**读关节角**、**清系统错误**、急停确认 |
| 分页「灵巧手」 | `set_modbus_mode`；开/闭手；分手指；写前自动 `set_modbus_mode`；渐进收指；手势（本机相机）；录制回放 |
| 分页「升降平台」 | 与《复合升降机器人平台》手册 V1.3 表述一致：竖直导轨由臂控驱动、JSON@8080；独立 RS485 口参数；`write_registers` 升/停/降与自定义 data |
| 分页「高级 JSON」 | 任意 JSON 文本发送 |
| 分页「底盘 AGV」 | **SSH 登录主控**，在 `catkin_ws` 下短脚本：joy 脉冲/停车、取消移动、查询状态/电量、标记点导航 |
| 日志 | 主窗口底部 + 独立日志窗口 |

**依赖**：底盘远程调试需本机安装 **`paramiko`**（`pip install paramiko`）。若使用空密码，将尝试 SSH 密钥/代理。

---

## 7. 安全提示

- 底盘与机械臂运动前确认空间与人机安全。  
- `set_arm_stop` 等与急停相关指令不可恢复，慎用。  
- 勿在仓库或日志中保存 SSH 密码；GUI 密码框仅会话内使用。
