import keyboard
import pymem
import numpy as np
import math
import win32api
import win32con
import win32gui
from PyQt5 import QtWidgets, QtGui, QtCore
import sys
import configparser

# 创建配置解析器
config = configparser.ConfigParser()
config.read(r'.\source\config.ini')

# 从配置文件中读取变量
LocalPlayerController = int(config['GameAddresses']['LocalPlayerController'], 16)
view_matrix_address = int(config['GameAddresses']['view_matrix_address'], 16)
EntityList = int(config['GameAddresses']['EntityList'], 16)
CCameraManager = int(config['GameAddresses']['CCameraManager'], 16)

aim_assist_threshold = float(config['Settings']['aim_assist_threshold'])
smoothing_factor = float(config['Settings']['smoothing_factor'])

# 设置要读取的进程
process = "project8.exe"
pm = pymem.Pymem(process)

# 获取 client.dll 模块的基地址
client_module = pymem.process.module_from_name(pm.process_handle, "client.dll")
client_base = client_module.lpBaseOfDll

# 读取实体列表的地址 EntityList:
entity_list = pm.read_longlong(client_base + EntityList)

# 读取控制器地址 LocalPlayerController:
controller_base = pm.read_longlong(client_base + LocalPlayerController)

# 读取摄像机的地址 CCameraManager:
camera = pm.read_longlong(client_base + CCameraManager + 0x28)

# 用于存储当前队伍目标（2 或 3）
current_team = 3  # 初始瞄准和 ESP 的队伍

# 按下 E 键切换队伍
def toggle_team():
    global current_team
    current_team = 3 if current_team == 2 else 2
    print(f"Switched to team {current_team}")

keyboard.add_hotkey('e', toggle_team)  # 按下 E 键切换队伍

class TransparentWindow(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint | 
            QtCore.Qt.WindowStaysOnTopHint | 
            QtCore.Qt.Tool |
            QtCore.Qt.X11BypassWindowManagerHint
        )
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self.setGeometry(0, 0, 1920, 1080)
        self.setWindowOpacity(0.8)
        self.text = "RainAIM"
        self.enemies = []
        self.fov_radius = 52

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        # 绘制霓虹风格文本
        font = QtGui.QFont("Courier", 40, QtGui.QFont.Bold)
        painter.setFont(font)
        painter.setPen(QtGui.QColor(255, 255, 255))
        painter.drawText(20, 20, self.text)

        # 绘制敌人，框框大小基于距离
        for enemy in self.enemies:
            pos, distance = enemy
            # 基于距离计算框框大小
            base_size = 60
            reference_distance = 500
            size_factor = reference_distance / max(distance, 1)
            enemy_width = int(base_size * size_factor)
            enemy_height = int(base_size * 2.5 * size_factor)

            enemy_pen = QtGui.QPen(QtGui.QColor(255, 0, 0), 2)
            painter.setPen(enemy_pen)
            painter.setBrush(QtGui.QColor(0, 0, 0, 0))

            top_left_x = pos[0] - enemy_width // 2
            top_left_y = pos[1] - enemy_height // 2

            painter.drawRect(top_left_x, top_left_y , enemy_width, enemy_height)

        # 绘制FOV圆圈
        screen_center_x, screen_center_y = self.width() // 2, self.height() // 2
        fov_pen = QtGui.QPen(QtGui.QColor(255, 255, 255), 2)
        painter.setPen(fov_pen)
        painter.setBrush(QtGui.QColor(0, 0, 0, 0))
        painter.drawEllipse(screen_center_x - self.fov_radius, screen_center_y - self.fov_radius,
                            self.fov_radius * 2, self.fov_radius * 2)

    def clear_enemies(self):
        self.enemies = []

    def add_enemy(self, pos, distance):
        self.enemies.append((pos, distance))

# 设置窗口为鼠标穿透
def set_window_transparent():
    hwnd = win32gui.FindWindow(None, "RainAIM")
    win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE,
                           win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE) | win32con.WS_EX_TRANSPARENT)

