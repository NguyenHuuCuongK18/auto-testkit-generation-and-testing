# auto-testkit-generation-and-testing

Simple GUI tools to generate test cases for client-server programs and to auto-grade student submissions.

Prerequisites
- Windows OS
- Python 3.8+
- .NET SDK (only if you need to build an executable with `dotnet publish`)

Install Python dependencies
- Ensure a requirements file exists as `requirements.txt`. If your repo has `requirements` rename or copy it to `requirements.txt`.
- Install with:

```bash
pip install -r requirements.txt
```

Build the executable (optional)
- If your client or server are .NET projects, and you need an `.exe`, publish with:

```bash
dotnet publish -c release -o ./<directory-name>
```

- The published `.exe` files will be in `./<directory-name>`.

Run the Test Case Generator
1. Start the GUI:

```bash
python TestCaseGenerator.py
```

2. In the GUI:
- For `Client Executable` choose the client `.exe`.
- For `Server Executable` choose the server `.exe`.
- Enter `Test Case Name`.
- Choose a `Save Location` directory.
- Interact with the running client: enter inputs and click `Submit Input`.
- When finished click `Record` and enter points; this creates a test case folder containing:
  - `testcase.xlsx`
  - `meta.json`
  - `record/client_record.txt`
  - `record/server_record.txt`

Auto testing (grading student submissions)
1. Start the GUI:

```bash
python AutoGrading.py
```

2. In the GUI:
- For `Test Cases Folder` select the folder that contains the test case folders created by the test generator.
- For `Student Client Executable` choose the student client `.exe`.
- For `Student Server Executable` choose the student server `.exe`.
- For `Save Log Folder` choose where to save grading logs and the results Excel file.

Output
- Grading produces a log saved under the chosen `Save Log Folder` and an Excel file `test_results_YYYYMMDD_HHMMSS.xlsx` with per-test results and totals.

Notes
- Ensure `Gooey` and `wxPython` are installed and compatible with your Python version. On Windows installing `wxPython` may require matching wheel files.
- If executables fail to start, confirm file paths and permissions.
- All files created by the generator are placed under the chosen `Save Location` inside a folder named by the test case.