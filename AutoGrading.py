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
        self.console = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.console, 1, wx.EXPAND | wx.ALL, 5)
        self.SetSizer(sizer)
        self.Show()
        threading.Thread(target=self.run_tests, args=(args,)).start()

    def append_to_console(self, text):
        wx.CallAfter(self.console.AppendText, text)

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
                    server_process = subprocess.Popen(
                        student_server_path,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        stdin=subprocess.PIPE,
                        text=True,
                        bufsize=1,
                        universal_newlines=True
                    )
                    time.sleep(1.2)
                    client_process = subprocess.Popen(
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
                    continue

                thread_server = threading.Thread(target=self.read_output, args=(server_process, student_server_record))
                thread_client = threading.Thread(target=self.read_output, args=(client_process, student_client_record))
                thread_server.daemon = True
                thread_client.daemon = True
                thread_server.start()
                thread_client.start()

                for value in inputs:
                    try:
                        client_process.stdin.write(value + '\n')
                        client_process.stdin.flush()
                        self.append_to_console(f"[{test_case['name']} Input] {value}\n")
                        time.sleep(1.2)
                    except Exception as e:
                        self.append_to_console(f"Failed to send input for {test_case['name']}: {e}\n")

                time.sleep(1.0)

                try:
                    client_process.stdin.close()
                    server_process.stdin.close()
                except Exception:
                    pass

                try:
                    client_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.append_to_console("Client process timed out. Forcing termination.\n")
                    client_process.terminate()
                try:
                    server_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.append_to_console("Server process timed out. Forcing termination.\n")
                    server_process.terminate()

                thread_client.join(timeout=5)
                thread_server.join(timeout=5)

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
                with _file_write_lock:
                    with open(filename, 'a', encoding='utf-8', errors='replace') as f:
                        f.write(line)
                        f.flush()
                self.append_to_console(line)
            except Exception as e:
                break

    def get_diff(self, file1, file2, label):
        with open(file1, 'r', encoding='utf-8', errors='replace') as f1, \
                open(file2, 'r', encoding='utf-8', errors='replace') as f2:
            lines1 = f1.readlines()
            lines2 = f2.readlines()

        diff = difflib.unified_diff(lines1, lines2, fromfile='expected', tofile='actual', lineterm='')
        diff_str = ''.join(diff)

        if diff_str:
            return f"{label} mismatch:\n{diff_str[:2000]}"
        return None

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