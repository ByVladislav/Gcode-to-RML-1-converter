import os
import re
import math
import tkinter as tk
from tkinter import ttk
from tkinter import filedialog, messagebox, scrolledtext
import threading


# ==================== Vector Operations ====================

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


# ==================== G-code to RML-1 Converter ====================

class GCode2RMLConverter:
    def __init__(self):
        # Imported settings
        self.home_position = Vector(0.0, 0.0, 0.0)
        self.pos_offset = Vector(0.0, 0.0, 0.0)
        self.rapid_feed_speed = 1000.0
        self.circular_resolution = 360.0

        # Processing state
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
        """Import settings from setting.txt file"""
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
                            # Remove parentheses and spaces
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
            self.log(f"Error reading settings: {e}")

    def plane_conv(self, vect, plane):
        """Coordinate transformation for selected plane"""
        if plane == 17:  # XY plane
            return Vector(vect.x, vect.y, vect.z)
        elif plane == 18:  # XZ plane
            return Vector(vect.z, vect.x, vect.y)
        elif plane == 19:  # YZ plane
            return Vector(vect.y, vect.z, vect.x)
        return vect

    def plane_conv_inv(self, vect, plane):
        """Inverse coordinate transformation"""
        if plane == 17:  # XY plane
            return Vector(vect.x, vect.y, vect.z)
        elif plane == 18:  # XZ plane
            return Vector(vect.y, vect.z, vect.x)
        elif plane == 19:  # YZ plane
            return Vector(vect.z, vect.x, vect.y)
        return vect

    def move(self, next_pos, feed_speed):
        """Generate movement command"""
        # Scaling and offset
        output_pos = next_pos.add(self.pos_offset)
        output_pos = output_pos.scale(100.0)
        output_pos = output_pos.add(Vector(0.5, 0.5, 0.5))

        # Format coordinates as integers
        x = int(output_pos.x)
        y = int(output_pos.y)
        z = int(output_pos.z)

        # Add speed command if changed
        if not hasattr(self, 'last_feed_speed') or feed_speed != self.last_feed_speed:
            speed_cmd = f"V{feed_speed / 60:.1f};"
            self.output_lines.append(speed_cmd)
            self.last_feed_speed = feed_speed

        # Movement command
        move_cmd = f"Z{x},{y},{z};"
        self.output_lines.append(move_cmd)

        # Update current position
        self.current_pos = next_pos

    def rapid_positioning(self, next_pos):
        """Handle G00 - rapid positioning"""
        self.move(next_pos, self.rapid_feed_speed)

    def linear_interpolation(self, next_pos, feed_speed):
        """Handle G01 - linear interpolation"""
        self.move(next_pos, feed_speed)

    def circular_interpolation(self, next_pos, center_pos_inc, plane_select, direction, feed_speed):
        """Handle G02/G03 - circular interpolation"""
        mid_current_pos = self.plane_conv(self.current_pos, plane_select)
        mid_next_pos = self.plane_conv(next_pos, plane_select)
        mid_center_pos_inc = self.plane_conv(center_pos_inc, plane_select)
        mid_center_pos = self.current_pos.add(mid_center_pos_inc)

        mid_start_pos = mid_current_pos
        mid_delta1 = mid_current_pos.sub(mid_center_pos)
        mid_delta2 = mid_next_pos.sub(mid_center_pos)

        # Zero Z for calculations in the plane
        mid_delta1.z = 0.0
        mid_delta2.z = 0.0

        # Calculate angle
        delta_angle = math.acos(mid_delta1.dot(mid_delta2) / (mid_delta2.size() * mid_delta1.size()))
        if delta_angle <= 0:
            delta_angle += 2 * math.pi

        # Number of steps
        delta_steps = int(self.circular_resolution * delta_angle / (2 * math.pi))

        for step in range(delta_steps):
            theta_temp = 2 * math.pi * step / self.circular_resolution
            if direction == 2:  # Clockwise
                theta_temp *= -1

            # Calculate intermediate point
            pos = Vector()
            pos.x = (mid_delta1.x * math.cos(theta_temp) +
                     mid_delta1.y * math.sin(-theta_temp) +
                     mid_center_pos.x)
            pos.y = (mid_delta1.x * math.sin(theta_temp) +
                     mid_delta1.y * math.cos(theta_temp) +
                     mid_center_pos.y)
            pos.z = ((mid_next_pos.z - mid_start_pos.z) *
                     (theta_temp / delta_angle) + mid_start_pos.z)

            # Transform back and move
            actual_pos = self.plane_conv_inv(pos, plane_select)
            self.move(actual_pos, feed_speed)
            self.current_pos = actual_pos

        # Final move to end point
        self.move(next_pos, feed_speed)
        self.current_pos = next_pos

    def return_home(self, via_pos):
        """Handle G28 - return to home position"""
        self.rapid_positioning(via_pos)
        self.rapid_positioning(self.home_position)

    def process_word(self, address, value_str):
        """Process a single G-code word"""
        try:
            if address == ';':  # End of block
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

            elif address == 'F':  # Feed rate
                self.feed_speed = float(value_str)

            elif address == 'G':  # G-codes
                g_code = int(value_str)

                if g_code == 0:  # Rapid positioning
                    self.mov_mode = 0
                elif g_code == 1:  # Linear interpolation
                    self.mov_mode = 1
                elif g_code == 2:  # Circular interpolation CW
                    self.mov_mode = 2
                elif g_code == 3:  # Circular interpolation CCW
                    self.mov_mode = 3
                elif g_code == 4:  # Dwell
                    self.dwell_enable = 1
                elif g_code == 17:  # XY plane
                    self.plane_select = 17
                elif g_code == 18:  # XZ plane
                    self.plane_select = 18
                elif g_code == 19:  # YZ plane
                    self.plane_select = 19
                elif g_code == 20:  # Inches
                    self.mm_in = 20
                elif g_code == 21:  # Millimeters
                    self.mm_in = 21
                elif g_code == 28:  # Return home
                    self.mov_mode = 28
                elif g_code == 40:  # Cutter radius compensation off
                    self.TROC_mode = 40
                    self.TROC_tool_num = 0
                elif g_code == 41:  # Cutter radius compensation left
                    self.TROC_mode = 41
                elif g_code == 42:  # Cutter radius compensation right
                    self.TROC_mode = 42
                elif g_code == 43:  # Tool length compensation positive
                    self.TLOC_mode = 43
                elif g_code == 44:  # Tool length compensation negative
                    self.TLOC_mode = 44
                elif g_code == 49:  # Tool length compensation cancel
                    self.TLOC_mode = 49
                elif 54 <= g_code <= 59:  # Coordinate systems
                    self.coor_sys = g_code - 54
                elif g_code == 90:  # Absolute coordinates
                    self.abs_inc = 90
                    self.output_lines.append("^PA;")
                elif g_code == 91:  # Relative coordinates
                    self.abs_inc = 91
                    self.output_lines.append("^PR;")
                elif g_code == 94:  # Feed per minute
                    self.feed_mode = 94
                elif g_code == 95:  # Feed per revolution
                    self.feed_mode = 95

            elif address == 'I':  # Center offset X
                self.center_pos_inc.x = float(value_str)
            elif address == 'J':  # Center offset Y
                self.center_pos_inc.y = float(value_str)
            elif address == 'K':  # Center offset Z
                self.center_pos_inc.z = float(value_str)

            elif address == 'M':  # M-codes
                m_code = int(value_str)

                if m_code == 3:  # Spindle CW
                    self.spindle_state = 1
                    self.output_lines.append("!RC15;!MC1;")
                elif m_code == 4:  # Spindle CCW
                    self.spindle_state = -1
                    self.output_lines.append("!RC15;!MC1;")
                elif m_code == 5:  # Spindle stop
                    self.spindle_state = 0
                    self.output_lines.append("!MC0;")

            elif address == 'S':  # Spindle speed
                self.spindle_speed = float(value_str)

            elif address == 'X':  # X coordinate
                val = float(value_str)
                if self.abs_inc == 91:
                    self.next_pos.x += val
                else:
                    self.next_pos.x = val
                self.coor_changed = 1

            elif address == 'Y':  # Y coordinate
                val = float(value_str)
                if self.abs_inc == 91:
                    self.next_pos.y += val
                else:
                    self.next_pos.y = val
                self.coor_changed = 1

            elif address == 'Z':  # Z coordinate
                val = float(value_str)
                if self.abs_inc == 91:
                    self.next_pos.z += val
                else:
                    self.next_pos.z = val
                self.coor_changed = 1

        except ValueError as e:
            self.log(f"Value conversion error: {address}{value_str} - {e}")

    def convert(self, input_file_path, output_file_path):
        """Main conversion method"""
        self.log("Starting conversion...")

        # Import settings
        setting_file = os.path.join(os.path.dirname(__file__), "setting.txt")
        if os.path.exists(setting_file):
            self.import_settings(setting_file)
        else:
            self.log("Settings file setting.txt not found, using default values")

        # Read input file
        try:
            with open(input_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        except Exception as e:
            self.log(f"Error reading file: {e}")
            return False

        # Initialize output file
        self.output_lines = []
        self.output_lines.append(";;^IN;")
        self.output_lines.append("V85.0;")
        self.output_lines.append("^PR;")
        self.output_lines.append("Z0,0,15500;")
        self.output_lines.append("^PA;")

        # Parse G-code
        lines = content.split('\n')
        total_lines = len(lines)

        comment_mode = False
        for i, line in enumerate(lines):
            # Update progress
            if self.callback_progress:
                progress = (i + 1) / total_lines * 100
                self.callback_progress(progress)

            line = line.strip()
            if not line:
                continue

            # Remove comments in parentheses
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

            # Split into words
            words = re.findall(r'[A-Z][^A-Z;]*', line)

            for word in words:
                if word:
                    address = word[0]
                    value = word[1:]
                    self.process_word(address, value)

            # Process end of line
            self.process_word(';', '0')

        # Final command
        self.output_lines.append("^IN;")

        # Write to output file
        try:
            with open(output_file_path, 'w', encoding='utf-8') as f:
                f.write(''.join(self.output_lines))
            self.log(f"Conversion completed successfully!\nFile saved: {output_file_path}")
            return True
        except Exception as e:
            self.log(f"Error writing file: {e}")
            return False


# ==================== Graphical User Interface ====================

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
        # File selection frame
        file_frame = tk.Frame(self.window)
        file_frame.pack(pady=10, padx=10, fill=tk.X)

        # Input file
        tk.Label(file_frame, text="Input G-code file:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.input_file_var = tk.StringVar()
        tk.Entry(file_frame, textvariable=self.input_file_var, width=50).grid(row=0, column=1, padx=5)
        tk.Button(file_frame, text="Browse...", command=self.browse_input_file).grid(row=0, column=2)

        # Output file
        tk.Label(file_frame, text="Output RML file:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.output_file_var = tk.StringVar()
        tk.Entry(file_frame, textvariable=self.output_file_var, width=50).grid(row=1, column=1, padx=5)
        tk.Button(file_frame, text="Browse...", command=self.browse_output_file).grid(row=1, column=2)

        # Progress bar
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(self.window, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(pady=10, padx=10, fill=tk.X)

        # Control buttons
        button_frame = tk.Frame(self.window)
        button_frame.pack(pady=10)

        tk.Button(button_frame, text="Convert", command=self.start_conversion,
                  bg="lightblue", padx=20, pady=5).pack(side=tk.LEFT, padx=5)
        tk.Button(button_frame, text="Clear Log", command=self.clear_log,
                  bg="lightgray", padx=20, pady=5).pack(side=tk.LEFT, padx=5)
        tk.Button(button_frame, text="Exit", command=self.window.quit,
                  bg="lightcoral", padx=20, pady=5).pack(side=tk.LEFT, padx=5)

        # Log
        tk.Label(self.window, text="Execution log:").pack(anchor=tk.W, padx=10)

        self.log_text = scrolledtext.ScrolledText(self.window, height=15)
        self.log_text.pack(pady=10, padx=10, fill=tk.BOTH, expand=True)

        # Status
        self.status_var = tk.StringVar(value="Ready")
        status_bar = tk.Label(self.window, textvariable=self.status_var,
                              relief=tk.SUNKEN, anchor=tk.W)
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)

    def browse_input_file(self):
        filename = filedialog.askopenfilename(
            title="Select G-code file",
            filetypes=[("G-code files", "*.nc;*.cnc;*.gcode;*.txt"), ("All files", "*.*")]
        )
        if filename:
            self.input_file_var.set(filename)
            # Auto-generate output filename
            base = os.path.splitext(filename)[0]
            self.output_file_var.set(base + ".rml")

    def browse_output_file(self):
        filename = filedialog.asksaveasfilename(
            title="Save RML file as",
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
        self.status_var.set(f"Progress: {value:.1f}%")
        self.window.update_idletasks()

    def clear_log(self):
        self.log_text.delete(1.0, tk.END)

    def conversion_thread(self):
        input_file = self.input_file_var.get()
        output_file = self.output_file_var.get()

        if not input_file:
            messagebox.showerror("Error", "Select input file!")
            return

        if not output_file:
            messagebox.showerror("Error", "Select output file!")
            return

        try:
            success = self.converter.convert(input_file, output_file)
            if success:
                self.status_var.set("Conversion completed successfully!")
                messagebox.showinfo("Success", "Conversion completed successfully!")
            else:
                self.status_var.set("Conversion error!")
        except Exception as e:
            self.log_message(f"Error: {e}")
            self.status_var.set("Conversion error!")

    def start_conversion(self):
        # Run in separate thread to avoid blocking GUI
        thread = threading.Thread(target=self.conversion_thread)
        thread.daemon = True
        thread.start()

    def run(self):
        self.window.mainloop()


# ==================== Entry Point ====================

if __name__ == "__main__":
    # Create default settings file if it doesn't exist
    setting_file = "setting.txt"
    if not os.path.exists(setting_file):
        default_settings = """# Home position
homePosition = ( 0.0, 0.0, 0.0)

# Output coordinate offset
posOffset = ( 0.0, 0.0, 0.0 );

# Rapid movement speed
rapidFeedSpeed = 1000.0

# Circular interpolation resolution (number of divisions per revolution)
circularResolution = 360.0
"""
        with open(setting_file, 'w', encoding='utf-8') as f:
            f.write(default_settings)

    # Launch GUI
    app = GCodeConverterGUI()
    app.run()
