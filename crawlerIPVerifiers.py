# main.py
# pip install PySide6 requests
# Run: python main.py
#
# Update: batch IP input + table output (IP | match | matched bots)

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import ipaddress
import requests
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

DEFAULT_SOURCES = [
    "https://www.bing.com/toolbox/bingbot.json",
    "https://openai.com/searchbot.json",
    "https://openai.com/chatgpt-user.json",
    "https://openai.com/gptbot.json",
    "https://developers.google.com/search/apis/ipranges/googlebot.json",
    "https://developers.google.com/search/apis/ipranges/special-crawlers.json",
    "https://developers.google.com/search/apis/ipranges/user-triggered-fetchers.json",
    "https://developers.google.com/search/apis/ipranges/user-triggered-fetchers-google.json",
]

# Friendly bot labels (used in output "matched bots" column)
DEFAULT_BOT_LABELS = {
    "https://www.bing.com/toolbox/bingbot.json": "Bingbot",
    "https://openai.com/searchbot.json": "OpenAI SearchBot",
    "https://openai.com/chatgpt-user.json": "ChatGPT-User",
    "https://openai.com/gptbot.json": "GPTBot",
    "https://developers.google.com/search/apis/ipranges/googlebot.json": "Googlebot",
    "https://developers.google.com/search/apis/ipranges/special-crawlers.json": "Google Special Crawlers",
    "https://developers.google.com/search/apis/ipranges/user-triggered-fetchers.json": "Google User-triggered Fetchers",
    "https://developers.google.com/search/apis/ipranges/user-triggered-fetchers-google.json": "Google User-triggered Fetchers (Google)",
}

APP_DIR = Path.home() / ".ip_range_checker_gui"
CACHE_DIR = APP_DIR / "cache"
CONFIG_PATH = APP_DIR / "sources.json"


