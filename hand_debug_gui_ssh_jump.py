import json
import shlex
import socket
import threading
import time
import tkinter as tk
from tkinter import filedialog
from tkinter import ttk, messagebox
from tkinter import scrolledtext
from datetime import datetime


class ToolTip:
    def __init__(self, widget, text: str):
        self.widget = widget
        self.text = text
        self.tip_window = None
        self.widget.bind("<Enter>", self._show)
        self.widget.bind("<Leave>", self._hide)

    def _show(self, _event=None):
        if self.tip_window is not None:
            return
        x = self.widget.winfo_rootx() + 16
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            tw,
            text=self.text,
            justify="left",
            bg="#ffffe0",
            relief="solid",
            borderwidth=1,
            font=("Segoe UI", 9),
            wraplength=420,
        )
        label.pack(ipadx=6, ipady=4)

    def _hide(self, _event=None):
        if self.tip_window is not None:
            self.tip_window.destroy()
            self.tip_window = None


class RealmanHandClient:
    def __init__(self, host: str, port: int, timeout: float = 1.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock = None
        self.lock = threading.Lock()

    def connect(self):
        with self.lock:
            if self.sock is not None:
                return
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3.0)
            s.connect((self.host, self.port))
            s.settimeout(self.timeout)
            self.sock = s

    def close(self):
        with self.lock:
            if self.sock is not None:
                try:
                    self.sock.close()
                finally:
                    self.sock = None

    def send_json(self, payload: dict, wait_ms: int = 250):
        line = (json.dumps(payload, ensure_ascii=False) + "\r\n").encode("utf-8")
        with self.lock:
            if self.sock is None:
                raise RuntimeError("Not connected")
            self.sock.sendall(line)
            self.sock.settimeout(wait_ms / 1000.0)
            buf = b""
            while True:
                try:
                    chunk = self.sock.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                except socket.timeout:
                    break

        msgs = []
        for row in buf.decode("utf-8", "ignore").splitlines():
            row = row.strip()
            if not row:
                continue
            try:
                msgs.append(json.loads(row))
            except Exception:
                msgs.append({"raw": row})
        return msgs


# 机械臂 JSON 始终在跳板机所连内网中的地址（本版写死，不经 GUI 修改）
ARM_JSON_VIA_JUMP_HOST = "192.168.10.18"
ARM_JSON_VIA_JUMP_PORT = 8080


class RealmanHandClientSSHJump:
    """经 SSH 跳板 `direct-tcpip` 转发，连接固定 ARM_JSON_VIA_JUMP_HOST:PORT。"""

    def __init__(self, jump_host: str, jump_user: str, jump_password: str, timeout: float = 1.0):
        self.jump_host = jump_host.strip()
        self.jump_user = (jump_user or "rm").strip()
        self.jump_password = jump_password
        self.timeout = timeout
        self._client = None
        self._transport = None
        self._chan = None
        self.lock = threading.Lock()

    def connect(self):
        import paramiko

        with self.lock:
            if self._chan is not None:
                return
            if not self.jump_host:
                raise ValueError("跳板 SSH 主机不能为空")
            cli = paramiko.SSHClient()
            cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            kw: dict = {
                "hostname": self.jump_host,
                "username": self.jump_user,
                "timeout": 25,
                "allow_agent": True,
                "look_for_keys": True,
            }
            pwd = self.jump_password or ""
            if pwd:
                kw["password"] = pwd
                kw["allow_agent"] = False
                kw["look_for_keys"] = False
            cli.connect(**kw)
            transport = cli.get_transport()
            if transport is None:
                cli.close()
                raise RuntimeError("SSH Transport 不可用")
            chan = transport.open_channel(
                "direct-tcpip",
                (ARM_JSON_VIA_JUMP_HOST, ARM_JSON_VIA_JUMP_PORT),
                ("127.0.0.1", 0),
            )
            self._client = cli
            self._transport = transport
            self._chan = chan
            self._chan.settimeout(3.0)

    def close(self):
        with self.lock:
            if self._chan is not None:
                try:
                    self._chan.close()
                except Exception:
                    pass
                self._chan = None
            if self._client is not None:
                try:
                    self._client.close()
                except Exception:
                    pass
                self._client = None
            self._transport = None

    def send_json(self, payload: dict, wait_ms: int = 250):
        line = (json.dumps(payload, ensure_ascii=False) + "\r\n").encode("utf-8")
        with self.lock:
            if self._chan is None:
                raise RuntimeError("Not connected")
            self._chan.sendall(line)
            self._chan.settimeout(max(0.05, wait_ms / 1000.0))
            buf = b""
            while True:
                try:
                    chunk = self._chan.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                except socket.timeout:
                    break

        msgs = []
        for row in buf.decode("utf-8", "ignore").splitlines():
            row = row.strip()
            if not row:
                continue
            try:
                msgs.append(json.loads(row))
            except Exception:
                msgs.append({"raw": row})
        return msgs


class HandDebugGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Realman 调试 (SSH跳板→192.168.10.18:8080)")
        self.root.geometry("940x720")
        self.root.minsize(820, 560)

        self.modbus_port_var = tk.IntVar(value=1)
        self.device_var = tk.IntVar(value=2)
        self.reg_addr_var = tk.IntVar(value=1135)
        self.reg_num_var = tk.IntVar(value=6)
        self.baud_var = tk.IntVar(value=115200)
        self.timeout_var = tk.IntVar(value=2)
        self.wait_ms_var = tk.IntVar(value=350)
        self.retry_on_false_var = tk.BooleanVar(value=True)
        self.retry_delay_ms_var = tk.IntVar(value=600)
        self.replay_interval_ms_var = tk.IntVar(value=900)
        self.replay_count_var = tk.IntVar(value=1)

        # 6-channel sliders for fine tuning.
        self.channel_vars = [tk.IntVar(value=0) for _ in range(6)]
        self.recording_enabled = False
        self.recorded_actions: list[dict] = []

        self.client = None
        self.log_popup = None
        self.log_popup_text = None
        self.gesture_running = False
        self.gesture_stop_event = threading.Event()
        self.gesture_thread = None
        self._last_gesture_send_ts = 0.0
        self.gesture_ema_alpha = 0.22
        # 关节/通道有效范围 0~255（与 ROH Modbus 载荷一致，uint16 低字节）
        self.roh_channel_max = 255
        self.gesture_deadband_u8 = 3
        self.gesture_send_min_delta_u8 = 5
        self._gesture_ema_channels = None
        self._gesture_last_sent_channels = None
        self._gesture_last_channels = None
        # ROH-LiteS001 / 现场载荷顺序（与 write_registers 6×uint16 一致）:
        # 通道1=拇指(主)；通道2~5=食/中/无名/小；通道6=拇指根部。
        # 开手预设: ch1~5≈0, ch6=255；闭手预设: ch1~5≈255, ch6=0（根部与其它指极性相反）。
        _thumb_default = int(round(6000 * self.roh_channel_max / 65535))
        self.roh_thumb_open_target_u8 = max(0, min(self.roh_channel_max, _thumb_default))
        self.roh_thumb_open_target_var = tk.IntVar(value=self.roh_thumb_open_target_u8)
        self._gesture_open_ref = None
        self._gesture_close_ref = None
        # 渐进收指握持（固定 ch1/ch6，四指递增至上限）
        self.ramp_stop_event = threading.Event()
        self.ramp_running = False
        self.ramp_ch1_fix_var = tk.IntVar(value=50)
        self.ramp_ch6_fix_var = tk.IntVar(value=255)
        self.ramp_step_var = tk.IntVar(value=5)
        self.ramp_interval_ms_var = tk.IntVar(value=180)
        # 通道2~5 各自停止阈值（达上限后该指不再增加，其余指继续）
        self.ramp_max_ch2_var = tk.IntVar(value=200)
        self.ramp_max_ch3_var = tk.IntVar(value=200)
        self.ramp_max_ch4_var = tk.IntVar(value=200)
        self.ramp_max_ch5_var = tk.IntVar(value=200)
        # 默认关闭：write_state=false 常因总线节奏/偶发忙，不等同“必须停握”
        self.ramp_stop_on_false_var = tk.BooleanVar(value=False)
        self.ramp_false_streak_limit = 6
        # 升降机：经机械臂 JSON 的 Modbus 转发（寄存器与 data 按现场升降机说明书填写）
        self.lift_use_separate_port_var = tk.BooleanVar(value=True)
        self.lift_modbus_port_var = tk.IntVar(value=2)
        self.lift_baud_var = tk.IntVar(value=9600)
        self.lift_timeout_var = tk.IntVar(value=2)
        self.lift_device_var = tk.IntVar(value=1)
        self.lift_address_var = tk.IntVar(value=0)
        self.lift_num_var = tk.IntVar(value=1)
        self.lift_cfg_before_send_var = tk.BooleanVar(value=True)
        # 与升降机区一致：很多现场若未先 set_modbus_mode，write_registers 会 write_state=false 或无效
        self.hand_cfg_before_write_var = tk.BooleanVar(value=True)
        self.lift_data_up_var = tk.StringVar(value="")
        self.lift_data_stop_var = tk.StringVar(value="")
        self.lift_data_down_var = tk.StringVar(value="")
        self.lift_custom_data_var = tk.StringVar(value="0,0")
        # 主控 SSH（底盘 ROS 等在主控上执行）
        self.ssh_host_var = tk.StringVar(value="192.168.0.115")
        self.ssh_user_var = tk.StringVar(value="rm")
        self.ssh_password_var = tk.StringVar(value="")
        self.ssh_catkin_var = tk.StringVar(value="/home/rm/catkin_ws")
        self.agv_linear_var = tk.StringVar(value="0.08")
        self.agv_angular_var = tk.StringVar(value="0.0")
        self.agv_pulse_sec_var = tk.StringVar(value="0.2")
        self.agv_marker_var = tk.StringVar(value="dianA")
        # 云迹栈上导航与 Joy 速度易互锁：新指令前先发 cancel + Joy 零速
        self.agv_auto_preunlock_var = tk.BooleanVar(value=True)
        # 水滴 TCP（与主控 agv_driver.py 内 chassis_host/chassis_port 一致，用于直连 /api/estop 等）
        self.agv_chassis_host_var = tk.StringVar(value="192.168.10.10")
        self.agv_chassis_port_var = tk.StringVar(value="31001")
        # 机械臂 TCP：movej/movel 参数（单位以控制器为准，常见关节为 0.001°）
        self.arm_movej_joint_var = tk.StringVar(value="0,0,0,0,0,0")
        self.arm_movej_v_var = tk.StringVar(value="20")
        self.arm_movej_r_var = tk.StringVar(value="0")
        self.arm_movel_pose_var = tk.StringVar(value="0,0,0,0,0,0")
        self.arm_movel_v_var = tk.StringVar(value="20")
        self.arm_movel_r_var = tk.StringVar(value="0")
        self._build_ui()

    def _build_tab_arm_tcp(self, parent: ttk.Frame):
        """机械臂 JSON 常用命令；开启录制时与灵巧手共用序列。"""
        top = ttk.LabelFrame(parent, text="状态与电源")
        top.pack(fill="x", padx=4, pady=6)
        btn_arm_s = ttk.Button(top, text="读整机状态", command=self.get_arm_state)
        btn_arm_s.grid(row=0, column=0, padx=6, pady=6)
        btn_arm_j = ttk.Button(top, text="读关节角", command=self.get_joint_degree)
        btn_arm_j.grid(row=0, column=1, padx=6, pady=6)
        btn_arm_ps = ttk.Button(top, text="读电源状态", command=self.arm_get_power_state)
        btn_arm_ps.grid(row=0, column=2, padx=6, pady=6)
        btn_arm_clr = ttk.Button(top, text="清系统错误", command=self.clear_system_error)
        btn_arm_clr.grid(row=0, column=3, padx=6, pady=6)
        btn_arm_on = ttk.Button(top, text="上电", command=lambda: self.arm_set_power(1))
        btn_arm_on.grid(row=0, column=4, padx=6, pady=6)
        btn_arm_off = ttk.Button(top, text="断电", command=lambda: self.arm_set_power(0))
        btn_arm_off.grid(row=0, column=5, padx=6, pady=6)
        ttk.Label(
            top,
            text="关节角常用 0.001°；位姿为 6 个数（缩放单位以控制器回包/手册为准）。",
            foreground="#666",
        ).grid(row=1, column=0, columnspan=6, padx=6, pady=4, sticky="w")

        traj = ttk.LabelFrame(parent, text="轨迹")
        traj.pack(fill="x", padx=4, pady=6)
        ttk.Button(traj, text="暂停轨迹", command=self.arm_traj_pause).grid(row=0, column=0, padx=6, pady=6)
        ttk.Button(traj, text="继续轨迹", command=self.arm_traj_continue).grid(row=0, column=1, padx=6, pady=6)
        ttk.Button(traj, text="删当前轨迹", command=self.arm_delete_current_trajectory).grid(
            row=0, column=2, padx=6, pady=6
        )
        ttk.Button(traj, text="删全部轨迹", command=self.arm_delete_all_trajectory).grid(
            row=0, column=3, padx=6, pady=6
        )
        ttk.Button(traj, text="查当前轨迹", command=self.arm_get_current_trajectory).grid(
            row=0, column=4, padx=6, pady=6
        )
        ttk.Button(traj, text="急停(确认)", command=self.arm_set_stop_confirm).grid(row=0, column=5, padx=6, pady=6)

        move = ttk.LabelFrame(parent, text="关节 / 直线运动")
        move.pack(fill="x", padx=4, pady=6)
        ttk.Label(move, text="movej joint(6)").grid(row=0, column=0, padx=6, pady=6, sticky="w")
        ttk.Entry(move, textvariable=self.arm_movej_joint_var, width=26).grid(row=0, column=1, padx=6, pady=6)
        ttk.Label(move, text="v").grid(row=0, column=2, padx=4, pady=6)
        ttk.Entry(move, textvariable=self.arm_movej_v_var, width=6).grid(row=0, column=3, padx=4, pady=6)
        ttk.Label(move, text="r").grid(row=0, column=4, padx=4, pady=6)
        ttk.Entry(move, textvariable=self.arm_movej_r_var, width=6).grid(row=0, column=5, padx=4, pady=6)
        btn_mj = ttk.Button(move, text="发送 movej", command=self.arm_send_movej)
        btn_mj.grid(row=0, column=6, padx=8, pady=6)

        ttk.Label(move, text="movel pose(6)").grid(row=1, column=0, padx=6, pady=6, sticky="w")
        ttk.Entry(move, textvariable=self.arm_movel_pose_var, width=26).grid(row=1, column=1, padx=6, pady=6)
        ttk.Label(move, text="v").grid(row=1, column=2, padx=4, pady=6)
        ttk.Entry(move, textvariable=self.arm_movel_v_var, width=6).grid(row=1, column=3, padx=4, pady=6)
        ttk.Label(move, text="r").grid(row=1, column=4, padx=4, pady=6)
        ttk.Entry(move, textvariable=self.arm_movel_r_var, width=6).grid(row=1, column=5, padx=4, pady=6)
        btn_ml = ttk.Button(move, text="发送 movel", command=self.arm_send_movel)
        btn_ml.grid(row=1, column=6, padx=8, pady=6)

        ToolTip(
            btn_arm_s,
            "TCP JSON: get_current_arm_state\n录制开启时会记入序列\n"
            "关节整数÷1000=度；与 get_joint_degree 可能差 0.001° 量级。",
        )
        ToolTip(
            btn_arm_j,
            "TCP JSON: get_joint_degree\n录制开启时会记入序列\n"
            "关节整数÷1000=度；与整机状态里的 joint 可能略有差异。",
        )
        ToolTip(btn_arm_ps, "TCP JSON: get_arm_power_state")
        ToolTip(btn_arm_clr, "TCP JSON: clear_system_err")
        ToolTip(btn_arm_on, "TCP JSON: set_arm_power, arm_power=1")
        ToolTip(btn_arm_off, "TCP JSON: set_arm_power, arm_power=0")
        ToolTip(btn_mj, "TCP JSON: movej, joint[6], v, r（关节常用 0.001°）")
        ToolTip(btn_ml, "TCP JSON: movel, pose[6], v, r")

    @staticmethod
    def _parse_csv_integers(s: str, n: int) -> list[int]:
        parts = [p.strip() for p in s.replace("，", ",").split(",") if p.strip() != ""]
        if len(parts) != n:
            raise ValueError(f"需要逗号分隔的 {n} 个数，当前 {len(parts)} 个")
        return [int(round(float(p))) for p in parts]

    def _build_tab_hand(self, parent: ttk.Frame):
        """分页：灵巧手相关控件。"""
        modbus = ttk.LabelFrame(parent, text="灵巧手通道（示教器常用）")
        modbus.pack(fill="x", padx=4, pady=6)

        ttk.Label(modbus, text="port").grid(row=0, column=0, padx=6, pady=6, sticky="w")
        ttk.Entry(modbus, textvariable=self.modbus_port_var, width=6).grid(row=0, column=1, padx=6, pady=6)
        ttk.Label(modbus, text="baudrate").grid(row=0, column=2, padx=6, pady=6, sticky="w")
        ttk.Entry(modbus, textvariable=self.baud_var, width=10).grid(row=0, column=3, padx=6, pady=6)
        ttk.Label(modbus, text="timeout(百ms)").grid(row=0, column=4, padx=6, pady=6, sticky="w")
        ttk.Entry(modbus, textvariable=self.timeout_var, width=8).grid(row=0, column=5, padx=6, pady=6)

        self.btn_enable = ttk.Button(modbus, text="使能灵巧手(配置Modbus)", command=self.enable_hand)
        self.btn_enable.grid(row=0, column=6, padx=6, pady=6)
        self.btn_disable = ttk.Button(modbus, text="失能灵巧手(安全释放)", command=self.disable_hand)
        self.btn_disable.grid(row=0, column=7, padx=6, pady=6)
        self.chk_hand_cfg_before_write = ttk.Checkbutton(
            modbus,
            text="write 前自动 set_modbus_mode",
            variable=self.hand_cfg_before_write_var,
        )
        self.chk_hand_cfg_before_write.grid(row=1, column=0, columnspan=4, padx=6, pady=4, sticky="w")

        quick = ttk.LabelFrame(parent, text="整体抓握控制")
        quick.pack(fill="x", padx=4, pady=6)

        ttk.Label(quick, text="device").grid(row=0, column=0, padx=6, pady=6, sticky="w")
        ttk.Entry(quick, textvariable=self.device_var, width=8).grid(row=0, column=1, padx=6, pady=6)
        ttk.Label(quick, text="address").grid(row=0, column=2, padx=6, pady=6, sticky="w")
        ttk.Entry(quick, textvariable=self.reg_addr_var, width=10).grid(row=0, column=3, padx=6, pady=6)
        ttk.Label(quick, text="num").grid(row=0, column=4, padx=6, pady=6, sticky="w")
        ttk.Entry(quick, textvariable=self.reg_num_var, width=8).grid(row=0, column=5, padx=6, pady=6)
        ttk.Label(quick, text="等待回包(ms)").grid(row=0, column=6, padx=6, pady=6, sticky="w")
        ttk.Entry(quick, textvariable=self.wait_ms_var, width=8).grid(row=0, column=7, padx=6, pady=6)
        ttk.Checkbutton(quick, text="false自动重试", variable=self.retry_on_false_var).grid(
            row=0, column=8, padx=6, pady=6
        )
        ttk.Label(quick, text="重试延时(ms)").grid(row=0, column=9, padx=6, pady=6, sticky="w")
        ttk.Entry(quick, textvariable=self.retry_delay_ms_var, width=8).grid(row=0, column=10, padx=6, pady=6)
        ttk.Label(quick, text="拇指开手目标").grid(row=0, column=11, padx=6, pady=6, sticky="w")
        ttk.Entry(quick, textvariable=self.roh_thumb_open_target_var, width=8).grid(row=0, column=12, padx=6, pady=6)
        self.btn_apply_thumb_target = ttk.Button(quick, text="应用", command=self.apply_thumb_open_target)
        self.btn_apply_thumb_target.grid(row=0, column=13, padx=6, pady=6)

        self.btn_open = ttk.Button(quick, text="开手(释放)", command=self.open_hand)
        self.btn_open.grid(row=1, column=8, padx=6, pady=6)
        self.btn_close = ttk.Button(quick, text="闭手(抓握)", command=self.close_hand)
        self.btn_close.grid(row=1, column=9, padx=6, pady=6)
        self.btn_gesture = ttk.Button(quick, text="开启手势控制", command=self.toggle_gesture_control)
        self.btn_gesture.grid(row=1, column=10, padx=6, pady=6)
        self.btn_gesture_open_cal = ttk.Button(quick, text="手势开手标定", command=self.capture_gesture_open_ref)
        self.btn_gesture_open_cal.grid(row=1, column=11, padx=6, pady=6)
        self.btn_gesture_close_cal = ttk.Button(quick, text="手势闭手标定", command=self.capture_gesture_close_ref)
        self.btn_gesture_close_cal.grid(row=1, column=12, padx=6, pady=6)
        self.btn_gesture_reset_cal = ttk.Button(quick, text="重置手势标定", command=self.reset_gesture_calibration)
        self.btn_gesture_reset_cal.grid(row=1, column=13, padx=6, pady=6)

        replay = ttk.LabelFrame(parent, text="动作序列录制 / 回放")
        replay.pack(fill="x", padx=4, pady=6)
        self.btn_rec_start = ttk.Button(replay, text="开始录制", command=self.start_recording)
        self.btn_rec_start.grid(row=0, column=0, padx=6, pady=6)
        self.btn_rec_stop = ttk.Button(replay, text="停止录制", command=self.stop_recording)
        self.btn_rec_stop.grid(row=0, column=1, padx=6, pady=6)
        self.btn_rec_clear = ttk.Button(replay, text="清空序列", command=self.clear_recording)
        self.btn_rec_clear.grid(row=0, column=2, padx=6, pady=6)
        self.btn_rec_replay = ttk.Button(replay, text="回放序列", command=self.replay_recording)
        self.btn_rec_replay.grid(row=0, column=3, padx=6, pady=6)
        self.btn_rec_save = ttk.Button(replay, text="保存JSON", command=self.save_recorded_sequence_json)
        self.btn_rec_save.grid(row=0, column=8, padx=6, pady=6)
        self.btn_rec_load = ttk.Button(replay, text="读取JSON", command=self.load_recorded_sequence_json)
        self.btn_rec_load.grid(row=0, column=9, padx=6, pady=6)
        ttk.Label(replay, text="步间隔(ms)").grid(row=0, column=4, padx=6, pady=6, sticky="w")
        ttk.Entry(replay, textvariable=self.replay_interval_ms_var, width=8).grid(row=0, column=5, padx=6, pady=6)
        ttk.Label(replay, text="回放次数").grid(row=0, column=6, padx=6, pady=6, sticky="w")
        ttk.Entry(replay, textvariable=self.replay_count_var, width=6).grid(row=0, column=7, padx=6, pady=6)
        ttk.Label(
            replay,
            text="录制开启时：灵巧手与「机械臂TCP」分页指令会写入序列；可保存/加载 JSON 备份。",
            foreground="#555",
        ).grid(row=1, column=0, columnspan=12, padx=6, pady=4, sticky="w")

        fingers = ttk.LabelFrame(parent, text="分手指控制（6通道，0~255）")
        fingers.pack(fill="x", padx=4, pady=6)

        ch_labels = ("1·拇指", "2·食指", "3·中指", "4·无名指", "5·小指", "6·拇指根部")
        for i in range(6):
            row = i // 2
            col_offset = (i % 2) * 4
            ttk.Label(fingers, text=f"通道{ch_labels[i]}").grid(row=row, column=col_offset, padx=6, pady=6, sticky="w")
            scale = ttk.Scale(
                fingers,
                from_=0,
                to=255,
                orient="horizontal",
                variable=self.channel_vars[i],
                length=220,
            )
            scale.grid(row=row, column=col_offset + 1, padx=6, pady=6)
            entry = ttk.Entry(fingers, textvariable=self.channel_vars[i], width=10)
            entry.grid(row=row, column=col_offset + 2, padx=6, pady=6)

        self.btn_finger_send = ttk.Button(fingers, text="发送分手指位置", command=self.send_finger_channels)
        self.btn_finger_send.grid(row=3, column=0, columnspan=8, padx=6, pady=8, sticky="we")

        rampf = ttk.LabelFrame(parent, text="渐进收指握持（防过握）")
        rampf.pack(fill="x", padx=4, pady=6)
        ttk.Label(rampf, text="固定ch1(拇指)").grid(row=0, column=0, padx=6, pady=4, sticky="w")
        ttk.Entry(rampf, textvariable=self.ramp_ch1_fix_var, width=6).grid(row=0, column=1, padx=4, pady=4)
        ttk.Label(rampf, text="固定ch6(拇指根)").grid(row=0, column=2, padx=6, pady=4, sticky="w")
        ttk.Entry(rampf, textvariable=self.ramp_ch6_fix_var, width=6).grid(row=0, column=3, padx=4, pady=4)
        ttk.Label(rampf, text="四指步长").grid(row=0, column=4, padx=6, pady=4, sticky="w")
        ttk.Entry(rampf, textvariable=self.ramp_step_var, width=6).grid(row=0, column=5, padx=4, pady=4)
        ttk.Label(rampf, text="步间隔(ms)").grid(row=0, column=6, padx=6, pady=4, sticky="w")
        ttk.Entry(rampf, textvariable=self.ramp_interval_ms_var, width=6).grid(row=0, column=7, padx=4, pady=4)
        self.chk_ramp_fail = ttk.Checkbutton(
            rampf,
            text="失败保护(每步先重试1次；仍失败计1步，连续6步才停)",
            variable=self.ramp_stop_on_false_var,
        )
        self.chk_ramp_fail.grid(row=0, column=8, padx=6, pady=4)
        ttk.Label(rampf, text="ch2食上限").grid(row=1, column=0, padx=4, pady=2, sticky="w")
        ttk.Entry(rampf, textvariable=self.ramp_max_ch2_var, width=5).grid(row=1, column=1, padx=2, pady=2)
        ttk.Label(rampf, text="ch3中上限").grid(row=1, column=2, padx=4, pady=2, sticky="w")
        ttk.Entry(rampf, textvariable=self.ramp_max_ch3_var, width=5).grid(row=1, column=3, padx=2, pady=2)
        ttk.Label(rampf, text="ch4无名上限").grid(row=1, column=4, padx=4, pady=2, sticky="w")
        ttk.Entry(rampf, textvariable=self.ramp_max_ch4_var, width=5).grid(row=1, column=5, padx=2, pady=2)
        ttk.Label(rampf, text="ch5小上限").grid(row=1, column=6, padx=4, pady=2, sticky="w")
        ttk.Entry(rampf, textvariable=self.ramp_max_ch5_var, width=5).grid(row=1, column=7, padx=2, pady=2)
        ttk.Label(rampf, text="(各指到各自上限后只停该指)").grid(row=1, column=8, columnspan=4, padx=6, pady=2, sticky="w")
        self.btn_ramp_start = ttk.Button(rampf, text="开始渐进收指", command=self.start_progressive_grasp)
        self.btn_ramp_start.grid(row=2, column=0, padx=6, pady=6, columnspan=3, sticky="w")
        self.btn_ramp_stop = ttk.Button(rampf, text="停止", command=self.stop_progressive_grasp, state="disabled")
        self.btn_ramp_stop.grid(row=2, column=3, padx=6, pady=6, sticky="w")

    def _build_tab_lift(self, parent: ttk.Frame):
        """升降平台：版式与现场调试面板一致；说明摘自《复合升降机器人平台》用户手册 V1.3。"""
        doc = (
            "手册 V1.3 §4.5：竖直导轨由机械臂控制器驱动升降电机，扩展垂直工作空间。"
            " §6.1：机械臂调试 IP 为 192.168.10.18、端口 8080（与 Web 示教器相同 JSON 通道）。"
            " 手册产品篇未给出逐寄存器 Modbus 表时，升/停/降的 data 字节请以随柜驱动器协议或集成方参数为准。"
        )
        ttk.Label(parent, text=doc, wraplength=860, justify="left", foreground="#333").pack(
            fill="x", padx=8, pady=(6, 10), anchor="w"
        )

        liftf = ttk.LabelFrame(
            parent,
            text="升降机 — 经机械臂控制器 Modbus 转发（与 Web 示教器并列的本地调试入口）",
        )
        liftf.pack(fill="both", expand=True, padx=4, pady=6)

        ttk.Checkbutton(
            liftf,
            text="使用独立RS485口",
            variable=self.lift_use_separate_port_var,
        ).grid(row=0, column=0, padx=6, pady=6, sticky="w")
        ttk.Label(liftf, text="lift port").grid(row=0, column=1, padx=4, pady=6, sticky="w")
        ttk.Entry(liftf, textvariable=self.lift_modbus_port_var, width=5).grid(row=0, column=2, padx=2, pady=6)
        ttk.Label(liftf, text="baud").grid(row=0, column=3, padx=4, pady=6, sticky="w")
        ttk.Entry(liftf, textvariable=self.lift_baud_var, width=8).grid(row=0, column=4, padx=2, pady=6)
        ttk.Label(liftf, text="timeout(百ms)").grid(row=0, column=5, padx=4, pady=6, sticky="w")
        ttk.Entry(liftf, textvariable=self.lift_timeout_var, width=5).grid(row=0, column=6, padx=2, pady=6)
        ttk.Label(liftf, text="device").grid(row=0, column=7, padx=4, pady=6, sticky="w")
        ttk.Entry(liftf, textvariable=self.lift_device_var, width=5).grid(row=0, column=8, padx=2, pady=6)
        ttk.Label(liftf, text="address").grid(row=0, column=9, padx=4, pady=6, sticky="w")
        ttk.Entry(liftf, textvariable=self.lift_address_var, width=7).grid(row=0, column=10, padx=2, pady=6)
        ttk.Label(liftf, text="num").grid(row=0, column=11, padx=4, pady=6, sticky="w")
        ttk.Entry(liftf, textvariable=self.lift_num_var, width=4).grid(row=0, column=12, padx=2, pady=6)

        ttk.Checkbutton(
            liftf,
            text="写入前 set_modbus_mode",
            variable=self.lift_cfg_before_send_var,
        ).grid(row=1, column=0, columnspan=2, padx=6, pady=4, sticky="w")
        self.btn_lift_cfg = ttk.Button(liftf, text="仅配置升降口", command=self.lift_configure_modbus_only)
        self.btn_lift_cfg.grid(row=1, column=2, padx=6, pady=4, sticky="w")
        ttk.Label(liftf, text="自定义 data（逗号分隔字节）").grid(row=1, column=3, padx=6, pady=4, sticky="w")
        ent_custom = ttk.Entry(liftf, textvariable=self.lift_custom_data_var)
        ent_custom.grid(row=1, column=4, columnspan=7, padx=2, pady=4, sticky="ew")
        for _wcol in (4, 5):
            liftf.columnconfigure(_wcol, weight=1)
        self.btn_lift_custom = ttk.Button(liftf, text="发送自定义", command=self.send_lift_custom_write)
        self.btn_lift_custom.grid(row=1, column=11, padx=6, pady=4, sticky="e")

        ttk.Label(liftf, text="升 data").grid(row=2, column=0, padx=6, pady=8, sticky="nw")
        ttk.Entry(liftf, textvariable=self.lift_data_up_var).grid(
            row=2, column=1, columnspan=9, padx=2, pady=8, sticky="ew"
        )
        self.btn_lift_up = ttk.Button(liftf, text="升", command=lambda: self.send_lift_preset("up"))
        self.btn_lift_up.grid(row=2, column=10, columnspan=3, padx=8, pady=8, sticky="e")

        ttk.Label(liftf, text="停 data").grid(row=3, column=0, padx=6, pady=8, sticky="nw")
        ttk.Entry(liftf, textvariable=self.lift_data_stop_var).grid(
            row=3, column=1, columnspan=9, padx=2, pady=8, sticky="ew"
        )
        self.btn_lift_stop = ttk.Button(liftf, text="停", command=lambda: self.send_lift_preset("stop"))
        self.btn_lift_stop.grid(row=3, column=10, columnspan=3, padx=8, pady=8, sticky="e")

        ttk.Label(liftf, text="降 data").grid(row=4, column=0, padx=6, pady=8, sticky="nw")
        ttk.Entry(liftf, textvariable=self.lift_data_down_var).grid(
            row=4, column=1, columnspan=9, padx=2, pady=8, sticky="ew"
        )
        self.btn_lift_down = ttk.Button(liftf, text="降", command=lambda: self.send_lift_preset("down"))
        self.btn_lift_down.grid(row=4, column=10, columnspan=3, padx=8, pady=8, sticky="e")

        foot = (
            "说明：厂商 Web 示教器无法二次开发嵌入自定义按钮；本页与示教器相同，"
            "向顶部「连接配置」中的控制器 IP:端口 发送 JSON（set_modbus_mode / write_registers）。"
        )
        ttk.Label(liftf, text=foot, wraplength=840, foreground="#444").grid(
            row=5, column=0, columnspan=13, padx=8, pady=(12, 6), sticky="w"
        )

    def _build_tab_agv(self, parent: ttk.Frame):
        agvf = ttk.LabelFrame(
            parent,
            text="底盘（云迹 AGV）远程调试 — SSH 在主控执行 ROS Python，需本机 pip install paramiko",
        )
        agvf.pack(fill="x", padx=4, pady=6)
        ttk.Label(agvf, text="SSH 主机").grid(row=0, column=0, padx=4, pady=4, sticky="w")
        ttk.Entry(agvf, textvariable=self.ssh_host_var, width=16).grid(row=0, column=1, padx=2, pady=4)
        ttk.Label(agvf, text="用户").grid(row=0, column=2, padx=4, pady=4, sticky="w")
        ttk.Entry(agvf, textvariable=self.ssh_user_var, width=8).grid(row=0, column=3, padx=2, pady=4)
        ttk.Label(agvf, text="密码(可空)").grid(row=0, column=4, padx=4, pady=4, sticky="w")
        ttk.Entry(agvf, textvariable=self.ssh_password_var, width=12, show="*").grid(row=0, column=5, padx=2, pady=4)
        ttk.Label(agvf, text="catkin 根").grid(row=0, column=6, padx=4, pady=4, sticky="w")
        ttk.Entry(agvf, textvariable=self.ssh_catkin_var, width=22).grid(row=0, column=7, padx=2, pady=4, sticky="we")
        agvf.columnconfigure(7, weight=1)

        ttk.Label(agvf, text="线速度m/s").grid(row=1, column=0, padx=4, pady=4, sticky="w")
        ttk.Entry(agvf, textvariable=self.agv_linear_var, width=8).grid(row=1, column=1, padx=2, pady=4)
        ttk.Label(agvf, text="角速度rad/s").grid(row=1, column=2, padx=4, pady=4, sticky="w")
        ttk.Entry(agvf, textvariable=self.agv_angular_var, width=8).grid(row=1, column=3, padx=2, pady=4)
        ttk.Label(agvf, text="脉冲秒").grid(row=1, column=4, padx=4, pady=4, sticky="w")
        ttk.Entry(agvf, textvariable=self.agv_pulse_sec_var, width=8).grid(row=1, column=5, padx=2, pady=4)
        self.btn_agv_pulse = ttk.Button(agvf, text="Joy 速度脉冲", command=self.agv_ssh_joy_pulse)
        self.btn_agv_pulse.grid(row=1, column=6, padx=6, pady=4, sticky="w")
        self.btn_agv_zero = ttk.Button(agvf, text="Joy 停车(零速)", command=self.agv_ssh_joy_zero)
        self.btn_agv_zero.grid(row=1, column=7, padx=6, pady=4, sticky="w")

        ttk.Label(agvf, text="标记点名").grid(row=2, column=0, padx=4, pady=4, sticky="w")
        ttk.Entry(agvf, textvariable=self.agv_marker_var, width=14).grid(row=2, column=1, padx=2, pady=4)
        self.btn_agv_go = ttk.Button(agvf, text="导航到标记", command=self.agv_ssh_go_marker)
        self.btn_agv_go.grid(row=2, column=2, padx=6, pady=4, sticky="w")
        self.btn_agv_cancel = ttk.Button(agvf, text="取消移动", command=self.agv_ssh_cancel_move)
        self.btn_agv_cancel.grid(row=2, column=3, padx=6, pady=4, sticky="w")
        self.btn_agv_unlock = ttk.Button(agvf, text="解锁底盘", command=self.agv_ssh_unlock_chassis)
        self.btn_agv_unlock.grid(row=2, column=4, padx=6, pady=4, sticky="w")
        ttk.Label(
            agvf,
            text=(
                "主控 agv_ros/scripts/agv_driver.py：发布 /navigation_feedback；订阅 "
                "/navigation_marker、/navigation_location、/navigation_multipoint、/navigation_move_cancel、"
                "/navigation_get_robot_status、/navigation_position_adjust_marker、/navigation_get_power_status、"
                "/navigation_joy_control、/navigation_max_speed、/navigation_max_speed_ratio、"
                "/navigation_max_speed_linear、/navigation_max_speed_angular、/navigation_get_params、"
                "/navigation_led_set_color；补丁 /navigation_soft_estop（Bool，true=软急停 on）→ 水滴 /api/estop（须合并 Realman/remote_agv_driver.py 补丁至主控 agv_driver.py）。"
                "未封装电机掉使能；规划互锁请用「取消移动」「解锁底盘」+ Joy 零速。"
            ),
            foreground="#444",
            wraplength=920,
            justify="left",
        ).grid(row=3, column=0, columnspan=8, padx=6, pady=4, sticky="w")
        self.btn_agv_stat = ttk.Button(agvf, text="查询底盘状态", command=self.agv_ssh_get_robot_status)
        self.btn_agv_stat.grid(row=4, column=0, padx=6, pady=4, sticky="w")
        self.btn_agv_pwr = ttk.Button(agvf, text="查询电量", command=self.agv_ssh_get_power_status)
        self.btn_agv_pwr.grid(row=4, column=1, padx=6, pady=4, sticky="w")
        self.btn_agv_params = ttk.Button(agvf, text="查询参数列表", command=self.agv_ssh_get_navigation_params)
        self.btn_agv_params.grid(row=4, column=2, padx=6, pady=4, sticky="w")
        self.btn_agv_fetch_driver = ttk.Button(
            agvf,
            text="SSH 摘录 agv_driver.py",
            command=self.agv_ssh_fetch_driver_snippet,
        )
        self.btn_agv_fetch_driver.grid(row=4, column=3, padx=6, pady=4, sticky="w")
        self.btn_agv_rostopic_nav = ttk.Button(
            agvf,
            text="SSH rostopic (navigation|agv)",
            command=self.agv_ssh_rostopic_navigation,
        )
        self.btn_agv_rostopic_nav.grid(row=4, column=4, padx=6, pady=4, sticky="w")
        ttk.Label(
            agvf,
            text="须先在主控运行: roslaunch agv_ros agv_start.launch",
            foreground="#555",
        ).grid(row=4, column=5, columnspan=3, padx=8, pady=4, sticky="w")
        self.chk_agv_preunlock = ttk.Checkbutton(
            agvf,
            text="导航 / Joy 脉冲前自动：取消导航 + Joy 零速（减轻与规划器互锁、卡住）",
            variable=self.agv_auto_preunlock_var,
        )
        self.chk_agv_preunlock.grid(row=5, column=0, columnspan=8, padx=6, pady=6, sticky="w")
        ttk.Label(agvf, text="水滴TCP IP").grid(row=6, column=0, padx=4, pady=4, sticky="w")
        ttk.Entry(agvf, textvariable=self.agv_chassis_host_var, width=14).grid(row=6, column=1, padx=2, pady=4, sticky="w")
        ttk.Label(agvf, text="端口").grid(row=6, column=2, padx=4, pady=4, sticky="w")
        ttk.Entry(agvf, textvariable=self.agv_chassis_port_var, width=7).grid(row=6, column=3, padx=2, pady=4, sticky="w")
        self.btn_agv_estop_clear = ttk.Button(agvf, text="软急停解除", command=self.agv_ssh_soft_estop_clear)
        self.btn_agv_estop_clear.grid(row=6, column=4, padx=6, pady=4, sticky="w")
        self.btn_agv_estop_on = ttk.Button(agvf, text="软急停触发", command=self.agv_ssh_soft_estop_trigger)
        self.btn_agv_estop_on.grid(row=6, column=5, padx=6, pady=4, sticky="w")
        self.btn_agv_estop_ros_off = ttk.Button(
            agvf,
            text="ROS 发解除(需补丁节点)",
            command=self.agv_ssh_publish_soft_estop_ros,
        )
        self.btn_agv_estop_ros_off.grid(row=6, column=6, padx=6, pady=4, sticky="w")
        ttk.Label(
            agvf,
            text="上排按钮：SSH 在主控执行 Python，直连水滴 TCP 发 /api/estop（须主控能 ping 通水滴 IP）",
            foreground="#555",
        ).grid(row=7, column=0, columnspan=8, padx=6, pady=2, sticky="w")

    def _build_tab_advanced(self, parent: ttk.Frame):
        raw = ttk.LabelFrame(parent, text="高级调试（自定义 JSON）")
        raw.pack(fill="both", expand=True, padx=4, pady=6)
        self.raw_text = tk.Text(raw, height=12, wrap="word")
        self.raw_text.pack(fill="both", expand=True, padx=6, pady=6)
        self.raw_text.insert("1.0", '{"command":"get_joint_degree"}')
        self.btn_raw_send = ttk.Button(raw, text="发送自定义JSON", command=self.send_raw_json)
        self.btn_raw_send.pack(padx=6, pady=6, anchor="w")

    def _build_ui(self):
        top = ttk.LabelFrame(self.root, text="连接配置")
        top.pack(fill="x", padx=10, pady=6)

        ttk.Label(top, text="跳板SSH").grid(row=0, column=0, padx=6, pady=6, sticky="w")
        ttk.Entry(top, textvariable=self.ssh_host_var, width=14).grid(row=0, column=1, padx=4, pady=6)
        ttk.Label(top, text="用户").grid(row=0, column=2, padx=4, pady=6, sticky="w")
        ttk.Entry(top, textvariable=self.ssh_user_var, width=8).grid(row=0, column=3, padx=4, pady=6)
        ttk.Label(top, text="密码").grid(row=0, column=4, padx=4, pady=6, sticky="w")
        ttk.Entry(top, textvariable=self.ssh_password_var, width=10, show="*").grid(row=0, column=5, padx=4, pady=6)

        btn_connect = ttk.Button(top, text="连接", command=self.connect)
        btn_connect.grid(row=0, column=6, padx=6, pady=6)
        btn_disconnect = ttk.Button(top, text="断开", command=self.disconnect)
        btn_disconnect.grid(row=0, column=7, padx=6, pady=6)
        btn_get_state = ttk.Button(top, text="读机械臂状态", command=self.get_arm_state)
        btn_get_state.grid(row=1, column=6, padx=6, pady=4)
        btn_log_window = ttk.Button(top, text="打开日志窗口", command=self.open_log_window)
        btn_log_window.grid(row=1, column=7, padx=6, pady=4)

        btn_joint_deg = ttk.Button(top, text="读关节角度", command=self.get_joint_degree)
        btn_joint_deg.grid(row=1, column=0, padx=6, pady=4, sticky="w")
        btn_clear_err = ttk.Button(top, text="清系统错误", command=self.clear_system_error)
        btn_clear_err.grid(row=1, column=1, padx=6, pady=4, sticky="w")
        btn_arm_stop = ttk.Button(top, text="急停轨迹", command=self.arm_set_stop_confirm)
        btn_arm_stop.grid(row=1, column=2, padx=6, pady=4, sticky="w")
        ttk.Label(
            top,
            text=(
                f"机械臂 JSON 固定 {ARM_JSON_VIA_JUMP_HOST}:{ARM_JSON_VIA_JUMP_PORT}（经跳板端口转发）；"
                "底盘分页 SSH 与跳板共用上方主机/用户/密码。"
            ),
            foreground="#444",
        ).grid(row=1, column=3, columnspan=3, padx=8, pady=4, sticky="w")

        nb = ttk.Notebook(self.root)
        nb.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)
        tab_arm = ttk.Frame(nb, padding=2)
        tab_hand = ttk.Frame(nb, padding=2)
        tab_lift = ttk.Frame(nb, padding=2)
        tab_agv = ttk.Frame(nb, padding=2)
        tab_adv = ttk.Frame(nb, padding=2)
        nb.add(tab_arm, text=" 机械臂 TCP ")
        nb.add(tab_hand, text=" 灵巧手 ")
        nb.add(tab_lift, text=" 升降平台 ")
        nb.add(tab_agv, text=" 底盘 AGV ")
        nb.add(tab_adv, text=" 高级 JSON ")
        self._build_tab_arm_tcp(tab_arm)
        self._build_tab_hand(tab_hand)
        self._build_tab_lift(tab_lift)
        self._build_tab_agv(tab_agv)
        self._build_tab_advanced(tab_adv)

        logf = ttk.LabelFrame(self.root, text="日志")
        logf.pack(fill="both", expand=False, padx=10, pady=6)
        self.log = tk.Text(logf, height=10, wrap="word")
        self.log.pack(fill="both", expand=True, padx=6, pady=6)
        self.log.insert(
            "end",
            "提示: 本程序为 SSH 跳板版，机械臂 JSON 经跳板转发至 "
            f"{ARM_JSON_VIA_JUMP_HOST}:{ARM_JSON_VIA_JUMP_PORT}；"
            "「机械臂 TCP」分页含轨迹/电源/movej/movel；灵巧手区可录制序列并保存/读取 JSON。"
            "需 pip install paramiko。底盘分页 SSH 与跳板共用顶部账号。\n",
        )

        # Button tooltips: show command and key params.
        ToolTip(
            btn_connect,
            f"SSH 登录跳板机，再 direct-tcpip 转发到 {ARM_JSON_VIA_JUMP_HOST}:{ARM_JSON_VIA_JUMP_PORT}（需 pip install paramiko）",
        )
        ToolTip(btn_disconnect, "断开当前TCP连接")
        ToolTip(
            btn_get_state,
            "读取机械臂当前状态\n命令: get_current_arm_state\n"
            "回包 arm_state.joint 为 0.001° 整数；与「读关节角」差几个计数时正常（≈0.001°）。",
        )
        ToolTip(btn_log_window, "打开独立日志窗口\n用于实时查看发送/回包/错误")
        ToolTip(
            self.btn_enable,
            "使能灵巧手通信\n命令: set_modbus_mode\n参数: port, baudrate, timeout(百ms)",
        )
        ToolTip(
            self.btn_disable,
            "安全失能(释放)\n命令: write_registers\n参数: port, address, num, data(开手), device",
        )
        ToolTip(
            self.chk_hand_cfg_before_write,
            "勾选后：每次灵巧手 write_registers（开/闭/分手指/渐进/手势）前先发一次 set_modbus_mode，\n"
            "避免未点「使能灵巧手」或口被其它任务改过导致 write_state=false。",
        )
        ToolTip(
            self.btn_open,
            "整体开手(释放)\n命令: write_registers\n参数: data=[0...0,255,0], address=1135, num=6",
        )
        ToolTip(
            self.btn_close,
            "整体闭手(抓握)\n命令: write_registers\n参数: data=[255...255,0,0], address=1135, num=6",
        )
        ToolTip(
            self.btn_gesture,
            "开启/关闭手势控制\n弹出相机调试窗口并实时映射到6通道；未连接时仅预览",
        )
        ToolTip(self.btn_gesture_open_cal, "张开手后点击，记录开手参考位（Open Ref）")
        ToolTip(self.btn_gesture_close_cal, "握拳后点击，记录闭手参考位（Close Ref）")
        ToolTip(self.btn_gesture_reset_cal, "清空开/闭手标定参考位")
        ToolTip(
            self.btn_apply_thumb_target,
            "应用拇指开手目标值(0~255)。\n值越大，开手时拇指越偏向回收；值越小，拇指越外展。",
        )
        ToolTip(self.btn_rec_start, "开始录制动作序列\n记录后续按钮发送的命令与参数")
        ToolTip(self.btn_rec_stop, "停止录制动作序列")
        ToolTip(self.btn_rec_clear, "清空已录制的动作序列")
        ToolTip(
            self.btn_rec_replay,
            "回放录制动作\n参数: 步间隔(ms), 回放次数\n回放时不再重复录制",
        )
        ToolTip(self.btn_rec_save, "将当前序列保存为 JSON（version/actions）")
        ToolTip(self.btn_rec_load, "从 JSON 文件加载序列到内存（覆盖当前列表）")
        ToolTip(
            self.btn_finger_send,
            "发送分手指控制\n命令: write_registers\n通道:1拇指 2~5四指 6拇指根\n参数: 0~255 -> 12字节(每通道低字节), address, num, device",
        )
        ToolTip(
            self.btn_ramp_start,
            "固定ch1/ch6；ch2~5 从0开始每步加「四指步长」。\n"
            "每根手指有独立「上限」：先到上限的手指先停在该值，其余手指继续加到各自上限。\n"
            "全部四指都达到各自上限或点「停止」则结束。",
        )
        ToolTip(self.btn_ramp_stop, "立即停止渐进收指线程")
        ToolTip(
            self.chk_ramp_fail,
            "默认关闭。开启后：每步若 write_state=false，会延时再发同一命令一次；\n"
            "两次都失败才计 1 个「失败步」；连续 6 个失败步才停止。\n"
            "用于总线偶发忙/误判，避免没抓到物体却很快停住。",
        )
        ToolTip(
            self.btn_raw_send,
            "发送自定义JSON命令\n参数: 文本框内完整JSON对象\n示例: {\"command\":\"get_joint_degree\"}",
        )
        ToolTip(
            self.btn_lift_cfg,
            "仅对升降所用口发送 set_modbus_mode\n不勾选独立口时，参数与「灵巧手」分页 port 相同",
        )
        ToolTip(self.btn_lift_custom, "将「自定义data」按当前 address/num/device 发送 write_registers")
        ToolTip(self.btn_lift_up, "发送「升 data」框内的字节序列；空则提示先填写")
        ToolTip(self.btn_lift_stop, "发送「停 data」")
        ToolTip(self.btn_lift_down, "发送「降 data」")
        ToolTip(
            btn_joint_deg,
            "TCP JSON: get_joint_degree；joint 为 0.001° 整数，日志会附换算(°)。\n"
            "与 get_current_arm_state 内关节可能略有不同（不同时刻/不同状态源）。",
        )
        ToolTip(btn_clear_err, "TCP JSON: {\"command\":\"clear_system_err\"}")
        ToolTip(btn_arm_stop, "TCP JSON: {\"command\":\"set_arm_stop\"}（急停，需确认）")
        ToolTip(self.btn_agv_pulse, "SSH 在主控发 /navigation_joy_control 短时脉冲（开环位移，非精确厘米）")
        ToolTip(self.btn_agv_zero, "SSH 发布零线速度/零角速度若干帧")
        ToolTip(
            self.btn_agv_go,
            "SSH 发布 /navigation_marker（勾选「自动取消+零速」时先发 cancel+Joy 零速再导航）",
        )
        ToolTip(self.btn_agv_cancel, "SSH：/navigation_move_cancel + 持续 /navigation_joy_control 零速，结束未完成任务并释放互锁")
        ToolTip(
            self.btn_agv_unlock,
            "与「取消移动」相同：/navigation_move_cancel + /navigation_joy_control 零速；卡住时先点此再发新指令",
        )
        ToolTip(
            self.chk_agv_preunlock,
            "开启后：「导航到标记」「Joy 脉冲/停车」前自动 cancel + Joy 零速，减少导航与手动速度互锁",
        )
        ToolTip(
            self.btn_agv_fetch_driver,
            "SSH 在 catkin 下查找 agv_driver.py，grep 订阅/Publisher/navigation_ 相关行，结果打在日志（需填对 catkin 根路径）",
        )
        ToolTip(
            self.btn_agv_rostopic_nav,
            "SSH source devel/setup.bash 后 rostopic list，过滤 navigation 或 agv（需 roscore 已起）",
        )
        ToolTip(
            self.btn_agv_stat,
            "SSH 发布 /navigation_get_robot_status 并等待 /navigation_feedback 回包（最多约 3s）",
        )
        ToolTip(
            self.btn_agv_pwr,
            "SSH 发布 /navigation_get_power_status 并等待 /navigation_feedback 回包（最多约 3s）",
        )
        ToolTip(
            self.btn_agv_params,
            "SSH 发布 /navigation_get_params（空 String）并等 /navigation_feedback；回包可能含水滴 API/参数名，可搜 motor、brake、manual 等关键字",
        )
        ToolTip(
            self.btn_agv_estop_clear,
            "SSH 在主控直连水滴 TCP 发 /api/estop?flag=false。"
            "若 agv_driver 已在跑，并行直连可能导致水滴踢掉驱动连接、Joy 报 Broken pipe；优先用「ROS 发解除」或先停驱动再直连。",
        )
        ToolTip(
            self.btn_agv_estop_on,
            "SSH 在主控直连水滴，发送 /api/estop?flag=true；需确认，机器人将软件急停",
        )
        ToolTip(
            self.btn_agv_estop_ros_off,
            "SSH 发布 /navigation_soft_estop Bool(False)；仅当主控 agv_driver 已合并 navigation_soft_estop 订阅时有效",
        )

    def _log(self, text: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {text}"
        self.log.insert("end", line + "\n")
        self.log.see("end")
        if self.log_popup_text is not None:
            self.log_popup_text.insert("end", line + "\n")
            self.log_popup_text.see("end")

    def open_log_window(self):
        if self.log_popup is not None and self.log_popup.winfo_exists():
            self.log_popup.lift()
            self.log_popup.focus_force()
            return
        self.log_popup = tk.Toplevel(self.root)
        self.log_popup.title("Realman Hand Debug - 实时日志")
        self.log_popup.geometry("900x420")
        self.log_popup_text = scrolledtext.ScrolledText(self.log_popup, wrap="word")
        self.log_popup_text.pack(fill="both", expand=True, padx=8, pady=8)
        existing = self.log.get("1.0", "end")
        self.log_popup_text.insert("1.0", existing)
        self.log_popup_text.see("end")
        self.log_popup.protocol("WM_DELETE_WINDOW", self._close_log_window)
        self._log("[OK] 已打开独立日志窗口")

    def _close_log_window(self):
        if self.log_popup is not None:
            self.log_popup.destroy()
        self.log_popup = None
        self.log_popup_text = None

    def _run_bg(self, func):
        threading.Thread(target=func, daemon=True).start()

    def connect(self):
        def work():
            cli = None
            try:
                jh = self.ssh_host_var.get().strip()
                ju = self.ssh_user_var.get().strip()
                jp = self.ssh_password_var.get()
                if self.client is not None:
                    try:
                        self.client.close()
                    except Exception:
                        pass
                    self.client = None
                cli = RealmanHandClientSSHJump(jh, ju, jp)
                cli.connect()
                self.client = cli
                cli = None
                self._log(
                    f"[OK] 已连接 跳板SSH {jh} → {ARM_JSON_VIA_JUMP_HOST}:{ARM_JSON_VIA_JUMP_PORT}"
                )
            except Exception as e:
                self._log(f"[ERR] 连接失败: {e}")
                if cli is not None:
                    try:
                        cli.close()
                    except Exception:
                        pass
                self.client = None

        self._run_bg(work)

    def disconnect(self):
        self.stop_progressive_grasp()
        if self.gesture_running:
            self.stop_gesture_control()
        if self.client:
            self.client.close()
            self.client = None
        self._log("[OK] 已断开连接")

    @staticmethod
    def _hint_joint_degrees_from_resp(item: dict) -> str | None:
        """TCP 回包关节多为 0.001° 整数；换算为度便于与示教器对照。"""
        if not isinstance(item, dict):
            return None
        st = item.get("state")
        joint = None
        if st == "joint_degree" and isinstance(item.get("joint"), list):
            joint = item["joint"]
        elif st == "current_arm_state":
            arm = item.get("arm_state")
            if isinstance(arm, dict) and isinstance(arm.get("joint"), list):
                joint = arm["joint"]
        if not joint:
            return None
        try:
            deg = [float(x) / 1000.0 for x in joint]
        except (TypeError, ValueError):
            return None
        parts = ", ".join(f"{d:.3f}" for d in deg)
        return (
            f"[HINT] 关节(°): [{parts}]"
        )

    def _send_and_log(self, payload: dict, wait_ms: int | None = None):
        if not self.client:
            raise RuntimeError("未连接控制器")
        use_wait = wait_ms if wait_ms is not None else self.wait_ms_var.get()
        self._log("[SEND] " + json.dumps(payload, ensure_ascii=False))
        resp = self.client.send_json(payload, wait_ms=use_wait)
        if not resp:
            self._log("[RESP] (none)")
        else:
            for item in resp:
                self._log("[RESP] " + json.dumps(item, ensure_ascii=False))
                hint = self._hint_joint_degrees_from_resp(item)
                if hint:
                    self._log(hint)
        return resp

    def _safe_intvar_get(self, var: tk.Variable, label: str = "") -> int:
        try:
            return int(var.get())
        except (tk.TclError, ValueError, TypeError) as e:
            hint = f" ({label})" if label else ""
            raise ValueError(f"请输入有效整数{hint}: {e}") from e

    def _bg_warn_not_connected(self):
        self.root.after(
            0,
            lambda: messagebox.showwarning(
                "未连接",
                "请先点击「连接」，并等待日志出现「已连接」后再发送灵巧手指令。",
            ),
        )

    def _hand_modbus_mode_cmd(self) -> dict:
        return {
            "command": "set_modbus_mode",
            "port": self._safe_intvar_get(self.modbus_port_var, "port"),
            "baudrate": self._safe_intvar_get(self.baud_var, "baudrate"),
            "timeout": self._safe_intvar_get(self.timeout_var, "timeout"),
        }

    def _hand_write_registers_cmd(self, data: list[int]) -> dict:
        return {
            "command": "write_registers",
            "port": self._safe_intvar_get(self.modbus_port_var, "port"),
            "address": self._safe_intvar_get(self.reg_addr_var, "address"),
            "num": self._safe_intvar_get(self.reg_num_var, "num"),
            "data": data,
            "device": self._safe_intvar_get(self.device_var, "device"),
        }

    def _send_hand_modbus_if_needed(self, tag: str = "HAND-MODBUS"):
        if not self.hand_cfg_before_write_var.get():
            return
        if not self.client:
            return
        cmd = self._hand_modbus_mode_cmd()
        self._log(f"[SEND] [{tag}] " + json.dumps(cmd, ensure_ascii=False))
        resp = self.client.send_json(cmd, wait_ms=500)
        for item in resp:
            self._log(f"[RESP] [{tag}] " + json.dumps(item, ensure_ascii=False))

    @staticmethod
    def _has_write_false(resp: list[dict]) -> bool:
        for item in resp:
            if isinstance(item, dict) and item.get("command") == "write_registers" and item.get("write_state") is False:
                return True
        return False

    def _record_action(self, name: str, payload: dict, wait_ms: int):
        if not self.recording_enabled:
            return
        self.recorded_actions.append(
            {
                "name": name,
                "payload": payload,
                "wait_ms": wait_ms,
            }
        )
        self._log(f"[REC] 已记录动作: {name}")

    def _execute_action(self, name: str, payload: dict, wait_ms: int, allow_retry: bool = True):
        resp = self._send_and_log(payload, wait_ms=wait_ms)
        self._record_action(name, payload, wait_ms)
        if allow_retry and self.retry_on_false_var.get() and self._has_write_false(resp):
            delay = max(100, int(self.retry_delay_ms_var.get()))
            self._log(f"[WARN] write_state=false，{delay}ms 后自动重试一次: {name}")
            self.root.after(delay, lambda: self._run_bg(lambda: self._send_and_log(payload, wait_ms=wait_ms)))
        return resp

    def get_arm_state(self):
        def work():
            try:
                if not self.client:
                    self._bg_warn_not_connected()
                    return
                self._execute_action(
                    "get_current_arm_state",
                    {"command": "get_current_arm_state"},
                    400,
                    allow_retry=False,
                )
            except Exception as e:
                self._log(f"[ERR] 读状态失败: {e}")

        self._run_bg(work)

    def get_joint_degree(self):
        def work():
            try:
                if not self.client:
                    self._bg_warn_not_connected()
                    return
                self._execute_action(
                    "get_joint_degree",
                    {"command": "get_joint_degree"},
                    500,
                    allow_retry=False,
                )
            except Exception as e:
                self._log(f"[ERR] 读关节角度失败: {e}")

        self._run_bg(work)

    def clear_system_error(self):
        def work():
            try:
                if not self.client:
                    self._bg_warn_not_connected()
                    return
                self._execute_action(
                    "clear_system_err",
                    {"command": "clear_system_err"},
                    500,
                    allow_retry=False,
                )
            except Exception as e:
                self._log(f"[ERR] 清系统错误失败: {e}")

        self._run_bg(work)

    def arm_get_power_state(self):
        def work():
            try:
                if not self.client:
                    self._bg_warn_not_connected()
                    return
                self._execute_action(
                    "get_arm_power_state",
                    {"command": "get_arm_power_state"},
                    400,
                    allow_retry=False,
                )
            except Exception as e:
                self._log(f"[ERR] 读电源状态失败: {e}")

        self._run_bg(work)

    def arm_set_power(self, arm_power: int):
        def work():
            try:
                if not self.client:
                    self._bg_warn_not_connected()
                    return
                p = 1 if int(arm_power) else 0
                self._execute_action(
                    f"set_arm_power_{p}",
                    {"command": "set_arm_power", "arm_power": p},
                    600,
                    allow_retry=True,
                )
            except Exception as e:
                self._log(f"[ERR] set_arm_power 失败: {e}")

        self._run_bg(work)

    def arm_traj_pause(self):
        def work():
            try:
                if not self.client:
                    self._bg_warn_not_connected()
                    return
                self._execute_action("set_arm_pause", {"command": "set_arm_pause"}, 500, allow_retry=True)
            except Exception as e:
                self._log(f"[ERR] 轨迹暂停失败: {e}")

        self._run_bg(work)

    def arm_traj_continue(self):
        def work():
            try:
                if not self.client:
                    self._bg_warn_not_connected()
                    return
                self._execute_action("set_arm_continue", {"command": "set_arm_continue"}, 500, allow_retry=True)
            except Exception as e:
                self._log(f"[ERR] 轨迹继续失败: {e}")

        self._run_bg(work)

    def arm_delete_current_trajectory(self):
        def work():
            try:
                if not self.client:
                    self._bg_warn_not_connected()
                    return
                self._execute_action(
                    "set_delete_current_trajectory",
                    {"command": "set_delete_current_trajectory"},
                    500,
                    allow_retry=True,
                )
            except Exception as e:
                self._log(f"[ERR] 删当前轨迹失败: {e}")

        self._run_bg(work)

    def arm_delete_all_trajectory(self):
        def work():
            try:
                if not self.client:
                    self._bg_warn_not_connected()
                    return
                self._execute_action(
                    "set_arm_delete_trajectory",
                    {"command": "set_arm_delete_trajectory"},
                    500,
                    allow_retry=True,
                )
            except Exception as e:
                self._log(f"[ERR] 删全部轨迹失败: {e}")

        self._run_bg(work)

    def arm_get_current_trajectory(self):
        def work():
            try:
                if not self.client:
                    self._bg_warn_not_connected()
                    return
                self._execute_action(
                    "get_arm_current_trajectory",
                    {"command": "get_arm_current_trajectory"},
                    500,
                    allow_retry=False,
                )
            except Exception as e:
                self._log(f"[ERR] 查当前轨迹失败: {e}")

        self._run_bg(work)

    def arm_send_movej(self):
        def work():
            try:
                if not self.client:
                    self._bg_warn_not_connected()
                    return
                joints = self._parse_csv_integers(self.arm_movej_joint_var.get(), 6)
                v = int(round(float(self.arm_movej_v_var.get().strip())))
                r = int(round(float(self.arm_movej_r_var.get().strip())))
                payload = {"command": "movej", "joint": joints, "v": v, "r": r}
                self._execute_action("movej", payload, 800, allow_retry=True)
            except Exception as e:
                self._log(f"[ERR] movej: {e}")

        self._run_bg(work)

    def arm_send_movel(self):
        def work():
            try:
                if not self.client:
                    self._bg_warn_not_connected()
                    return
                pose = self._parse_csv_integers(self.arm_movel_pose_var.get(), 6)
                v = int(round(float(self.arm_movel_v_var.get().strip())))
                r = int(round(float(self.arm_movel_r_var.get().strip())))
                payload = {"command": "movel", "pose": pose, "v": v, "r": r}
                self._execute_action("movel", payload, 800, allow_retry=True)
            except Exception as e:
                self._log(f"[ERR] movel: {e}")

        self._run_bg(work)

    def arm_set_stop_confirm(self):
        if not messagebox.askyesno("急停确认", "将发送 TCP 指令 set_arm_stop（轨迹急停）。是否继续？"):
            return

        def work():
            try:
                if not self.client:
                    self._bg_warn_not_connected()
                    return
                self._execute_action("set_arm_stop", {"command": "set_arm_stop"}, 600, allow_retry=False)
            except Exception as e:
                self._log(f"[ERR] 急停失败: {e}")

        self._run_bg(work)

    def _ssh_run_remote_python(self, code: str) -> tuple[int, str, str]:
        try:
            import paramiko
        except ImportError as e:
            raise RuntimeError("请安装 paramiko：pip install paramiko") from e
        host = self.ssh_host_var.get().strip()
        user = (self.ssh_user_var.get().strip() or "rm")
        ws = (self.ssh_catkin_var.get().strip() or "/home/rm/catkin_ws")
        pwd = self.ssh_password_var.get()
        if not host:
            raise ValueError("请填写 SSH 主机")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kw: dict = {"hostname": host, "username": user, "timeout": 25, "allow_agent": True, "look_for_keys": True}
        if pwd:
            kw["password"] = pwd
            kw["allow_agent"] = False
            kw["look_for_keys"] = False
        client.connect(**kw)
        try:
            setup = shlex.quote(f"{ws}/devel/setup.bash")
            inner = f"source {setup} 2>/dev/null; exec python3 -"
            cmd = f"bash -lc {shlex.quote(inner)}"
            stdin, stdout, stderr = client.exec_command(cmd, timeout=120)
            stdin.write(code.encode("utf-8"))
            stdin.flush()
            stdin.close()
            out_b = stdout.read()
            err_b = stderr.read()
            code_exit = stdout.channel.recv_exit_status()
            return (
                code_exit,
                out_b.decode("utf-8", "replace"),
                err_b.decode("utf-8", "replace"),
            )
        finally:
            client.close()

    def _ssh_run_remote_bash(self, bash_one_liner: str, timeout: int = 90) -> tuple[int, str, str]:
        """在已配置的主控上执行一条 bash 命令（非交互），用于拉取源码摘录、rostopic 等。"""
        try:
            import paramiko
        except ImportError as e:
            raise RuntimeError("请安装 paramiko：pip install paramiko") from e
        host = self.ssh_host_var.get().strip()
        user = (self.ssh_user_var.get().strip() or "rm")
        pwd = self.ssh_password_var.get()
        if not host:
            raise ValueError("请填写 SSH 主机")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kw: dict = {"hostname": host, "username": user, "timeout": 25, "allow_agent": True, "look_for_keys": True}
        if pwd:
            kw["password"] = pwd
            kw["allow_agent"] = False
            kw["look_for_keys"] = False
        client.connect(**kw)
        try:
            cmd = f"bash -lc {shlex.quote(bash_one_liner.strip())}"
            _stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
            out_b = stdout.read()
            err_b = stderr.read()
            code_exit = stdout.channel.recv_exit_status()
            return (
                code_exit,
                out_b.decode("utf-8", "replace"),
                err_b.decode("utf-8", "replace"),
            )
        finally:
            client.close()

    def _agv_log_ssh_result(self, tag: str, exit_code: int, out: str, err: str):
        self._log(f"[SSH][{tag}] exit={exit_code}")
        if out.strip():
            self._log(f"[SSH][{tag}] stdout:\n{out.rstrip()}")
        if err.strip():
            self._log(f"[SSH][{tag}] stderr:\n{err.rstrip()}")

    def _agv_ssh_cancel_and_joy_zero(self, log_tag: str):
        """取消导航规划并持续发布 Joy 零速，释放与自动导航的互锁。"""
        py = """import rospy
from std_msgs.msg import String
from agv_ros.msg import navigation_joy_control
rospy.init_node("hand_gui_agv_canceljoy", anonymous=True)
pub_cancel = rospy.Publisher("/navigation_move_cancel", String, queue_size=1)
pub_joy = rospy.Publisher("/navigation_joy_control", navigation_joy_control, queue_size=10)
rospy.sleep(0.48)
pub_cancel.publish(String())
rospy.sleep(0.35)
z = navigation_joy_control()
z.linear_velocity = 0.0
z.angular_velocity = 0.0
r = rospy.Rate(25)
for _ in range(35):
    pub_joy.publish(z)
    r.sleep()
rospy.sleep(0.1)
print("cancel_navigation_and_joy_zero_done")
"""

        def work():
            try:
                ec, o, e = self._ssh_run_remote_python(py)
                self._agv_log_ssh_result(log_tag, ec, o, e)
            except Exception as ex:
                self._log(f"[ERR] SSH {log_tag} 失败: {ex}")

        self._run_bg(work)

    def agv_ssh_joy_pulse(self):
        def work():
            try:
                lv = float(self.agv_linear_var.get().strip())
                av = float(self.agv_angular_var.get().strip())
                dur = float(self.agv_pulse_sec_var.get().strip())
            except ValueError as e:
                self._log(f"[ERR] 底盘脉冲参数无效: {e}")
                return
            lv = max(-0.5, min(0.5, lv))
            av = max(-1.0, min(1.0, av))
            dur = max(0.02, min(5.0, dur))
            pre = bool(self.agv_auto_preunlock_var.get())
            py = f"""import rospy, time
from std_msgs.msg import String
from agv_ros.msg import navigation_joy_control
rospy.init_node("hand_gui_agv_pulse", anonymous=True)
_do_pre = {pre!r}
pub = rospy.Publisher("/navigation_joy_control", navigation_joy_control, queue_size=10)
rospy.sleep(0.45)
if _do_pre:
    pub_c = rospy.Publisher("/navigation_move_cancel", String, queue_size=1)
    rospy.sleep(0.05)
    pub_c.publish(String())
    rospy.sleep(0.32)
    z0 = navigation_joy_control()
    z0.linear_velocity = 0.0
    z0.angular_velocity = 0.0
    r0 = rospy.Rate(25)
    for _ in range(28):
        pub.publish(z0)
        r0.sleep()
    rospy.sleep(0.12)
m = navigation_joy_control()
m.linear_velocity = {lv!r}
m.angular_velocity = {av!r}
rate = rospy.Rate(20)
t1 = time.time() + {dur!r}
while time.time() < t1 and not rospy.is_shutdown():
    pub.publish(m)
    rate.sleep()
m.linear_velocity = 0.0
m.angular_velocity = 0.0
for _ in range(14):
    pub.publish(m)
    rate.sleep()
print("joy_pulse_done")
"""
            try:
                ec, o, e = self._ssh_run_remote_python(py)
                self._agv_log_ssh_result("agv_joy_pulse", ec, o, e)
            except Exception as ex:
                self._log(f"[ERR] SSH 底盘脉冲失败: {ex}")

        self._run_bg(work)

    def agv_ssh_joy_zero(self):
        pre = bool(self.agv_auto_preunlock_var.get())
        py = f"""import rospy
from std_msgs.msg import String
from agv_ros.msg import navigation_joy_control
rospy.init_node("hand_gui_agv_zero", anonymous=True)
_do_pre = {pre!r}
pub = rospy.Publisher("/navigation_joy_control", navigation_joy_control, queue_size=10)
rospy.sleep(0.45)
if _do_pre:
    pc = rospy.Publisher("/navigation_move_cancel", String, queue_size=1)
    pc.publish(String())
    rospy.sleep(0.3)
m = navigation_joy_control()
m.linear_velocity = 0.0
m.angular_velocity = 0.0
rate = rospy.Rate(25)
n = 40 if _do_pre else 16
for _ in range(n):
    pub.publish(m)
    rate.sleep()
print("joy_zero_done")
"""

        def work():
            try:
                ec, o, e = self._ssh_run_remote_python(py)
                self._agv_log_ssh_result("agv_joy_zero", ec, o, e)
            except Exception as ex:
                self._log(f"[ERR] SSH Joy 停车失败: {ex}")

        self._run_bg(work)

    def agv_ssh_cancel_move(self):
        self._agv_ssh_cancel_and_joy_zero("agv_cancel")

    def agv_ssh_unlock_chassis(self):
        self._agv_ssh_cancel_and_joy_zero("agv_unlock")

    def agv_ssh_fetch_driver_snippet(self):
        ws = (self.ssh_catkin_var.get().strip() or "/home/rm/catkin_ws")
        ws_q = shlex.quote(ws)
        bash = (
            f"WS={ws_q}; "
            'if [ -f "$WS/devel/setup.bash" ]; then . "$WS/devel/setup.bash"; fi; '
            'F=$(find "$WS/src" -name agv_driver.py -print 2>/dev/null | head -1); '
            'if [ -z "$F" ]; then echo "未找到 agv_driver.py，已搜索 $WS/src"; exit 0; fi; '
            'echo "=== FILE: $F ==="; '
            r'grep -nE "Subscriber|subscribe\(|Publisher|advertise\(|navigation_" "$F" 2>/dev/null | head -160 || true'
        )

        def work():
            try:
                ec, o, e = self._ssh_run_remote_bash(bash, timeout=120)
                self._agv_log_ssh_result("agv_driver_snip", ec, o, e)
            except Exception as ex:
                self._log(f"[ERR] SSH 摘录 agv_driver 失败: {ex}")

        self._run_bg(work)

    def agv_ssh_rostopic_navigation(self):
        ws = (self.ssh_catkin_var.get().strip() or "/home/rm/catkin_ws")
        ws_q = shlex.quote(ws)
        bash = (
            f"WS={ws_q}; "
            'if [ -f "$WS/devel/setup.bash" ]; then . "$WS/devel/setup.bash"; fi; '
            "rostopic list 2>/dev/null | grep -E 'navigation|agv' || "
            "echo '(无匹配、rostopic 失败或未连接 roscore，请确认主控已 launch)'; "
            "true"
        )

        def work():
            try:
                ec, o, e = self._ssh_run_remote_bash(bash, timeout=60)
                self._agv_log_ssh_result("agv_rostopic", ec, o, e)
            except Exception as ex:
                self._log(f"[ERR] SSH rostopic 失败: {ex}")

        self._run_bg(work)

    def agv_ssh_get_robot_status(self):
        py = """import rospy
from std_msgs.msg import String

rospy.init_node("hand_gui_agv_stat", anonymous=True)
got = []

def _fb(m):
    got.append(m.data)

sub = rospy.Subscriber("/navigation_feedback", String, _fb, queue_size=32)
pub = rospy.Publisher("/navigation_get_robot_status", String, queue_size=1)
rospy.sleep(0.55)
pub.publish(String())
deadline = rospy.Time.now() + rospy.Duration(3.0)
while rospy.Time.now() < deadline and not rospy.is_shutdown():
    rospy.sleep(0.05)
    if got:
        break
if not got:
    print("[WARN] 3s 内未收到 /navigation_feedback，请确认主控已 roslaunch agv_ros agv_start.launch，"
          "并可在终端执行: rostopic echo /navigation_feedback")
else:
    for i, s in enumerate(got):
        print("[feedback %d] %s" % (i, s))
"""

        def work():
            try:
                ec, o, e = self._ssh_run_remote_python(py)
                self._agv_log_ssh_result("agv_robot_status", ec, o, e)
            except Exception as ex:
                self._log(f"[ERR] SSH 查询状态失败: {ex}")

        self._run_bg(work)

    def agv_ssh_get_power_status(self):
        py = """import rospy
from std_msgs.msg import String

rospy.init_node("hand_gui_agv_pwr", anonymous=True)
got = []

def _fb(m):
    got.append(m.data)

sub = rospy.Subscriber("/navigation_feedback", String, _fb, queue_size=32)
pub = rospy.Publisher("/navigation_get_power_status", String, queue_size=1)
rospy.sleep(0.55)
pub.publish(String())
deadline = rospy.Time.now() + rospy.Duration(3.0)
while rospy.Time.now() < deadline and not rospy.is_shutdown():
    rospy.sleep(0.05)
    if got:
        break
if not got:
    print("[WARN] 3s 内未收到 /navigation_feedback（电量结果通常经此话题返回）")
else:
    for i, s in enumerate(got):
        print("[feedback %d] %s" % (i, s))
"""

        def work():
            try:
                ec, o, e = self._ssh_run_remote_python(py)
                self._agv_log_ssh_result("agv_power", ec, o, e)
            except Exception as ex:
                self._log(f"[ERR] SSH 查询电量失败: {ex}")

        self._run_bg(work)

    def agv_ssh_get_navigation_params(self):
        py = """import rospy
from std_msgs.msg import String

rospy.init_node("hand_gui_agv_params", anonymous=True)
got = []

def _fb(m):
    got.append(m.data)

sub = rospy.Subscriber("/navigation_feedback", String, _fb, queue_size=32)
pub = rospy.Publisher("/navigation_get_params", String, queue_size=1)
rospy.sleep(0.55)
pub.publish(String())
deadline = rospy.Time.now() + rospy.Duration(8.0)
while rospy.Time.now() < deadline and not rospy.is_shutdown():
    rospy.sleep(0.05)
    if got:
        break
if not got:
    print("[WARN] 8s 内未收到 /navigation_feedback（参数列表可能较长或底盘无响应）")
else:
    for i, s in enumerate(got):
        print("[feedback %d] %s" % (i, s))
"""

        def work():
            try:
                ec, o, e = self._ssh_run_remote_python(py)
                self._agv_log_ssh_result("agv_get_params", ec, o, e)
            except Exception as ex:
                self._log(f"[ERR] SSH 查询参数列表失败: {ex}")

        self._run_bg(work)

    def _agv_chassis_socket_host_port(self):
        host = self.agv_chassis_host_var.get().strip()
        if not host:
            self._log("[ERR] 水滴 TCP IP 不能为空")
            return None
        try:
            port = int(self.agv_chassis_port_var.get().strip())
        except ValueError:
            self._log("[ERR] 水滴 TCP 端口无效")
            return None
        if not (1 <= port <= 65535):
            self._log("[ERR] 水滴 TCP 端口超出范围")
            return None
        return host, port

    def _agv_ssh_chassis_send_api(self, api_line: str, log_tag: str):
        hp = self._agv_chassis_socket_host_port()
        if hp is None:
            return
        host, port = hp
        cmd_lit = repr(api_line)
        py = f"""import socket
host = {host!r}
port = {port}
cmd = {cmd_lit}
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(4.0)
s.connect((host, port))
s.send(cmd.encode("utf-8"))
buf = bytearray()
try:
    for _ in range(64):
        chunk = s.recv(4096)
        if not chunk:
            break
        buf += chunk
        if buf.count(10) >= 2 or len(buf) > 16000:
            break
except socket.timeout:
    pass
finally:
    s.close()
text = bytes(buf).decode("utf-8", "replace")
print("sent:", cmd)
print("recv_snip:", text[:12000])
"""

        def work():
            try:
                ec, o, e = self._ssh_run_remote_python(py)
                self._agv_log_ssh_result(log_tag, ec, o, e)
            except Exception as ex:
                self._log(f"[ERR] SSH {log_tag} 失败: {ex}")

        self._run_bg(work)

    def agv_ssh_soft_estop_clear(self):
        self._agv_ssh_chassis_send_api("/api/estop?flag=false", "agv_estop_clear")

    def agv_ssh_soft_estop_trigger(self):
        if not messagebox.askyesno(
            "软急停确认",
            "将向水滴 TCP 发送 /api/estop?flag=true（软件急停）。是否继续？",
        ):
            return
        self._agv_ssh_chassis_send_api("/api/estop?flag=true", "agv_estop_on")

    def agv_ssh_publish_soft_estop_ros(self):
        py = """import rospy
from std_msgs.msg import Bool
rospy.init_node("hand_gui_soft_estop_pub", anonymous=True)
pub = rospy.Publisher("/navigation_soft_estop", Bool, queue_size=1, latch=True)
rospy.sleep(0.5)
pub.publish(Bool(data=False))
rospy.sleep(0.15)
print("published /navigation_soft_estop data=False (需主控 agv_driver 已合并 /navigation_soft_estop 补丁)")
"""

        def work():
            try:
                ec, o, e = self._ssh_run_remote_python(py)
                self._agv_log_ssh_result("agv_soft_estop_ros", ec, o, e)
            except Exception as ex:
                self._log(f"[ERR] SSH ROS 软急停解除失败: {ex}")

        self._run_bg(work)

    def agv_ssh_go_marker(self):
        name = self.agv_marker_var.get().strip()
        if not name:
            self._log("[ERR] 标记点名不能为空")
            return
        lit = json.dumps(name, ensure_ascii=False)
        pre = bool(self.agv_auto_preunlock_var.get())
        py = f"""import rospy
from std_msgs.msg import String
from agv_ros.msg import navigation_joy_control
rospy.init_node("hand_gui_agv_marker", anonymous=True)
_do_pre = {pre!r}
pub_marker = rospy.Publisher("/navigation_marker", String, queue_size=1)
pub_joy = rospy.Publisher("/navigation_joy_control", navigation_joy_control, queue_size=10)
pub_cancel = rospy.Publisher("/navigation_move_cancel", String, queue_size=1)
rospy.sleep(0.5)
if _do_pre:
    pub_cancel.publish(String())
    rospy.sleep(0.35)
    z = navigation_joy_control()
    z.linear_velocity = 0.0
    z.angular_velocity = 0.0
    rz = rospy.Rate(25)
    for _ in range(30):
        pub_joy.publish(z)
        rz.sleep()
    rospy.sleep(0.15)
msg = String()
msg.data = {lit}
pub_marker.publish(msg)
rospy.sleep(0.2)
print("marker_sent", repr(msg.data))
"""

        def work():
            try:
                ec, o, e = self._ssh_run_remote_python(py)
                self._agv_log_ssh_result("agv_marker", ec, o, e)
            except Exception as ex:
                self._log(f"[ERR] SSH 标记导航失败: {ex}")

        self._run_bg(work)

    def _lift_comm_params(self) -> tuple[int, int, int]:
        if self.lift_use_separate_port_var.get():
            return (
                int(self.lift_modbus_port_var.get()),
                int(self.lift_baud_var.get()),
                int(self.lift_timeout_var.get()),
            )
        return (
            int(self.modbus_port_var.get()),
            int(self.baud_var.get()),
            int(self.timeout_var.get()),
        )

    @staticmethod
    def _parse_comma_data_bytes(s: str) -> list[int]:
        parts = [p.strip() for p in s.replace(";", ",").split(",") if p.strip()]
        out = []
        for p in parts:
            out.append(int(p, 0) & 0xFF)
        return out

    def lift_configure_modbus_only(self):
        def work():
            try:
                port, baud, to = self._lift_comm_params()
                cmd = {"command": "set_modbus_mode", "port": port, "baudrate": baud, "timeout": to}
                self._execute_action("lift_set_modbus", cmd, wait_ms=500, allow_retry=False)
            except Exception as e:
                self._log(f"[ERR] 升降口配置失败: {e}")

        self._run_bg(work)

    def _lift_write_registers(self, data: list[int], tag: str):
        if not self.client:
            raise RuntimeError("未连接控制器")
        port, baud, to = self._lift_comm_params()
        if self.lift_cfg_before_send_var.get():
            cmd0 = {"command": "set_modbus_mode", "port": port, "baudrate": baud, "timeout": to}
            self._execute_action(f"lift_{tag}_modbus", cmd0, wait_ms=500, allow_retry=False)
        cmd = {
            "command": "write_registers",
            "port": port,
            "address": int(self.lift_address_var.get()),
            "num": int(self.lift_num_var.get()),
            "data": data,
            "device": int(self.lift_device_var.get()),
        }
        self._execute_action(f"lift_{tag}_write", cmd, wait_ms=800, allow_retry=True)

    def send_lift_custom_write(self):
        def work():
            try:
                if not self.client:
                    self.root.after(0, lambda: messagebox.showwarning("升降机", "请先连接控制器"))
                    return
                data = self._parse_comma_data_bytes(self.lift_custom_data_var.get())
                if not data:
                    self.root.after(0, lambda: messagebox.showwarning("升降机", "自定义 data 为空"))
                    return
                self._lift_write_registers(data, "custom")
            except ValueError as e:
                self.root.after(0, lambda: messagebox.showerror("升降机", f"data 解析失败: {e}"))
            except Exception as e:
                self._log(f"[ERR] 升降自定义写入失败: {e}")

        self._run_bg(work)

    def send_lift_preset(self, which: str):
        labels = {"up": "升", "stop": "停", "down": "降"}
        varmap = {
            "up": self.lift_data_up_var,
            "stop": self.lift_data_stop_var,
            "down": self.lift_data_down_var,
        }
        var = varmap[which]

        def work():
            try:
                if not self.client:
                    self.root.after(0, lambda: messagebox.showwarning("升降机", "请先连接控制器"))
                    return
                raw = var.get().strip()
                if not raw:
                    self.root.after(
                        0,
                        lambda w=labels[which]: messagebox.showwarning(
                            "升降机", f"请先填写「{w}」对应的 data 字节（逗号分隔）"
                        ),
                    )
                    return
                data = self._parse_comma_data_bytes(raw)
                self._lift_write_registers(data, which)
            except ValueError as e:
                self.root.after(0, lambda: messagebox.showerror("升降机", f"data 解析失败: {e}"))
            except Exception as e:
                self._log(f"[ERR] 升降{labels[which]}失败: {e}")

        self._run_bg(work)

    def enable_hand(self):
        def work():
            try:
                if not self.client:
                    self._bg_warn_not_connected()
                    return
                cmd = self._hand_modbus_mode_cmd()
                self._execute_action("enable_hand", cmd, wait_ms=500, allow_retry=False)
            except Exception as e:
                self._log(f"[ERR] 使能失败: {e}")

        self._run_bg(work)

    def disable_hand(self):
        # Safe "disable" action: release hand and leave comm channel idle.
        def work():
            try:
                if not self.client:
                    self._bg_warn_not_connected()
                    return
                self._send_hand_modbus_if_needed()
                cmd = self._hand_write_registers_cmd([0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 255, 0])
                self._execute_action("disable_hand_release", cmd, wait_ms=700, allow_retry=True)
                self._log("[OK] 已执行安全释放（开手）")
            except Exception as e:
                self._log(f"[ERR] 失能失败: {e}")

        self._run_bg(work)

    def open_hand(self):
        def work():
            try:
                if not self.client:
                    self._bg_warn_not_connected()
                    return
                self._send_hand_modbus_if_needed()
                cmd = self._hand_write_registers_cmd([0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 255, 0])
                self._execute_action("open_hand", cmd, wait_ms=800, allow_retry=True)
            except Exception as e:
                self._log(f"[ERR] 开手失败: {e}")

        self._run_bg(work)

    def close_hand(self):
        def work():
            try:
                if not self.client:
                    self._bg_warn_not_connected()
                    return
                self._send_hand_modbus_if_needed()
                cmd = self._hand_write_registers_cmd(
                    [255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 0, 0]
                )
                self._execute_action("close_hand", cmd, wait_ms=800, allow_retry=True)
            except Exception as e:
                self._log(f"[ERR] 闭手失败: {e}")

        self._run_bg(work)

    def send_finger_channels(self):
        def work():
            try:
                if not self.client:
                    self._bg_warn_not_connected()
                    return
                mx = int(self.roh_channel_max)
                values = [
                    max(0, min(mx, self._safe_intvar_get(self.channel_vars[i], f"通道{i+1}")))
                    for i in range(6)
                ]
                # Pack 6 x uint16 little-endian：关节 0~255 放在低字节，高字节 0。
                data = []
                for val in values:
                    data.append(val & 0xFF)
                    data.append(0)
                self._send_hand_modbus_if_needed()
                cmd = self._hand_write_registers_cmd(data)
                self._execute_action("finger_channels", cmd, wait_ms=800, allow_retry=True)
            except Exception as e:
                self._log(f"[ERR] 分手指发送失败: {e}")

        self._run_bg(work)

    def _values_to_write_cmd(self, values: list[int]) -> dict:
        mx = int(self.roh_channel_max)
        data = []
        for val in values[:6]:
            v = max(0, min(mx, int(val)))
            data.append(v & 0xFF)
            data.append(0)
        return self._hand_write_registers_cmd(data)

    def _send_ramp_cmd_with_retry(self, cmd: dict, wait_ms: int) -> tuple[list, bool]:
        """同一渐进步内：先发一次，若 write_state=false 则延时再发一次。返回 (最后一次 resp, 是否成功)。"""
        if not self.client:
            return [], False
        resp = self.client.send_json(cmd, wait_ms=wait_ms)
        if not self._has_write_false(resp):
            return resp, True
        delay = max(0.12, min(1.0, int(self.retry_delay_ms_var.get()) / 1000.0))
        time.sleep(delay)
        resp2 = self.client.send_json(cmd, wait_ms=wait_ms)
        if not self._has_write_false(resp2):
            return resp2, True
        return resp2, False

    def _set_channel_sliders(self, values: list[int]):
        for i, v in enumerate(values[:6]):
            self.channel_vars[i].set(int(v))

    def start_progressive_grasp(self):
        if self.ramp_running:
            self._log("[WARN] 渐进收指已在运行")
            return
        if not self.client:
            self._log("[ERR] 请先连接控制器")
            return
        self.ramp_stop_event.clear()
        self.ramp_running = True
        self.btn_ramp_start.config(state="disabled")
        self.btn_ramp_stop.config(state="normal")
        self._run_bg(self._progressive_grasp_worker)

    def stop_progressive_grasp(self):
        self.ramp_stop_event.set()
        self._log("[OK] 已请求停止渐进收指")

    def _progressive_grasp_worker(self):
        try:
            mx = int(self.roh_channel_max)
            ch1 = max(0, min(mx, int(self.ramp_ch1_fix_var.get())))
            ch6 = max(0, min(mx, int(self.ramp_ch6_fix_var.get())))
            step = max(1, min(mx, int(self.ramp_step_var.get())))
            interval = max(50, int(self.ramp_interval_ms_var.get())) / 1000.0
            caps = [
                max(0, min(mx, int(self.ramp_max_ch2_var.get()))),
                max(0, min(mx, int(self.ramp_max_ch3_var.get()))),
                max(0, min(mx, int(self.ramp_max_ch4_var.get()))),
                max(0, min(mx, int(self.ramp_max_ch5_var.get()))),
            ]
            wait_ms = max(200, int(self.wait_ms_var.get()))
            # ch2~5 当前值
            fcur = [0, 0, 0, 0]
            fail_streak = 0
            iteration = 0
            self._log(
                f"[RAMP] 开始: 固定ch1={ch1} ch6={ch6}；ch2~5 从0递增，"
                f"各自上限(食/中/无/小)={caps}，步长={step}，间隔={interval*1000:.0f}ms"
            )
            try:
                self._send_hand_modbus_if_needed("HAND-MODBUS-RAMP")
            except Exception as e:
                self._log(f"[WARN] [RAMP] set_modbus_mode: {e}")
            while not self.ramp_stop_event.is_set():
                if not self.client:
                    self._log("[RAMP] 连接已断开，停止")
                    break
                if all(fcur[i] >= caps[i] for i in range(4)):
                    self._log(f"[RAMP] 四指均已达到各自上限 {caps}，正常结束")
                    break
                nxt = []
                for i in range(4):
                    c, cap = fcur[i], caps[i]
                    if c >= cap:
                        nxt.append(c)
                    else:
                        nxt.append(min(cap, c + step))
                if nxt == fcur:
                    break
                fcur = nxt
                c2, c3, c4, c5 = fcur
                vals = [ch1, c2, c3, c4, c5, ch6]
                cmd = self._values_to_write_cmd(vals)
                iteration += 1
                try:
                    resp, ok = self._send_ramp_cmd_with_retry(cmd, wait_ms)
                    if iteration == 1 or iteration % 8 == 0:
                        self._log("[SEND] [RAMP] " + json.dumps(cmd, ensure_ascii=False))
                        for item in resp:
                            if isinstance(item, dict):
                                self._log("[RESP] [RAMP] " + json.dumps(item, ensure_ascii=False))
                    if not ok:
                        fail_streak += 1
                        lim = int(self.ramp_false_streak_limit)
                        self._log(
                            f"[WARN] [RAMP] 本步重试后仍失败（连续失败步 {fail_streak}/{lim}）"
                        )
                        if self.ramp_stop_on_false_var.get() and fail_streak >= lim:
                            self._log("[RAMP] 已达失败步数上限，停止")
                            break
                    else:
                        fail_streak = 0
                except Exception as e:
                    fail_streak += 1
                    self._log(f"[ERR] [RAMP] 发送异常: {e}")
                    lim = int(self.ramp_false_streak_limit)
                    if self.ramp_stop_on_false_var.get() and fail_streak >= lim:
                        break
                self.root.after(0, lambda v=list(vals): self._set_channel_sliders(v))
                time.sleep(interval)
            self._log("[RAMP] 渐进收指线程结束")
        finally:
            self.ramp_running = False
            self.root.after(0, lambda: self.btn_ramp_start.config(state="normal"))
            self.root.after(0, lambda: self.btn_ramp_stop.config(state="disabled"))

    def send_raw_json(self):
        def work():
            try:
                txt = self.raw_text.get("1.0", "end").strip()
                if not txt:
                    return
                payload = json.loads(txt)
                if not isinstance(payload, dict):
                    raise ValueError("JSON 顶层必须是对象")
                self._execute_action("raw_json", payload, wait_ms=self.wait_ms_var.get(), allow_retry=True)
            except Exception as e:
                self._log(f"[ERR] 自定义JSON失败: {e}")

        self._run_bg(work)

    def start_recording(self):
        self.recording_enabled = True
        self._log("[REC] 开始录制动作序列")

    def stop_recording(self):
        self.recording_enabled = False
        self._log(f"[REC] 停止录制，当前共 {len(self.recorded_actions)} 步")

    def clear_recording(self):
        self.recorded_actions = []
        self._log("[REC] 已清空动作序列")

    def replay_recording(self):
        def work():
            try:
                if not self.recorded_actions:
                    self._log("[REC] 无可回放动作")
                    return
                if not self.client:
                    self._bg_warn_not_connected()
                    return
                try:
                    self._send_hand_modbus_if_needed("HAND-MODBUS-REPLAY")
                except Exception as e:
                    self._log(f"[WARN] [REC] 回放前 set_modbus_mode: {e}")
                interval = max(100, int(self.replay_interval_ms_var.get()))
                count = max(1, int(self.replay_count_var.get()))
                was_recording = self.recording_enabled
                self.recording_enabled = False
                self._log(f"[REC] 开始回放，步数={len(self.recorded_actions)}，次数={count}")
                for idx_loop in range(count):
                    self._log(f"[REC] 回放轮次 {idx_loop + 1}/{count}")
                    for idx, action in enumerate(self.recorded_actions, 1):
                        name = action.get("name", f"step_{idx}")
                        payload = action.get("payload", {})
                        wait_ms = int(action.get("wait_ms", self.wait_ms_var.get()))
                        self._log(f"[REC] 回放第{idx}步: {name}")
                        self._send_and_log(payload, wait_ms=wait_ms)
                        threading.Event().wait(interval / 1000.0)
                self._log("[REC] 回放结束")
                self.recording_enabled = was_recording
            except Exception as e:
                self._log(f"[ERR] 回放失败: {e}")

        self._run_bg(work)

    def save_recorded_sequence_json(self):
        if not self.recorded_actions:
            messagebox.showinfo("保存", "当前序列为空，无可保存内容。")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("全部", "*.*")],
            title="保存动作序列",
        )
        if not path:
            return
        try:
            data = {
                "version": 1,
                "description": "hand_debug_gui_ssh_jump 动作序列",
                "actions": self.recorded_actions,
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self._log(f"[REC] 已保存 {len(self.recorded_actions)} 步到 {path}")
        except OSError as e:
            messagebox.showerror("保存失败", str(e))

    def load_recorded_sequence_json(self):
        path = filedialog.askopenfilename(
            filetypes=[("JSON", "*.json"), ("全部", "*.*")],
            title="加载动作序列",
        )
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            actions = data.get("actions")
            if not isinstance(actions, list):
                raise ValueError("文件缺少有效的 actions 数组")
            for i, a in enumerate(actions):
                if not isinstance(a, dict):
                    raise ValueError(f"第 {i + 1} 步不是对象")
                if "payload" not in a:
                    raise ValueError(f"第 {i + 1} 步缺少 payload")
            self.recorded_actions = actions
            self._log(f"[REC] 已从 {path} 加载 {len(actions)} 步")
        except (OSError, json.JSONDecodeError, ValueError) as e:
            messagebox.showerror("加载失败", str(e))

    @staticmethod
    def _clamp01(x: float) -> float:
        return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x

    @staticmethod
    def _map01_to_u8(v: float) -> int:
        return int(round(HandDebugGUI._clamp01(v) * 255))

    @staticmethod
    def _vec3(a, b):
        return (a.x - b.x, a.y - b.y, a.z - b.z)

    @staticmethod
    def _norm3(v) -> float:
        return (v[0] ** 2 + v[1] ** 2 + v[2] ** 2) ** 0.5

    @staticmethod
    def _palm_center(lm):
        ids = (0, 5, 9, 13, 17)
        x = sum(lm[i].x for i in ids) / len(ids)
        y = sum(lm[i].y for i in ids) / len(ids)
        z = sum(lm[i].z for i in ids) / len(ids)
        return x, y, z

    def _tip_flex(self, lm, tip_id: int) -> float:
        cx, cy, cz = self._palm_center(lm)
        c = type("P", (), {"x": cx, "y": cy, "z": cz})
        palm_scale = self._norm3(self._vec3(lm[9], lm[0])) + 1e-6
        tip_dist = self._norm3(self._vec3(lm[tip_id], c)) / palm_scale
        open_ratio = self._clamp01((tip_dist - 0.65) / (1.55 - 0.65))
        return 1.0 - open_ratio

    def _landmarks_to_channels(self, lm):
        # 与硬件通道一致: 1拇指尖 2~5四指 6拇指根部(MCP 附近，启发式)
        thumb_tip = self._tip_flex(lm, 4)
        index = self._tip_flex(lm, 8)
        middle = self._tip_flex(lm, 12)
        ring = self._tip_flex(lm, 16)
        little = self._tip_flex(lm, 20)
        thumb_root = self._tip_flex(lm, 2)
        return [
            self._map01_to_u8(thumb_tip),
            self._map01_to_u8(index),
            self._map01_to_u8(middle),
            self._map01_to_u8(ring),
            self._map01_to_u8(little),
            self._map01_to_u8(thumb_root),
        ]

    def _smooth_channels(self, raw_channels: list[int]) -> list[int]:
        if self._gesture_ema_channels is None:
            self._gesture_ema_channels = [float(v) for v in raw_channels]
            return list(raw_channels)
        out = []
        a = max(0.01, min(0.95, float(self.gesture_ema_alpha)))
        for i, v in enumerate(raw_channels):
            prev = self._gesture_ema_channels[i]
            cur = prev * (1.0 - a) + float(v) * a
            self._gesture_ema_channels[i] = cur
            out.append(int(round(cur)))
        return out

    def _apply_deadband(self, channels: list[int]) -> list[int]:
        if self._gesture_last_sent_channels is None:
            return channels
        db = max(0, int(self.gesture_deadband_u8))
        out = []
        for i, v in enumerate(channels):
            base = self._gesture_last_sent_channels[i]
            if abs(v - base) < db:
                out.append(base)
            else:
                out.append(v)
        return out

    def _apply_roh_range_calibration(self, channels: list[int]) -> list[int]:
        """
        Remap gesture channels to ROH-LiteS001 target ranges using two-point calibration.
        Open target: [拇指开目标, 0,0,0,0, 255]（ch6 根部与开手预设一致为满量程）
        Close target: [255,255,255,255,255, 0]（ch6 闭手为 0）
        """
        if self._gesture_open_ref is None or self._gesture_close_ref is None:
            return channels

        mx = int(self.roh_channel_max)
        open_target = [int(self.roh_thumb_open_target_u8), 0, 0, 0, 0, mx]
        close_target = [mx, mx, mx, mx, mx, 0]
        out = []
        for i, v in enumerate(channels):
            o = float(self._gesture_open_ref[i])
            c = float(self._gesture_close_ref[i])
            if abs(c - o) < 1e-6:
                t = 0.0
            else:
                t = (float(v) - o) / (c - o)
            t = self._clamp01(t)
            mapped = int(round(open_target[i] + t * (close_target[i] - open_target[i])))
            out.append(max(0, min(mx, mapped)))
        return out

    def _send_channels_quiet(self, channels: list[int]):
        if not self.client:
            return
        data = []
        mx = int(self.roh_channel_max)
        for val in channels:
            v = max(0, min(mx, int(val)))
            data.append(v & 0xFF)
            data.append(0)
        try:
            cmd = self._hand_write_registers_cmd(data)
            resp = self.client.send_json(cmd, wait_ms=120)
            if any(isinstance(x, dict) and x.get("write_state") is False for x in resp):
                self._log("[WARN] 手势控制下发 write_state=false")
        except Exception as e:
            self._log(f"[ERR] 手势控制下发失败: {e}")

    def toggle_gesture_control(self):
        if self.gesture_running:
            self.stop_gesture_control()
        else:
            self.start_gesture_control()

    def capture_gesture_open_ref(self):
        if self._gesture_last_channels is None:
            self._log("[WARN] 当前无手势通道数据，无法记录开手标定")
            return
        self._gesture_open_ref = list(self._gesture_last_channels)
        self._gesture_last_sent_channels = None
        self._log(f"[OK] 已记录开手标定: {self._gesture_open_ref}")

    def capture_gesture_close_ref(self):
        if self._gesture_last_channels is None:
            self._log("[WARN] 当前无手势通道数据，无法记录闭手标定")
            return
        self._gesture_close_ref = list(self._gesture_last_channels)
        self._gesture_last_sent_channels = None
        self._log(f"[OK] 已记录闭手标定: {self._gesture_close_ref}")

    def reset_gesture_calibration(self):
        self._gesture_open_ref = None
        self._gesture_close_ref = None
        self._gesture_last_sent_channels = None
        self._log("[OK] 已重置手势开/闭标定")

    def apply_thumb_open_target(self):
        try:
            mx = int(self.roh_channel_max)
            v = int(self.roh_thumb_open_target_var.get())
            v = max(0, min(mx, v))
            self.roh_thumb_open_target_u8 = v
            self.roh_thumb_open_target_var.set(v)
            self._gesture_last_sent_channels = None
            self._log(f"[OK] 已应用拇指开手目标: {v}")
        except Exception as e:
            self._log(f"[ERR] 拇指开手目标设置失败: {e}")

    def start_gesture_control(self):
        if self.gesture_running:
            return
        self.gesture_stop_event.clear()
        self.gesture_running = True
        self._gesture_ema_channels = None
        self._gesture_last_sent_channels = None
        self._gesture_last_channels = None
        self.btn_gesture.config(text="关闭手势控制")
        self._log("[OK] 手势控制已开启，弹出相机窗口调试")
        self.gesture_thread = threading.Thread(target=self._gesture_loop, daemon=True)
        self.gesture_thread.start()

    def stop_gesture_control(self):
        self.gesture_stop_event.set()
        self.gesture_running = False
        self.btn_gesture.config(text="开启手势控制")
        self._log("[OK] 手势控制已关闭")

    def _gesture_loop(self):
        try:
            import cv2
            import mediapipe as mp
        except Exception as e:
            self._log(f"[ERR] 手势控制依赖缺失: {e}")
            self.root.after(0, self.stop_gesture_control)
            return

        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            self._log("[ERR] 无法打开摄像头")
            self.root.after(0, self.stop_gesture_control)
            return

        hand_connections = [
            (0, 1), (1, 2), (2, 3), (3, 4),
            (0, 5), (5, 6), (6, 7), (7, 8),
            (5, 9), (9, 10), (10, 11), (11, 12),
            (9, 13), (13, 14), (14, 15), (15, 16),
            (13, 17), (17, 18), (18, 19), (19, 20),
            (0, 17),
        ]

        def draw_manual(frame, lm):
            h, w = frame.shape[:2]
            pts = []
            for p in lm:
                x = int(max(0, min(w - 1, p.x * w)))
                y = int(max(0, min(h - 1, p.y * h)))
                pts.append((x, y))
                cv2.circle(frame, (x, y), 3, (0, 0, 255), -1)
            for a, b in hand_connections:
                cv2.line(frame, pts[a], pts[b], (0, 255, 0), 2)

        runtime_mode = "unknown"
        mp_hands = None
        mp_drawing = None
        mp_tasks_python = None
        mp_tasks_vision = None
        hand_detector = None

        try:
            if hasattr(mp, "solutions") and hasattr(mp.solutions, "hands"):
                runtime_mode = "solutions"
                mp_hands = mp.solutions.hands
                mp_drawing = mp.solutions.drawing_utils
                hand_detector = mp_hands.Hands(
                    model_complexity=1,
                    max_num_hands=1,
                    min_detection_confidence=0.6,
                    min_tracking_confidence=0.6,
                )
            else:
                runtime_mode = "tasks"
                from mediapipe.tasks import python as mp_tasks_python  # type: ignore
                from mediapipe.tasks.python import vision as mp_tasks_vision  # type: ignore
                options = mp_tasks_vision.HandLandmarkerOptions(
                    base_options=mp_tasks_python.BaseOptions(
                        model_asset_path="Vision2DexterousHand/models/hand_landmarker.task"
                    ),
                    running_mode=mp_tasks_vision.RunningMode.VIDEO,
                    num_hands=1,
                    min_hand_detection_confidence=0.6,
                    min_hand_presence_confidence=0.6,
                    min_tracking_confidence=0.6,
                )
                hand_detector = mp_tasks_vision.HandLandmarker.create_from_options(options)
            self._log(f"[OK] 手势控制运行时: {runtime_mode}")
        except Exception as e:
            self._log(f"[ERR] 手势控制初始化失败: {e}")
            cap.release()
            self.root.after(0, self.stop_gesture_control)
            return

        if self.client:
            try:
                self._send_hand_modbus_if_needed("HAND-MODBUS-GESTURE")
            except Exception as e:
                self._log(f"[WARN] 手势前 set_modbus_mode: {e}")

        try:
            while not self.gesture_stop_event.is_set():
                ok, frame = cap.read()
                if not ok:
                    continue
                frame = cv2.flip(frame, 1)
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                landmarks = None

                if runtime_mode == "solutions":
                    results = hand_detector.process(rgb)
                    if results.multi_hand_landmarks:
                        landmarks = results.multi_hand_landmarks[0].landmark
                        mp_drawing.draw_landmarks(
                            frame, results.multi_hand_landmarks[0], mp_hands.HAND_CONNECTIONS
                        )
                else:
                    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                    ts_ms = int(time.time() * 1000)
                    results = hand_detector.detect_for_video(mp_image, ts_ms)
                    if results.hand_landmarks:
                        landmarks = results.hand_landmarks[0]
                        draw_manual(frame, landmarks)

                if landmarks is not None:
                    raw_channels = self._landmarks_to_channels(landmarks)
                    channels = self._smooth_channels(raw_channels)
                    self._gesture_last_channels = list(channels)
                    channels = self._apply_roh_range_calibration(channels)
                    channels = self._apply_deadband(channels)
                    cv2.putText(
                        frame, f"6D: {channels}", (12, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (60, 255, 255), 2
                    )
                    cal_status = "CAL:READY" if (self._gesture_open_ref is not None and self._gesture_close_ref is not None) else "CAL:WAIT"
                    cv2.putText(
                        frame,
                        f"{cal_status} (open/close)",
                        (12, 58),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.56,
                        (120, 230, 120),
                        1,
                    )
                    cv2.putText(
                        frame,
                        f"ThumbOpenTarget: {int(self.roh_thumb_open_target_u8)}",
                        (12, 82),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.56,
                        (130, 210, 255),
                        1,
                    )
                    for i, val in enumerate(channels):
                        self.root.after(0, lambda idx=i, v=val: self.channel_vars[idx].set(v))

                    now = time.time()
                    if now - self._last_gesture_send_ts >= 0.08:
                        need_send = False
                        if self._gesture_last_sent_channels is None:
                            need_send = True
                        else:
                            delta = max(
                                abs(channels[i] - self._gesture_last_sent_channels[i]) for i in range(6)
                            )
                            need_send = delta >= int(self.gesture_send_min_delta_u8)
                        if need_send:
                            self._send_channels_quiet(channels)
                            self._gesture_last_sent_channels = list(channels)
                            self._last_gesture_send_ts = now
                else:
                    cv2.putText(
                        frame,
                        "No hand detected",
                        (12, 32),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.62,
                        (180, 180, 255),
                        2,
                    )

                cv2.putText(
                    frame,
                    "Press q in this window to stop",
                    (12, 60),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.58,
                    (200, 220, 120),
                    1,
                )
                cv2.imshow("Gesture Control Debug View", frame)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q") or key == 27:
                    break
        finally:
            try:
                if runtime_mode == "solutions" and hand_detector is not None:
                    hand_detector.close()
                if runtime_mode == "tasks" and hand_detector is not None:
                    hand_detector.close()
            except Exception:
                pass
            cap.release()
            cv2.destroyAllWindows()
            self.root.after(0, self.stop_gesture_control)


def main():
    root = tk.Tk()
    app = HandDebugGUI(root)
    root.protocol("WM_DELETE_WINDOW", lambda: on_close(app, root))
    root.mainloop()


def on_close(app: HandDebugGUI, root: tk.Tk):
    app.stop_progressive_grasp()
    app.stop_gesture_control()
    if app.client:
        app.client.close()
    root.destroy()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        messagebox.showerror("启动失败", str(exc))