# 获取实体索引函数
def get_index(i):
    address_base = pm.read_longlong(entity_list + 0x8 * ((i & 0x7FFF) >> 0x9) + 0x10)
    controller_base = pm.read_longlong(address_base + 120 * (i & 0x1FF))
    pawn_handle = pm.read_longlong(controller_base + 0x60C)
    list_entry = pm.read_longlong(entity_list + 0x8 * ((pawn_handle & 0x7FFF) >> 0x9) + 0x10)
    pawn = pm.read_longlong(list_entry + 0x78 * (pawn_handle & 0x1FF))
    team = pm.read_uchar(pawn + 0x3EB)

    game_scene_node = pm.read_longlong(pawn + 0x328)
    pos_addr = game_scene_node + 0xD0
    pos = pm.read_float(pos_addr)
    pos2 = pm.read_float(pos_addr + 4)
    pos3 = pm.read_float(pos_addr + 8)

    pos_vector = (pos, pos2, pos3)  
    return team, pawn, pos_vector

# 读取视角矩阵
def get_view_matrix():
    matrix = []
    for i in range(16):
        matrix.append(pm.read_float(client_base + view_matrix_address + i * 4))
    return np.array(matrix).reshape(4, 4)

# 将3D世界坐标转换为2D屏幕坐标
def world_to_screen(world_pos, view_matrix, screen_width, screen_height):
    clip_coords = np.dot(view_matrix, np.array([world_pos[0], world_pos[1], world_pos[2], 1.0]))

    if clip_coords[3] < 0.1:
        return None

    ndc_x = clip_coords[0] / clip_coords[3]
    ndc_y = clip_coords[1] / clip_coords[3]

    screen_x = (screen_width / 2) * (ndc_x + 1)
    screen_y = (screen_height / 2) * (1 - ndc_y)

    return int(screen_x), int(screen_y)

# 处理摄像头旋转
def calculate_camera_rotation(camera_pos, enemy_pos):
    delta_x = enemy_pos[0] - camera_pos[0]
    delta_y = enemy_pos[1] - camera_pos[1]
    delta_z = enemy_pos[2] - camera_pos[2]
    
    pitch = -math.atan2(delta_z, math.hypot(delta_x, delta_y))
    yaw = math.atan2(delta_y, delta_x)

    pitch = math.degrees(pitch)
    yaw = math.degrees(yaw)

    if yaw < 0:
        yaw += 360
    return yaw, pitch

# 计算与敌人之间的角度差
def calculate_angle_distance(camera_yaw, enemy_pos):
    cam_pos = get_cam()
    enemy_yaw, _ = calculate_camera_rotation(cam_pos, enemy_pos)
    
    angle_diff = enemy_yaw - camera_yaw
    if angle_diff < -180:
        angle_diff += 360
    if angle_diff > 180:
        angle_diff -= 360

    return abs(angle_diff)

# 获取摄像头位置
def get_cam():
    camera_pos = pm.read_longlong(client_base + CCameraManager + 0x28)
    c_x = pm.read_float(camera_pos + 0x38)
    c_y = pm.read_float(camera_pos + 0x3c)
    c_z = pm.read_float(camera_pos + 0x40)
    return (c_x, c_y, c_z)

# 使用 win32api 的 mouse_event 函数移动鼠标
def move_mouse(x, y):
    win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, int(x), int(y), 0, 0)

from PyQt5 import QtWidgets, QtGui, QtCore

