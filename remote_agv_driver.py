#!/usr/bin/env python3
# -*- coding=UTF-8 -*-
"""
版权所有 (c) 2024 [睿尔曼智能科技有限公司]。保留所有权利。
作者: Robert 时间: 2024/07/20

在满足以下条件的情况下，允许重新分发和使用源代码和二进制形式的代码，无论是否修改：
1. 重新分发的源代码必须保留上述版权声明、此条件列表和以下免责声明。
2. 以二进制形式重新分发的代码必须在随分发提供的文档和/或其他材料中复制上述版权声明、此条件列表和以下免责声明。

本软件由版权持有者和贡献者“按原样”提供，不提供任何明示或暗示的保证，
包括但不限于对适销性和特定用途适用性的暗示保证。
在任何情况下，即使被告知可能发生此类损害的情况下，
版权持有者或贡献者也不对任何直接的、间接的、偶然的、特殊的、惩罚性的或后果性的损害
（包括但不限于替代商品或服务的采购；使用、数据或利润的损失；或业务中断）负责，
无论是基于合同责任、严格责任还是侵权行为（包括疏忽或其他原因）。

此模块允许AGV底盘各个话题订阅者的回调函数直接调用，方便后续ROS节点相应功能的开发。

此模块提供[
    单目标预设点导航、单目标点位导航、多目标预设点导航、导航取消、获取机器人全局状态、
    矫正机器人位姿、获取AGV底盘电量、速度控制、各项最大速度设置、获取参数值、底盘RGB三色灯设置、
    水滴软急停(/api/estop，话题/navigation_soft_estop)
]的功能。这些功能皆根据函数的形式封装，等待ROS话题订阅者调用。可理解为ROS与底盘的桥梁，扮演驱动的角色。

示例用法：
rospy.init_node('process', anonymous=True)
rospy.Subscriber("/navigation_marker", String, 
                 callback_navigation_marker)
其他函数功能使用方式类似。
"""

import socket
import threading
import rospy
from std_msgs.msg import String
from std_msgs.msg import Float64
from std_msgs.msg import Bool
from agv_ros.msg import navigation_location  
from agv_ros.msg import navigation_joy_control
from agv_ros.msg import navigation_led_set_color

# 与 __main__ 中一致；重连时使用
chassis_host = "192.168.10.10"
chassis_port = 31001
chassis_client = None
_chassis_sock_lock = threading.Lock()


def _open_chassis_socket_unlocked():
    """在已持有 _chassis_sock_lock 时调用，或启动阶段单线程调用。"""
    global chassis_client
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((chassis_host, chassis_port))
    chassis_client = s


def chassis_send(move_command: str):
    """发往水滴 TCP；Broken pipe 时自动关闭并重连后再试一次。

    注意：若另有程序（如调试脚本）同时直连同一 31001，服务端可能踢掉本连接，
    导致此处重连；尽量避免与 agv_driver 并行占线。
    """
    global chassis_client
    payload = move_command.encode("utf-8")
    with _chassis_sock_lock:
        for attempt in range(2):
            try:
                if chassis_client is None:
                    _open_chassis_socket_unlocked()
                chassis_client.send(payload)
                return
            except (BrokenPipeError, ConnectionResetError, OSError) as ex:
                rospy.logwarn("chassis send failed (%s), reconnect attempt %d" % (ex, attempt + 1))
                try:
                    if chassis_client is not None:
                        chassis_client.close()
                except Exception:
                    pass
                chassis_client = None
                try:
                    _open_chassis_socket_unlocked()
                except Exception as ex2:
                    rospy.logerr("chassis reconnect failed: %s" % ex2)
                    return


def callback_navigation_marker(data: String):
    """话题/navigation_marker的回调函数

    订阅话题/navigation_marker的String类型数据，并将得到的结果发送至AGV，从而达到单点导航

    Args:
        data (String): ROS中std_msgs.msg下的String数据类型，通过订阅话题而监听到的数据
    """
 
    rospy.loginfo(f"callback_navigation_marker Received letter: { data.data }")
    target_marker = data.data
    # 主控原版误写为 marke r=（中间有空格），会导致单点导航 API 异常；已改为 marker=
    move_command = f'/api/move?marker={target_marker}'    # /api/move?marker={point_A}
    chassis_send(move_command)
    rospy.loginfo(f"send chassis_client :/api/move?marker = { target_marker }")


