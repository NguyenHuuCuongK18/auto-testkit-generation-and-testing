import wx
from gooey import Gooey, GooeyParser
import subprocess
import threading
import os
import openpyxl
import time
import json
from datetime import datetime
from threading import Lock

# Global lock for file writes
_file_write_lock = Lock()

class InteractiveFrame(wx.Frame):
    def __init__(self, args):
        super().__init__(None, title=f"Test Case Generator - {args.test_case_name}", size=(800, 600))
        self.args = args
        self.current_stage = 1
        self.inputs = []
        self.client_process = None
        self.server_process = None
        self.thread_client = None
        self.thread_server = None
        self.client_record_file = None
        self.server_record_file = None
        self.client_output = []  # Store client console output
        self.server_output = []  # Store server console output

        # UI elements
        self.console = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
        self.stage_label = wx.StaticText(self, label=f"Current Stage: {self.current_stage}")
        self.input_label = wx.StaticText(self, label="Enter input (number or value):")
        self.input_entry = wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER)
        self.input_entry.Bind(wx.EVT_TEXT_ENTER, self.on_submit)
        submit_btn = wx.Button(self, label="Submit Input")
        submit_btn.Bind(wx.EVT_BUTTON, self.on_submit)
        record_btn = wx.Button(self, label="Record")
        record_btn.Bind(wx.EVT_BUTTON, self.on_record)

        # Layout
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.console, 1, wx.EXPAND | wx.ALL, 5)
        sizer.Add(self.stage_label, 0, wx.ALL, 5)
        sizer.Add(self.input_label, 0, wx.ALL, 5)
        sizer.Add(self.input_entry, 0, wx.EXPAND | wx.ALL, 5)
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        btn_sizer.Add(submit_btn, 0, wx.ALL, 5)
        btn_sizer.Add(record_btn, 0, wx.ALL, 5)
        sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)
        self.SetSizer(sizer)

        # Start processes
        self.setup_folders()
        self.start_processes()
        self.Show()

    def setup_folders(self):
        test_case_folder = os.path.join(self.args.save_location, self.args.test_case_name)
        if os.path.exists(test_case_folder):
            dlg = wx.MessageDialog(self, f"Test case folder '{self.args.test_case_name}' already exists. Overwrite?", "Confirm", wx.YES_NO)
            if dlg.ShowModal() != wx.ID_YES:
                self.Close()
                return
        os.makedirs(test_case_folder, exist_ok=True)
        record_folder = os.path.join(test_case_folder, "record")
        os.makedirs(record_folder, exist_ok=True)
        self.client_record_file = os.path.join(record_folder, "client_record.txt")
        self.server_record_file = os.path.join(record_folder, "server_record.txt")
        self.excel_file = os.path.join(test_case_folder, "testcase.xlsx")
        self.meta_file = os.path.join(test_case_folder, "meta.json")

        with _file_write_lock:
            open(self.client_record_file, 'w', encoding='utf-8', errors='replace').close()
            open(self.server_record_file, 'w', encoding='utf-8', errors='replace').close()

    def start_processes(self):
        client_path = self.args.client_path
        server_path = self.args.server_path

        if not os.path.exists(client_path) or not os.path.exists(server_path):
            wx.MessageBox("Selected executable files do not exist.", "Error")
            self.Close()
            return

        try:
            self.server_process = subprocess.Popen(
                server_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE,
                text=True,
                bufsize=1,
                universal_newlines=True
            )
            time.sleep(1.2)
            self.client_process = subprocess.Popen(
                client_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE,
                text=True,
                bufsize=1,
                universal_newlines=True
            )
        except Exception as e:
            wx.MessageBox(f"Failed to start processes: {e}", "Error")
            self.Close()
            return

        self.thread_server = threading.Thread(target=self.read_output, args=(self.server_process, self.server_output))
        self.thread_client = threading.Thread(target=self.read_output, args=(self.client_process, self.client_output))
        self.thread_server.daemon = True
        self.thread_client.daemon = True
        self.thread_server.start()
        self.thread_client.start()

    def read_output(self, process, output_list):
        while True:
            try:
                line = process.stdout.readline()
                if not line:
                    break
                with _file_write_lock:
                    output_list.append(line)
                wx.CallAfter(self.append_to_console, line)
            except Exception as e:
                break

    def append_to_console(self, text):
        self.console.AppendText(text)

    def on_submit(self, event):
        value = self.input_entry.GetValue().strip()
        if not value:
            wx.MessageBox("Input is required.", "Error")
            return
        self.inputs.append(value)
        self.append_to_console(f"[Input] Stage {self.current_stage}: {value}\n")
        try:
            self.client_process.stdin.write(value + '\n')
            self.client_process.stdin.flush()
        except Exception as e:
            wx.MessageBox(f"Failed to send input to client: {e}", "Error")
            return
        self.input_entry.Clear()
        self.current_stage += 1
        self.stage_label.SetLabel(f"Current Stage: {self.current_stage}")
        time.sleep(0.2)

    def on_record(self, event):
        time.sleep(0.6)
        dlg = wx.TextEntryDialog(self, "Enter points for this test case:", "Enter Points")
        if dlg.ShowModal() == wx.ID_OK:
            points = dlg.GetValue().strip()
            if not points:
                wx.MessageBox("Points are required.", "Error")
                return
            # Send empty input to trigger client exit
            try:
                self.client_process.stdin.write('\n')
                self.client_process.stdin.flush()
                self.append_to_console("[Input] Stage {}: <empty input to exit>\n".format(self.current_stage))
            except Exception as e:
                self.append_to_console(f"Failed to send empty input to client: {e}\n")

            # Allow processes to output final messages
            time.sleep(1.0)

            # Close processes
            try:
                self.client_process.stdin.close()
                self.server_process.stdin.close()
            except Exception:
                pass

            try:
                self.client_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.append_to_console("Client process timed out. Forcing termination.\n")
                self.client_process.terminate()
            try:
                self.server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.append_to_console("Server process timed out. Forcing termination.\n")
                self.server_process.terminate()

            self.thread_client.join(timeout=5)
            self.thread_server.join(timeout=5)

            # Write accumulated output to files
            with _file_write_lock:
                with open(self.client_record_file, 'w', encoding='utf-8', errors='replace') as f:
                    f.writelines(self.client_output)
                with open(self.server_record_file, 'w', encoding='utf-8', errors='replace') as f:
                    f.writelines(self.server_output)

            # Create meta
            meta = {
                "test_case_name": self.args.test_case_name,
                "stages": len(self.inputs),
                "inputs": self.inputs,
                "points": points,
                "timestamp": datetime.now().isoformat()
            }
            with open(self.meta_file, 'w', encoding='utf-8') as f:
                json.dump(meta, f, indent=4, ensure_ascii=False)

            # Create Excel
            wb = openpyxl.Workbook()
            for stage in range(1, self.current_stage):
                ws = wb.create_sheet(title=f"Stage {stage}")
                for i in range(1, stage + 1):
                    ws.cell(row=i, column=1).value = f"Stage {i}"
                    ws.cell(row=i, column=2).value = "input"
                    ws.cell(row=i, column=3).value = self.inputs[i - 1] if i - 1 < len(self.inputs) else ""
                if stage == self.current_stage - 1:
                    ws.cell(row=1, column=4).value = "record"
                    for i in range(1, stage + 1):
                        ws.cell(row=i, column=4).value = "yes" if i == stage else "none"
            if 'Sheet' in wb.sheetnames:
                wb.remove(wb['Sheet'])
            wb.save(self.excel_file)

            wx.MessageBox(f"Recorded to {self.excel_file}, {os.path.dirname(self.client_record_file)}, and {self.meta_file}", "Success")
            self.Close()

@Gooey(program_name="Test Case Generator", default_size=(800, 600))
def main():
    parser = GooeyParser(description="Generate test cases for client-server applications")
    parser.add_argument('client_path', help="Client Executable", widget='FileChooser')
    parser.add_argument('server_path', help="Server Executable", widget='FileChooser')
    parser.add_argument('test_case_name', help="Test Case Name")
    parser.add_argument('save_location', help="Save Location (directory)", widget='DirChooser', default=os.getcwd())
    args = parser.parse_args()

    app = wx.App(False)
    frame = InteractiveFrame(args)
    app.MainLoop()

if __name__ == '__main__':
    main()