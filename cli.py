"""Buffett Screener CLI."""
import asyncio
import json
import csv
import sys
import logging
from typing import Optional
import typer

from core.database import Database
from core.pipeline import Pipeline
from config.settings import DB_PATH

app = typer.Typer(
    name="buffett-screener",
    help="Multi-layer investment analysis pipeline inspired by Warren Buffett's framework.",
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)


def get_db() -> Database:
    return Database(DB_PATH)


def get_pipeline() -> Pipeline:
    db = get_db()
    db.init_db()
    return Pipeline(db)


@app.command()
def run(
    limit: Optional[int] = typer.Option(None, help="Limit number of companies to analyze"),
    resume: Optional[str] = typer.Option(None, "--resume", help="Resume a paused pipeline run"),
):
    """Run the full pipeline on the stock universe."""
    pipeline = get_pipeline()

    if resume:
        typer.echo(f"Resuming pipeline run: {resume}")
        run_id = asyncio.run(pipeline.resume(resume))
    else:
        typer.echo(f"Starting pipeline run (limit={limit or 'all'})...")
        run_id = asyncio.run(pipeline.run(limit=limit))

    typer.echo(f"\nPipeline run complete: {run_id}")

    summary = pipeline.db.get_run_summary(run_id)
    typer.echo(f"\nResults:")
    typer.echo(f"  Total analyzed: {summary.get('total', 0)}")
    typer.echo(f"  Filter 1 (Business Type): {summary.get('f1_passed', 0)} passed")
    typer.echo(f"  Filter 2 (Management Quality): {summary.get('f2_passed', 0)} passed")
    typer.echo(f"  Filter 3 (Valuation): {summary.get('f3_passed', 0)} passed")
    typer.echo(f"  Filter 4 (Capital Allocation): {summary.get('f4_passed', 0)} passed")
    typer.echo(f"  Final: {summary.get('final_passed', 0)} passed all filters")