def ensure_dirs() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def safe_filename(url: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", url).strip("_")
    return s[:180] + ".json"


def now_ts() -> int:
    return int(time.time())


def fmt_ts(ts: Optional[int]) -> str:
    if not ts:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


@dataclass
class SourceResult:
    url: str
    ok: bool
    from_cache: bool
    fetched_at: Optional[int]
    creation_time: Optional[str]
    prefixes_count: int
    networks_v4: List[ipaddress.IPv4Network]
    networks_v6: List[ipaddress.IPv6Network]
    error: Optional[str]


class SourceManager:
    def __init__(self) -> None:
        ensure_dirs()
        self.sources: List[str] = self.load_sources()

    def load_sources(self) -> List[str]:
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text("utf-8"))
                if isinstance(data, list) and all(isinstance(x, str) for x in data):
                    return data
            except Exception:
                pass
        return DEFAULT_SOURCES.copy()

    def save_sources(self, sources: List[str]) -> None:
        CONFIG_PATH.write_text(json.dumps(sources, indent=2, ensure_ascii=False), "utf-8")
        self.sources = sources

    def export_sources(self, path: str) -> None:
        Path(path).write_text(json.dumps(self.sources, indent=2, ensure_ascii=False), "utf-8")

    def import_sources(self, path: str) -> None:
        data = json.loads(Path(path).read_text("utf-8"))
        if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
            raise ValueError("Invalid sources file: must be a JSON array of URL strings.")
        self.save_sources(data)

    def cache_path_for(self, url: str) -> Path:
        return CACHE_DIR / safe_filename(url)

    def load_cached(self, url: str) -> Tuple[Optional[dict], Optional[int], Optional[str]]:
        p = self.cache_path_for(url)
        if not p.exists():
            return None, None, "No cache file."
        try:
            data = json.loads(p.read_text("utf-8"))
            fetched_at = None
            meta = data.get("_meta") if isinstance(data, dict) else None
            if isinstance(meta, dict):
                fetched_at = meta.get("fetched_at")
            return data, fetched_at, None
        except Exception as e:
            return None, None, f"Failed to read cache: {e}"

    def write_cache(self, url: str, json_data: dict) -> None:
        p = self.cache_path_for(url)
        payload = json_data
        if isinstance(payload, dict):
            payload = dict(payload)
            payload["_meta"] = {"url": url, "fetched_at": now_ts()}
        p.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")

    def fetch_json(self, url: str, timeout: int = 15) -> dict:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
            ),
            "Accept": "application/json,text/plain,*/*",
        }
        r = requests.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def parse_prefixes(
        self, obj: dict
    ) -> Tuple[Optional[str], List[ipaddress.IPv4Network], List[ipaddress.IPv6Network]]:
        creation_time = None
        if isinstance(obj, dict):
            creation_time = obj.get("creationTime") if isinstance(obj.get("creationTime"), str) else None
            prefixes = obj.get("prefixes", [])
        else:
            prefixes = []

        v4: List[ipaddress.IPv4Network] = []
        v6: List[ipaddress.IPv6Network] = []

        if not isinstance(prefixes, list):
            return creation_time, v4, v6

        for item in prefixes:
            if not isinstance(item, dict):
                continue
            p4 = item.get("ipv4Prefix")
            p6 = item.get("ipv6Prefix")
            if isinstance(p4, str):
                try:
                    v4.append(ipaddress.ip_network(p4, strict=False))  # type: ignore[arg-type]
                except Exception:
                    pass
            if isinstance(p6, str):
                try:
                    v6.append(ipaddress.ip_network(p6, strict=False))  # type: ignore[arg-type]
                except Exception:
                    pass
        return creation_time, v4, v6

    def load_all_sources(self, offline_only: bool = False) -> List[SourceResult]:
        results: List[SourceResult] = []
        for url in [u.strip() for u in self.sources if u.strip()]:
            if offline_only:
                data, fetched_at, err = self.load_cached(url)
                if data is None:
                    results.append(
                        SourceResult(
                            url=url,
                            ok=False,
                            from_cache=True,
                            fetched_at=fetched_at,
                            creation_time=None,
                            prefixes_count=0,
                            networks_v4=[],
                            networks_v6=[],
                            error=err,
                        )
                    )
                    continue
                try:
                    creation_time, v4, v6 = self.parse_prefixes(data)
                    results.append(
                        SourceResult(
                            url=url,
                            ok=True,
                            from_cache=True,
                            fetched_at=fetched_at,
                            creation_time=creation_time,
                            prefixes_count=len(v4) + len(v6),
                            networks_v4=v4,
                            networks_v6=v6,
                            error=None,
                        )
                    )
                except Exception as e:
                    results.append(
                        SourceResult(
                            url=url,
                            ok=False,
                            from_cache=True,
                            fetched_at=fetched_at,
                            creation_time=None,
                            prefixes_count=0,
                            networks_v4=[],
                            networks_v6=[],
                            error=str(e),
                        )
                    )
                continue

            try:
                data = self.fetch_json(url)
                self.write_cache(url, data)
                creation_time, v4, v6 = self.parse_prefixes(data)
                results.append(
                    SourceResult(
                        url=url,
                        ok=True,
                        from_cache=False,
                        fetched_at=now_ts(),
                        creation_time=creation_time,
                        prefixes_count=len(v4) + len(v6),
                        networks_v4=v4,
                        networks_v6=v6,
                        error=None,
                    )
                )
            except Exception as e_fetch:
                data, fetched_at, _ = self.load_cached(url)
                if data is not None:
                    try:
                        creation_time, v4, v6 = self.parse_prefixes(data)
                        results.append(
                            SourceResult(
                                url=url,
                                ok=True,
                                from_cache=True,
                                fetched_at=fetched_at,
                                creation_time=creation_time,
                                prefixes_count=len(v4) + len(v6),
                                networks_v4=v4,
                                networks_v6=v6,
                                error=f"Fetch failed; used cache. Fetch error: {e_fetch}",
                            )
                        )
                    except Exception as e_parse:
                        results.append(
                            SourceResult(
                                url=url,
                                ok=False,
                                from_cache=True,
                                fetched_at=fetched_at,
                                creation_time=None,
                                prefixes_count=0,
                                networks_v4=[],
                                networks_v6=[],
                                error=f"Fetch failed: {e_fetch}; cache parse failed: {e_parse}",
                            )
                        )
                else:
                    results.append(
                        SourceResult(
                            url=url,
                            ok=False,
                            from_cache=False,
                            fetched_at=None,
                            creation_time=None,
                            prefixes_count=0,
                            networks_v4=[],
                            networks_v6=[],
                            error=str(e_fetch),
                        )
                    )
        return results


