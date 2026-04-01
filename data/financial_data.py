import logging
from typing import Dict, List, Optional, Any
from edgar import Company
from config.settings import EDGAR_IDENTITY

logger = logging.getLogger(__name__)

# XBRL concepts we need for capital allocation analysis
CAPITAL_ALLOCATION_CONCEPTS = {
    "buybacks": "us-gaap_PaymentsForRepurchaseOfCommonStock",
    "dividends": "us-gaap_PaymentsOfDividends",
    "acquisitions": "us-gaap_PaymentsToAcquireBusinessesNetOfCashAcquired",
    "depreciation": "us-gaap_DepreciationDepletionAndAmortization",
    "shares_outstanding": "us-gaap_CommonStockSharesOutstanding",
    "capex": "us-gaap_PaymentsToAcquirePropertyPlantAndEquipment",
    "net_income": "us-gaap_NetIncomeLoss",
    "revenue": "us-gaap_RevenueFromContractWithCustomerExcludingAssessedTax",
    "operating_income": "us-gaap_OperatingIncomeLoss",
    "total_equity": "us-gaap_StockholdersEquity",
    "total_assets": "us-gaap_Assets",
    "total_liabilities": "us-gaap_Liabilities",
    "operating_cash_flow": "us-gaap_NetCashProvidedByUsedInOperatingActivities",
    "cash": "us-gaap_CashAndCashEquivalentsAtCarryingValue",
    "long_term_debt": "us-gaap_LongTermDebt",
    "stock_comp": "us-gaap_ShareBasedCompensation",
    "interest_expense": "us-gaap_InterestExpense",
}


