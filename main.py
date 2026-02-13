# gcode2rml.py
import os
import re
import math
import tkinter as tk
from tkinter import ttk
from tkinter import filedialog, messagebox, scrolledtext
import threading


# ==================== Векторные операции ====================

class Vector:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x = x
        self.y = y
        self.z = z

    @staticmethod
    def get(x, y, z):
        return Vector(x, y, z)

    def add(self, other):
        return Vector(self.x + other.x, self.y + other.y, self.z + other.z)

    def sub(self, other):
        return Vector(self.x - other.x, self.y - other.y, self.z - other.z)

    def dot(self, other):
        return self.x * other.x + self.y * other.y + self.z * other.z

    def size(self):
        return math.sqrt(self.x * self.x + self.y * self.y + self.z * self.z)

    def scale(self, scale):
        return Vector(self.x * scale, self.y * scale, self.z * scale)

    def cross(self, other):
        return Vector(
            self.y * other.z - self.z * other.y,
            self.z * other.x - self.x * other.z,
            self.x * other.y - self.y * other.x
        )

    def norm(self):
        size = self.size()
        if size == 0:
            return Vector(0, 0, 0)
        return Vector(self.x / size, self.y / size, self.z / size)

    def __str__(self):
        return f"({self.x}, {self.y}, {self.z})"


# ==================== Конвертер G-code в RML-1 ====================