def callback_navigation_location(data: navigation_location):
    """话题/navigation_location的回调函数

    订阅话题/navigation_location的navigation_location类型数据，并将得到的结果发送至AGV，从而导航至指定坐标位置
 
    Args:
        data (navigation_location): ROS中agv.msg下的navigation_location数据类型，通过订阅话题而监听到的数据
    """
 
    rospy.loginfo(f"callback_navigation_location Received letter: { data }")
    target_x_y_theta = f'{ data.x }, { data.y }, { data.theta }'    # /api/move?location=(15.0,4.0,1.5707963)
    move_command = f'/api/move?location={ target_x_y_theta }'    # 移动至location(15.0, 4.0, Pai/2)的目标点
    chassis_send(move_command)
    rospy.loginfo(f"send chassis_client :/api/move?location={ target_x_y_theta }")   
    

def callback_navigation_multipoint(data: String):
    """话题/navigation_multipoint的回调函数
 
    订阅话题/navigation_multipoint的String类型数据，并将得到的结果发送至AGV，从而实现多目标点移动功能

    Args:
        data (String): ROS中std_msgs.msg下的String数据类型，通过订阅话题而监听到的数据
    """
 
    rospy.loginfo(f"callback_navigation_multipoint Received letter: {data.data}") 
    target_multipoint_marker = data.data    # m1,m2,m3   /api/move?markers=m1,m2,m3&distance_tolerance=1.0&count=-1
    move_command = f'/api/move?markers={ target_multipoint_marker }\
        &distance_tolerance=0.5&count=1'   # 调用移动接口，移动至location(15.0, 4.0, pai/2)的目标点
    chassis_send(move_command)
    rospy.loginfo(f"send chassis_client :/api/move?markers={ target_multipoint_marker }\
        &distance_tolerance=0.5&count=1")   


def callback_navigation_move_cancel(data: String):
    """话题/navigation_move_cancel的回调函数

    通过订阅/navigation_move_cancel去取消当前正在进行的移动指令，停止规划器的工作

    Args:
        data (String): ROS中std_msgs.msg下的String数据类型，通过订阅话题而监听到的数据
    """
 
    rospy.loginfo(f"callback_navigation_move_cancel Received letter: { data.data }")
    move_command = f'/api/move/cancel'    # /api/move/cancel 停止导航
    chassis_send(move_command)
    rospy.loginfo(f"send chassis_client :/api/move/cancel0&count=-1")   


def callback_navigation_soft_estop(data: Bool):
    """话题/navigation_soft_estop：对应水滴手册 /api/estop?flag=true|false（软件急停）。"""
    rospy.loginfo(f"callback_navigation_soft_estop data={data.data}")
    flag = "true" if data.data else "false"
    move_command = f"/api/estop?flag={flag}"
    chassis_send(move_command)
    rospy.loginfo(f"send chassis_client :{move_command}")


def callback_get_robot_status(data: String):
    """话题/navigation_get_robot_status的回调函数

    通过订阅/navigation_get_robot_status去获取AGV底盘当前全局状态

    Args:
        data (String): ROS中std_msgs.msg下的String数据类型，通过订阅话题而监听到的数据
    """
    
    rospy.loginfo(f"callback_get_robot_status Received letter: { data.data }")
    move_command = f'/api/robot_status'    # /api/robot_status 获得机器人当前的全局状态
    chassis_send(move_command)
    rospy.loginfo(f"send chassis_client :/api/robot_status")   
    
    
def callback_navigation_position_adjust_marker(data: String):
    """话题/navigation_position_adjust_marker的回调函数

    通过订阅/navigation_position_adjust_marker话题去矫正AGV底盘运动位姿
 
    Args:
        data (String): ROS中std_msgs.msg下的String数据类型，通过订阅话题而监听到的数据
    """
    
    rospy.loginfo(f"callback_navigation_position_adjust_marker Received letter: { data.data }")
    target_marker = data.data    # 指定marker校正机器人位置
    move_command = f'/api/position_adjust?marker={ target_marker }'   # 告知机器人当前处于代号为001的marker点位上
    chassis_send(move_command)
    rospy.loginfo(f"send chassis_client :/api/position_adjust?marker={ target_marker }")


def callback_get_power_status(data: String):
    """话题/navigation_get_power_status的回调函数

    通过订阅/navigation_get_power_status话题去获取AGV底盘电量
 
    Args:
        data (String): ROS中std_msgs.msg下的String数据类型，通过订阅话题而监听到的数据
    """

    rospy.loginfo(f"callback_get_power_status Received letter: { data.data }")
    move_command = f'/api/get_power_status'    # /api/get_power_status 获取底盘电量
    chassis_send(move_command)
    rospy.loginfo(f"send chassis_client :/api/get_power_status")   

    