@app.command()
def analyze(
    ticker: str = typer.Argument(..., help="Stock ticker to analyze"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
    full: bool = typer.Option(False, "--full", help="Run all filters even if a company fails (bypass filter gates)"),
    save: bool = typer.Option(True, "--save/--no-save", help="Save full analysis as markdown report"),
):
    """Run deep analysis on a single ticker."""
    pipeline = get_pipeline()

    typer.echo(f"Analyzing {ticker.upper()}...")
    result = asyncio.run(pipeline.run_single(ticker.upper(), verbose=verbose, bypass_filters=full))

    typer.echo(f"\n{'='*60}")
    typer.echo(f"ANALYSIS RESULTS: {result.company.ticker} ({result.company.name})")
    typer.echo(f"{'='*60}")

    if result.f1_result:
        status = "PASS" if result.f1_result.passed else "FAIL"
        typer.echo(f"\nFilter 1 (Business Type): {status}")
        typer.echo(f"  {result.f1_result.reasoning}")

    if result.f2_result:
        status = "PASS" if result.f2_result.passed else "FAIL"
        score = f" ({result.f2_result.score:.1f}/100)" if result.f2_result.score is not None else ""
        typer.echo(f"\nFilter 2 (Management Quality): {status}{score}")
        if result.f2_scores:
            typer.echo(f"  Business Clarity: {result.f2_scores.business_clarity:.1f}/10")
            typer.echo(f"  Risk Honesty: {result.f2_scores.risk_honesty:.1f}/10")
            typer.echo(f"  MD&A Transparency: {result.f2_scores.mda_transparency:.1f}/10")
            typer.echo(f"  KPI Quality: {result.f2_scores.kpi_quality:.1f}/10")
            typer.echo(f"  Tone Authenticity: {result.f2_scores.tone_authenticity:.1f}/10")

    if result.f3_result:
        status = "PASS" if result.f3_result.passed else "FAIL"
        typer.echo(f"\nFilter 3 (Valuation): {status}")
        if result.f3_valuation:
            if result.f3_valuation.intrinsic_value:
                typer.echo(f"  Intrinsic Value: ${result.f3_valuation.intrinsic_value:.2f}")
            if result.f3_valuation.current_price:
                typer.echo(f"  Current Price: ${result.f3_valuation.current_price:.2f}")
            if result.f3_valuation.margin_of_safety is not None:
                typer.echo(f"  Margin of Safety: {result.f3_valuation.margin_of_safety:.1%}")
            if result.f3_valuation.moat_type:
                typer.echo(f"  Moat: {result.f3_valuation.moat_type} (strength: {result.f3_valuation.moat_strength}/10)")

    if result.f4_result:
        status = "PASS" if result.f4_result.passed else "FAIL"
        score = f" ({result.f4_result.score:.1f}/100)" if result.f4_result.score is not None else ""
        typer.echo(f"\nFilter 4 (Capital Allocation): {status}{score}")
        if result.f4_scores:
            typer.echo(f"  Buyback Quality: {result.f4_scores.buyback_quality:.1f}/10")
            typer.echo(f"  Capital Return: {result.f4_scores.capital_return:.1f}/10")
            typer.echo(f"  Acquisition Quality: {result.f4_scores.acquisition_quality:.1f}/10")
            typer.echo(f"  Debt Management: {result.f4_scores.debt_management:.1f}/10")
            typer.echo(f"  Reinvestment Quality: {result.f4_scores.reinvestment_quality:.1f}/10")

    typer.echo(f"\nFinal Result: {'*** PASSED ALL FILTERS ***' if result.final_passed else 'Did not pass all filters'}")

    if save:
        from core.report import save_report
        path = save_report(result)
        typer.echo(f"\nReport saved: {path}")


@app.command()
def status(
    run_id: Optional[str] = typer.Argument(None, help="Pipeline run ID"),
):
    """Check the status of a pipeline run."""
    pipeline = get_pipeline()
    info = pipeline.get_status(run_id)

    if info.get("error"):
        typer.echo(info["error"])
        raise typer.Exit(1)

    typer.echo(f"Run: {info['run_id']}")
    typer.echo(f"Status: {info['status']}")
    typer.echo(f"Current Filter: {info['current_filter']}")
    typer.echo(f"Current Ticker Index: {info['current_ticker_idx']}")
    if info.get('started_at'):
        typer.echo(f"Started: {info['started_at']}")

    summary = info.get('summary', {})
    if summary:
        typer.echo(f"\nProgress:")
        typer.echo(f"  Total: {summary.get('total', 0)}")
        typer.echo(f"  F1 passed: {summary.get('f1_passed', 0)}")
        typer.echo(f"  F2 passed: {summary.get('f2_passed', 0)}")
        typer.echo(f"  F3 passed: {summary.get('f3_passed', 0)}")
        typer.echo(f"  F4 passed: {summary.get('f4_passed', 0)}")


@app.command()
def results(
    run_id: Optional[str] = typer.Option(None, help="Pipeline run ID"),
    format: str = typer.Option("json", help="Output format: json or csv"),
    passed_only: bool = typer.Option(False, "--passed-only", help="Only show companies that passed all filters"),
):
    """Export pipeline results."""
    db = get_db()
    db.init_db()

    if not run_id:
        typer.echo("Please provide a --run-id")
        raise typer.Exit(1)

    all_results = db.get_run_results(run_id)

    if passed_only:
        all_results = [r for r in all_results if r.get("final_passed")]

    if not all_results:
        typer.echo("No results found.")
        raise typer.Exit(0)

    if format == "json":
        typer.echo(json.dumps(all_results, indent=2, default=str))
    elif format == "csv":
        writer = csv.DictWriter(sys.stdout, fieldnames=all_results[0].keys())
        writer.writeheader()
        writer.writerows(all_results)


@app.command("db")
def db_command(
    action: str = typer.Argument(..., help="Database action: init"),
):
    """Database management commands."""
    if action == "init":
        db = get_db()
        db.init_db()
        typer.echo(f"Database initialized at {DB_PATH}")
    else:
        typer.echo(f"Unknown action: {action}")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