class ControlWindow(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("RainAIM")
        self.setGeometry(200, 200, 250, 250)

        # 添加背景图片
        self.background_label = QtWidgets.QLabel(self)
        self.background_label.setPixmap(QtGui.QPixmap(r".\source\path.jpg"))  # 替换为您的图片路径
        self.background_label.setScaledContents(True)  # 使图片适应窗口大小

        # 创建布局
        layout = QtWidgets.QVBoxLayout()
        layout.setSpacing(10)  # 控件之间的间距
        layout.setContentsMargins(20, 20, 20, 20)  # 窗口内边距

        # 添加输入框和按钮
        self.localplayer_edit = self.create_input_field(layout, "Keygen 1", hex(LocalPlayerController))
        self.view_matrix_edit = self.create_input_field(layout, "Keygen 2", hex(view_matrix_address))
        self.entitylist_edit = self.create_input_field(layout, "Keygen 3", hex(EntityList))
        self.cameramanager_edit = self.create_input_field(layout, "Keygen 4", hex(CCameraManager))

        # 添加平滑因子滑块
        self.smoothing_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.smoothing_slider.setRange(1, 25)  # 对应 0.1 到 2.5 的范围
        self.smoothing_slider.setValue(int(smoothing_factor * 10))  # 显示当前值的十倍
        layout.addWidget(QtWidgets.QLabel("AimSpeed"))

        # 添加标签以显示当前值
        self.smoothing_value_label = QtWidgets.QLabel(f"{smoothing_factor:.1f}")
        layout.addWidget(self.smoothing_value_label)

        layout.addWidget(self.smoothing_slider)

        # 连接滑块值变化信号
        self.smoothing_slider.valueChanged.connect(self.update_smoothing_value)

        # 添加保存按钮
        save_button = QtWidgets.QPushButton("Apply")
        save_button.clicked.connect(self.save_config)
        layout.addWidget(save_button)

        self.setLayout(layout)

        # 设置样式
        self.setStyleSheet("""
            color: #FFFFFF;  /* 字体颜色 */
            font-family: Arial;  /* 字体 */
            font-size: 12pt;  /* 字体大小 */
        """)

        # 设置按钮样式
        save_button.setStyleSheet("""
            background-color: #007BFF;  /* 按钮背景色 */
            color: #FFFFFF;  /* 字体颜色 */
            border: none;  /* 去掉边框 */
            padding: 10px;  /* 内边距 */
            border-radius: 5px;  /* 圆角 */
        """)
        save_button.setCursor(QtCore.Qt.PointingHandCursor)  # 设置鼠标指针样式

        # 设置滑块样式
        self.smoothing_slider.setStyleSheet("""
            QSlider {
                background: #555555;  /* 滑块背景 */
                height: 10px;  /* 滑块高度 */
            }
            QSlider::handle {
                background: #007BFF;  /* 滑块手柄颜色 */
                border-radius: 5px;  /* 圆角 */
                width: 15px;  /* 手柄宽度 */
                margin-top: -2;  /* 手柄与滑块上下居中 */
                margin-bottom: -2;
            }
            QSlider::groove:horizontal {
                background: #444444;  /* 轨道背景 */
                height: 10px;  /* 轨道高度 */
                border-radius: 1px;  /* 轨道圆角 */
            }
        """)

        # 连接窗口大小变化事件
        self.resizeEvent(None)

    def create_input_field(self, layout, label_text, default_value):
        label = QtWidgets.QLabel(label_text)
        layout.addWidget(label)
        input_field = QtWidgets.QLineEdit(default_value)
        layout.addWidget(input_field)

        # 设置输入框样式
        input_field.setStyleSheet("""
            background-color: #444444;  /* 输入框背景色 */
            color: #FFFFFF;  /* 字体颜色 */
            padding: 5px;  /* 内边距 */
            border: 1px solid #007BFF;  /* 边框颜色 */
            border-radius: 5px;  /* 圆角 */
        """)

        return input_field

    def resizeEvent(self, event):
        # 设置背景标签的几何形状为窗口的几何形状
        self.background_label.setGeometry(self.rect())

    def update_smoothing_value(self):
        # 更新标签以显示当前滑块值
        current_value = self.smoothing_slider.value() / 10.0  # 将值缩小为原来的十倍
        self.smoothing_value_label.setText(f"{current_value:.1f}")

    def save_config(self):
        # 更新配置文件中的值
        config['GameAddresses']['LocalPlayerController'] = self.localplayer_edit.text()
        config['GameAddresses']['ViewMatrixAddress'] = self.view_matrix_edit.text()
        config['GameAddresses']['EntityList'] = self.entitylist_edit.text()
        config['GameAddresses']['CCameraManager'] = self.cameramanager_edit.text()

        # 更新平滑因子
        global smoothing_factor
        smoothing_factor = self.smoothing_slider.value() / 10.0  # 将值缩小为原来的十倍

        # 保存配置
        with open(r'.\source\config.ini', 'w') as configfile:
            config.write(configfile)

        # 重新读取配置文件，更新全局变量
        global LocalPlayerController, view_matrix_address, EntityList, CCameraManager
        LocalPlayerController = int(config['GameAddresses']['LocalPlayerController'], 16)
        view_matrix_address = int(config['GameAddresses']['ViewMatrixAddress'], 16)
        EntityList = int(config['GameAddresses']['EntityList'], 16)
        CCameraManager = int(config['GameAddresses']['CCameraManager'], 16)

        print("Config updated successfully!")  # 使用打印替代弹窗

class ESPApplication(QtWidgets.QApplication):
    def __init__(self, args):
        super().__init__(args)

        # 初始化透明窗口和控制窗口
        self.transparent_window = TransparentWindow()
        self.transparent_window.setWindowTitle("RainAIM")
        self.transparent_window.show()

        self.control_window = ControlWindow()
        self.control_window.show()

        # 在窗口显示后设置鼠标穿透
        QtCore.QTimer.singleShot(100, set_window_transparent)

        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.main_loop)
        self.timer.start(16)

    def main_loop(self):
        self.transparent_window.clear_enemies()

        for i in range(1, 16):
            try:
                team, e1, enemy_pos_vector = get_index(i)
                enemy_pos_vector = (enemy_pos_vector[0], enemy_pos_vector[1], enemy_pos_vector[2] + 50)

                view_matrix = get_view_matrix()
                screen_pos = world_to_screen(enemy_pos_vector, view_matrix, 1920, 1080)

                if screen_pos and team == current_team:
                    # 计算到敌人的距离
                    cam_pos = get_cam()
                    distance = math.sqrt(sum((a - b) ** 2 for a, b in zip(cam_pos, enemy_pos_vector)))
                    self.transparent_window.add_enemy(screen_pos, distance)
            except Exception as e:
                pass

        # 自动瞄准逻辑
        if win32api.GetAsyncKeyState(win32con.VK_LBUTTON) and not win32api.GetAsyncKeyState(win32con.VK_RBUTTON):
            closest_enemy = None
            closest_distance = float('inf')
            screen_center_x, screen_center_y = 1920 // 2, 1080 // 2

            for i in range(1, 16):
                try:
                    team, e1, enemy_pos_vector = get_index(i)
                    if team != current_team:
                        continue
                    enemy_pos_vector = (enemy_pos_vector[0], enemy_pos_vector[1], enemy_pos_vector[2] + 65)

                    camera_yaw = pm.read_float(camera + 0x48)

                    # 检查敌人是否在 FOV 圆圈内
                    enemy_screen_pos = world_to_screen(enemy_pos_vector, get_view_matrix(), 1920, 1080)
                    if enemy_screen_pos:
                        diff_x = enemy_screen_pos[0] - screen_center_x
                        diff_y = enemy_screen_pos[1] - screen_center_y
                        if math.hypot(diff_x, diff_y) <= self.transparent_window.fov_radius:
                            cam_pos = get_cam()
                            distance = math.sqrt(sum((a - b) ** 2 for a, b in zip(cam_pos, enemy_pos_vector)))

                            # 寻找最近的敌人
                            if distance < closest_distance:
                                closest_distance = distance
                                closest_enemy = enemy_pos_vector
                except Exception as e:
                    pass

            if closest_enemy is not None:

                closest_angle_diff = calculate_angle_distance(pm.read_float(camera + 0x48), closest_enemy)

                if closest_angle_diff > aim_assist_threshold:
                    screen_pos = world_to_screen(closest_enemy, get_view_matrix(), 1920, 1080)
                    
                    if screen_pos:
                        diff_x = screen_pos[0] - screen_center_x
                        diff_y = screen_pos[1] - screen_center_y
                        move_mouse(diff_x * smoothing_factor, diff_y * smoothing_factor)

        self.transparent_window.update()
        pass

if __name__ == "__main__":
    app = ESPApplication(sys.argv)
    sys.exit(app.exec_())