def callback_navigation_joy_control(data: navigation_joy_control):
    """话题/navigation_joy_control的回调函数

    通过订阅/navigation_joy_control话题去直接控制基于机器全局坐标系的x, y轴上的速度变化
    
    Args:
        data (navigation_joy_control): ROS中agv.msg下的navigation_joy_control数据类型，通过订阅话题而监听到的数据
    """
    
    rospy.loginfo(f"callback_navigation_joy_control Received letter: { data }")  # 机器人直接控制指令，可以执行左传右转前进后退等
    angular_velocity = data.angular_velocity   # 机器人角速度设值范围为 (-1.0 ~ 1.0)rad/s  正 机器人原地左转;负 机器人原地右转
    linear_velocity = data.linear_velocity    # 机器人线速度设值范围为 (-0.5 ~ 0.5)m/s  正  机器人前进 ;负 机器人后退
    move_command = f'/api/joy_control?angular_velocity={ angular_velocity }&linear_velocity={ linear_velocity }'  
    chassis_send(move_command)
    rospy.loginfo(f"send chassis_client :/api/joy_control?angular_velocity={ angular_velocity }\
        &linear_velocity={ linear_velocity }")   


def callback_navigation_max_speed(data: Float64):
    """话题/navigation_max_speed的回调函数

    通过订阅/navigation_max_speed话题去直接设置AGV最大行进速度，范围[0.3, 0.7]
 
    Args:
        data (Float64): ROS中std_msgs.msg下的Float64数据类型，通过订阅话题而监听到的数据
    """
    
    rospy.loginfo(f"callback_navigation_max_speed Received letter: { data.data }")
    target = data.data
    move_command = f'/api/set_params?max_speed={ target }'   # 机器人最大行进速度(百分比) 取值范围[0.3,0.7]
    chassis_send(move_command)
    rospy.loginfo(f"send chassis_client :/api/set_params?max_speed={ target }")


def callback_navigation_max_speed_ratio(data: Float64):
    """话题/navigation_max_speed_ratio的回调函数

    通过订阅/navigation_max_speed_ratio话题去直接设置AGV最大行进速度比，范围[0.3, 1.4] 
 
    Args:
        data (Float64): ROS中std_msgs.msg下的Float64数据类型，通过订阅话题而监听到的数据
    """

    rospy.loginfo(f"callback_navigation_max_speed_ratio Received letter: { data.data }")
    target = data.data
    move_command = f'/api/set_params?max_speed_ratio={ target }'    # 机器人最大行进速度百分比 取值范围[0.3,1.4]
    chassis_send(move_command)
    rospy.loginfo(f"send chassis_client :/api/set_params?max_speed_ratio={ target }")
 

def callback_navigation_max_speed_linear(data: Float64):
    """话题/navigation_max_speed_linear的回调函数
    
    通过订阅/navigation_max_speed_linear话题去直接设置AGV最大线速度(m/s)，范围[0.1, 1.0] 
    
    Args:
        data (Float64): ROS中std_msgs.msg下的Float64数据类型，通过订阅话题而监听到的数据
    """

    rospy.loginfo(f"callback_navigation_max_speed_linear Received letter: { data.data }")
    target = data.data
    move_command = f'/api/set_params?max_speed_linear={ target }'    # 机器人最大线速度(m/s) 取值范围[0.1, 1.0]
    chassis_send(move_command)
    rospy.loginfo(f"send chassis_client :/api/set_params?max_speed_linear={ target }")
 

def callback_navigation_max_speed_angular(data: Float64):
    """话题/navigation_max_speed_angular的回调函数
 
    通过订阅/navigation_max_speed_angular话题去直接设置AGV最大角速度(rad/s)，范围[0.5,3.5] 

    Args:
        data (Float64): ROS中std_msgs.msg下的Float64数据类型，通过订阅话题而监听到的数据
    """

    rospy.loginfo(f"callback_navigation_max_speed_angular Received letter: { data.data }")
    target = data.data
    move_command = f'/api/set_params?max_speed_angular={ target }'    # 机器人最大角速度(rad/s) 取值范围[0.5,3.5] 
    chassis_send(move_command)
    rospy.loginfo(f"send chassis_client :/api/set_params?max_speed_angular={ target }")
 
 
def callback_navigation_get_params(data: String):
    """话题/navigation_get_params的回调函数

    通过订阅/navigation_get_params话题去获取参数列表和当前值
 
    Args:
        data (String): ROS中std_msgs.msg下的String数据类型，通过订阅话题而监听到的数据
    """
    
    rospy.loginfo(f"callback_navigation_get_params Received letter: { data.data }") 
    move_command = f'/api/get_params'    # 获取参数列表和当前值
    chassis_send(move_command)
    rospy.loginfo(f"send chassis_client :/api/get_params")    
 
 
