import sqlite3
from contextlib import contextmanager
from typing import Optional, List
from datetime import datetime

from config.settings import DB_PATH
from core.models import PipelineState, PipelineStatus


class Database:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pipeline_state (
                    run_id TEXT PRIMARY KEY,
                    current_filter INTEGER,
                    current_ticker_idx INTEGER,
                    started_at TEXT,
                    paused_at TEXT,
                    completed_at TEXT,
                    status TEXT,
                    ticker_limit INTEGER
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analysis_results (
                    ticker TEXT,
                    run_id TEXT,
                    f1_passed BOOLEAN, f1_reason TEXT,
                    f2_passed BOOLEAN, f2_score REAL,
                    f2_business_clarity REAL, f2_risk_honesty REAL,
                    f2_mda_transparency REAL, f2_kpi_quality REAL, f2_tone REAL,
                    f2_reasoning TEXT,
                    f3_passed BOOLEAN, f3_normalized_earnings REAL,
                    f3_moat_type TEXT, f3_moat_strength REAL,
                    f3_earning_power_multiple REAL, f3_intrinsic_value REAL,
                    f3_current_price REAL, f3_margin_of_safety REAL,
                    f3_reasoning TEXT,
                    f4_passed BOOLEAN, f4_score REAL,
                    f4_buyback_quality REAL, f4_capital_return REAL,
                    f4_acquisition_quality REAL, f4_debt_management REAL,
                    f4_reinvestment_quality REAL,
                    f4_reasoning TEXT,
                    final_passed BOOLEAN,
                    analyzed_at TEXT,
                    UNIQUE(ticker, run_id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tenk_cache (
                    ticker TEXT,
                    filing_date TEXT,
                    accession TEXT,
                    section TEXT,
                    text_content TEXT,
                    token_estimate INTEGER,
                    UNIQUE(ticker, accession, section)
                )
            """)

    # -- Pipeline State --

    def save_pipeline_state(self, state: PipelineState):
        with self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO pipeline_state
                    (run_id, current_filter, current_ticker_idx, started_at, paused_at, completed_at, status, ticker_limit)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                state.run_id,
                state.current_filter,
                state.current_ticker_idx,
                state.started_at.isoformat() if state.started_at else None,
                state.paused_at.isoformat() if state.paused_at else None,
                state.completed_at.isoformat() if state.completed_at else None,
                state.status.value,
                state.ticker_limit,
            ))

    def load_pipeline_state(self, run_id: str) -> Optional[PipelineState]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM pipeline_state WHERE run_id = ?", (run_id,)
            ).fetchone()
        if not row:
            return None
        return PipelineState(
            run_id=row["run_id"],
            current_filter=row["current_filter"],
            current_ticker_idx=row["current_ticker_idx"],
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            paused_at=datetime.fromisoformat(row["paused_at"]) if row["paused_at"] else None,
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
            status=PipelineStatus(row["status"]),
            ticker_limit=row["ticker_limit"] if "ticker_limit" in row.keys() else None,
        )

    def update_pipeline_status(self, run_id: str, status: PipelineStatus, **kwargs):
        fields = ["status = ?"]
        values = [status.value]
        for key in ("current_filter", "current_ticker_idx"):
            if key in kwargs:
                fields.append(f"{key} = ?")
                values.append(kwargs[key])
        for key in ("paused_at", "completed_at"):
            if key in kwargs:
                val = kwargs[key]
                fields.append(f"{key} = ?")
                values.append(val.isoformat() if isinstance(val, datetime) else val)
        values.append(run_id)
        with self._conn() as conn:
            conn.execute(
                f"UPDATE pipeline_state SET {', '.join(fields)} WHERE run_id = ?",
                values,
            )

    # -- Analysis Results --

    def save_analysis_result(self, ticker: str, run_id: str, **kwargs):
        all_columns = [
            "f1_passed", "f1_reason",
            "f2_passed", "f2_score",
            "f2_business_clarity", "f2_risk_honesty",
            "f2_mda_transparency", "f2_kpi_quality", "f2_tone",
            "f2_reasoning",
            "f3_passed", "f3_normalized_earnings",
            "f3_moat_type", "f3_moat_strength",
            "f3_earning_power_multiple", "f3_intrinsic_value",
            "f3_current_price", "f3_margin_of_safety",
            "f3_reasoning",
            "f4_passed", "f4_score",
            "f4_buyback_quality", "f4_capital_return",
            "f4_acquisition_quality", "f4_debt_management",
            "f4_reinvestment_quality",
            "f4_reasoning",
            "final_passed", "analyzed_at",
        ]
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT * FROM analysis_results WHERE ticker = ? AND run_id = ?",
                (ticker, run_id),
            ).fetchone()

            if existing:
                # Update only provided fields
                update_cols = [k for k in kwargs if k in all_columns]
                if not update_cols:
                    return
                set_clause = ", ".join(f"{c} = ?" for c in update_cols)
                values = [kwargs[c] for c in update_cols]
                values.extend([ticker, run_id])
                conn.execute(
                    f"UPDATE analysis_results SET {set_clause} WHERE ticker = ? AND run_id = ?",
                    values,
                )
            else:
                # Insert new row with provided fields
                cols = ["ticker", "run_id"]
                vals = [ticker, run_id]
                for c in all_columns:
                    cols.append(c)
                    vals.append(kwargs.get(c))
                placeholders = ", ".join("?" for _ in cols)
                col_names = ", ".join(cols)
                conn.execute(
                    f"INSERT INTO analysis_results ({col_names}) VALUES ({placeholders})",
                    vals,
                )

    def load_analysis_result(self, ticker: str, run_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM analysis_results WHERE ticker = ? AND run_id = ?",
                (ticker, run_id),
            ).fetchone()
        if not row:
            return None
        return dict(row)

    def get_passed_tickers(self, run_id: str, filter_num: int) -> List[str]:
        col = f"f{filter_num}_passed"
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT ticker FROM analysis_results WHERE run_id = ? AND {col} = 1",
                (run_id,),
            ).fetchall()
        return [r["ticker"] for r in rows]

    def get_run_results(self, run_id: str) -> List[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM analysis_results WHERE run_id = ?", (run_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_run_summary(self, run_id: str) -> dict:
        with self._conn() as conn:
            row = conn.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN f1_passed = 1 THEN 1 ELSE 0 END) as f1_passed,
                    SUM(CASE WHEN f2_passed = 1 THEN 1 ELSE 0 END) as f2_passed,
                    SUM(CASE WHEN f3_passed = 1 THEN 1 ELSE 0 END) as f3_passed,
                    SUM(CASE WHEN f4_passed = 1 THEN 1 ELSE 0 END) as f4_passed,
                    SUM(CASE WHEN final_passed = 1 THEN 1 ELSE 0 END) as final_passed
                FROM analysis_results WHERE run_id = ?
            """, (run_id,)).fetchone()
        return dict(row)

    # -- 10-K Cache --

    def save_tenk_cache(self, ticker: str, filing_date: str, accession: str,
                        section: str, text_content: str):
        token_estimate = len(text_content) // 4 if text_content else 0
        with self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO tenk_cache
                    (ticker, filing_date, accession, section, text_content, token_estimate)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (ticker, filing_date, accession, section, text_content, token_estimate))

    def load_tenk_cache(self, ticker: str, section: str,
                        accession: str = None) -> Optional[str]:
        with self._conn() as conn:
            if accession:
                row = conn.execute(
                    "SELECT text_content FROM tenk_cache WHERE ticker = ? AND section = ? AND accession = ?",
                    (ticker, section, accession),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT text_content FROM tenk_cache WHERE ticker = ? AND section = ? ORDER BY filing_date DESC LIMIT 1",
                    (ticker, section),
                ).fetchone()
        if not row:
            return None
        return row["text_content"]
