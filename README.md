[README.md](https://github.com/user-attachments/files/31865369/README.md)
# Port-Harcourt-High-Court-Website# HCT6 File Portal

Standalone filing desk for the **High Court Portal**.

The existing High Court 6 Excel workbook stays the backend. This app automates intake, sitting notes, cause lists, counsel lookup, and quarterly figures without changing LISTS, Insights, Back Office, Auto Cause List, or any Excel formulas.

## What it does

- Files a matter onto the `HCT6_CR` case register
- Records a sitting onto the `HCT6_HL` hearing log
- Auto-fills parties and counsel from the register
- Builds a printable daily cause list from next dates
- Extracts the counsel directory
- Computes the same KPI / NJC quarterly counts the workbook already calculates
- Keeps timestamped backups before every Excel export
- Stores daily work in a local desk database so the original workbook is never overwritten

## How to use

### 1. Direct Start (Recommended for Windows)

Double-click **`Open-HCT6.cmd`** or **`Start-HCT6.bat`** in either the root folder or the `hct6-file-portal` directory.

The launcher will:
1. Automatically detect your local Python installation (Python 3.8 to 3.14+).
2. Validate and self-heal the virtual environment if copied from another PC.
3. Launch the portal in the background.
4. Automatically open the portal in your default browser at `http://127.0.0.1:8765`.

If the portal is already running, clicking the start file simply brings up your browser directly to the portal.

### 2. Open from PowerShell

You can also start the desk using PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\hct6-file-portal\Start-HCT6.ps1"
```

Or from inside the `hct6-file-portal` directory:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\Start-HCT6.ps1"
```

### 3. In Pinokio

1. Open **HCT6 File Portal**.
2. Click **Install**, then **Start**.
3. Click **Open High Court Portal**.
4. File a matter, optionally list it for mention, then record sittings after court.
5. Print the cause list for the day. Open Insights or Quarterly return when you need NJC figures.

---

## Daily Filing & Excel Synchronization

Daily filing is stored locally in SQLite (`app/data/desk.db`). Use **Settings → Export working copy** when you want a filled Excel workbook. That export is built from the original template; the source portal file is not overwritten. Point Settings at another copy of the same portal if you already keep the live file elsewhere.

## What is not changed

- No sheet is redesigned
- Named ranges, slicers, and formulas stay in the workbook
- The app only writes user-entered cells in `CASE REGISTER` and `HEARING LOG`

## API Endpoints

- `GET /api/health`
- `GET /api/cases`
- `POST /api/cases`
- `GET /api/hearings`
- `POST /api/hearings`
- `GET /api/cause-list?date_on=YYYY-MM-DD`
- `GET /api/counsel`
- `GET /api/insights?year=YYYY&quarter=QX`
- `GET /api/lookups`