def callback_navigation_led_set_color(data: navigation_led_set_color):
    """话题/navigation_led_set_color的回调函数
 
    通过订阅/navigation_led_set_color话题设置灯带颜色，RGB三通道数据范围皆是[0, 100]

    Args:
        data (navigation_led_set_color): ROS中agv.msg下的navigation_led_set_color数据类型，
          通过订阅话题而监听到的数据
    """
    
    rospy.loginfo(f"callback_navigation_led_set_color Received letter: {data}") 
    set_color_R = data.r
    set_color_G = data.g
    set_color_B = data.b
    move_command = f'/api/LED/set_color?r={ set_color_R }\
         &g={set_color_G}&b={set_color_B}'  # 设置灯带颜色为绿色  /api/LED/set_color?r=100&g=100&b=0
    chassis_send(move_command)
    rospy.loginfo(f"send chassis_client :/api/LED/set_color?r={ set_color_R }\
        &g={ set_color_G }&b={ set_color_B }")


if __name__ == '__main__':
    chassis_host = '192.168.10.10'    # 设置IP，本机电脑设置IP为：192.168.10.xxx(其中xxx不为10)
    chassis_port = 31001    # 设置端口号
    _open_chassis_socket_unlocked()

    print('---------------------------final----------------------------')

    rospy.init_node('process', anonymous=True)
    rospy.Subscriber("/navigation_marker", String, 
                     callback_navigation_marker)   # 单目标点移动,移动至代号为"target_name"的目标点
    rospy.Subscriber("/navigation_location", navigation_location, 
                     callback_navigation_location)    # 单目标点移动,移动至location(15.0, 4.0, pai/2)的目标点
    rospy.Subscriber("/navigation_multipoint", String, 
                     callback_navigation_multipoint)    # 多目标点移动
    rospy.Subscriber("/navigation_move_cancel", String, 
                     callback_navigation_move_cancel)    # 取消当前正在进行的移动指令
    rospy.Subscriber("/navigation_soft_estop", Bool,
                     callback_navigation_soft_estop)    # 软急停：true=触发 false=解除（水滴 /api/estop）
    rospy.Subscriber("/navigation_get_robot_status", String, 
                     callback_get_robot_status)    # 获取机器人当前全局状态
    rospy.Subscriber("/navigation_position_adjust_marker", String, 
                     callback_navigation_position_adjust_marker)    # 指定marker校正机器人位置
    rospy.Subscriber("/navigation_get_power_status", String, 
                     callback_get_power_status)    # 获取底盘电量
    rospy.Subscriber("/navigation_joy_control", navigation_joy_control, 
                     callback_navigation_joy_control)    # 机器人直接控制指令，可以执行左传右转前进后退等
    rospy.Subscriber("/navigation_max_speed", Float64, 
                     callback_navigation_max_speed)    # 机器人最大行进速度(百分比) 取值范围[0.3,0.7]
    rospy.Subscriber("/navigation_max_speed_ratio", Float64, 
                     callback_navigation_max_speed_ratio)    # 机器人最大行进速度比 取值范围[0.3,1.4]
    rospy.Subscriber("/navigation_max_speed_linear", Float64, 
                     callback_navigation_max_speed_linear)    # 机器人最大线速度(m/s) 取值范围[0.1,1.0]
    rospy.Subscriber("/navigation_max_speed_angular", Float64, 
                     callback_navigation_max_speed_angular)    # 机器人最大角速度(rad/s) 取值范围[0.5,3.5]
    rospy.Subscriber("/navigation_get_params", String, 
                     callback_navigation_get_params)    # 获取参数列表和当前值
    rospy.Subscriber("/navigation_led_set_color", navigation_led_set_color, 
                     callback_navigation_led_set_color)    # 设置灯带颜色
    
    pub_navigation_feedback = rospy.Publisher('/navigation_feedback', 
                                              String, queue_size=10)    # 发布接收到的网口数据

    rate = rospy.Rate(10)  # 发布频率10hz
    while not rospy.is_shutdown():
        full_response = ''    # 清空字符串
        try:
            while not rospy.is_shutdown():
                try:
                    # 不在此持锁：recv 阻塞时会饿死 chassis_send（Joy 等回调）
                    part = chassis_client.recv(1024).decode()
                    if len(part) > 2:
                        full_response += part
                        rospy.loginfo(f"part  recv  data: { part }")
                        pub_navigation_feedback.publish(part)
                    if full_response.count('\n') >= 3:
                        break
                except socket.timeout:
                    rospy.logerr("Socket timeout while reading response")
                    break
        except (BrokenPipeError, ConnectionResetError, OSError) as ex:
            rospy.logwarn("chassis recv failed (%s), reconnect" % ex)
            with _chassis_sock_lock:
                try:
                    if chassis_client is not None:
                        chassis_client.close()
                except Exception:
                    pass
                chassis_client = None
                try:
                    _open_chassis_socket_unlocked()
                except Exception as ex2:
                    rospy.logerr("chassis recv reconnect failed: %s" % ex2)
                    rospy.sleep(0.5)
        # rate.sleep()

    print('---------------------------final----------------------------')