class FinancialDataService:
    """Extracts multi-year financial data from XBRL via edgartools."""

    def __init__(self):
        pass

    def get_financial_history(self, ticker: str, years: int = 10) -> List[Dict[str, Any]]:
        """
        Get up to `years` years of annual financial data for a ticker.
        Returns list of dicts, one per year, with all available XBRL fields.
        Most recent year first.
        """
        try:
            company = Company(ticker)
            filings = company.get_filings(form="10-K")

            if not filings or len(filings) == 0:
                logger.warning(f"{ticker}: no 10-K filings")
                return []

            count = min(years, len(filings))
            results = []

            for i in range(count):
                filing = filings[i]
                year_data = {
                    "fiscal_year": filing.filing_date.year if hasattr(filing, 'filing_date') else None,
                    "filing_date": str(filing.filing_date) if hasattr(filing, 'filing_date') else None,
                }

                try:
                    xbrl = filing.xbrl()
                    if xbrl is None:
                        tenk = filing.obj()
                        if hasattr(tenk, 'financials'):
                            year_data.update(self._extract_from_financials(tenk.financials))
                        results.append(year_data)
                        continue

                    for field_name, concept in CAPITAL_ALLOCATION_CONCEPTS.items():
                        try:
                            concept_clean = concept.replace("us-gaap_", "us-gaap:")
                            facts = xbrl.query(concept_clean) if hasattr(xbrl, 'query') else None
                            if facts is not None and len(facts) > 0:
                                val = self._get_annual_value(facts)
                                if val is not None:
                                    year_data[field_name] = val
                        except Exception:
                            pass

                    results.append(year_data)

                except Exception as e:
                    logger.warning(f"{ticker}: error extracting XBRL for year {i}: {e}")
                    results.append(year_data)

            for data in results:
                self._compute_derived_metrics(data)

            return results

        except Exception as e:
            logger.error(f"{ticker}: error getting financial history: {e}")
            return []

    def _get_annual_value(self, facts) -> Optional[float]:
        """Extract the annual value from a facts result."""
        try:
            if hasattr(facts, 'to_list'):
                values = facts.to_list()
                if values:
                    return float(values[0])
            elif hasattr(facts, 'value'):
                return float(facts.value)
            elif isinstance(facts, (int, float)):
                return float(facts)
            elif hasattr(facts, '__iter__'):
                for item in facts:
                    if hasattr(item, 'value'):
                        return float(item.value)
                    return float(item)
        except (ValueError, TypeError):
            pass
        return None

    def _extract_from_financials(self, financials) -> Dict[str, Any]:
        """Fallback: extract from a Financials object."""
        data = {}
        try:
            if hasattr(financials, 'income_statement'):
                inc = financials.income_statement
                if inc is not None:
                    data.update(self._dataframe_to_dict(inc, {
                        "Revenue": "revenue",
                        "Net Income": "net_income",
                        "Operating Income": "operating_income",
                    }))
            if hasattr(financials, 'balance_sheet'):
                bs = financials.balance_sheet
                if bs is not None:
                    data.update(self._dataframe_to_dict(bs, {
                        "Total Assets": "total_assets",
                        "Total Equity": "total_equity",
                        "Cash & Equivalents": "cash",
                    }))
            if hasattr(financials, 'cash_flow_statement'):
                cf = financials.cash_flow_statement
                if cf is not None:
                    data.update(self._dataframe_to_dict(cf, {
                        "Capital Expenditures": "capex",
                        "Net Cash from Operations": "operating_cash_flow",
                        "Stock Repurchases": "buybacks",
                    }))
        except Exception as e:
            logger.warning(f"Error extracting from financials object: {e}")
        return data

    def _dataframe_to_dict(self, df, mapping: Dict[str, str]) -> Dict[str, Any]:
        """Extract specific rows from a financial statement DataFrame."""
        data = {}
        try:
            for label, field in mapping.items():
                if hasattr(df, 'get_value'):
                    val = df.get_value(label)
                    if val is not None:
                        data[field] = float(val)
                elif hasattr(df, 'loc'):
                    try:
                        val = df.loc[label].iloc[0] if label in df.index else None
                        if val is not None:
                            data[field] = float(val)
                    except Exception:
                        pass
        except Exception:
            pass
        return data

    def _compute_derived_metrics(self, data: Dict[str, Any]) -> None:
        """Compute owner earnings, ROIC, capital intensity, margins."""
        ni = data.get("net_income")
        dep = data.get("depreciation", 0)
        capex = data.get("capex", 0)
        rev = data.get("revenue")
        eq = data.get("total_equity")
        assets = data.get("total_assets")
        op_income = data.get("operating_income")

        # Owner earnings = net income + depreciation - maintenance capex
        # Estimate maintenance capex as 70% of total capex (heuristic)
        if ni is not None:
            maintenance_capex = abs(capex) * 0.7 if capex else 0
            data["owner_earnings"] = ni + abs(dep) - maintenance_capex

        # ROIC = NOPAT / invested capital
        if op_income is not None and assets is not None:
            tax_rate = 0.21
            nopat = op_income * (1 - tax_rate)
            cash = data.get("cash", 0)
            current_liabilities = data.get("current_liabilities", 0)
            invested_capital = assets - cash - current_liabilities if assets else None
            if invested_capital and invested_capital > 0:
                data["roic"] = nopat / invested_capital

        # Capital intensity = capex / revenue
        if rev and rev > 0 and capex:
            data["capital_intensity"] = abs(capex) / rev

        # Margins
        if rev and rev > 0:
            if ni is not None:
                data["net_margin"] = ni / rev
            if op_income is not None:
                data["operating_margin"] = op_income / rev

        # ROE
        if ni is not None and eq and eq > 0:
            data["roe"] = ni / eq

        # ROA
        if ni is not None and assets and assets > 0:
            data["roa"] = ni / assets

    def compute_normalized_earnings(self, history: List[Dict[str, Any]], field: str = "owner_earnings") -> Optional[float]:
        """
        5-year average of owner earnings, dropping the best and worst year.
        """
        values = [d.get(field) for d in history[:5] if d.get(field) is not None]
        if len(values) < 3:
            return None

        values_sorted = sorted(values)
        trimmed = values_sorted[1:-1]
        return sum(trimmed) / len(trimmed) if trimmed else None

    def get_financial_summary(self, ticker: str) -> Dict[str, Any]:
        """
        Get a complete financial summary for a ticker, suitable for passing to valuation agents.
        """
        history = self.get_financial_history(ticker)
        if not history:
            return {"ticker": ticker, "error": "No financial data available"}

        normalized = self.compute_normalized_earnings(history)

        return {
            "ticker": ticker,
            "years_of_data": len(history),
            "history": history,
            "normalized_owner_earnings": normalized,
            "latest": history[0] if history else {},
        }
