import wx
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
from typing import List, Optional, Dict, Any
from gooey import Gooey, GooeyParser
import wx.adv  # For wx.MessageDialog
import shutil  # <-- added

# Global lock for thread-safe file writes
_file_write_lock: Lock = Lock()

class GradingFrame(wx.Frame):
    """GUI frame for grading client-server applications against test cases."""
    def __init__(self, args: GooeyParser) -> None:
        """
        Initialize the GradingFrame with provided arguments.

        Args:
            args (GooeyParser): Parsed command-line arguments containing test cases folder,
                               student client/server paths, and save log folder.
        """
        super().__init__(None, title="Auto Grading", size=(800, 600))
        self.args: GooeyParser = args
        self.console: wx.TextCtrl = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
        self.current_client_process: Optional[subprocess.Popen] = None
        self.current_server_process: Optional[subprocess.Popen] = None
        self.current_client_thread: Optional[threading.Thread] = None
        self.current_server_thread: Optional[threading.Thread] = None

        # Initialize UI elements
        end_btn: wx.Button = wx.Button(self, label="End All Processes")
        end_btn.Bind(wx.EVT_BUTTON, self.on_end_processes)

        # Set up layout
        sizer: wx.BoxSizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.console, 1, wx.EXPAND | wx.ALL, 5)
        btn_sizer: wx.BoxSizer = wx.BoxSizer(wx.HORIZONTAL)
        btn_sizer.Add(end_btn, 0, wx.ALL, 5)
        sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)
        self.SetSizer(sizer)
        self.Show()

        # Bind close event
        self.Bind(wx.EVT_CLOSE, self.on_close)

        # Start test execution in a separate thread
        threading.Thread(target=self.run_tests, args=(args,), daemon=True).start()

    def append_to_console(self, text: str) -> None:
        """
        Append text to the console display.

        Args:
            text (str): The text to append.
        """
        wx.CallAfter(self.console.AppendText, text)

    def on_end_processes(self, event: wx.Event) -> None:
        """Handle the 'End All Processes' button click."""
        self.append_to_console("Ending all current processes...\n")
        self.cleanup_current_processes()
        self.append_to_console("Processes ended.\n")

    def cleanup_current_processes(self) -> None:
        """Clean up running processes and threads."""
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

    def on_close(self, event: wx.Event) -> None:
        """
        Handle window close event, ensuring processes are cleaned up.

        Args:
            event (wx.Event): The close event.
        """
        self.append_to_console("Closing application. Cleaning up processes...\n")
        self.cleanup_current_processes()
        event.Skip()

    def _build_command_for_path(self, path: str):
        """
        Build subprocess command for given path.
        If path is a .dll, run via dotnet; otherwise return the path directly.
        """
        if path.lower().endswith('.dll'):
            dotnet_path = shutil.which('dotnet')
            if not dotnet_path:
                wx.MessageBox("dotnet runtime not found in PATH. Please install .NET or ensure 'dotnet' is available.", "Error")
                return None
            return [dotnet_path, path]
        return path

    def run_tests(self, args: GooeyParser) -> None:
        """
        Run all test cases and compare outputs with expected results.

        Args:
            args (GooeyParser): Parsed command-line arguments.
        """
        test_cases_folder: str = args.test_cases_folder
        student_client_path: str = args.student_client
        student_server_path: str = args.student_server
        save_log_folder: str = args.save_log_folder

        if not os.path.exists(student_client_path) or not os.path.exists(student_server_path):
            self.append_to_console("Selected executable/DLL files do not exist.\n")
            return

        test_cases: List[Dict[str, str]] = []
        for item in os.listdir(test_cases_folder):
            item_path: str = os.path.join(test_cases_folder, item)
            if os.path.isdir(item_path):
                meta_path: str = os.path.join(item_path, "meta.json")
                record_folder: str = os.path.join(item_path, "record")
                client_record: str = os.path.join(record_folder, "client_record.txt")
                server_record: str = os.path.join(record_folder, "server_record.txt")
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

        total_points: int = 0
        awarded_points: int = 0
        results: List[Dict[str, Any]] = []

        for test_case in test_cases:
            self.cleanup_current_processes()  # Ensure clean state

            self.append_to_console(f"\nRunning test case: {test_case['name']}\n")

            with open(test_case['meta_path'], 'r', encoding='utf-8') as f:
                meta: Dict[str, Any] = json.load(f)
            inputs: List[str] = meta.get('inputs', [])
            points: str = meta.get('points', '0')
            try:
                points_value: int = int(points)
            except ValueError:
                points_value = 0
            total_points += points_value

            with tempfile.TemporaryDirectory() as temp_dir:
                student_client_record: str = os.path.join(temp_dir, "student_client_record.txt")
                student_server_record: str = os.path.join(temp_dir, "student_server_record.txt")
                with _file_write_lock:
                    open(student_client_record, 'w', encoding='utf-8', errors='replace').close()
                    open(student_server_record, 'w', encoding='utf-8', errors='replace').close()

                try:
                    client_cmd = self._build_command_for_path(student_client_path)
                    server_cmd = self._build_command_for_path(student_server_path)
                    if client_cmd is None or server_cmd is None:
                        results.append({'test_case': test_case['name'], 'status': 'Failed to start', 'points': 0, 'reason': 'dotnet not available for DLL'})
                        break

                    self.current_server_process = subprocess.Popen(
                        server_cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        stdin=subprocess.PIPE,
                        text=True,
                        bufsize=1,
                        universal_newlines=True
                    )
                    time.sleep(1.2)  # Allow server to initialize
                    self.current_client_process = subprocess.Popen(
                        client_cmd,
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
                    self.cleanup_current_processes()
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
                        time.sleep(0.5)
                    except Exception as e:
                        self.append_to_console(f"Failed to send input for {test_case['name']}: {e}\n")
                        self.cleanup_current_processes()
                        break

                time.sleep(3.0)  # Allow final outputs

                if self.current_client_process.poll() is None:
                    self.append_to_console("Terminating client process.\n")
                    self.current_client_process.terminate()
                if self.current_server_process.poll() is None:
                    self.append_to_console("Terminating server process.\n")
                    self.current_server_process.terminate()

                self.current_client_thread.join(timeout=5)
                self.current_server_thread.join(timeout=5)

                client_diff: Optional[str] = self.get_diff(test_case['client_record'], student_client_record, "Client")
                server_diff: Optional[str] = self.get_diff(test_case['server_record'], student_server_record, "Server")

                if not client_diff and not server_diff:
                    awarded_points += points_value
                    self.append_to_console(f"Test case {test_case['name']} passed. Awarded {points_value} points.\n")
                    results.append({'test_case': test_case['name'], 'status': 'Passed', 'points': points_value, 'reason': 'Outputs match'})
                else:
                    reason: str = ''
                    if client_diff:
                        reason += client_diff + '\n'
                    if server_diff:
                        reason += server_diff + '\n'
                    self.append_to_console(f"Test case {test_case['name']} failed. Reason: {reason}\n")
                    results.append({'test_case': test_case['name'], 'status': 'Failed', 'points': 0, 'reason': reason})

                self.cleanup_current_processes()

        self.append_to_console(f"\nTotal points: {awarded_points} / {total_points}\n")

        os.makedirs(save_log_folder, exist_ok=True)
        results_excel: str = os.path.join(save_log_folder, f"test_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")
        wb: openpyxl.Workbook = openpyxl.Workbook()
        ws: openpyxl.worksheet.worksheet.Worksheet = wb.active
        ws.title = "Test Results"
        ws.append(["Test Case", "Status", "Points Awarded", "Reason"])
        for res in results:
            ws.append([res['test_case'], res['status'], res['points'], res['reason']])
        ws.append(["Total", "", awarded_points, f"/ {total_points}"])
        wb.save(results_excel)
        self.append_to_console(f"Results saved to {results_excel}\n")

        wx.CallAfter(wx.MessageBox, f"Testing completed. Total points: {awarded_points} / {total_points}. Results in {results_excel}", "Completed")

    def read_output(self, process: subprocess.Popen, filename: str) -> None:
        """
        Read output from a process and write to a file.

        Args:
            process (subprocess.Popen): The process to read output from.
            filename (str): The file to write output to.
        """
        while True:
            try:
                line: str = process.stdout.readline()
                if not line:
                    break
                normalized_line: str = line.replace('\r\n', '\n').rstrip('\n') + '\n'
                with _file_write_lock:
                    with open(filename, 'a', encoding='utf-8', errors='replace') as f:
                        f.write(normalized_line)
                        f.flush()
                self.append_to_console(normalized_line)
            except Exception:
                break

    def get_diff(self, file1: str, file2: str, label: str) -> Optional[str]:
        """
        Compare two files and return a diff if they differ.

        This version normalizes outputs to avoid false negatives caused by:
          - Different newline/flush behavior between .exe and .dll runs
          - Prompts printed with or without trailing newline
          - Extra whitespace or empty lines

        Normalization steps:
          - Normalize CRLF to LF
          - Remove occurrences of the interactive prompt:
              "Enter ProductId to query (Press Enter to exit):"
            (adjust this string if your prompt text is different)
          - Collapse multiple whitespace into single spaces on each line
          - Remove empty lines at the ends and in-between
        """
        prompt = "Enter ProductId to query (Press Enter to exit):"

        def read_and_normalize(path: str) -> List[str]:
            with open(path, 'r', encoding='utf-8', errors='replace') as fh:
                raw = fh.read()
            # Normalize line endings
            raw = raw.replace('\r\n', '\n').replace('\r', '\n')
            # Remove the prompt everywhere (standalone or appended)
            raw = raw.replace(prompt, '')
            # Split into lines, collapse whitespace on each line, and drop empty lines
            lines = []
            for line in raw.split('\n'):
                collapsed = ' '.join(line.split())  # collapse all whitespace
                if collapsed != '':
                    lines.append(collapsed)
            # Trim trailing empty lines if any (shouldn't be any after filtering)
            while lines and lines[-1] == '':
                lines.pop()
            return lines

        lines1 = read_and_normalize(file1)
        lines2 = read_and_normalize(file2)

        if lines1 == lines2:
            return None

        diff: List[str] = list(difflib.unified_diff(
            lines1,
            lines2,
            fromfile='expected',
            tofile='actual',
            lineterm='\n'
        ))
        diff_str: str = ''.join(diff)
        # limit diff length so logs don't explode
        return f"{label} mismatch:\n{diff_str[:2000]}"


@Gooey(program_name="Auto Grading", default_size=(800, 600))
def main() -> None:
    """Main function to set up the GUI and parse command-line arguments."""
    parser: GooeyParser = GooeyParser(description="Grade student client-server applications")
    parser.add_argument('test_cases_folder', help="Test Cases Folder", widget='DirChooser')
    parser.add_argument('student_client', help="Student Client Executable or DLL", widget='FileChooser')
    parser.add_argument('student_server', help="Student Server Executable or DLL", widget='FileChooser')
    parser.add_argument('save_log_folder', help="Save Log Folder", widget='DirChooser', default=os.getcwd())
    args: GooeyParser = parser.parse_args()

    app: wx.App = wx.App(False)
    frame: GradingFrame = GradingFrame(args)
    app.MainLoop()

if __name__ == '__main__':
    main()