class GCode2RMLConverter:
    def __init__(self):
        # Импортированные настройки
        self.home_position = Vector(0.0, 0.0, 0.0)
        self.pos_offset = Vector(0.0, 0.0, 0.0)
        self.rapid_feed_speed = 1000.0
        self.circular_resolution = 360.0

        # Состояние обработки
        self.abs_inc = 90  # 90: abs / 91: inc
        self.mm_in = 21  # 20: inches / 21: millimeters
        self.mov_mode = 0  # 0: rapid / 1: linear / 2: CW circle / 3: CCW circle / 28: return home
        self.plane_select = 17  # 17: XY / 18: XZ / 19: YZ
        self.TLOC_mode = 49  # 43: positive / 44: negative / 49: canceled
        self.TROC_mode = 40  # 40: canceled / 41: left / 42: right
        self.feed_mode = 94  # 94: per min / 95: per rev
        self.coor_sys = 1

        self.next_pos = Vector(0, 0, 0)
        self.center_pos_inc = Vector(0, 0, 0)
        self.current_pos = Vector(0, 0, 0)

        self.feed_speed = 0
        self.spindle_speed = 0
        self.spindle_state = 0  # 0: stop / 1: CW / -1: CCW
        self.program_number = 0
        self.next_tool_num = 0
        self.TROC_tool_num = 0
        self.TLOC_tool_num = 0
        self.dwell_enable = 0
        self.coor_changed = 0

        self.output_lines = []

        self.callback_progress = None
        self.callback_log = None

    def log(self, message):
        if self.callback_log:
            self.callback_log(message)

    def import_settings(self, setting_file_path):
        """Импорт настроек из файла setting.txt"""
        try:
            with open(setting_file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue

                    if '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()

                        if key == "homePosition":
                            # Удаляем скобки и пробелы
                            value = value.replace('(', '').replace(')', '').replace(' ', '')
                            coords = value.split(',')
                            if len(coords) == 3:
                                self.home_position = Vector(float(coords[0]), float(coords[1]), float(coords[2]))
                        elif key == "posOffset":
                            value = value.replace('(', '').replace(')', '').replace(' ', '')
                            coords = value.split(',')
                            if len(coords) == 3:
                                self.pos_offset = Vector(float(coords[0]), float(coords[1]), float(coords[2]))
                        elif key == "rapidFeedSpeed":
                            self.rapid_feed_speed = float(value)
                        elif key == "circularResolution":
                            self.circular_resolution = float(value)
        except Exception as e:
            self.log(f"Ошибка при чтении настроек: {e}")

    def plane_conv(self, vect, plane):
        """Преобразование координат для выбранной плоскости"""
        if plane == 17:  # XY plane
            return Vector(vect.x, vect.y, vect.z)
        elif plane == 18:  # XZ plane
            return Vector(vect.z, vect.x, vect.y)
        elif plane == 19:  # YZ plane
            return Vector(vect.y, vect.z, vect.x)
        return vect

    def plane_conv_inv(self, vect, plane):
        """Обратное преобразование координат"""
        if plane == 17:  # XY plane
            return Vector(vect.x, vect.y, vect.z)
        elif plane == 18:  # XZ plane
            return Vector(vect.y, vect.z, vect.x)
        elif plane == 19:  # YZ plane
            return Vector(vect.z, vect.x, vect.y)
        return vect

    def move(self, next_pos, feed_speed):
        """Генерация команды перемещения"""
        # Масштабирование и смещение
        output_pos = next_pos.add(self.pos_offset)
        output_pos = output_pos.scale(100.0)
        output_pos = output_pos.add(Vector(0.5, 0.5, 0.5))

        # Форматирование координат как целых чисел
        x = int(output_pos.x)
        y = int(output_pos.y)
        z = int(output_pos.z)

        # Добавление команды скорости, если она изменилась
        if not hasattr(self, 'last_feed_speed') or feed_speed != self.last_feed_speed:
            speed_cmd = f"V{feed_speed / 60:.1f};"
            self.output_lines.append(speed_cmd)
            self.last_feed_speed = feed_speed

        # Команда перемещения
        move_cmd = f"Z{x},{y},{z};"
        self.output_lines.append(move_cmd)

        # Обновление текущей позиции
        self.current_pos = next_pos

    def rapid_positioning(self, next_pos):
        """Обработка G00 - быстрое позиционирование"""
        self.move(next_pos, self.rapid_feed_speed)

    def linear_interpolation(self, next_pos, feed_speed):
        """Обработка G01 - линейная интерполяция"""
        self.move(next_pos, feed_speed)

    def circular_interpolation(self, next_pos, center_pos_inc, plane_select, direction, feed_speed):
        """Обработка G02/G03 - круговая интерполяция"""
        mid_current_pos = self.plane_conv(self.current_pos, plane_select)
        mid_next_pos = self.plane_conv(next_pos, plane_select)
        mid_center_pos_inc = self.plane_conv(center_pos_inc, plane_select)
        mid_center_pos = self.current_pos.add(mid_center_pos_inc)

        mid_start_pos = mid_current_pos
        mid_delta1 = mid_current_pos.sub(mid_center_pos)
        mid_delta2 = mid_next_pos.sub(mid_center_pos)

        # Обнуляем Z для вычислений в плоскости
        mid_delta1.z = 0.0
        mid_delta2.z = 0.0

        # Вычисление угла
        delta_angle = math.acos(mid_delta1.dot(mid_delta2) / (mid_delta2.size() * mid_delta1.size()))
        if delta_angle <= 0:
            delta_angle += 2 * math.pi

        # Количество шагов
        delta_steps = int(self.circular_resolution * delta_angle / (2 * math.pi))

        for step in range(delta_steps):
            theta_temp = 2 * math.pi * step / self.circular_resolution
            if direction == 2:  # По часовой стрелке
                theta_temp *= -1

            # Вычисление промежуточной точки
            pos = Vector()
            pos.x = (mid_delta1.x * math.cos(theta_temp) +
                     mid_delta1.y * math.sin(-theta_temp) +
                     mid_center_pos.x)
            pos.y = (mid_delta1.x * math.sin(theta_temp) +
                     mid_delta1.y * math.cos(theta_temp) +
                     mid_center_pos.y)
            pos.z = ((mid_next_pos.z - mid_start_pos.z) *
                     (theta_temp / delta_angle) + mid_start_pos.z)

            # Преобразование обратно и перемещение
            actual_pos = self.plane_conv_inv(pos, plane_select)
            self.move(actual_pos, feed_speed)
            self.current_pos = actual_pos

        # Финальное перемещение в конечную точку
        self.move(next_pos, feed_speed)
        self.current_pos = next_pos

    def return_home(self, via_pos):
        """Обработка G28 - возврат в домашнюю позицию"""
        self.rapid_positioning(via_pos)
        self.rapid_positioning(self.home_position)

    def process_word(self, address, value_str):
        """Обработка одного слова G-code"""
        try:
            if address == ';':  # Конец блока
                if self.coor_changed == 0:
                    return

                if self.mov_mode == 0:
                    self.rapid_positioning(self.next_pos)
                elif self.mov_mode == 1:
                    self.linear_interpolation(self.next_pos, self.feed_speed)
                elif self.mov_mode == 2 or self.mov_mode == 3:
                    self.circular_interpolation(self.next_pos, self.center_pos_inc,
                                                self.plane_select, self.mov_mode, self.feed_speed)
                elif self.mov_mode == 28:
                    self.return_home(self.next_pos)

                self.coor_changed = 0
                self.center_pos_inc = Vector(0, 0, 0)

            elif address == 'F':  # Скорость подачи
                self.feed_speed = float(value_str)

            elif address == 'G':  # G-коды
                g_code = int(value_str)

                if g_code == 0:  # Быстрое позиционирование
                    self.mov_mode = 0
                elif g_code == 1:  # Линейная интерполяция
                    self.mov_mode = 1
                elif g_code == 2:  # Круговая интерполяция по часовой
                    self.mov_mode = 2
                elif g_code == 3:  # Круговая интерполяция против часовой
                    self.mov_mode = 3
                elif g_code == 4:  # Задержка
                    self.dwell_enable = 1
                elif g_code == 17:  # Плоскость XY
                    self.plane_select = 17
                elif g_code == 18:  # Плоскость XZ
                    self.plane_select = 18
                elif g_code == 19:  # Плоскость YZ
                    self.plane_select = 19
                elif g_code == 20:  # Дюймы
                    self.mm_in = 20
                elif g_code == 21:  # Миллиметры
                    self.mm_in = 21
                elif g_code == 28:  # Возврат домой
                    self.mov_mode = 28
                elif g_code == 40:  # Компенсация радиуса инструмента выкл
                    self.TROC_mode = 40
                    self.TROC_tool_num = 0
                elif g_code == 41:  # Компенсация радиуса инструмента лево
                    self.TROC_mode = 41
                elif g_code == 42:  # Компенсация радиуса инструмента право
                    self.TROC_mode = 42
                elif g_code == 43:  # Компенсация длины инструмента плюс
                    self.TLOC_mode = 43
                elif g_code == 44:  # Компенсация длины инструмента минус
                    self.TLOC_mode = 44
                elif g_code == 49:  # Компенсация длины инструмента отмена
                    self.TLOC_mode = 49
                elif 54 <= g_code <= 59:  # Системы координат
                    self.coor_sys = g_code - 54
                elif g_code == 90:  # Абсолютные координаты
                    self.abs_inc = 90
                    self.output_lines.append("^PA;")
                elif g_code == 91:  # Относительные координаты
                    self.abs_inc = 91
                    self.output_lines.append("^PR;")
                elif g_code == 94:  # Подача в минуту
                    self.feed_mode = 94
                elif g_code == 95:  # Подача на оборот
                    self.feed_mode = 95

            elif address == 'I':  # Смещение центра по X
                self.center_pos_inc.x = float(value_str)
            elif address == 'J':  # Смещение центра по Y
                self.center_pos_inc.y = float(value_str)
            elif address == 'K':  # Смещение центра по Z
                self.center_pos_inc.z = float(value_str)

            elif address == 'M':  # M-коды
                m_code = int(value_str)

                if m_code == 3:  # Шпиндель по часовой
                    self.spindle_state = 1
                    self.output_lines.append("!RC15;!MC1;")
                elif m_code == 4:  # Шпиндель против часовой
                    self.spindle_state = -1
                    self.output_lines.append("!RC15;!MC1;")
                elif m_code == 5:  # Стоп шпинделя
                    self.spindle_state = 0
                    self.output_lines.append("!MC0;")

            elif address == 'S':  # Скорость шпинделя
                self.spindle_speed = float(value_str)

            elif address == 'X':  # Координата X
                val = float(value_str)
                if self.abs_inc == 91:
                    self.next_pos.x += val
                else:
                    self.next_pos.x = val
                self.coor_changed = 1

            elif address == 'Y':  # Координата Y
                val = float(value_str)
                if self.abs_inc == 91:
                    self.next_pos.y += val
                else:
                    self.next_pos.y = val
                self.coor_changed = 1

            elif address == 'Z':  # Координата Z
                val = float(value_str)
                if self.abs_inc == 91:
                    self.next_pos.z += val
                else:
                    self.next_pos.z = val
                self.coor_changed = 1

        except ValueError as e:
            self.log(f"Ошибка преобразования значения: {address}{value_str} - {e}")

    def convert(self, input_file_path, output_file_path):
        """Основной метод конвертации"""
        self.log("Начало конвертации...")

        # Импорт настроек
        setting_file = os.path.join(os.path.dirname(__file__), "setting.txt")
        if os.path.exists(setting_file):
            self.import_settings(setting_file)
        else:
            self.log("Файл настроек setting.txt не найден, используются значения по умолчанию")

        # Чтение входного файла
        try:
            with open(input_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        except Exception as e:
            self.log(f"Ошибка чтения файла: {e}")
            return False

        # Инициализация выходного файла
        self.output_lines = []
        self.output_lines.append(";;^IN;")
        self.output_lines.append("V85.0;")
        self.output_lines.append("^PR;")
        self.output_lines.append("Z0,0,15500;")
        self.output_lines.append("^PA;")

        # Разбор G-code
        lines = content.split('\n')
        total_lines = len(lines)

        comment_mode = False
        for i, line in enumerate(lines):
            # Отображение прогресса
            if self.callback_progress:
                progress = (i + 1) / total_lines * 100
                self.callback_progress(progress)

            line = line.strip()
            if not line:
                continue

            # Удаление комментариев в скобках
            result_line = ""
            for char in line:
                if char == '(':
                    comment_mode = True
                elif char == ')':
                    comment_mode = False
                elif not comment_mode:
                    result_line += char

            line = result_line.strip()
            if not line or line.startswith('%'):
                continue

            # Разделение на слова
            words = re.findall(r'[A-Z][^A-Z;]*', line)

            for word in words:
                if word:
                    address = word[0]
                    value = word[1:]
                    self.process_word(address, value)

            # Обработка конца строки
            self.process_word(';', '0')

        # Завершающая команда
        self.output_lines.append("^IN;")

        # Запись в выходной файл
        try:
            with open(output_file_path, 'w', encoding='utf-8') as f:
                f.write(''.join(self.output_lines))
            self.log(f"Конвертация завершена успешно!\nФайл сохранен: {output_file_path}")
            return True
        except Exception as e:
            self.log(f"Ошибка записи файла: {e}")
            return False


# ==================== Графический интерфейс ====================

class GCodeConverterGUI:
    def __init__(self):
        self.window = tk.Tk()
        self.window.title("G-code to RML-1 Converter")
        self.window.geometry("800x600")

        self.converter = GCode2RMLConverter()
        self.converter.callback_log = self.log_message
        self.converter.callback_progress = self.update_progress

        self.create_widgets()

    def create_widgets(self):
        # Фрейм для выбора файлов
        file_frame = tk.Frame(self.window)
        file_frame.pack(pady=10, padx=10, fill=tk.X)

        # Входной файл
        tk.Label(file_frame, text="Входной G-code файл:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.input_file_var = tk.StringVar()
        tk.Entry(file_frame, textvariable=self.input_file_var, width=50).grid(row=0, column=1, padx=5)
        tk.Button(file_frame, text="Обзор...", command=self.browse_input_file).grid(row=0, column=2)

        # Выходной файл
        tk.Label(file_frame, text="Выходной RML файл:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.output_file_var = tk.StringVar()
        tk.Entry(file_frame, textvariable=self.output_file_var, width=50).grid(row=1, column=1, padx=5)
        tk.Button(file_frame, text="Обзор...", command=self.browse_output_file).grid(row=1, column=2)

        # Прогресс-бар
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(self.window, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(pady=10, padx=10, fill=tk.X)

        # Кнопки управления
        button_frame = tk.Frame(self.window)
        button_frame.pack(pady=10)

        tk.Button(button_frame, text="Конвертировать", command=self.start_conversion,
                  bg="lightblue", padx=20, pady=5).pack(side=tk.LEFT, padx=5)
        tk.Button(button_frame, text="Очистить лог", command=self.clear_log,
                  bg="lightgray", padx=20, pady=5).pack(side=tk.LEFT, padx=5)
        tk.Button(button_frame, text="Выход", command=self.window.quit,
                  bg="lightcoral", padx=20, pady=5).pack(side=tk.LEFT, padx=5)

        # Лог
        tk.Label(self.window, text="Лог выполнения:").pack(anchor=tk.W, padx=10)

        self.log_text = scrolledtext.ScrolledText(self.window, height=15)
        self.log_text.pack(pady=10, padx=10, fill=tk.BOTH, expand=True)

        # Статус
        self.status_var = tk.StringVar(value="Готов к работе")
        status_bar = tk.Label(self.window, textvariable=self.status_var,
                              relief=tk.SUNKEN, anchor=tk.W)
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)

    def browse_input_file(self):
        filename = filedialog.askopenfilename(
            title="Выберите G-code файл",
            filetypes=[("G-code files", "*.nc;*.cnc;*.gcode;*.txt"), ("All files", "*.*")]
        )
        if filename:
            self.input_file_var.set(filename)
            # Автогенерация имени выходного файла
            base = os.path.splitext(filename)[0]
            self.output_file_var.set(base + ".rml")

    def browse_output_file(self):
        filename = filedialog.asksaveasfilename(
            title="Сохранить RML файл как",
            defaultextension=".rml",
            filetypes=[("RML files", "*.rml"), ("All files", "*.*")]
        )
        if filename:
            self.output_file_var.set(filename)

    def log_message(self, message):
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.window.update_idletasks()

    def update_progress(self, value):
        self.progress_var.set(value)
        self.status_var.set(f"Прогресс: {value:.1f}%")
        self.window.update_idletasks()

    def clear_log(self):
        self.log_text.delete(1.0, tk.END)

    def conversion_thread(self):
        input_file = self.input_file_var.get()
        output_file = self.output_file_var.get()

        if not input_file:
            messagebox.showerror("Ошибка", "Выберите входной файл!")
            return

        if not output_file:
            messagebox.showerror("Ошибка", "Выберите выходной файл!")
            return

        try:
            success = self.converter.convert(input_file, output_file)
            if success:
                self.status_var.set("Конвертация завершена успешно!")
                messagebox.showinfo("Успех", "Конвертация завершена успешно!")
            else:
                self.status_var.set("Ошибка конвертации!")
        except Exception as e:
            self.log_message(f"Ошибка: {e}")
            self.status_var.set("Ошибка конвертации!")

    def start_conversion(self):
        # Запуск в отдельном потоке, чтобы не блокировать GUI
        thread = threading.Thread(target=self.conversion_thread)
        thread.daemon = True
        thread.start()

    def run(self):
        self.window.mainloop()


# ==================== Точка входа ====================

if __name__ == "__main__":
    # Создаем файл настроек по умолчанию, если его нет
    setting_file = "setting.txt"
    if not os.path.exists(setting_file):
        default_settings = """# Домашняя позиция
homePosition = ( 0.0, 0.0, 0.0)

# Смещение координат вывода
posOffset = ( 0.0, 0.0, 0.0 );

# Скорость быстрого перемещения
rapidFeedSpeed = 1000.0

# Разрешение круговой интерполяции (количество делений на один оборот)
circularResolution = 360.0
"""
        with open(setting_file, 'w', encoding='utf-8') as f:
            f.write(default_settings)

    # Запуск GUI
    app = GCodeConverterGUI()
    app.run()