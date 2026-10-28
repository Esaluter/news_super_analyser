"""Isolated Google Sheets API experiment for the native =AI() formula.

The script touches only ``Тест джемини!A3:B3`` and never uses the production
Apps Script gateway. Authentication is a short-lived user OAuth access token in
``GOOGLE_SHEETS_ACCESS_TOKEN``. The token is never written to output or logs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests


DEFAULT_SPREADSHEET_ID = "1t-QAB23gHpggzHEq49AGVfpjHf2cu7jcxRY9fObbOL8"
SHEET_NAME = "Тест джемини"
INPUT_RANGE = f"'{SHEET_NAME}'!A3"
FORMULA_RANGE = f"'{SHEET_NAME}'!B3"
PAIR_RANGE = f"'{SHEET_NAME}'!A3:B3"
INPUT_TEXT = "Москва, АО Газпром"
AI_FORMULA = (
    '=AI("Find one publicly available address of the company in the referenced '
    'cell. Return only the address in Russian.";A3)'
)
API_ROOT = "https://sheets.googleapis.com/v4/spreadsheets"
OUTPUT_PATH = Path("experiments/output/google_sheets_ai_formula_result.json")
ERROR_VALUES = {
    "#ERROR!",
    "#N/A",
    "#NAME?",
    "#REF!",
    "#VALUE!",
    "#NUM!",
    "#NULL!",
}
PENDING_VALUES = {"", "loading...", "загрузка...", "generating...", "создание..."}


class ExperimentError(RuntimeError):
    pass


@dataclass
class ExperimentResult:
    started_at: str
    finished_at: str = ""
    spreadsheet_id: str = ""
    sheet_name: str = SHEET_NAME
    input_range: str = INPUT_RANGE
    formula_range: str = FORMULA_RANGE
    status: str = "not_started"
    reason: str = ""
    previous_formula_view: list[list[str]] | None = None
    previous_formatted_view: list[list[str]] | None = None
    write_response: dict[str, Any] | None = None
    formula_view: list[list[str]] | None = None
    formatted_view: list[list[str]] | None = None
    polls: int = 0
    elapsed_seconds: float = 0.0


class SheetsApi:
    def __init__(self, spreadsheet_id: str, token: str, timeout: float) -> None:
        self.spreadsheet_id = spreadsheet_id
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        )

    def sheet_titles(self) -> set[str]:
        response = self._request(
            "GET",
            f"{API_ROOT}/{self.spreadsheet_id}",
            params={"fields": "sheets.properties.title"},
        )
        return {
            str(sheet.get("properties", {}).get("title", ""))
            for sheet in response.get("sheets", [])
        }

    def read_values(self, cell_range: str, render: str) -> list[list[str]]:
        encoded = quote(cell_range, safe="")
        payload = self._request(
            "GET",
            f"{API_ROOT}/{self.spreadsheet_id}/values/{encoded}",
            params={
                "valueRenderOption": render,
                "dateTimeRenderOption": "FORMATTED_STRING",
            },
        )
        values = payload.get("values", [])
        if not isinstance(values, list):
            raise ExperimentError("Google Sheets API returned invalid values")
        return values

    def write_test_cells(self) -> dict[str, Any]:
        encoded = quote(PAIR_RANGE, safe="")
        return self._request(
            "PUT",
            f"{API_ROOT}/{self.spreadsheet_id}/values/{encoded}",
            params={
                "valueInputOption": "USER_ENTERED",
                "includeValuesInResponse": "true",
                "responseValueRenderOption": "FORMULA",
            },
            json={
                "range": PAIR_RANGE,
                "majorDimension": "ROWS",
                "values": [[INPUT_TEXT, AI_FORMULA]],
            },
        )

    def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self.session.request(
                method, url, timeout=self.timeout, **kwargs
            )
        except requests.RequestException as exc:
            raise ExperimentError(
                f"Google Sheets transport failure: {type(exc).__name__}"
            ) from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise ExperimentError(_safe_http_error(response))
        try:
            payload = response.json()
        except ValueError as exc:
            raise ExperimentError("Google Sheets API returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ExperimentError("Google Sheets API returned invalid response")
        return payload


def run_experiment(
    *,
    spreadsheet_id: str,
    token: str,
    poll_seconds: float,
    max_wait_seconds: float,
    timeout_seconds: float,
) -> ExperimentResult:
    result = ExperimentResult(
        started_at=_utc_now(), spreadsheet_id=spreadsheet_id
    )
    started = time.monotonic()
    api = SheetsApi(spreadsheet_id, token, timeout_seconds)
    try:
        if SHEET_NAME not in api.sheet_titles():
            raise ExperimentError(f"Required sheet is missing: {SHEET_NAME}")
        result.previous_formula_view = api.read_values(PAIR_RANGE, "FORMULA")
        result.previous_formatted_view = api.read_values(PAIR_RANGE, "FORMATTED_VALUE")
        write = api.write_test_cells()
        result.write_response = _safe_write_response(write)

        while True:
            result.polls += 1
            result.formula_view = api.read_values(PAIR_RANGE, "FORMULA")
            result.formatted_view = api.read_values(PAIR_RANGE, "FORMATTED_VALUE")
            formula_value = _cell(result.formula_view, 0, 1)
            displayed_value = _cell(result.formatted_view, 0, 1)

            if formula_value != AI_FORMULA:
                result.status = "formula_not_stored"
                result.reason = "B3 does not contain the requested formula"
                break
            if displayed_value in ERROR_VALUES:
                result.status = "formula_error"
                result.reason = f"Google Sheets displays {displayed_value}"
                break
            if (
                displayed_value.strip().casefold() not in PENDING_VALUES
                and not displayed_value.startswith("=")
            ):
                result.status = "success"
                result.reason = "B3 returned a non-empty calculated value through the API"
                break
            if time.monotonic() - started >= max_wait_seconds:
                result.status = "timeout"
                result.reason = "No calculated B3 value appeared before the time limit"
                break
            time.sleep(poll_seconds)
    except ExperimentError as exc:
        result.status = "error"
        result.reason = str(exc)
    finally:
        result.elapsed_seconds = round(time.monotonic() - started, 3)
        result.finished_at = _utc_now()
    return result


def _cell(values: list[list[str]] | None, row: int, column: int) -> str:
    if not values or len(values) <= row or len(values[row]) <= column:
        return ""
    return str(values[row][column])


def _safe_http_error(response: requests.Response) -> str:
    message = ""
    try:
        payload = response.json()
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        if isinstance(error, dict):
            message = str(error.get("message", ""))
    except ValueError:
        pass
    message = " ".join(message.split())[:300]
    suffix = f": {message}" if message else ""
    return f"Google Sheets API HTTP {response.status_code}{suffix}"


def _safe_write_response(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: payload[key]
        for key in (
            "spreadsheetId",
            "updatedRange",
            "updatedRows",
            "updatedColumns",
            "updatedCells",
            "updatedData",
        )
        if key in payload
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _save_result(result: ExperimentResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(result), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Проверка программного запуска Google Sheets =AI() в A3:B3"
    )
    parser.add_argument(
        "--spreadsheet-id",
        default=os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", DEFAULT_SPREADSHEET_ID),
    )
    parser.add_argument("--poll-seconds", type=float, default=10.0)
    parser.add_argument("--max-wait-seconds", type=float, default=180.0)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    args = build_parser().parse_args(argv)
    token = os.getenv("GOOGLE_SHEETS_ACCESS_TOKEN", "").strip()
    if not token:
        print(
            "Ошибка: задайте короткоживущий пользовательский OAuth token в "
            "GOOGLE_SHEETS_ACCESS_TOKEN.",
            file=sys.stderr,
        )
        return 2
    if args.poll_seconds <= 0 or args.max_wait_seconds <= 0 or args.timeout_seconds <= 0:
        print("Ошибка: интервалы и тайм-ауты должны быть положительными.", file=sys.stderr)
        return 2
    result = run_experiment(
        spreadsheet_id=args.spreadsheet_id,
        token=token,
        poll_seconds=args.poll_seconds,
        max_wait_seconds=args.max_wait_seconds,
        timeout_seconds=args.timeout_seconds,
    )
    _save_result(result, args.output)
    print(f"Статус: {result.status}")
    print(f"Причина: {result.reason}")
    print(f"Опросов API: {result.polls}; прошло секунд: {result.elapsed_seconds}")
    print(f"Диагностика: {args.output}")
    return 0 if result.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
