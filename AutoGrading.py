import wx
from gooey import Gooey, GooeyParser
import subprocess
import threading
import os
import openpyxl
import time
import json
from datetime import datetime
import difflib
import tempfile
from threading import Lock

# Global lock for file writes
_file_write_lock = Lock()

class GradingFrame(wx.Frame):
    def __init__(self, args):
        super().__init__(None, title="Auto Grading", size=(800, 600))
        self.args = args
        self.console = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
        self.current_client_process = None
        self.current_server_process = None
        self.current_client_thread = None
        self.current_server_thread = None
        end_btn = wx.Button(self, label="End All Processes")
        end_btn.Bind(wx.EVT_BUTTON, self.on_end_processes)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.console, 1, wx.EXPAND | wx.ALL, 5)
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        btn_sizer.Add(end_btn, 0, wx.ALL, 5)
        sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)
        self.SetSizer(sizer)
        self.Show()

        # Bind close event
        self.Bind(wx.EVT_CLOSE, self.on_close)

        threading.Thread(target=self.run_tests, args=(args,), daemon=True).start()

    def append_to_console(self, text):
        wx.CallAfter(self.console.AppendText, text)

    def on_end_processes(self, event):
        self.append_to_console("Ending all current processes...\n")
        self.cleanup_current_processes()
        self.append_to_console("Processes ended.\n")

    def cleanup_current_processes(self):
        if self.current_client_process and self.current_client_process.poll() is None:
            self.current_client_process.terminate()
            try:
                self.current_client_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.current_client_process.kill()
        if self.current_server_process and self.current_server_process.poll() is None:
            self.current_server_process.terminate()
            try:
                self.current_server_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.current_server_process.kill()
        if self.current_client_thread:
            self.current_client_thread.join(timeout=5)
        if self.current_server_thread:
            self.current_server_thread.join(timeout=5)
        self.current_client_process = None
        self.current_server_process = None
        self.current_client_thread = None
        self.current_server_thread = None

    def on_close(self, event):
        self.append_to_console("Closing application. Cleaning up processes...\n")
        self.cleanup_current_processes()
        event.Skip()  # Allow the default close behavior after cleanup

    def run_tests(self, args):
        test_cases_folder = args.test_cases_folder
        student_client_path = args.student_client
        student_server_path = args.student_server
        save_log_folder = args.save_log_folder

        if not os.path.exists(student_client_path) or not os.path.exists(student_server_path):
            self.append_to_console("Selected executable files do not exist.\n")
            return

        test_cases = []
        for item in os.listdir(test_cases_folder):
            item_path = os.path.join(test_cases_folder, item)
            if os.path.isdir(item_path):
                meta_path = os.path.join(item_path, "meta.json")
                record_folder = os.path.join(item_path, "record")
                client_record = os.path.join(record_folder, "client_record.txt")
                server_record = os.path.join(record_folder, "server_record.txt")
                if os.path.exists(meta_path) and os.path.exists(client_record) and os.path.exists(server_record):
                    test_cases.append({
                        'name': item,
                        'meta_path': meta_path,
                        'client_record': client_record,
                        'server_record': server_record
                    })

        if not test_cases:
            self.append_to_console("No valid test cases found in the folder.\n")
            return

        self.append_to_console(f"Found {len(test_cases)} test cases.\n")

        total_points = 0
        awarded_points = 0
        results = []

        for test_case in test_cases:
            # Clean up any previous processes before starting new ones
            self.cleanup_current_processes()

            self.append_to_console(f"\nRunning test case: {test_case['name']}\n")

            with open(test_case['meta_path'], 'r', encoding='utf-8') as f:
                meta = json.load(f)
            inputs = meta.get('inputs', [])
            points = meta.get('points', '0')
            try:
                points_value = int(points)
            except ValueError:
                points_value = 0
            total_points += points_value

            with tempfile.TemporaryDirectory() as temp_dir:
                student_client_record = os.path.join(temp_dir, "student_client_record.txt")
                student_server_record = os.path.join(temp_dir, "student_server_record.txt")
                with _file_write_lock:
                    open(student_client_record, 'w', encoding='utf-8', errors='replace').close()
                    open(student_server_record, 'w', encoding='utf-8', errors='replace').close()

                try:
                    self.current_server_process = subprocess.Popen(
                        student_server_path,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        stdin=subprocess.PIPE,
                        text=True,
                        bufsize=1,
                        universal_newlines=True
                    )
                    time.sleep(1.2)
                    self.current_client_process = subprocess.Popen(
                        student_client_path,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        stdin=subprocess.PIPE,
                        text=True,
                        bufsize=1,
                        universal_newlines=True
                    )
                except Exception as e:
                    self.append_to_console(f"Failed to start processes for {test_case['name']}: {e}\n")
                    results.append({'test_case': test_case['name'], 'status': 'Failed to start', 'points': 0, 'reason': str(e)})
                    self.cleanup_current_processes()  # Ensure cleanup on failure
                    continue

                self.current_server_thread = threading.Thread(target=self.read_output, args=(self.current_server_process, student_server_record))
                self.current_client_thread = threading.Thread(target=self.read_output, args=(self.current_client_process, student_client_record))
                self.current_server_thread.daemon = True
                self.current_client_thread.daemon = True
                self.current_server_thread.start()
                self.current_client_thread.start()

                for value in inputs:
                    try:
                        self.current_client_process.stdin.write(value + '\n')
                        self.current_client_process.stdin.flush()
                        self.append_to_console(f"[{test_case['name']} Input] {value}\n")
                        time.sleep(0.5)  # Adjusted for consistency with generator
                    except Exception as e:
                        self.append_to_console(f"Failed to send input for {test_case['name']}: {e}\n")
                        self.cleanup_current_processes()  # Clean up on input error
                        break

                # Allow time for final outputs without closing stdin
                time.sleep(3.0)

                # Terminate processes if still running
                if self.current_client_process.poll() is None:
                    self.append_to_console("Terminating client process.\n")
                    self.current_client_process.terminate()
                if self.current_server_process.poll() is None:
                    self.append_to_console("Terminating server process.\n")
                    self.current_server_process.terminate()

                self.current_client_thread.join(timeout=5)
                self.current_server_thread.join(timeout=5)

                client_diff = self.get_diff(test_case['client_record'], student_client_record, "Client")
                server_diff = self.get_diff(test_case['server_record'], student_server_record, "Server")

                if not client_diff and not server_diff:
                    awarded_points += points_value
                    self.append_to_console(f"Test case {test_case['name']} passed. Awarded {points_value} points.\n")
                    results.append({'test_case': test_case['name'], 'status': 'Passed', 'points': points_value, 'reason': 'Outputs match'})
                else:
                    reason = ''
                    if client_diff:
                        reason += client_diff + '\n'
                    if server_diff:
                        reason += server_diff + '\n'
                    self.append_to_console(f"Test case {test_case['name']} failed. Reason: {reason}\n")
                    results.append({'test_case': test_case['name'], 'status': 'Failed', 'points': 0, 'reason': reason})

                # Clean up after each test
                self.cleanup_current_processes()

        self.append_to_console(f"\nTotal points: {awarded_points} / {total_points}\n")

        # Ensure the save_log_folder exists
        os.makedirs(save_log_folder, exist_ok=True)
        results_excel = os.path.join(save_log_folder, f"test_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Test Results"
        ws.append(["Test Case", "Status", "Points Awarded", "Reason"])
        for res in results:
            ws.append([res['test_case'], res['status'], res['points'], res['reason']])
        ws.append(["Total", "", awarded_points, f"/ {total_points}"])
        wb.save(results_excel)
        self.append_to_console(f"Results saved to {results_excel}\n")

        wx.CallAfter(wx.MessageBox, f"Testing completed. Total points: {awarded_points} / {total_points}. Results in {results_excel}", "Completed")

    def read_output(self, process, filename):
        while True:
            try:
                line = process.stdout.readline()
                if not line:
                    break
                # Normalize line endings to \n
                normalized_line = line.replace('\r\n', '\n').rstrip('\n') + '\n'
                with _file_write_lock:
                    with open(filename, 'a', encoding='utf-8', errors='replace') as f:
                        f.write(normalized_line)
                        f.flush()
                self.append_to_console(normalized_line)
            except Exception as e:
                break

    def get_diff(self, file1, file2, label):
        with open(file1, 'r', encoding='utf-8', errors='replace') as f1, \
                open(file2, 'r', encoding='utf-8', errors='replace') as f2:
            # Read lines and normalize line endings
            lines1 = [line.replace('\r\n', '\n').rstrip('\n') for line in f1.readlines()]
            lines2 = [line.replace('\r\n', '\n').rstrip('\n') for line in f2.readlines()]
            # Remove trailing empty lines to avoid false diffs
            while lines1 and not lines1[-1]:
                lines1.pop()
            while lines2 and not lines2[-1]:
                lines2.pop()

        # Compare normalized lines
        if lines1 == lines2:
            return None

        # Generate diff for reporting
        diff = difflib.unified_diff(
            lines1,
            lines2,
            fromfile='expected',
            tofile='actual',
            lineterm='\n'
        )
        diff_str = ''.join(diff)
        return f"{label} mismatch:\n{diff_str[:2000]}"

@Gooey(program_name="Auto Grading", default_size=(800, 600))
def main():
    parser = GooeyParser(description="Grade student client-server applications")
    parser.add_argument('test_cases_folder', help="Test Cases Folder", widget='DirChooser')
    parser.add_argument('student_client', help="Student Client Executable", widget='FileChooser')
    parser.add_argument('student_server', help="Student Server Executable", widget='FileChooser')
    parser.add_argument('save_log_folder', help="Save Log Folder", widget='DirChooser', default=os.getcwd())
    args = parser.parse_args()

    app = wx.App(False)
    frame = GradingFrame(args)
    app.MainLoop()

if __name__ == '__main__':
    main()