class SourcesEditor(QWidget):
    def __init__(self, mgr: SourceManager, on_changed) -> None:
        super().__init__()
        self.mgr = mgr
        self.on_changed = on_changed

        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        self.btn_add = QPushButton("Add")
        self.btn_remove = QPushButton("Remove")
        self.btn_reset = QPushButton("Reset to defaults")
        top.addWidget(self.btn_add)
        top.addWidget(self.btn_remove)
        top.addStretch(1)
        top.addWidget(self.btn_reset)
        layout.addLayout(top)

        self.listw = QListWidget()
        self.listw.setSelectionMode(QListWidget.ExtendedSelection)
        layout.addWidget(self.listw, 1)

        bottom = QHBoxLayout()
        self.btn_import = QPushButton("Import JSON…")
        self.btn_export = QPushButton("Export JSON…")
        self.btn_save = QPushButton("Save")
        bottom.addWidget(self.btn_import)
        bottom.addWidget(self.btn_export)
        bottom.addStretch(1)
        bottom.addWidget(self.btn_save)
        layout.addLayout(bottom)

        self.populate()

        self.btn_add.clicked.connect(self.add_item)
        self.btn_remove.clicked.connect(self.remove_selected)
        self.btn_reset.clicked.connect(self.reset_defaults)
        self.btn_save.clicked.connect(self.save)
        self.btn_import.clicked.connect(self.import_sources)
        self.btn_export.clicked.connect(self.export_sources)

    def populate(self) -> None:
        self.listw.clear()
        for url in self.mgr.sources:
            item = QListWidgetItem(url)
            item.setFlags(item.flags() | Qt.ItemIsEditable)
            self.listw.addItem(item)

    def add_item(self) -> None:
        item = QListWidgetItem("https://example.com/your-bot.json")
        item.setFlags(item.flags() | Qt.ItemIsEditable)
        self.listw.addItem(item)
        self.listw.editItem(item)

    def remove_selected(self) -> None:
        for item in self.listw.selectedItems():
            self.listw.takeItem(self.listw.row(item))

    def reset_defaults(self) -> None:
        self.mgr.save_sources(DEFAULT_SOURCES.copy())
        self.populate()
        self.on_changed()

    def current_sources(self) -> List[str]:
        out: List[str] = []
        for i in range(self.listw.count()):
            txt = self.listw.item(i).text().strip()
            if txt:
                out.append(txt)
        return out

    def save(self) -> None:
        sources = self.current_sources()
        if not sources:
            QMessageBox.warning(self, "Invalid", "Source list is empty.")
            return
        self.mgr.save_sources(sources)
        self.on_changed()
        QMessageBox.information(self, "Saved", f"Saved {len(sources)} sources to:\n{CONFIG_PATH}")

    def import_sources(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import Sources JSON", str(Path.home()), "JSON (*.json)")
        if not path:
            return
        try:
            self.mgr.import_sources(path)
            self.populate()
            self.on_changed()
            QMessageBox.information(self, "Imported", f"Imported {len(self.mgr.sources)} sources.")
        except Exception as e:
            QMessageBox.critical(self, "Import failed", str(e))

    def export_sources(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Sources JSON", str(Path.home() / "sources.json"), "JSON (*.json)"
        )
        if not path:
            return
        try:
            self.mgr.export_sources(path)
            QMessageBox.information(self, "Exported", f"Exported to:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Export failed", str(e))


class CheckerWidget(QWidget):
    def __init__(self, mgr: SourceManager) -> None:
        super().__init__()
        self.mgr = mgr
        self.last_loaded: List[SourceResult] = []

        root = QVBoxLayout(self)

        input_box = QGroupBox("Batch IP Check")
        grid = QVBoxLayout(input_box)

        self.ip_text = QTextEdit()
        self.ip_text.setPlaceholderText(
            "Paste IPs here (one per line). Example:\n"
            "20.15.133.174\n"
            "66.249.66.1\n"
            "2001:4860:4801:10::1\n"
        )

        btn_row = QHBoxLayout()
        self.chk_offline = QCheckBox("Offline only (use cache)")
        self.btn_refresh = QPushButton("Load/Refresh IP ranges")
        self.btn_check = QPushButton("Check IPs")
        self.btn_export_csv = QPushButton("Export results CSV…")
        self.btn_export_csv.setEnabled(False)

        btn_row.addWidget(self.chk_offline)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_refresh)
        btn_row.addWidget(self.btn_check)
        btn_row.addWidget(self.btn_export_csv)

        grid.addWidget(self.ip_text, 1)
        grid.addLayout(btn_row)

        root.addWidget(input_box)

        splitter = QSplitter(Qt.Vertical)

        self.summary = QTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setPlaceholderText("Load status will appear here…")

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["IP", "Match", "Matched bots"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSortingEnabled(True)

        splitter.addWidget(self.summary)
        splitter.addWidget(self.table)
        splitter.setSizes([250, 450])

        root.addWidget(splitter, 1)

        self.btn_refresh.clicked.connect(self.refresh_sources)
        self.btn_check.clicked.connect(self.check_ips)
        self.btn_export_csv.clicked.connect(self.export_csv)

        self.render_intro()

        self.last_results: List[Tuple[str, bool, str]] = []

    def render_intro(self) -> None:
        self.summary.setPlainText(
            "1) Click “Load/Refresh IP ranges” to fetch & cache ranges.\n"
            "2) Paste IPs (one per line) and click “Check IPs”.\n\n"
            f"Cache directory:\n{CACHE_DIR}\n"
        )

    def refresh_sources(self) -> None:
        offline = self.chk_offline.isChecked()
        self.summary.setPlainText("Loading IP ranges...\n")
        QApplication.processEvents()

        results = self.mgr.load_all_sources(offline_only=offline)
        self.last_loaded = results

        ok_cnt = sum(1 for r in results if r.ok)
        bad_cnt = len(results) - ok_cnt
        cache_cnt = sum(1 for r in results if r.ok and r.from_cache)
        online_cnt = sum(1 for r in results if r.ok and not r.from_cache)

        lines = []
        lines.append(f"Loaded sources: {len(results)}")
        lines.append(f"OK: {ok_cnt} | Failed: {bad_cnt}")
        lines.append(f"OK (online): {online_cnt} | OK (cache): {cache_cnt}")
        lines.append("")
        for r in results:
            status = "OK" if r.ok else "FAIL"
            src = "CACHE" if r.from_cache else "ONLINE"
            note = f" | note: {r.error}" if r.error else ""
            lines.append(
                f"- {status} [{src}] prefixes={r.prefixes_count} creationTime={r.creation_time or '-'} fetchedAt={fmt_ts(r.fetched_at)}\n"
                f"  {r.url}{note}"
            )

        self.summary.setPlainText("\n".join(lines))

    def _extract_ips(self, text: str) -> List[str]:
        lines = []
        for raw in text.splitlines():
            s = raw.strip()
            if not s:
                continue
            # remove inline comments after whitespace '#'
            s = re.split(r"\s+#", s, maxsplit=1)[0].strip()
            if s:
                lines.append(s)
        return lines

    def _match_one_ip(self, ip_str: str) -> Tuple[bool, str]:
        # returns (matched?, matched_bots_string)
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except Exception:
            return False, "Invalid IP"

        matched_labels: List[str] = []
        for r in self.last_loaded:
            if not r.ok:
                continue
            label = DEFAULT_BOT_LABELS.get(r.url, r.url)

            if isinstance(ip_obj, ipaddress.IPv4Address):
                for net in r.networks_v4:
                    if ip_obj in net:
                        matched_labels.append(label)
                        break
            else:
                for net in r.networks_v6:
                    if ip_obj in net:
                        matched_labels.append(label)
                        break

        if matched_labels:
            # de-dup while keeping order
            seen = set()
            uniq = []
            for x in matched_labels:
                if x not in seen:
                    seen.add(x)
                    uniq.append(x)
            return True, ", ".join(uniq)
        return False, ""

    def check_ips(self) -> None:
        ips = self._extract_ips(self.ip_text.toPlainText())
        if not ips:
            QMessageBox.warning(self, "Invalid", "Please paste at least one IP (one per line).")
            return

        if not self.last_loaded:
            self.refresh_sources()

        # build results
        results: List[Tuple[str, bool, str]] = []
        for ip in ips:
            matched, matched_bots = self._match_one_ip(ip)
            results.append((ip, matched, matched_bots))

        self.last_results = results
        self.btn_export_csv.setEnabled(True)

        # render table
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)

        for ip, matched, bots in results:
            row = self.table.rowCount()
            self.table.insertRow(row)

            self.table.setItem(row, 0, QTableWidgetItem(ip))
            self.table.setItem(row, 1, QTableWidgetItem("YES" if matched else "NO"))
            self.table.setItem(row, 2, QTableWidgetItem(bots))

            # visual hints
            for col in range(3):
                item = self.table.item(row, col)
                if item:
                    item.setFlags(item.flags() ^ Qt.ItemIsEditable)

        self.table.setSortingEnabled(True)

        yes = sum(1 for _, m, _ in results if m)
        no = len(results) - yes
        self.summary.append(f"\nChecked IPs: {len(results)} | Match: {yes} | No match: {no}")

    def export_csv(self) -> None:
        if not self.last_results:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export Results CSV", str(Path.home() / "ip_check_results.csv"), "CSV (*.csv)")
        if not path:
            return

        # Simple CSV (escape quotes)
        def esc(s: str) -> str:
            s = s.replace('"', '""')
            return f'"{s}"'

        lines = ["ip,match,matched_bots"]
        for ip, matched, bots in self.last_results:
            lines.append(",".join([esc(ip), "YES" if matched else "NO", esc(bots)]))

        try:
            Path(path).write_text("\n".join(lines), "utf-8")
            QMessageBox.information(self, "Exported", f"Saved:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Export failed", str(e))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("IP Range Checker (Batch)")
        self.resize(1050, 760)

        self.mgr = SourceManager()

        self.tabs = QTabWidget()
        self.checker = CheckerWidget(self.mgr)
        self.sources = SourcesEditor(self.mgr, on_changed=self.on_sources_changed)

        self.tabs.addTab(self.checker, "Checker")
        self.tabs.addTab(self.sources, "Sources")
        self.setCentralWidget(self.tabs)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.update_status()

        self._build_menu()

    def _build_menu(self) -> None:
        menu = self.menuBar()

        file_menu = menu.addMenu("File")
        act_open_cache = QAction("Open cache folder", self)
        act_open_cache.triggered.connect(self.open_cache_folder)
        file_menu.addAction(act_open_cache)

        act_quit = QAction("Quit", self)
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        help_menu = menu.addMenu("Help")
        act_about = QAction("About", self)
        act_about.triggered.connect(self.about)
        help_menu.addAction(act_about)

    def update_status(self) -> None:
        self.status.showMessage(f"Sources: {len(self.mgr.sources)} | Config: {CONFIG_PATH}")

    def on_sources_changed(self) -> None:
        self.checker.last_loaded = []
        self.checker.summary.append("\n\nSources changed. Please click “Load/Refresh IP ranges”.")
        self.update_status()

    def open_cache_folder(self) -> None:
        ensure_dirs()
        path = str(CACHE_DIR)
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            os.system(f'open "{path}"')
        else:
            os.system(f'xdg-open "{path}"')

    def about(self) -> None:
        QMessageBox.information(
            self,
            "About",
            "IP Range Checker (Batch)\n\n"
            "Batch checks whether IPv4/IPv6 addresses fall within published bot IP CIDR ranges.\n"
            "Output table: IP | Match | Matched bots\n\n"
            f"Config: {CONFIG_PATH}\nCache: {CACHE_DIR}",
        )


def main() -> None:
    ensure_dirs()
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
